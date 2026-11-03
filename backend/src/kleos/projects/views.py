"""Read models for the existing workbench; stages are display projections only."""

import hashlib

from sqlalchemy import select

from kleos.contracts.models import DomainError
from kleos.execution.gateway import NativeAction, NativeJob
from kleos.orchestration.models import ModelAttempt
from kleos.projects.store import Membership, Objective, Project
from kleos.validation.service import Candidate, Release, ValidationCheck


def project_view(project, *, budget=None):
    data = project.data
    return {
        "id": project.id,
        "name": data["name"],
        "brief": data["brief"],
        "revision": project.revision,
        "engineeringRevision": data.get("engineeringRevision", 1),
        "policyRevision": data.get("policyRevision", 1),
        "modelPolicy": data.get("modelPolicy", {}),
        "stage": data.get("displayStage", 0),
        "status": data.get("status", "draft"),
        "requirements": data["requirements"],
        "interfaces": data["interfaces"],
        "state": data.get("state", {}),
        "acceptedReleaseId": data.get("acceptedReleaseId"),
        "budget": budget if budget is not None else data.get(
            "budget", {"allowanceUsd": None, "unlimited": False, "spentEstimateUsd": 0.0, "reservedUsd": 0.0}
        ),
        "mode": "real",
        "createdAt": data["createdAt"],
    }


def project_budgets(session, projects):
    """Project provider estimates; uncertain usage keeps its durable reservation."""
    budgets = {project.id: {
        "allowanceUsd": project.data.get("budget", {}).get("allowanceUsd"),
        "unlimited": project.data.get("budget", {}).get("unlimited", False),
        "spentEstimateUsd": 0.0, "reservedUsd": 0.0,
    } for project in projects}
    totals = {ident: [0, 0] for ident in budgets}
    if not budgets:
        return budgets
    rows = session.execute(select(Objective.project_id, ModelAttempt).join(
        ModelAttempt, ModelAttempt.run_id == Objective.id).where(Objective.project_id.in_(budgets)))
    for project_id, attempt in rows:
        # Errors, cancelled HTTP calls and missing usage cannot prove a zero charge.
        settled = attempt.data.get("usage") is not None or attempt.status == "rate_limited"
        totals[project_id][0 if settled else 1] += attempt.data["committedMicroUsd"]
    for ident, (spent, reserved) in totals.items():
        budgets[ident].update(spentEstimateUsd=spent / 1_000_000, reservedUsd=reserved / 1_000_000)
    return budgets


def run_view(run, *, tasks=False, native_jobs=None):
    result = {
        "id": run.id,
        "projectId": run.project_id,
        "kind": run.data.get("kind", "objective"),
        "status": run.status,
        "revision": run.revision,
        "traceId": run.id,
        "blocker": run.data.get("blocker"),
        "createdAt": run.data["createdAt"],
    }
    if tasks:
        result["tasks"] = [
            {
                "id": t["id"],
                "lane": t["capability"],
                "status": run.data.get("results", {}).get(t["id"], {}).get("status", "pending"),
                "dependencies": t.get("dependsOn", t.get("depends_on", [])),
                "result": run.data.get("results", {}).get(t["id"], {}),
                "attempts": [],
                "nativeJobs": (native_jobs or {}).get(t["id"], []),
            }
            for t in run.data.get("tasks", [])
        ]
    return result


def native_job_view(job):
    return {"id": job.id, "purpose": "build", "lane": job.request['lane'],
        "status": "succeeded" if job.status == 'finished' else job.status,
        "toolVersion": job.request['imageDigest'], "hasLogs": True,
        "inputManifest": {"sources": [{"path": path, "sha256": digest}
            for path, digest in sorted(job.source_manifest.items())],
            "revision": job.request['revision'], "requestHash": job.request_hash},
        "limitations": ["Native results are candidates; completion is not independent or physical acceptance."],
        "resources": job.request.get('resources', {}), "parentJobId": job.request.get('rebuildOf')}


def agent_views(run, project):
    """Project actual objectives/tasks; do not invent long-lived agent instances."""
    shared = {
        "lifetime": "bounded",
        "engineeringRevision": run.revision,
        "historical": run.revision != project.context_revision,
        "graph": "kleos.objective.v1",
        "deadline": run.data.get("scope", {}).get("deadline_at"),
        "nextWakeAt": None,
        "remainingAllowanceUsd": None,
    }
    rows = [{**shared, "id": run.id, "agentType": "orchestrator",
        "lifecycle": run.status, "currentNode": None,
        "lastUsefulObservation": run.data.get("summary"), "blockerReason": run.data.get("blocker")}]
    for task in run.data.get("tasks", []):
        result = run.data.get("results", {}).get(task["id"], {})
        status = result.get("status", "pending")
        if not result and run.status in {"cancelled", "blocked"}:
            status = run.status
        if task["id"] in run.data.get("superseded", {}):
            status = "superseded"
        ident = hashlib.sha256((run.id + ":" + task["id"]).encode()).hexdigest()
        rows.append({**shared, "id": ident, "agentType": task["capability"],
            "lifecycle": status, "currentNode": None,
            "lastUsefulObservation": result.get("summary"),
            "blockerReason": "; ".join(result.get("blockers", [])) or run.data.get("blocker")})
    return rows


def validation_views(session, project):
    accepted_id = project.data.get("acceptedReleaseId")
    accepted = session.get(Release, accepted_id) if accepted_id else None
    current_accepted = (accepted.candidate_id if accepted and accepted.project_id == project.id
                        and accepted.revision == project.context_revision else None)
    rows = session.execute(select(Candidate, ValidationCheck).join(
        ValidationCheck, ValidationCheck.candidate_id == Candidate.id).where(
        Candidate.project_id == project.id).order_by(Candidate.id, ValidationCheck.checker_key))
    return [{"id": candidate.id + ":" + check.checker_key, "name": check.checker_key,
        "status": check.status if check.status in {"PASS", "FAIL", "UNKNOWN", "ERROR", "NOT_APPLICABLE"} else "UNKNOWN",
        "evidenceKind": "digital", "details": {**check.data, "candidateId": candidate.id,
            "candidateStatus": candidate.status, "revision": candidate.revision,
            "historical": candidate.revision != project.context_revision and candidate.id != current_accepted,
            "executionState": check.status, "physicalAcceptance": False}}
        for candidate, check in rows]


class ProjectViews:
    def __init__(self, store, *, artifacts=None, external_jobs=None):
        self.store = store
        self.artifacts = artifacts
        self.external_jobs = external_jobs

    def list(self, actor, cursor=None):
        with self.store.transaction() as s:
            self.store.authorize_account(s, actor)
            query = (
                select(Project)
                .join(Membership, Membership.project_id == Project.id)
                .where(Project.tenant_id == actor.tenant_id, Membership.user_id == actor.user_id,
                       Project.data['internalPurpose'].as_string().is_(None))
                .where(Project.data['deletion'].as_string().is_(None))
            )
            if cursor:
                query = query.where(Project.id > cursor)
            rows = list(s.scalars(query.order_by(Project.id).limit(51)))
            budgets = project_budgets(s, rows[:50])
            return {
                "projects": [project_view(p, budget=budgets[p.id]) for p in rows[:50]],
                "nextCursor": rows[49].id if len(rows) == 51 else None,
            }

    def workspace(self, actor, project_id):
        with self.store.transaction() as s:
            p = self.store.authorized(s, actor, project_id)
            runs = list(s.scalars(select(Objective).where(Objective.project_id == p.id)))
            runs.sort(key=lambda run: (run.data.get('createdAt', ''), run.id), reverse=True)
            return {
                "project": project_view(p, budget=project_budgets(s, [p])[p.id]),
                "revision": p.revision,
                "eventCursor": p.event_sequence,
                "runs": [run_view(run) for run in runs],
                "artifacts": self.artifacts.list(s, project_id) if self.artifacts else [],
                "checks": validation_views(s, p),
                "trainingOptedIn": p.data.get('trainingConsent', {}).get('optedIn') is True,
                "agents": [agent for run in runs for agent in agent_views(run, p)],
                "externalJobs": self.external_jobs(s, p) if self.external_jobs else [],
            }

    def agents(self, actor, project_id, *, after=None, limit=25):
        if after is not None and (not isinstance(after, str) or not 1 <= len(after) <= 100):
            raise DomainError("Invalid agent cursor", "invalid_request", 422)
        try:
            if isinstance(limit, bool) or str(int(limit)) != str(limit) or not 1 <= int(limit) <= 100:
                raise ValueError
            limit = int(limit)
        except (TypeError, ValueError):
            raise DomainError("Invalid agent page size", "invalid_request", 422) from None
        with self.store.transaction() as session:
            project = self.store.authorized(session, actor, project_id)
            runs = session.scalars(select(Objective).where(Objective.project_id == project_id))
            rows = sorted([agent for run in runs for agent in agent_views(run, project)], key=lambda row: row['id'])
            rows = [row for row in rows if after is None or row['id'] > after]
            return {"agents": rows[:limit], "nextCursor": rows[limit - 1]['id'] if len(rows) > limit else None}

    def run(self, actor, run_id):
        with self.store.transaction() as s:
            run = s.get(Objective, run_id)
            if run is None:
                raise DomainError("Run not found", "not_found", 404)
            self.store.authorized(s, actor, run.project_id)
            jobs = {}
            for job in s.scalars(select(NativeJob).where(NativeJob.run_id == run.id,
                                                        NativeJob.project_id == run.project_id)):
                jobs.setdefault(job.task_id, []).append(native_job_view(job))
            return run_view(run, tasks=True, native_jobs=jobs)

    def native_logs(self, actor, job_id, access):
        with self.store.transaction() as session:
            job = session.get(NativeJob, job_id)
            if job is None:
                raise DomainError("Native job not found", "not_found", 404)
            self.store.authorized(session, actor, job.project_id)
            access._member(session, actor, admin=True)
            # Never expose raw read/patch data, generated output or control paths.
            actions = session.scalars(select(NativeAction).where(NativeAction.job_id == job.id)
                .order_by(NativeAction.action_id).limit(129))
            return {"jobId": job.id, "status": job.status,
                "scope": "Structured action history; raw process output is not exposed.",
                "actions": [{"actionId": row.action_id, "status": row.status,
                    "observations": [{"name": item.get('name'), "status": item.get('status')}
                        for item in row.result.get('observations', [])]} for row in actions]}

    def events(self, actor, project_id, after):
        if isinstance(after, bool):
            raise DomainError("Invalid event cursor", "invalid_request", 422)
        try:
            cursor = int(after)
        except (ValueError, TypeError):
            raise DomainError("Invalid event cursor", "invalid_request", 422) from None
        if cursor < 0:
            raise DomainError("Invalid event cursor", "invalid_request", 422)
        return {
            "events": [
                {
                    "id": f"{project_id}:{e['sequence']}",
                    "sequence": e["sequence"],
                    "type": e["type"],
                    "data": e["payload"],
                    "traceId": e["payload"].get("runId"),
                    "createdAt": e["createdAt"],
                }
                for e in self.store.events(actor, project_id, cursor)
            ]
        }
