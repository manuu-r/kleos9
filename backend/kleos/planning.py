"""Bounded planning state and illustrative concept records; never engineering evidence."""
import hashlib
import json

from .anatomy_skill import seed_concept, skill_metadata
from .contracts import ConceptScene, DomainError
from .db import now, uid

PLANNING_KINDS = {"project.capture", "project.research", "project.requestReview"}


def planning_state(project):
    stored = project.state.get("planning", {})
    existing = bool(project.state.get("proposal"))
    return {"autoResume": False, "pending": False, "inputVersion": 0, "appliedInputVersion": 0,
            "captureComplete": existing, "feasibilityComplete": existing and project.stage >= 2,
            "pendingResearch": False, "lastResearchInputVersion": -1,
            "phase": "ready" if existing else "capture", **stored}


def concept_snapshot(project):
    stored = project.state.get("concept")
    if stored:
        return stored
    scene = ConceptScene.model_validate(seed_concept(project.brief)).model_dump(mode="json", by_alias=True)
    return {"version": 0, "scene": scene, "phase": "capture", "basedOnAnswerVersion": 0,
            "basedOnInputVersion": 0, "updatedAt": project.created_at.isoformat(), "skill": skill_metadata(),
            "illustrative": True, "stale": bool(project.state.get("clarificationAnswerVersion", 0))}


def seed_project_concept(project):
    concept = {**concept_snapshot(project), "version": 1}
    project.state = {**project.state, "concept": concept, "conceptHistory": []}


def update_concept(project, output, phase, run_id, attempt_id, input_version):
    raw = output.get("conceptScene", output.get("concept_scene"))
    current = concept_snapshot(project)
    if raw is None:
        project.state = {**project.state, "concept": {**current, "stale": True}}
        return
    scene = ConceptScene.model_validate(raw).model_dump(mode="json", by_alias=True)
    version = current["version"] + 1
    stamp = now().isoformat()
    concept = {"version": version, "scene": scene, "phase": phase,
               "basedOnAnswerVersion": project.state.get("clarificationAnswerVersion", 0),
               "basedOnInputVersion": input_version, "updatedAt": stamp, "skill": skill_metadata(),
               "illustrative": True, "stale": False}
    history = [*project.state.get("conceptHistory", []), {
        "version": version, "sceneHash": hashlib.sha256(json.dumps(scene, sort_keys=True).encode()).hexdigest(),
        "phase": phase, "inputVersion": input_version, "runId": run_id, "attemptId": attempt_id, "updatedAt": stamp}][-24:]
    project.state = {**project.state, "concept": concept, "conceptHistory": history}


def append_message(project, role, text, kind, **metadata):
    messages = project.state.get("conversation", [])
    message = {"id": uid(), "role": role, "text": text[:4000], "createdAt": now().isoformat(),
               "kind": kind, **metadata}
    project.state = {**project.state, "conversation": [*messages, message][-80:]}
    return message


def conversation_context(project, max_chars=6500):
    messages, used = [], 0
    for message in reversed(project.state.get("conversation", [])):
        size = len(json.dumps(message))
        if used + size > max_chars:
            break
        messages.insert(0, message)
        used += size
    return messages


def validate_part_reference(project, payload):
    part_id = payload.get("selectedPartId")
    version = payload.get("conceptVersion")
    if part_id is None and version is None:
        return {}
    current = concept_snapshot(project)
    if not isinstance(version, int) or isinstance(version, bool) or version != current["version"]:
        raise DomainError("The concept changed; select the part again", 409, "concept_changed")
    part = next((p for p in current["scene"]["parts"] if p["id"] == part_id), None)
    if not part:
        raise DomainError("Select a current concept part", 422, "unknown_concept_part")
    return {"selectedPartId": part_id, "conceptVersion": version,
            "partReference": {"id": part_id, "label": part["label"], "role": part["role"]}}


def planning_context(project, automatic):
    return {**planning_state(project), "automatic": automatic, "skill": skill_metadata()}


def model_packet(context):
    """Send useful file metadata once; keep full immutable provenance in the ledger."""
    packet = {**context}
    if context.get("baseFiles"):
        packet["baseFiles"] = [{**{k: v for k, v in item.items() if k != "content"},
                                "contentOmitted": True, "immutableDependencyLock": True}
                               if item.get("name") == "pubspec.lock" else item for item in context["baseFiles"]]
    if "sourceArtifacts" in context:
        packet["sourceArtifacts"] = [{k: ref[k] for k in ("name", "sha256", "size") if k in ref}
                                     for ref in context["sourceArtifacts"]]
    if context.get("manifest"):
        manifest = {**context["manifest"]}
        refs = manifest.pop("sourceArtifacts", None)
        if refs is not None:
            manifest.update(sourceArtifactCount=len(refs), sourceManifestSha256=hashlib.sha256(
                json.dumps(refs, sort_keys=True).encode()).hexdigest())
        packet["manifest"] = manifest
    return packet


def context_manifest(context):
    """Diagnostic references contain hashes and IDs rather than prompt bodies."""
    concept = context.get("concept", {})
    planning = context.get("planning", {})
    conversation = context.get("conversation", [])
    selected = {k: v for k, v in context.items() if k in {
        "projectId", "revision", "task", "lane", "inputArtifacts", "acceptanceCriteria",
        "remainingAllowanceUsd", "pivotReason", "retrieval"}}
    if context.get("baseFiles"):
        selected["baseFiles"] = [{k: file[k] for k in ("name", "sha256", "templateId", "templateManifestSha256")}
                                 for file in context["baseFiles"]]
    if context.get("sourceArtifacts"):
        selected["sourceArtifacts"] = [{k: ref[k] for k in ("id", "name", "sha256")}
                                       for ref in context["sourceArtifacts"]]
    if context.get("engineeringTool"):
        selected["engineeringTool"] = {k: v for k, v in context["engineeringTool"].items()
                                       if k in {"id", "version", "definitionHash", "installationHash", "policyVersion",
                                                "runner", "executionDefinition", "executionBackend", "executionUnavailable"}}
    if context.get("approvedDesign"):
        selected["approvedDesign"] = {k: v for k, v in context["approvedDesign"].items() if k in {
            "schemaVersion", "version", "sceneVersion", "sceneHash", "engineeringRevision"}}
    if concept:
        selected["concept"] = {k: v for k, v in concept.items() if k in {
            "version", "phase", "basedOnAnswerVersion", "basedOnInputVersion", "illustrative", "stale"}}
        selected["concept"]["sceneHash"] = hashlib.sha256(json.dumps(concept.get("scene", {}), sort_keys=True).encode()).hexdigest()
    if planning:
        selected["planning"] = {k: v for k, v in planning.items() if k in {
            "inputVersion", "appliedInputVersion", "automatic", "phase", "captureComplete", "feasibilityComplete",
            "pendingResearch", "lastResearchInputVersion"}}
        selected["anatomy"] = planning.get("skill", {})
    selected["conversation"] = {"messageCount": len(conversation), "messageIds": [m["id"] for m in conversation]}
    selected["inputCharacterCount"] = len(json.dumps(context))
    return selected
