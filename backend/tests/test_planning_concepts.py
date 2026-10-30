import pytest
from pydantic import ValidationError
from sqlalchemy import func, select
from test_clarification import question
from test_control_plane import command, create, engine

from kleos.anatomy_skill import seed_concept
from kleos.contracts import AgentOutput, Command, ConceptScene, DomainError
from kleos.db import Invocation, Project, Run, Source, Task


def workspace(platform, ident):
    return platform.control.workspace(platform.principal, ident)["project"]


def start(platform, brief="A desktop air quality monitor"):
    response = platform.control.command(platform.principal, Command(type="project.create", payload={
        "brief": brief, "autoStart": True}), "automatic-create")
    return response["projectId"], response["runId"]


def admit(platform, worker, run_id):
    with platform.db.transaction() as session:
        project = session.get(Project, session.get(Run, run_id).project_id)
        project.state = {**project.state, "planning": {**project.state.get("planning", {}), "notBefore": None}}
    tasks = worker.admit_run(run_id)
    assert tasks
    return tasks[0], worker.begin_attempt(tasks[0])


def finish(worker, run_id, attempt, **kwargs):
    output = AgentOutput(summary="Let's refine your monitor.", conceptScene=seed_concept("desktop monitor"), **kwargs)
    worker.finish_attempt(attempt, output.model_dump(mode="json"))
    worker.finish_run(run_id)


def next_run(platform, ident):
    with platform.db.transaction() as session:
        run = session.scalar(select(Run).where(Run.project_id == ident, Run.status.in_(["queued", "running"])))
        return run.id if run else None


def test_automatic_capture_answers_feasibility_and_review_stop_before_cad(platform):
    ident, run_id = start(platform)
    worker = engine(platform)
    p = workspace(platform, ident)
    assert p["stage"] == 0 and p["state"]["concept"]["version"] == 1
    task, attempt = admit(platform, worker, run_id)
    context, *_ = worker.attempt_input(attempt)
    assert context["task"] == "project.capture" and context["planning"]["automatic"]
    assert context["concept"]["scene"]["schemaVersion"] == "concept-scene-v1"
    finish(worker, run_id, attempt, clarificationQuestions=[question()])
    assert next_run(platform, ident) is None
    p = workspace(platform, ident)
    q = p["state"]["clarification"]["questions"][0]
    response = command(platform, ident, "project.answer", {"questionId": q["id"], "questionHash": q["questionHash"],
        "values": ["usb"], "autoResume": True})
    research = response["runId"]
    assert research
    task, attempt = admit(platform, worker, research)
    assert worker.attempt_input(attempt)[0]["task"] == "project.research"
    finish(worker, research, attempt, requirements={"power": "usb"})
    p = workspace(platform, ident)
    assert p["state"]["planning"]["phase"] == "ready"
    assert p["state"]["planning"]["feasibilityComplete"]
    assert p["state"]["clarification"]["readyToConfirm"]
    assert p["state"]["confirmed"] is False and p["stage"] == 2
    assert next_run(platform, ident) is None
    assert p["state"]["concept"]["version"] == 3
    assert all("scene" not in item for item in p["state"]["conceptHistory"])
    with platform.db.transaction() as session:
        assert session.scalar(select(func.count(Run.id)).where(Run.kind == "project.generate")) == 0


def test_answer_during_active_review_is_saved_and_stale_result_does_not_publish(platform):
    ident, capture = start(platform)
    worker = engine(platform)
    _, attempt = admit(platform, worker, capture)
    finish(worker, capture, attempt, clarificationQuestions=[question()])
    response = command(platform, ident, "project.answer", {"questionId": "power", "values": ["usb"], "autoResume": True})
    research = response["runId"]
    task, attempt = admit(platform, worker, research)
    old_version = workspace(platform, ident)["state"]["concept"]["version"]
    command(platform, ident, "project.answer", {"questionId": "power", "values": ["battery"], "autoResume": True})
    finish(worker, research, attempt, requirements={"power": "usb"})
    p = workspace(platform, ident)
    assert p["state"]["clarification"]["answers"]["power"] == ["battery"]
    assert p["state"]["concept"]["version"] == old_version
    assert p["state"]["proposal"]["requirements"] != {"power": "usb"}
    followup = next_run(platform, ident)
    assert followup and followup != research
    with platform.db.transaction() as session:
        assert session.get(Task, task).result["supersededPlanningInput"]
        assert session.scalar(select(func.count(Run.id)).where(Run.project_id == ident, Run.status == "queued")) == 1


def test_chat_coalesces_queued_turns_is_idempotent_and_references_current_part(platform):
    ident, capture = start(platform)
    concept = workspace(platform, ident)["state"]["concept"]
    part = concept["scene"]["parts"][0]
    payload = {"text": "Make this rounder", "clientMessageId": "message-1", "autoResume": True,
               "selectedPartId": part["id"], "conceptVersion": concept["version"]}
    response = command(platform, ident, "project.message", payload)
    assert response["runId"] == capture
    p = workspace(platform, ident)
    assert p["state"]["planning"]["inputVersion"] == 1
    assert p["state"]["conversation"][-1]["partReference"]["label"] == part["label"]
    command(platform, ident, "project.message", payload)
    assert workspace(platform, ident)["state"]["planning"]["inputVersion"] == 1
    command(platform, ident, "project.message", {"text": "Use a larger display", "clientMessageId": "message-2", "autoResume": True}, key="second-message")
    assert workspace(platform, ident)["state"]["planning"]["inputVersion"] == 2
    worker = engine(platform)
    _, attempt = admit(platform, worker, capture)
    context = worker.attempt_input(attempt)[0]
    assert context["planning"]["inputVersion"] == 2
    assert context["conversation"][-1]["text"] == "Use a larger display"
    with pytest.raises(DomainError) as exc:
        command(platform, ident, "project.message", {**payload, "clientMessageId": "message-3", "conceptVersion": -1}, key="stale-part-reference")
    assert exc.value.code == "concept_changed"


def test_failed_autoplanning_does_not_retry_forever_and_cancel_stops_it(platform):
    ident, capture = start(platform)
    worker = engine(platform)
    task, attempt = admit(platform, worker, capture)
    worker.block_task(task, attempt, "Provider unavailable")
    worker.finish_run(capture)
    assert next_run(platform, ident) is None
    assert workspace(platform, ident)["state"]["planning"]["phase"] == "blocked"
    response = command(platform, ident, "project.message", {"text": "Try a square enclosure", "clientMessageId": "resume", "autoResume": True})
    assert response["runId"]
    command(platform, ident, "project.cancel")
    assert next_run(platform, ident) is None
    assert workspace(platform, ident)["state"]["planning"]["autoResume"] is False


def test_unapplied_chat_blocks_confirmation_even_if_no_question_answers_changed(platform):
    ident = create(platform)["projectId"]
    with platform.db.transaction() as session:
        p = session.get(Project, ident)
        p.state = {**p.state, "proposal": {"summary": "Existing feasible concept", "questions": []}}
        p.stage = 2
    command(platform, ident, "project.message", {"text": "Change the screen to e-paper", "clientMessageId": "unapplied", "autoResume": False})
    p = workspace(platform, ident)
    assert p["state"]["clarification"]["needsReview"] and not p["state"]["clarification"]["readyToConfirm"]
    with pytest.raises(DomainError) as exc:
        command(platform, ident, "project.confirm")
    assert exc.value.code == "clarification_review_required"


def test_question_hash_and_unsafe_scene_are_rejected(platform):
    ident, run_id = start(platform)
    worker = engine(platform)
    _, attempt = admit(platform, worker, run_id)
    finish(worker, run_id, attempt, clarificationQuestions=[question()])
    with pytest.raises(DomainError) as exc:
        command(platform, ident, "project.answer", {"questionId": "power", "values": ["usb"], "questionHash": "stale", "autoResume": True})
    assert exc.value.code == "question_changed"
    scene = seed_concept("monitor")
    scene["parts"][0]["size"]["x"] = float("inf")
    with pytest.raises(ValidationError):
        ConceptScene.model_validate(scene)


def test_manifest_references_concept_skill_and_conversation_without_prompt_bodies(platform):
    ident, run_id = start(platform, "My private device brief")
    worker = engine(platform)
    _, attempt = admit(platform, worker, run_id)
    context, *_ = worker.attempt_input(attempt)
    manifest = platform.control.inspect_run(platform.principal, run_id)["tasks"][0]["attempts"][0]["contextManifest"]
    assert manifest["concept"]["version"] == 1
    assert len(manifest["concept"]["sceneHash"]) == 64
    assert manifest["anatomy"]["name"] == "anatomy"
    assert manifest["conversation"]["messageCount"] == 1
    assert manifest["conversation"]["messageIds"] == [context["conversation"][0]["id"]]
    assert "My private device brief" not in str(manifest)
    assert "scene" not in manifest["concept"]


def test_automatic_context_has_one_scene_bounded_sources_and_character_manifest(platform):
    ident, capture = start(platform)
    worker = engine(platform)
    _, attempt = admit(platform, worker, capture)
    finish(worker, capture, attempt, clarificationQuestions=[question()])
    with platform.db.transaction() as session:
        project = session.get(Project, ident)
        proposal = {**project.state["proposal"], "conceptScene": seed_concept("monitor")}
        project.state = {**project.state, "proposal": proposal}
        for index in range(5):
            session.add(Source(id=f"context-source-{index}", tenant_id=project.tenant_id, project_id=ident,
                revision=project.engineering_revision, uri=f"https://example.com/{index}", sha256=f"hash-{index}",
                text="Long source content " * 1000, authority="research"))
    response = command(platform, ident, "project.answer", {"questionId": "power", "values": ["usb"], "autoResume": True})
    _, attempt = admit(platform, worker, response["runId"])
    context = worker.attempt_input(attempt)[0]
    assert "conceptScene" not in context["proposal"] and "concept_scene" not in context["proposal"]
    assert all("concept_scene" not in entry["output"] and "conceptScene" not in entry["output"] for entry in context["repairHistory"])
    assert len(context["sourceExcerpts"]) == 3
    assert all(len(source["text"]) <= 1200 and source["excerpted"] for source in context["sourceExcerpts"])
    import json
    assert context["manifest"]["inputCharacterCount"] == len(json.dumps(context))
    assert context["manifest"]["concept"]["sceneHash"]


def test_superseded_planning_input_does_not_reserve_an_unnecessary_paid_call(platform):
    ident, capture = start(platform)
    worker = engine(platform)
    _, attempt = admit(platform, worker, capture)
    command(platform, ident, "project.message", {"text": "Make the screen larger", "clientMessageId": "newer-input", "autoResume": True})
    with pytest.raises(DomainError) as exc:
        worker.reserve_invocation(attempt)
    assert exc.value.code == "planning_input_superseded"
    with platform.db.transaction() as session:
        assert session.scalar(select(func.count(Invocation.id))) == 0


def test_unaccepted_chat_and_hashed_answer_tolerate_progress_revisions_only(platform):
    ident, capture = start(platform)
    old_revision = workspace(platform, ident)["revision"]
    worker = engine(platform)
    _, attempt = admit(platform, worker, capture)
    finish(worker, capture, attempt, clarificationQuestions=[question()])
    current = workspace(platform, ident)
    hash_value = current["state"]["clarification"]["questions"][0]["questionHash"]
    assert current["revision"] > old_revision
    platform.control.command(platform.principal, Command(type="project.message", expectedRevision=old_revision,
        payload={"projectId": ident, "text": "A quiet desk device", "clientMessageId": "stale-progress-chat", "autoResume": True}),
        "stale-progress-chat")
    platform.control.command(platform.principal, Command(type="project.answer", expectedRevision=old_revision,
        payload={"projectId": ident, "questionId": "power", "questionHash": hash_value, "values": ["usb"], "autoResume": True}),
        "stale-progress-answer")
    assert workspace(platform, ident)["state"]["clarification"]["answers"]["power"] == ["usb"]
    with pytest.raises(DomainError) as exc:
        platform.control.command(platform.principal, Command(type="project.answer", expectedRevision=old_revision,
            payload={"projectId": ident, "questionId": "power", "questionHash": "old-semantic-hash", "values": ["battery"], "autoResume": True}),
            "stale-semantic-answer")
    assert exc.value.code == "question_changed"
    with pytest.raises(DomainError) as exc:
        platform.control.command(platform.principal, Command(type="project.message", expectedRevision=current["revision"] + 1000,
            payload={"projectId": ident, "text": "Future revision", "clientMessageId": "future", "autoResume": True}), "future-revision")
    assert exc.value.code == "stale_revision"


def test_accepted_or_manual_work_keeps_strict_revision_checks(platform):
    ident = create(platform)["projectId"]
    old_revision = workspace(platform, ident)["revision"]
    command(platform, ident, "project.setModelPolicy", {"modelPolicy": platform.control.registry.default_policy()})
    with pytest.raises(DomainError) as exc:
        platform.control.command(platform.principal, Command(type="project.message", expectedRevision=old_revision,
            payload={"projectId": ident, "text": "Manual planning", "clientMessageId": "manual", "autoResume": False}), "manual-stale")
    assert exc.value.code == "stale_revision"
    with platform.db.transaction() as session:
        project = session.get(Project, ident)
        project.state = {**project.state, "confirmed": True}
    with pytest.raises(DomainError) as exc:
        platform.control.command(platform.principal, Command(type="project.message", expectedRevision=old_revision,
            payload={"projectId": ident, "text": "Accepted baseline", "clientMessageId": "accepted", "autoResume": True}), "accepted-stale")
    assert exc.value.code == "stale_revision"


def test_review_can_request_one_fresh_research_pass_and_research_cannot_self_loop(platform):
    ident, capture = start(platform)
    worker = engine(platform)
    _, attempt = admit(platform, worker, capture)
    finish(worker, capture, attempt)
    research = next_run(platform, ident)
    _, attempt = admit(platform, worker, research)
    finish(worker, research, attempt, researchRequired=True)
    assert next_run(platform, ident) is None
    response = command(platform, ident, "project.message", {"text": "Use a different air sensor", "clientMessageId": "new-sensor", "autoResume": True})
    review = response["runId"]
    _, attempt = admit(platform, worker, review)
    finish(worker, review, attempt, researchRequired=True)
    lookup = next_run(platform, ident)
    assert lookup and lookup != review
    assert not workspace(platform, ident)["state"]["clarification"]["readyToConfirm"]
    _, attempt = admit(platform, worker, lookup)
    assert worker.attempt_input(attempt)[0]["task"] == "project.research"
    finish(worker, lookup, attempt, researchRequired=True)
    assert next_run(platform, ident) is None
    p = workspace(platform, ident)
    assert p["state"]["planning"]["lastResearchInputVersion"] == 1
    assert p["state"]["planning"]["pendingResearch"] is False
    assert p["state"]["clarification"]["readyToConfirm"]


def test_requested_research_waits_for_answers_and_failed_research_does_not_unlock_confirmation(platform):
    ident, capture = start(platform)
    worker = engine(platform)
    _, attempt = admit(platform, worker, capture)
    finish(worker, capture, attempt)
    research = next_run(platform, ident)
    _, attempt = admit(platform, worker, research)
    finish(worker, research, attempt)
    response = command(platform, ident, "project.message", {"text": "Try a new power system", "clientMessageId": "power-research", "autoResume": True})
    review = response["runId"]
    _, attempt = admit(platform, worker, review)
    finish(worker, review, attempt, researchRequired=True, clarificationQuestions=[question()])
    assert next_run(platform, ident) is None
    response = command(platform, ident, "project.answer", {"questionId": "power", "values": ["battery"], "autoResume": True})
    lookup = response["runId"]
    task, attempt = admit(platform, worker, lookup)
    assert worker.attempt_input(attempt)[0]["task"] == "project.research"
    worker.block_task(task, attempt, "Research unavailable")
    worker.finish_run(lookup)
    assert next_run(platform, ident) is None
    assert not workspace(platform, ident)["state"]["clarification"]["readyToConfirm"]
    with pytest.raises(DomainError):
        command(platform, ident, "project.confirm")


def test_explicit_retry_preserves_compact_automatic_planning_contract(platform):
    ident, capture = start(platform)
    worker = engine(platform)
    task, attempt = admit(platform, worker, capture)
    worker.block_task(task, attempt, "Transient model failure")
    worker.finish_run(capture)
    response = command(platform, ident, "project.retry", {"runId": capture})
    _, attempt = admit(platform, worker, response["runId"])
    context = worker.attempt_input(attempt)[0]
    assert context["planning"]["automatic"] is True
    assert context["task"] == "project.capture"
