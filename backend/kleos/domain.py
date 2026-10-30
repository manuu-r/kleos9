import hashlib
import json
import math
from copy import deepcopy
from datetime import timedelta

from sqlalchemy import func, or_, select, text

from .auth import Principal
from .clarification import clarification_view, question_hash, validate_answer
from .contracts import Command, DomainError
from .db import (
    Artifact,
    Attempt,
    CommandReceipt,
    Consent,
    DailyBudget,
    Event,
    ExternalIntent,
    Invocation,
    NativeJob,
    Organization,
    Outbox,
    Project,
    ProjectMembership,
    Release,
    Run,
    Task,
    Validation,
    now,
    uid,
)
from .design_intent import approved_context, freeze_for_confirmation
from .engineering_tools import DEFAULT_TOOL_POLICY, EngineeringToolRegistry, tool_policy
from .models import ModelRegistry
from .planning import (
    PLANNING_KINDS,
    append_message,
    concept_snapshot,
    context_manifest,
    planning_state,
    seed_project_concept,
    validate_part_reference,
)
from .telemetry import redact
from .telemetry import trace_id as current_trace_id

MODULES = (
    "kernel", "coordination", "mechanical", "electronics", "software",
    "quality", "commerce", "production", "lifecycle",
)
ACTIVE = ("queued", "running", "waiting")
ENGINEERING_LANES = ("mechanics", "electronics", "firmware", "application")
ASYNC_LANES = {
    "project.capture": ["coordination"],
    "project.research": ["research"],
    "project.requestReview": ["coordination"],
    "project.solveInterfaces": ["coordination"],
    "project.generate": list(ENGINEERING_LANES),
    "project.verify": ["review"],
    "project.repair": list(ENGINEERING_LANES),
    "project.pivot": list(ENGINEERING_LANES),
    "project.prepareKit": ["coordination"],
}
SIMULATED = {
    "project.requestQuotes": ("quotes", 6),
    "project.placeOrders": ("prototype_order", 6),
    "project.createPhysicalTasks": ("physical_tasks", 7),
    "project.acceptPhysical": ("physical_acceptance", 7),
    "project.acceptKit": ("kit_acceptance", 8),
    "project.ship": ("shipment", 9),
    "project.activate": ("activation", 9),
}


def emit(session, project, kind, data, trace_id=None):
    # Callers hold the project row lock; cursor order follows transaction commit order.
    project.event_sequence += 1
    project.revision += 1
    session.add(Event(
        tenant_id=project.tenant_id, project_id=project.id, sequence=project.event_sequence,
        type=kind, data=redact(data), trace_id=trace_id or current_trace_id(),
    ))


def lock_platform(session):
    if session.bind.dialect.name == "postgresql":
        session.execute(text("SELECT pg_advisory_xact_lock(84922419)"))


def settle_native_reservation(session, job, project, refund=False):
    reservation = job.request.get("reservation")
    if not reservation or reservation.get("settled"):
        return
    amount = float(reservation["reservedUsd"])
    daily = session.get(DailyBudget, reservation["day"])
    project.reserved_usd = max(0, project.reserved_usd - amount)
    daily.reserved = max(0, daily.reserved - amount)
    debit = 0 if refund else amount
    project.spent_usd += debit
    daily.spent += debit
    job.request = {**job.request, "reservation": {**reservation, "settled": True, "allowanceDebitUsd": debit}}
    emit(session, project, "compute.nativeSettled", {"jobId": job.id, "allowanceDebitUsd": debit,
                                                   "basis": "conservative_reservation"})


def contract_hash(requirements, interfaces):
    return hashlib.sha256(json.dumps({"requirements": requirements, "interfaces": interfaces},
                                    sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def get_project(session, project_id, principal=None, write=False, lock=True):
    query = select(Project).where(Project.id == project_id, Project.deleted_at.is_(None))
    if principal:
        query = query.where(Project.tenant_id == principal.tenant_id)
    project = session.scalar(query.with_for_update() if lock else query)
    if not project:
        raise DomainError("Project not found", 404, "not_found")
    if principal and principal.role not in {"owner", "admin"} and project.owner_id != principal.subject:
        member = session.scalar(select(ProjectMembership).where(
            ProjectMembership.project_id == project.id, ProjectMembership.subject == principal.subject))
        if not member:
            raise DomainError("Project not found", 404, "not_found")
        if write and member.role == "viewer":
            raise DomainError("This project membership is read-only", 403, "forbidden")
    if principal and write:
        principal.require_write()
    return project


def project_view(project, settings=None):
    unlimited = bool(settings and settings.development_unlimited_usage)
    return redact({
        "id": project.id, "name": project.name, "brief": project.brief,
        "revision": project.revision, "engineeringRevision": project.engineering_revision,
        "policyRevision": project.policy_revision, "modelPolicy": project.model_policy,
        "stage": project.stage, "status": project.status, "requirements": project.requirements,
        "interfaces": project.interfaces, "state": {**project.state, "toolPolicy": tool_policy(project),
            "toolPolicyVersion": project.state.get("toolPolicyVersion", 0), "clarification": clarification_view(project),
            "planning": planning_state(project), "concept": concept_snapshot(project)},
        "acceptedReleaseId": project.accepted_release_id,
        "budget": {"allowanceUsd": None if unlimited else project.budget_usd, "unlimited": unlimited, "spentEstimateUsd": project.spent_usd,
                   "reservedUsd": project.reserved_usd},
        "mode": "real_engineering_simulated_fulfillment",
        "createdAt": project.created_at.isoformat(),
    })


class ControlPlane:
    def __init__(self, db, settings, store):
        self.db, self.settings, self.store = db, settings, store
        self.registry = ModelRegistry(settings)
        self.tool_registry = EngineeringToolRegistry(settings)
        self._workspace_cache = {}

    def command(self, principal: Principal, command: Command, key: str):
        principal.require_write()
        fingerprint = hashlib.sha256(json.dumps(command.model_dump(mode="json"), sort_keys=True,
                                                 separators=(",", ":")).encode()).hexdigest()
        with self.db.transaction() as session:
            # Serialize a tenant's command receipts so concurrent HTTP retries cannot race.
            session.scalar(select(Organization).where(
                Organization.id == principal.tenant_id).with_for_update())
            receipt = session.scalar(select(CommandReceipt).where(
                CommandReceipt.tenant_id == principal.tenant_id,
                CommandReceipt.actor_id == principal.subject, CommandReceipt.idempotency_key == key))
            if receipt:
                if receipt.fingerprint != fingerprint:
                    raise DomainError("Idempotency key was used for a different command", 409, "idempotency_conflict")
                if receipt.response.get("deleted"):
                    raise DomainError("This command belongs to a deleted project", 410, "project_deleted")
                return receipt.response
            command_id, payload = uid(), command.payload
            run_id = None
            if command.type == "project.create":
                brief = payload.get("brief", "")
                if not isinstance(brief, str):
                    raise DomainError("The brief must be text", 422)
                brief = brief.strip()
                if not brief or len(brief) > 10000:
                    raise DomainError("Provide a brief between 1 and 10,000 characters", 422)
                policy = self.registry.validate_policy(payload["modelPolicy"]) if "modelPolicy" in payload else (
                    self.registry.default_policy())
                tools = self.tool_registry.validate_policy(payload["toolPolicy"], principal.tenant_id) if "toolPolicy" in payload else (
                    deepcopy(DEFAULT_TOOL_POLICY))
                project = Project(
                    id=uid(), tenant_id=principal.tenant_id, owner_id=principal.subject,
                    name=brief.split("\n")[0][:80], brief=brief, model_policy=policy,
                    budget_usd=self.settings.project_budget_usd,
                    state={"confirmed": False, "toolPolicy": tools, "toolPolicyVersion": 1},
                )
                session.add(project)
                session.flush()
                emit(session, project, "project.created", {"brief": brief})
                auto_start = payload.get("autoStart", False)
                if not isinstance(auto_start, bool):
                    raise DomainError("autoStart must be true or false", 422)
                seed_project_concept(project)
                if auto_start:
                    project.state = {**project.state, "planning": {**planning_state(project), "autoResume": True,
                                     "pending": True, "phase": "capture"}}
                    append_message(project, "user", brief, "brief")
                    run_id = self._resume_planning(session, project, command_id)
            else:
                project = get_project(session, payload.get("projectId", ""), principal, write=True)
                if command.expected_revision != project.revision:
                    # Chat is append-only, and a hashed choice identifies its
                    # exact semantics. During unaccepted planning these can
                    # safely coexist with unrelated progress/event revisions.
                    safe_planning = (payload.get("autoResume") is True and project.stage <= 2
                                     and not project.state.get("confirmed") and not project.accepted_release_id
                                     and command.expected_revision is not None
                                     and command.expected_revision < project.revision)
                    safe_append = safe_planning and command.type == "project.message"
                    safe_answer = False
                    if safe_planning and command.type == "project.answer" and payload.get("questionHash"):
                        progress = clarification_view(project)
                        ident = payload.get("questionId", payload.get("questionKey"))
                        current = next((q for q in [*progress["questions"], *progress["resolvedQuestions"]]
                                        if q["id"] == ident or q["prompt"] == ident), None)
                        if not current or payload["questionHash"] != question_hash(current):
                            raise DomainError("This question changed; review its current choices", 409, "question_changed")
                        safe_answer = True
                    if not (safe_append or safe_answer):
                        raise DomainError("Project changed; refresh before applying this command", 409, "stale_revision")
                kind = command.type
                if kind == "project.setModelPolicy":
                    project.model_policy = self.registry.validate_policy(payload.get("modelPolicy", {}))
                    project.policy_revision += 1
                    emit(session, project, "modelPolicy.changed", {
                        "policyRevision": project.policy_revision, "appliesTo": "next_attempt",
                        "modelPolicy": project.model_policy,
                    })
                elif kind == "project.setToolPolicy":
                    tools = self.tool_registry.validate_policy(payload.get("toolPolicy"), principal.tenant_id)
                    if tools != tool_policy(project):
                        version = project.state.get("toolPolicyVersion", 0) + 1
                        project.state = {**project.state, "toolPolicy": tools, "toolPolicyVersion": version}
                        emit(session, project, "toolPolicy.changed", {
                            "toolPolicyVersion": version, "appliesTo": "next_attempt", "toolPolicy": tools})
                elif kind == "project.answer":
                    question_key = payload.get("questionId", payload.get("questionKey"))
                    clarification = clarification_view(project)
                    question = next((q for q in [*clarification["questions"], *clarification["resolvedQuestions"]]
                                     if q["id"] == question_key or q["prompt"] == question_key), None)
                    if question is None:
                        raise DomainError("This question is no longer available; refresh the requirements", 409, "unknown_question")
                    self._check_planning_input(session, project, payload)
                    if payload.get("questionHash") is not None and payload["questionHash"] != question_hash(question):
                        raise DomainError("This question changed; review its current choices", 409, "question_changed")
                    answer = validate_answer(question, payload.get("values"))
                    if question["id"] in clarification["answers"] and clarification["answers"][question["id"]] == answer:
                        if payload.get("autoResume") is True and clarification["needsReview"]:
                            project.state = {**project.state, "planning": {**planning_state(project), "autoResume": True, "pending": True}}
                            run_id = self._resume_planning(session, project, command_id)
                    else:
                        previous_state = project.state
                        if previous_state.get("confirmed") or project.interfaces:
                            self._invalidate(session, project, "requirements_changed")
                            retained = {k: v for k, v in previous_state.items()
                                        if k in {"proposal", "answers", "clarificationAnswers", "clarificationAnswerVersion",
                                                 "clarificationReviewedVersion", "proposalReview", "planning", "conversation",
                                                 "concept", "conceptHistory", "clientMessageIds"}}
                            project.state = {**project.state, **retained}
                        records = {**project.state.get("clarificationAnswers", {}), question["id"]: {
                            "values": answer, "questionHash": question_hash(question),
                            "question": {k: v for k, v in question.items() if k != "questionHash"}}}
                        project.state = {**project.state, "confirmed": False, "clarificationAnswers": records,
                                         "answers": {**project.state.get("answers", {}), question["id"]: answer},
                                         "clarificationAnswerVersion": project.state.get("clarificationAnswerVersion", 0) + 1}
                        progress = clarification_view(project)
                        emit(session, project, "requirements.answerSaved", {
                            "questionId": question["id"], "answeredCount": progress["answeredCount"],
                            "remainingRequired": progress["remainingRequired"], "needsReview": progress["needsReview"]})
                        labels = [next((o["label"] for o in question["options"] if o["value"] == v), v) for v in answer]
                        message = append_message(project, "user", f"{question['prompt']} {', '.join(labels) if labels else 'No preference'}",
                                                 "answer", questionId=question["id"])
                        emit(session, project, "planning.userMessage", message)
                        self._record_planning_input(project, payload)
                        run_id = self._resume_planning(session, project, command_id)
                elif kind == "project.message":
                    self._check_planning_input(session, project, payload)
                    message_text = payload.get("text")
                    client_id = payload.get("clientMessageId")
                    if (not isinstance(message_text, str) or not 1 <= len(message_text.strip()) <= 4000
                            or not isinstance(client_id, str) or not 1 <= len(client_id) <= 100):
                        raise DomainError("Provide a message and a stable clientMessageId", 422, "invalid_message")
                    if client_id not in project.state.get("clientMessageIds", []):
                        reference = validate_part_reference(project, payload)
                        if project.state.get("confirmed") or project.interfaces:
                            previous_state = project.state
                            self._invalidate(session, project, "requirements_message")
                            retained = {k: v for k, v in previous_state.items() if k in {
                                "proposal", "answers", "clarificationAnswers", "clarificationAnswerVersion", "clarificationReviewedVersion",
                                "proposalReview", "planning", "conversation", "concept", "conceptHistory", "clientMessageIds"}}
                            project.state = {**project.state, **retained}
                        project.state = {**project.state, "confirmed": False,
                                         "clientMessageIds": [*project.state.get("clientMessageIds", []), client_id][-256:]}
                        message = append_message(project, "user", message_text.strip(), "chat", clientMessageId=client_id, **reference)
                        emit(session, project, "planning.userMessage", message)
                        self._record_planning_input(project, payload)
                        run_id = self._resume_planning(session, project, command_id)
                elif kind == "project.confirm":
                    if session.scalar(select(Run.id).where(Run.project_id == project.id, Run.status.in_(ACTIVE))):
                        raise DomainError("Wait for the current requirements review before confirming", 409, "run_active")
                    if not project.state.get("proposal"):
                        raise DomainError("Research the concept before confirming it", 409)
                    planning = planning_state(project)
                    if planning["autoResume"] and not planning["feasibilityComplete"]:
                        raise DomainError("Complete feasibility planning before confirming", 409, "feasibility_required")
                    clarification = clarification_view(project)
                    if clarification["remainingRequired"]:
                        raise DomainError("Answer the remaining required questions before confirming", 409, "clarification_required")
                    if clarification["needsReview"]:
                        raise DomainError("Review your saved answers before confirming the design", 409, "clarification_review_required")
                    proposal = project.state["proposal"]
                    requirements = ({**project.requirements, **proposal.get("requirements", {})}
                                    if project.state.get("proposalReview") or project.state.get("clarificationReviewedVersion", 0) > 0
                                    else {**proposal.get("requirements", {}), **project.requirements})
                    # A proposed requirement can never manufacture approval authority.
                    requirements.pop("designIntent", None)
                    changed_requirements = requirements != {k: v for k, v in project.requirements.items() if k != "designIntent"}
                    approval, design_intent = freeze_for_confirmation(project, principal.subject, now().isoformat(),
                                                                     force_new=changed_requirements)
                    if design_intent is not None:
                        requirements["designIntent"] = design_intent
                    if requirements.get("validationProfile") == "watch-telemetry-v1":
                        required = requirements.get("requiredPeripheralCoverage")
                        excluded = requirements.get("excludedCoverage")
                        known = {"ble", "sensor", "display", "radio", "thermal", "physicalOperation"}
                        if (not isinstance(required, list) or not isinstance(excluded, list)
                                or any(not isinstance(v, str) for v in required + excluded)
                                or set(required) & set(excluded) or not set(excluded).issubset(known)
                                or not known.issubset(set(required) | set(excluded))):
                            raise DomainError("Explicitly review requiredPeripheralCoverage and excludedCoverage before confirming", 422,
                                              "scope_required")
                    if requirements != project.requirements and (
                            project.state.get("confirmed") or project.interfaces or project.accepted_release_id):
                        retained = {k: v for k, v in project.state.items()
                                    if k in {"answers", "clarificationAnswers", "clarificationAnswerVersion",
                                             "clarificationReviewedVersion", "proposalReview", "planning", "conversation", "concept", "conceptHistory", "clientMessageIds"}}
                        self._invalidate(session, project, "requirements_reconfirmed")
                        project.state = {**project.state, **retained}
                    if approval is not None:
                        previous = project.state.get("approvedDesign")
                        history = project.state.get("approvedDesignHistory", [])
                        if not previous or previous.get("version") != approval["version"]:
                            history = [*history, {key: approval[key] for key in (
                                "version", "sceneVersion", "sceneHash", "approvedAt", "approvedBy")}][-24:]
                            approval["engineeringRevision"] = project.engineering_revision
                        project.state = {**project.state, "approvedDesign": approval, "approvedDesignHistory": history,
                                         "approvedDesignStatus": "approved", "designIntentCheck": {"status": "UNKNOWN", "details": {
                                             "sceneHash": approval["sceneHash"], "reasonCode": "awaiting_engineering_checks",
                                             "reason": "Awaiting engineering dimensions and independent checks"}}}
                    project.state = {**project.state, "proposal": proposal, "confirmed": True}
                    project.requirements = requirements
                    project.stage = max(3, project.stage)
                    emit(session, project, "concept.confirmed", {"actor": principal.subject})
                elif kind == "project.capture" and payload.get("autoResume") is True:
                    self._check_planning_input(session, project, payload)
                    project.state = {**project.state, "planning": {**planning_state(project), "autoResume": True, "pending": True}}
                    run_id = self._resume_planning(session, project, command_id)
                elif kind in ASYNC_LANES:
                    run_id = self._enqueue(session, project, command_id, kind, payload)
                elif kind == "project.retryNative":
                    run_id = self._retry_native(session, project, command_id, payload)
                elif kind == "project.cancel":
                    project.state = {**project.state, "planning": {**planning_state(project), "autoResume": False,
                                     "pending": False, "phase": "cancelled"}}
                    self._cancel(session, project, "Cancelled by the user")
                    emit(session, project, "project.cancelled", {})
                elif kind == "project.retry":
                    previous = session.scalar(select(Run).where(
                        Run.id == payload.get("runId"), Run.project_id == project.id,
                        Run.status.in_(["blocked", "failed"])))
                    if not previous:
                        raise DomainError("Choose a failed or blocked run", 409)
                    if previous.kind == "project.retryNative":
                        raise DomainError("Use project.retryNative with the previous native job to rebuild without inference", 409,
                                          "native_retry_required")
                    run_id = self._enqueue(session, project, command_id, previous.kind, payload)
                elif kind == "project.swapBattery":
                    capacity = payload.get("capacityMah")
                    dimensions = payload.get("dimensionsMm")
                    values = [capacity, *(dimensions.values() if isinstance(dimensions, dict) else [])]
                    if (not isinstance(dimensions, dict) or set(dimensions) != {"x", "y", "z"}
                            or any(isinstance(v, bool) or not isinstance(v, (int, float))
                                   or not math.isfinite(v) or v <= 0 for v in values)):
                        raise DomainError("Provide battery capacityMah and dimensionsMm", 422)
                    self._invalidate(session, project, "battery_changed")
                    project.requirements = {**project.requirements, "battery": {
                        "capacityMah": capacity, "dimensionsMm": dimensions}}
                    project.state = {**project.state, "confirmed": False}
                    emit(session, project, "engineering.invalidated", {
                        "reason": "battery_changed", "lanes": list(ENGINEERING_LANES) + ["guide"]})
                elif kind == "project.acceptRelease":
                    self._accept_release(session, project, principal)
                elif kind in SIMULATED:
                    self._simulate(session, project, kind)
                elif kind == "project.setTrainingConsent":
                    value = payload.get("optedIn")
                    if not isinstance(value, bool):
                        raise DomainError("optedIn must be true or false", 422)
                    consent = session.scalar(select(Consent).where(
                        Consent.project_id == project.id, Consent.tenant_id == principal.tenant_id))
                    if not consent:
                        consent = Consent(tenant_id=principal.tenant_id, project_id=project.id,
                                          actor_id=principal.subject)
                        session.add(consent)
                        session.flush()
                    consent.opted_in = value
                    consent.version += 1
                    consent.updated_at = now()
                    consent.actor_id = principal.subject
                    from .training_consent import exclude_project_training
                    exclude_project_training(session, project)
                    emit(session, project, "training.consentChanged", {"optedIn": value, "version": consent.version})
                elif kind == "project.selectPrint":
                    if payload.get("mode") not in {"home", "order"}:
                        raise DomainError("Print mode must be home or order", 422)
                    project.state = {**project.state, "printMode": payload["mode"]}
                    emit(session, project, "fulfillment.preferenceChanged", {"mode": payload["mode"]})
                elif kind == "project.assemblyStep":
                    step = payload.get("step")
                    if not isinstance(step, int) or step < 0:
                        raise DomainError("An assembly step is required", 422)
                    project.state = {**project.state, "assemblyStep": step}
                    emit(session, project, "assembly.stepRecorded", {"step": step, "evidenceKind": "synthetic"})
                else:
                    raise DomainError(f"Unsupported command: {kind}", 422, "unsupported_command")
            response = {"commandId": command_id, "projectId": project.id,
                        "runId": run_id, "revision": project.revision}
            session.add(CommandReceipt(
                id=command_id, tenant_id=principal.tenant_id, actor_id=principal.subject,
                idempotency_key=key, fingerprint=fingerprint, type=command.type,
                project_id=project.id, response=response,
            ))
            return response

    def _check_planning_input(self, session, project, payload):
        automatic = payload.get("autoResume", planning_state(project)["autoResume"])
        if not isinstance(automatic, bool):
            raise DomainError("autoResume must be true or false", 422)
        active = session.scalars(select(Run).where(Run.project_id == project.id, Run.status.in_(ACTIVE))).all()
        if active and (not automatic or any(run.kind not in PLANNING_KINDS for run in active)):
            raise DomainError("Wait for the current work before changing requirements", 409, "run_active")

    @staticmethod
    def _record_planning_input(project, payload):
        state = planning_state(project)
        state = {**state, "autoResume": payload.get("autoResume", state["autoResume"]), "pending": True,
                 "inputVersion": state["inputVersion"] + 1, "notBefore": (now() + timedelta(seconds=.8)).isoformat()}
        project.state = {**project.state, "planning": state}
        if project.state.get("concept"):
            project.state = {**project.state, "concept": {**project.state["concept"], "stale": True}}

    def _resume_planning(self, session, project, command_id=None):
        state = planning_state(project)
        if not state["autoResume"] or project.state.get("confirmed"):
            return None
        active = session.scalar(select(Run).where(Run.project_id == project.id, Run.status.in_(ACTIVE)))
        if active:
            # A queued, unbilled run can absorb the latest user turn. An active
            # model invocation finishes against its pinned input, then coalesces.
            if active.status == "queued" and active.kind in PLANNING_KINDS:
                for task in session.scalars(select(Task).where(Task.run_id == active.id)):
                    task.result = {**task.result, "planningInputVersion": state["inputVersion"],
                                   "planningNotBefore": state.get("notBefore")}
                project.state = {**project.state, "planning": {**state, "pending": False}}
            return active.id
        progress = clarification_view(project)
        next_kind = None
        research_pending = (state["pendingResearch"] and not progress["remainingRequired"]
                            and state["lastResearchInputVersion"] < state["inputVersion"])
        if state["pending"]:
            next_kind = ("project.capture" if not state["captureComplete"] or (
                not state["feasibilityComplete"] and progress["remainingRequired"]) else
                "project.research" if not state["feasibilityComplete"] or research_pending else "project.requestReview")
        elif state["captureComplete"] and not state["feasibilityComplete"] and not progress["remainingRequired"]:
            next_kind = "project.research"
        elif research_pending:
            next_kind = "project.research"
        if next_kind is None:
            return None
        phase = {"project.capture": "capture", "project.research": "researching", "project.requestReview": "refining"}[next_kind]
        run_id = self._enqueue(session, project, command_id or uid(), next_kind, {})
        task = session.scalar(select(Task).where(Task.run_id == run_id))
        task.result = {**task.result, "autoPlanning": True, "planningInputVersion": state["inputVersion"],
                       "planningNotBefore": state.get("notBefore")}
        project.state = {**project.state, "planning": {**state, "pending": False, "phase": phase, "runId": run_id}}
        emit(session, project, "planning.queued", {"runId": run_id, "kind": next_kind, "inputVersion": state["inputVersion"]})
        return run_id

    def _enqueue(self, session, project, command_id, kind, payload):
        if session.scalar(select(Run.id).where(Run.project_id == project.id, Run.status.in_(ACTIVE))):
            raise DomainError("A run is already active for this project", 409, "run_active")
        if kind not in PLANNING_KINDS:
            approved_context(project)  # legacy without an approved reference remains compatible
        if kind in {"project.generate", "project.repair", "project.pivot", "project.solveInterfaces"} and not project.state.get("confirmed"):
            raise DomainError("Confirm the concept before detailed generation", 409, "confirmation_required")
        if kind in {"project.generate", "project.repair", "project.pivot"} and not project.interfaces:
            raise DomainError("Establish shared interfaces first", 409)
        if kind == "project.pivot" and (not isinstance(payload.get("reason"), str)
                                        or not 20 <= len(payload["reason"]) <= 2000):
            raise DomainError("A pivot requires a justification between 20 and 2,000 characters", 422)
        if kind == "project.verify" and not session.scalar(select(Artifact.id).where(
                Artifact.project_id == project.id, Artifact.revision == project.engineering_revision)):
            raise DomainError("Generate candidate artifacts before verification", 409)
        if kind == "project.prepareKit" and not project.state.get("physical_acceptance"):
            raise DomainError("Complete simulated physical review first", 409)
        if kind == "project.solveInterfaces" and project.interfaces:
            proposal = project.state.get("proposal", {})
            self._invalidate(session, project, "interfaces_revised")
            project.state = {**project.state, "confirmed": True, "proposal": proposal}
        run_id = uid()
        run = Run(id=run_id, tenant_id=project.tenant_id, project_id=project.id,
                  command_id=command_id, kind=kind, revision=project.engineering_revision,
                  trace_id=current_trace_id() or uid().replace("-", ""))
        session.add(run)
        session.flush()
        lanes = ASYNC_LANES[kind]
        if kind in {"project.generate", "project.repair", "project.pivot"} and "lane" in payload:
            if payload["lane"] not in ENGINEERING_LANES:
                raise DomainError("Choose a supported engineering lane", 422, "invalid_lane")
            lanes = [payload["lane"]]
        planning = planning_state(project)
        automatic = kind in PLANNING_KINDS and planning["autoResume"]
        for lane in lanes:
            metadata = {"pivotReason": payload["reason"]} if kind == "project.pivot" else {}
            if automatic:
                metadata = {**metadata, "autoPlanning": True, "planningInputVersion": planning["inputVersion"],
                            "planningNotBefore": planning.get("notBefore")}
            session.add(Task(id=uid(), tenant_id=project.tenant_id, project_id=project.id,
                             run_id=run_id, lane=lane, kind=kind, revision=project.engineering_revision, result=metadata))
        if automatic:
            phase = {"project.capture": "capture", "project.research": "researching", "project.requestReview": "refining"}[kind]
            project.state = {**project.state, "planning": {**planning, "pending": False, "phase": phase, "runId": run_id}}
        session.add(Outbox(key=f"run:{run_id}", kind="run", payload={"runId": run_id}))
        project.status = "queued"
        emit(session, project, "run.queued", {"runId": run_id, "kind": kind}, run.trace_id)
        return run_id

    def _retry_native(self, session, project, command_id, payload):
        """Create a pinned native-only attempt; original source bytes/provenance remain immutable."""
        from_attempt = set(payload) == {"projectId", "sourceAttemptId"} and isinstance(payload.get("sourceAttemptId"), str)
        from_job = set(payload) == {"projectId", "jobId"} and isinstance(payload.get("jobId"), str)
        if not (from_attempt or from_job):
            raise DomainError("Native rebuild requires projectId and either jobId or sourceAttemptId", 422)
        if session.scalar(select(Run.id).where(Run.project_id == project.id, Run.status.in_(ACTIVE))):
            raise DomainError("A run is already active for this project", 409, "run_active")
        approved_context(project)
        if not project.state.get("confirmed") or not project.interfaces:
            raise DomainError("Confirm the current concept and interfaces before rebuilding", 409, "confirmation_required")
        if self.settings.execution_backend == "disabled":
            raise DomainError("Native worker images are not configured", 503, "native_unconfigured")
        job = session.get(NativeJob, payload["jobId"]) if from_job else None
        old_attempt = session.get(Attempt, job.attempt_id if job else payload.get("sourceAttemptId")) if job or from_attempt else None
        old_task = session.get(Task, old_attempt.task_id) if old_attempt else None
        if (not old_task or not old_attempt or (from_job and (not job or job.tenant_id != project.tenant_id))
                or old_task.project_id != project.id or old_task.lane not in ENGINEERING_LANES
                or old_task.tenant_id != project.tenant_id or old_attempt.tenant_id != project.tenant_id
                or old_attempt.task_id != old_task.id):
            raise DomainError("Native build source not found", 404)
        if (old_task.status in ACTIVE or old_task.revision != project.engineering_revision or (job and (
                job.purpose != "build" or job.status not in {"succeeded", "failed", "unsupported"}
                or job.request.get("revision") != project.engineering_revision))):
            raise DomainError("Choose completed source from the current engineering revision", 409, "native_retry_invalid")
        if from_attempt:
            if session.scalar(select(NativeJob.id).where(NativeJob.attempt_id == old_attempt.id)):
                raise DomainError("This source already has a native job; rebuild using its jobId", 409, "native_retry_invalid")
            recorded_model = session.scalar(select(Invocation.id).where(Invocation.attempt_id == old_attempt.id,
                Invocation.operation == "generate", Invocation.status == "succeeded"))
            if (old_task.kind not in {"project.generate", "project.repair", "project.pivot"}
                    or old_attempt.status not in {"succeeded", "blocked", "failed"} or not recorded_model
                    or not old_attempt.output.get("files")):
                raise DomainError("Choose a recorded model-generated source attempt awaiting native execution", 409, "native_retry_invalid")
        source_request = job.request if job else old_attempt.context
        current_contract = contract_hash(project.requirements, project.interfaces)
        if (source_request.get("retentionExpired") or old_attempt.context.get("retentionExpired")
                or contract_hash(source_request.get("requirements", {}), source_request.get("interfaces", {})) != current_contract):
            raise DomainError("The original native inputs or engineering contract are no longer available", 409, "stale_artifacts")
        root_attempt_id = source_request.get("nativeRetry", {}).get("rootSourceAttemptId", old_attempt.id) if job else old_attempt.id
        used = session.scalar(select(func.count(Attempt.id)).join(Task, Task.id == Attempt.task_id).where(
            Task.project_id == project.id, Task.revision == project.engineering_revision, Task.kind == "project.retryNative",
            Attempt.context["nativeRetry"]["rootSourceAttemptId"].as_string() == root_attempt_id))
        if used >= 2 and not self.settings.development_unlimited_usage:
            raise DomainError("Two native rebuilds were already requested for this source; review the blocker", 409,
                              "native_retry_exhausted")
        refs = job.request.get("inputArtifacts") if job else [{
            "id": artifact.id, "name": artifact.name, "lane": artifact.lane, "sha256": artifact.sha256,
            "size": artifact.size, "mediaType": artifact.media_type, "provenance": deepcopy(artifact.provenance),
        } for artifact in session.scalars(select(Artifact).where(
            Artifact.attempt_id == old_attempt.id, Artifact.lane == old_task.lane,
            Artifact.status.in_(["candidate", "accepted"])).order_by(Artifact.name))
            if not artifact.name.startswith("native/") and not artifact.provenance.get("nativeJobId")]
        if from_attempt:
            output_files = old_attempt.output.get("files")
            if (not isinstance(output_files, list) or any(not isinstance(item, dict) for item in output_files)
                    or len({item.get("path") for item in output_files}) != len(output_files)
                    or {item.get("path") for item in output_files} != {ref["name"] for ref in refs}):
                raise DomainError("The complete generated source closure is no longer available", 409, "stale_artifacts")
        if (not isinstance(refs, list) or not refs or len(refs) > 50
                or any(not isinstance(ref, dict) or not isinstance(ref.get("id"), str) for ref in refs)
                or len({ref["id"] for ref in refs}) != len(refs)):
            raise DomainError("The native build has no complete reusable source manifest", 409, "stale_artifacts")
        source_ids = {ref["id"] for ref in refs}
        for ref in refs:
            artifact = session.get(Artifact, ref["id"])
            expected = {"id": artifact.id, "name": artifact.name, "lane": artifact.lane, "sha256": artifact.sha256,
                        "size": artifact.size, "mediaType": artifact.media_type, "provenance": artifact.provenance} if artifact else None
            if (not artifact or artifact.tenant_id != project.tenant_id or artifact.project_id != project.id
                    or artifact.revision != project.engineering_revision or artifact.lane != old_task.lane
                    or artifact.attempt_id != root_attempt_id or artifact.status not in {"candidate", "accepted"}
                    or artifact.name.startswith("native/") or artifact.provenance.get("nativeJobId") or expected != ref):
                raise DomainError("Native source artifacts were changed or superseded", 409, "stale_artifacts")
        current = session.scalars(select(Artifact).where(Artifact.project_id == project.id,
            Artifact.revision == project.engineering_revision, Artifact.lane == old_task.lane,
            Artifact.status.in_(["candidate", "accepted"]))).all()
        if any(a.id not in source_ids and not (a.name.startswith("native/") or a.provenance.get("nativeJobId")) for a in current):
            raise DomainError("A newer source closure exists; choose its completed native job", 409, "stale_artifacts")
        run = Run(id=uid(), tenant_id=project.tenant_id, project_id=project.id, command_id=command_id,
                  kind="project.retryNative", revision=project.engineering_revision,
                  trace_id=current_trace_id() or uid().replace("-", ""))
        session.add(run)
        session.flush()
        task = Task(id=uid(), tenant_id=project.tenant_id, project_id=project.id, run_id=run.id,
                    lane=old_task.lane, kind="project.retryNative", revision=project.engineering_revision,
                    fence=1, attempt_count=1)
        session.add(task)
        session.flush()
        retry = {"sourceJobId": job.id if job else None, "sourceKind": "job" if job else "attempt",
                 "sourceAttemptId": old_attempt.id, "sourceTaskId": old_task.id,
                 "rootSourceAttemptId": root_attempt_id, "inputArtifacts": deepcopy(refs),
                 "contractHash": current_contract, "number": used + 1}
        attempt = Attempt(id=uid(), tenant_id=project.tenant_id, task_id=task.id, number=1, fence=1,
                          policy_revision=old_attempt.policy_revision, provider=old_attempt.provider, model=old_attempt.model,
                          context={**deepcopy(old_attempt.context), "nativeRetry": retry}, status="waiting")
        session.add(attempt)
        # Fence late callbacks; only existing native outputs are superseded, never the original sources.
        old_task.fence += 1
        for artifact in current:
            if artifact.id not in source_ids:
                artifact.status = "stale"
        if project.accepted_release_id:
            project.state = {**project.state, "previousAcceptedReleaseId": project.accepted_release_id}
            project.accepted_release_id = None
        project.stage, project.status = min(project.stage, 5), "queued"
        session.add(Outbox(key=f"run:{run.id}", kind="run", payload={"runId": run.id}))
        emit(session, project, "native.rebuildRequested", {"runId": run.id, "taskId": task.id, "attemptId": attempt.id,
            "sourceJobId": job.id if job else None, "lane": task.lane, "sourceAttemptId": old_attempt.id, "nativeRetryNumber": used + 1,
            "provider": attempt.provider, "model": attempt.model, "policyRevision": attempt.policy_revision,
            "modelInvoked": False, "requiresIndependentValidation": True}, run.trace_id)
        return run.id

    def _cancel(self, session, project, reason):
        for run in session.scalars(select(Run).where(Run.project_id == project.id, Run.status.in_(ACTIVE))):
            run.status, run.blocker, run.finished_at = "cancelled", reason, now()
            for task in session.scalars(select(Task).where(Task.run_id == run.id)):
                if task.status in ACTIVE:
                    task.status, task.fence = "cancelled", task.fence + 1
            session.add(Outbox(key=f"cancel:{run.id}", kind="cancel", payload={"runId": run.id}))
        project.status = "cancelled"

    def _invalidate(self, session, project, reason):
        self._cancel(session, project, reason)
        project.engineering_revision += 1
        project.interfaces = {}
        project.stage, project.status = 0, "draft"
        for artifact in session.scalars(select(Artifact).where(Artifact.project_id == project.id)):
            if artifact.status != "accepted":
                artifact.status = "stale"
        # Preserve the old accepted release as a baseline, not a current release authorization.
        project.state = {"previousAcceptedReleaseId": project.accepted_release_id,
                         "toolPolicy": tool_policy(project),
                         "toolPolicyVersion": project.state.get("toolPolicyVersion", 0),
                         **({"approvedDesign": project.state["approvedDesign"],
                             "approvedDesignHistory": project.state.get("approvedDesignHistory", []),
                             "approvedDesignStatus": "approved" if reason == "interfaces_revised" else "superseded"}
                            if project.state.get("approvedDesign") else {})}

    def _accept_release(self, session, project, principal):
        approved = approved_context(project)
        if session.scalar(select(Run.id).where(Run.project_id == project.id, Run.status.in_(ACTIVE))):
            raise DomainError("Wait for current engineering work before accepting a release", 409, "run_active")
        artifacts = session.scalars(select(Artifact).where(
            Artifact.project_id == project.id, Artifact.revision == project.engineering_revision,
            Artifact.status.in_(["candidate", "accepted"]))).all()
        checks_query = select(Validation).where(
            Validation.project_id == project.id, Validation.revision == project.engineering_revision)
        rebuilt_at = session.scalar(select(func.max(Run.created_at)).where(Run.project_id == project.id,
            Run.revision == project.engineering_revision, Run.kind == "project.retryNative"))
        if rebuilt_at:
            checks_query = checks_query.where(Validation.created_at >= rebuilt_at)
        checks = session.scalars(checks_query).all()
        hashes = {a.id: a.sha256 for a in artifacts}
        current_contract = contract_hash(project.requirements, project.interfaces)
        applicable = [c for c in checks if c.artifact_hashes == hashes and c.evidence_kind == "digital"
                      and c.details.get("contractHash") == current_contract]
        required = {"interfaces", "mechanics", "electronics", "firmware", "application"}
        if approved is not None:
            required |= {"approved_design_contract", "approved_enclosure_form", "approved_pcb_layout"}
        passed = {c.name for c in applicable if c.status == "PASS"}
        blocking = [c for c in applicable if not (c.details.get("required") is False
                                                 and c.details.get("scopeExcluded") is True)]
        if not artifacts or not required.issubset(passed) or any(
                c.status not in {"PASS", "NOT_APPLICABLE"} for c in blocking):
            raise DomainError("Current artifacts do not have all required independent digital checks", 409, "gate_failed")
        release = Release(id=uid(), tenant_id=project.tenant_id, project_id=project.id,
                          revision=project.engineering_revision, mode="digital",
                          approved_by=principal.subject,
                          manifest={"artifacts": hashes, "checks": [c.id for c in applicable],
                                    "requirements": deepcopy(project.requirements), "interfaces": deepcopy(project.interfaces),
                                    **({"approvedDesign": approved} if approved is not None else {}),
                                    "limitations": [{"name": c.name, "status": c.status, "details": c.details}
                                                    for c in applicable if c not in blocking]})
        session.add(release)
        for artifact in artifacts:
            artifact.status = "accepted"
        project.accepted_release_id, project.stage = release.id, 6
        emit(session, project, "release.accepted", {"releaseId": release.id, "mode": "digital"})

    def _simulate(self, session, project, kind):
        approved_context(project)
        if not self.settings.demo_mode:
            raise DomainError("Live fulfillment is disabled", 403)
        release = session.get(Release, project.accepted_release_id) if project.accepted_release_id else None
        if not release or release.revision != project.engineering_revision:
            raise DomainError("Accept a current digital release first", 409)
        operation, stage = SIMULATED[kind]
        prerequisites = {
            "prototype_order": "quotes", "physical_tasks": "prototype_order",
            "physical_acceptance": "physical_tasks", "kit_acceptance": "kit",
            "shipment": "kit_acceptance", "activation": "shipment",
        }
        if prerequisites.get(operation) and not project.state.get(prerequisites[operation]):
            raise DomainError(f"Complete {prerequisites[operation].replace('_', ' ')} first", 409)
        intent = session.scalar(select(ExternalIntent).where(
            ExternalIntent.project_id == project.id, ExternalIntent.operation == operation,
            ExternalIntent.revision == project.engineering_revision))
        if not intent:
            intent = ExternalIntent(id=uid(), tenant_id=project.tenant_id, project_id=project.id,
                                    revision=project.engineering_revision, operation=operation,
                                    mode="simulated", status="completed",
                                    receipt={"evidenceKind": "synthetic", "realExternalEffect": False})
            session.add(intent)
        project.state = {**project.state, operation: {
            "intentId": intent.id, "mode": "simulated", "evidenceKind": "synthetic"}}
        project.stage = max(stage, project.stage)
        emit(session, project, "fulfillment.simulated", {"operation": operation, "intentId": intent.id,
                                                       "evidenceKind": "synthetic"})

    def projects(self, principal, after=None, limit=25):
        with self.db.transaction() as session:
            q = select(Project).where(Project.tenant_id == principal.tenant_id, Project.deleted_at.is_(None))
            if principal.role not in {"owner", "admin"}:
                q = q.where(or_(Project.owner_id == principal.subject, Project.id.in_(
                    select(ProjectMembership.project_id).where(ProjectMembership.subject == principal.subject))))
            if after:
                q = q.where(Project.id > after)
            rows = session.scalars(q.order_by(Project.id).limit(limit + 1)).all()
            return {"projects": [project_view(p, self.settings) for p in rows[:limit]],
                    "nextCursor": rows[limit - 1].id if len(rows) > limit else None}

    def workspace(self, principal, project_id):
        with self.db.transaction(read_only=True) as session:
            project = get_project(session, project_id, principal, lock=False)
            cache_key = (project.tenant_id, project.id, project.revision)
            # Authorization is always rechecked above. Cached snapshots are immutable by revision.
            if cache_key in self._workspace_cache:
                return self._workspace_cache[cache_key]
            runs = session.scalars(select(Run).where(Run.project_id == project_id).order_by(
                Run.created_at.desc()).limit(30)).all()
            artifacts = session.scalars(select(Artifact).where(Artifact.project_id == project_id).limit(200)).all()
            consent = session.scalar(select(Consent).where(Consent.project_id == project_id))
            checks = session.scalars(select(Validation).where(
                Validation.project_id == project_id, Validation.revision == project.engineering_revision)).all()
            snapshot = redact({"project": project_view(project, self.settings), "revision": project.revision,
                    "eventCursor": project.event_sequence, "runs": [self._run_view(r) for r in runs],
                    "artifacts": [self._artifact_view(a) for a in artifacts],
                    "checks": [{"id": c.id, "name": c.name, "status": c.status,
                                "evidenceKind": c.evidence_kind, "details": c.details} for c in checks],
                    "trainingOptedIn": bool(consent and consent.opted_in)})
            if len(self._workspace_cache) >= 100:
                self._workspace_cache.pop(next(iter(self._workspace_cache)), None)
            self._workspace_cache[cache_key] = snapshot
            return snapshot

    def events(self, principal, project_id, after=0):
        with self.db.transaction(read_only=True) as session:
            p = get_project(session, project_id, principal, lock=False)
            if after < p.event_floor:
                raise DomainError("Event cursor expired; reload the workspace", 410, "cursor_expired")
            if after > p.event_sequence:
                raise DomainError("Event cursor is ahead of the project", 409, "cursor_invalid")
            rows = session.scalars(select(Event).where(Event.project_id == project_id, Event.sequence > after)
                                   .order_by(Event.sequence).limit(100)).all()
            return [{"id": e.id, "sequence": e.sequence, "type": e.type, "data": e.data,
                     "traceId": e.trace_id, "createdAt": e.created_at.isoformat()} for e in rows]

    def inspect_run(self, principal, run_id):
        with self.db.transaction(read_only=True) as session:
            run = session.scalar(select(Run).where(Run.id == run_id, Run.tenant_id == principal.tenant_id))
            if not run:
                raise DomainError("Run not found", 404)
            get_project(session, run.project_id, principal, lock=False)
            tasks = session.scalars(select(Task).where(Task.run_id == run_id).order_by(Task.created_at)).all()
            result = self._run_view(run)
            result["tasks"] = []
            for task in tasks:
                attempts = session.scalars(select(Attempt).where(Attempt.task_id == task.id)
                                          .order_by(Attempt.number)).all()
                attempt_views = []
                for a in attempts:
                    calls = session.scalars(select(Invocation).where(Invocation.attempt_id == a.id)).all()
                    attempt_views.append({
                        "id": a.id, "number": a.number, "status": a.status, "provider": a.provider,
                        "model": a.model, "policyRevision": a.policy_revision, "error": a.error,
                        "contextManifest": context_manifest(a.context),
                        "output": a.output, "calls": [{
                            "id": c.id, "operation": c.operation, "status": c.status,
                            "usage": {**c.usage, **({"allowanceDebitUsd": c.usage.get("allowanceDebitUsd", c.reserved_usd)}
                                       if c.usage.get("costBasis") == "conservative_reservation" else {})},
                            "estimatedUsd": c.estimated_usd, "hasTranscript": bool(c.transcript_key),
                        } for c in calls],
                    })
                jobs = session.scalars(select(NativeJob).where(NativeJob.task_id == task.id)).all()
                result["tasks"].append({
                    "id": task.id, "lane": task.lane, "status": task.status,
                    "dependencies": task.dependencies, "result": task.result, "attempts": attempt_views,
                    "nativeJobs": [{"id": j.id, "purpose": j.purpose, "status": j.status,
                                    "inputManifest": j.request.get("inputArtifacts", []),
                                    "toolVersion": j.request.get("toolVersion"),
                                    "hasLogs": bool(j.result.get("logKey")),
                                    "limitations": j.result.get("limitations", [])} for j in jobs],
                })
            return redact(result)

    @staticmethod
    def _run_view(run):
        return {"id": run.id, "projectId": run.project_id, "kind": run.kind, "status": run.status,
                "revision": run.revision, "traceId": run.trace_id, "blocker": run.blocker,
                "createdAt": run.created_at.isoformat()}

    @staticmethod
    def _artifact_view(a):
        return {"id": a.id, "name": a.name, "lane": a.lane, "revision": a.revision,
                "sha256": a.sha256, "size": a.size, "status": a.status,
                "mediaType": a.media_type, "provenance": a.provenance}
