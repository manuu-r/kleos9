import pytest
from pydantic import ValidationError
from test_control_plane import command, create, engine

from kleos.clarification import answer_context, get_clarification_questions
from kleos.contracts import AgentOutput, ClarificationQuestion, DomainError
from kleos.db import Project


def question(**overrides):
    return {"id": "power", "prompt": "How should it be powered?", "type": "single_select",
            "options": [{"value": "usb", "label": "Plug it in"}, {"value": "battery", "label": "Rechargeable battery"}],
            "required": True, **overrides}


def setup_proposal(platform, questions=None, legacy=None):
    ident = create(platform)["projectId"]
    with platform.db.transaction() as session:
        p = session.get(Project, ident)
        p.state = {"confirmed": False, "proposal": {"summary": "A desk monitor", "requirements": {"power": "usb"},
            "clarificationQuestions": questions if questions is not None else [question()], "questions": legacy or []}}
    return ident


def view(platform, ident):
    return platform.control.workspace(platform.principal, ident)["project"]["state"]["clarification"]


def test_choice_answers_are_durable_and_must_be_reviewed_before_confirmation(platform):
    ident = setup_proposal(platform)
    with pytest.raises(DomainError) as exc:
        command(platform, ident, "project.confirm")
    assert exc.value.code == "clarification_required"
    result = command(platform, ident, "project.answer", {"questionId": "power", "values": ["battery"]})
    assert result["runId"] is None
    state = platform.control.workspace(platform.principal, ident)["project"]
    assert state["requirements"] == {}  # UI answer strings do not pollute accepted engineering requirements.
    assert state["state"]["proposal"]["summary"] == "A desk monitor"
    assert view(platform, ident)["answers"] == {"power": ["battery"]}
    assert view(platform, ident)["remainingRequired"] == []
    assert view(platform, ident)["needsReview"] is True
    with pytest.raises(DomainError) as exc:
        command(platform, ident, "project.confirm")
    assert exc.value.code == "clarification_review_required"

    receipt = command(platform, ident, "project.requestReview")
    worker = engine(platform)
    task = worker.admit_run(receipt["runId"])[0]
    attempt = worker.begin_attempt(task)
    context, *_ = worker.attempt_input(attempt)
    assert context["clarification"]["answers"][0]["labels"] == ["Rechargeable battery"]
    worker.finish_attempt(attempt, AgentOutput(summary="A rechargeable monitor", requirements={"power": "battery"}).model_dump())
    worker.finish_run(receipt["runId"])
    assert view(platform, ident)["readyToConfirm"] is True
    command(platform, ident, "project.confirm")
    assert platform.control.workspace(platform.principal, ident)["project"]["requirements"]["power"] == "battery"


def test_existing_legacy_questions_get_stable_ids_and_accept_old_prompt_keys(platform):
    ident = setup_proposal(platform, questions=[], legacy=["What display do you prefer?"])
    q = view(platform, ident)["questions"][0]
    assert q["id"].startswith("legacy-") and q["type"] == "text"
    command(platform, ident, "project.answer", {"questionKey": q["prompt"], "values": ["A small readable screen"]})
    assert view(platform, ident)["questions"][0]["id"] == q["id"]
    assert view(platform, ident)["answers"][q["id"]] == ["A small readable screen"]


@pytest.mark.parametrize("values", [["invalid"], ["usb", "battery"], [True], [""], {"usb": True}, ["usb", "usb"]])
def test_invalid_answers_are_rejected(platform, values):
    ident = setup_proposal(platform)
    with pytest.raises(DomainError) as exc:
        command(platform, ident, "project.answer", {"questionId": "power", "values": values})
    assert exc.value.code == "invalid_answer"
    assert view(platform, ident)["answers"] == {}


def test_custom_multi_and_number_answers_are_checked(platform):
    questions = [question(id="sensors", type="multi_select", allowCustom=True),
                 {"id": "budget", "prompt": "Your budget?", "type": "number", "min": 10, "max": 200, "unit": "USD"}]
    ident = setup_proposal(platform, questions=questions)
    command(platform, ident, "project.answer", {"questionId": "sensors", "values": ["usb", "Solar panel"]})
    for value in ["NaN", "inf", "9", "201", "not a number"]:
        with pytest.raises(DomainError):
            command(platform, ident, "project.answer", {"questionId": "budget", "values": [value]})
    command(platform, ident, "project.answer", {"questionId": "budget", "values": ["120"]})
    assert view(platform, ident)["answeredCount"] == 2


def test_changed_question_does_not_reuse_stale_answer_and_unknown_questions_fail(platform):
    ident = setup_proposal(platform)
    command(platform, ident, "project.answer", {"questionId": "power", "values": ["usb"]})
    with platform.db.transaction() as session:
        p = session.get(Project, ident)
        p.state = {**p.state, "proposal": {"clarificationQuestions": [question(prompt="Power for outdoor use?")]}}
    assert view(platform, ident)["answers"] == {}
    assert view(platform, ident)["remainingRequired"] == ["power"]
    with pytest.raises(DomainError) as exc:
        command(platform, ident, "project.answer", {"questionId": "unknown", "values": ["usb"]})
    assert exc.value.code == "unknown_question"


def test_editing_accepted_answers_invalidates_interfaces_but_keeps_questions(platform):
    ident = setup_proposal(platform)
    with platform.db.transaction() as session:
        p = session.get(Project, ident)
        p.state = {**p.state, "confirmed": True}
        p.interfaces = {"power": "usb"}
    command(platform, ident, "project.answer", {"questionId": "power", "values": ["battery"]})
    p = platform.control.workspace(platform.principal, ident)["project"]
    assert p["interfaces"] == {} and p["engineeringRevision"] == 2
    assert p["state"]["proposal"] and p["state"]["confirmed"] is False


def test_structured_question_schema_rejects_duplicate_ids_and_empty_choices():
    with pytest.raises(ValidationError):
        ClarificationQuestion.model_validate(question(options=[]))
    with pytest.raises(ValidationError):
        AgentOutput(summary="test", clarificationQuestions=[question(), question()])
    assert len(get_clarification_questions({"questions": ["same", "same"]})) == 1


def test_optional_skips_survive_reload_and_review_context(platform):
    ident = setup_proposal(platform, questions=[question(required=False)])
    command(platform, ident, "project.answer", {"questionId": "power", "values": []})
    assert view(platform, ident)["answers"] == {"power": []}
    assert view(platform, ident)["answeredCount"] == 1
    assert view(platform, ident)["remainingRequired"] == []
    with platform.db.transaction() as session:
        p = session.get(Project, ident)
        assert answer_context(p)["answers"][0]["values"] == []


def test_resolved_choices_remain_in_later_model_context(platform):
    ident = setup_proposal(platform)
    command(platform, ident, "project.answer", {"questionId": "power", "values": ["battery"]})
    with platform.db.transaction() as session:
        p = session.get(Project, ident)
        p.state = {**p.state, "proposal": {"summary": "Battery chosen", "questions": []}}
    with platform.db.transaction() as session:
        p = session.get(Project, ident)
        context = answer_context(p)
        assert context["answers"][0]["prompt"] == "How should it be powered?"
        assert context["answers"][0]["labels"] == ["Rechargeable battery"]
        assert context["answers"][0]["status"] == "resolved"


def test_reviewed_requirement_changes_invalidate_previous_interface_baseline(platform):
    ident = setup_proposal(platform, questions=[])
    with platform.db.transaction() as session:
        p = session.get(Project, ident)
        p.requirements = {"power": "usb"}
        p.interfaces = {"power": "usb-interface"}
        p.state = {**p.state, "confirmed": True}
    receipt = command(platform, ident, "project.requestReview")
    worker = engine(platform)
    task = worker.admit_run(receipt["runId"])[0]
    attempt = worker.begin_attempt(task)
    worker.finish_attempt(attempt, AgentOutput(summary="New power proposal", requirements={"power": "battery"}).model_dump())
    worker.finish_run(receipt["runId"])
    command(platform, ident, "project.confirm")
    p = platform.control.workspace(platform.principal, ident)["project"]
    assert p["requirements"]["power"] == "battery"
    assert p["interfaces"] == {} and p["engineeringRevision"] == 2
    assert p["state"]["proposal"]["summary"] == "New power proposal"


def test_required_followup_cannot_be_bypassed_and_active_review_blocks_answer_edit(platform):
    ident = setup_proposal(platform)
    command(platform, ident, "project.answer", {"questionId": "power", "values": ["usb"]})
    receipt = command(platform, ident, "project.requestReview")
    with pytest.raises(DomainError) as exc:
        command(platform, ident, "project.answer", {"questionId": "power", "values": ["battery"]})
    assert exc.value.code == "run_active"
    worker = engine(platform)
    task = worker.admit_run(receipt["runId"])[0]
    attempt = worker.begin_attempt(task)
    worker.finish_attempt(attempt, AgentOutput(summary="One followup", clarificationQuestions=[
        question(id="display", prompt="Should the screen always be on?")]).model_dump())
    worker.finish_run(receipt["runId"])
    assert view(platform, ident)["remainingRequired"] == ["display"]
    with pytest.raises(DomainError) as exc:
        command(platform, ident, "project.confirm")
    assert exc.value.code == "clarification_required"


@pytest.mark.parametrize("kind", ["project.requestReview", "project.research"])
def test_model_cannot_drop_required_unanswered_questions(platform, kind):
    ident = setup_proposal(platform, questions=[], legacy=["How should it be powered?", "What should it measure?"])
    before = view(platform, ident)["questions"]
    receipt = command(platform, ident, kind)
    worker = engine(platform)
    task = worker.admit_run(receipt["runId"])[0]
    attempt = worker.begin_attempt(task)
    context, *_ = worker.attempt_input(attempt)
    assert context["clarification"]["questions"] == [{k: v for k, v in q.items() if k != "questionHash"} for q in before]
    assert context["clarification"]["remainingRequired"] == [q["id"] for q in before]
    worker.finish_attempt(attempt, AgentOutput(summary="Model mistakenly assumed all preferences", questions=[]).model_dump())
    worker.finish_run(receipt["runId"])
    after = view(platform, ident)
    assert after["questions"] == before
    assert len(after["remainingRequired"]) == 2
    assert after["readyToConfirm"] is False
    with pytest.raises(DomainError) as exc:
        command(platform, ident, "project.confirm")
    assert exc.value.code == "clarification_required"


def test_review_can_upgrade_open_question_but_cannot_make_it_optional(platform):
    ident = setup_proposal(platform, questions=[], legacy=["Should power use a USB-C bus or Li-ion charger circuit?"])
    old = view(platform, ident)["questions"][0]
    receipt = command(platform, ident, "project.requestReview")
    worker = engine(platform)
    task = worker.admit_run(receipt["runId"])[0]
    attempt = worker.begin_attempt(task)
    upgraded = question(id=old["id"], prompt="How would you like to power it?", required=False)
    worker.finish_attempt(attempt, AgentOutput(summary="A clearer choice", clarificationQuestions=[upgraded]).model_dump())
    worker.finish_run(receipt["runId"])
    after = view(platform, ident)
    assert after["questions"][0]["id"] == old["id"]
    assert after["questions"][0]["type"] == "single_select"
    assert after["questions"][0]["required"] is True
    assert after["remainingRequired"] == [old["id"]]


def test_resolved_required_questions_can_leave_the_active_review(platform):
    ident = setup_proposal(platform, questions=[question(), question(id="display", prompt="Which screen?")])
    command(platform, ident, "project.answer", {"questionId": "power", "values": ["usb"]})
    receipt = command(platform, ident, "project.requestReview")
    worker = engine(platform)
    task = worker.admit_run(receipt["runId"])[0]
    attempt = worker.begin_attempt(task)
    worker.finish_attempt(attempt, AgentOutput(summary="Power resolved, display undecided").model_dump())
    worker.finish_run(receipt["runId"])
    after = view(platform, ident)
    assert [q["id"] for q in after["questions"]] == ["display"]
    assert after["remainingRequired"] == ["display"]
    with platform.db.transaction() as session:
        assert answer_context(session.get(Project, ident))["answers"][0]["questionId"] == "power"


def test_completed_review_keeps_resolved_choices_editable_without_recovery(platform):
    ident = setup_proposal(platform)
    command(platform, ident, "project.answer", {"questionId": "power", "values": ["usb"]})
    receipt = command(platform, ident, "project.requestReview")
    worker = engine(platform)
    task = worker.admit_run(receipt["runId"])[0]
    attempt = worker.begin_attempt(task)
    worker.finish_attempt(attempt, AgentOutput(summary="USB selected", requirements={"power": "usb"}).model_dump())
    worker.finish_run(receipt["runId"])
    completed = view(platform, ident)
    assert completed["questions"] == []
    assert completed["answers"] == {"power": ["usb"]}
    assert completed["resolvedQuestions"][0]["id"] == "power"
    assert completed["readyToConfirm"] is True
    command(platform, ident, "project.answer", {"questionId": "power", "values": ["battery"]})
    changed = view(platform, ident)
    assert changed["answers"] == {"power": ["battery"]}
    assert changed["needsReview"] is True and changed["readyToConfirm"] is False
    with pytest.raises(DomainError) as exc:
        command(platform, ident, "project.confirm")
    assert exc.value.code == "clarification_review_required"


def test_active_changed_question_takes_precedence_over_historical_definition(platform):
    ident = setup_proposal(platform)
    command(platform, ident, "project.answer", {"questionId": "power", "values": ["usb"]})
    with platform.db.transaction() as session:
        p = session.get(Project, ident)
        p.state = {**p.state, "proposal": {"clarificationQuestions": [question(options=[
            {"value": "solar", "label": "Solar panel"}])]}}
    current = view(platform, ident)
    assert current["resolvedQuestions"] == []
    assert current["answers"] == {} and current["remainingRequired"] == ["power"]
    with pytest.raises(DomainError) as exc:
        command(platform, ident, "project.answer", {"questionId": "power", "values": ["usb"]})
    assert exc.value.code == "invalid_answer"
    command(platform, ident, "project.answer", {"questionId": "power", "values": ["solar"]})
    assert view(platform, ident)["answers"] == {"power": ["solar"]}


def test_cleared_resolved_required_choice_becomes_an_active_question_again(platform):
    ident = setup_proposal(platform)
    command(platform, ident, "project.answer", {"questionId": "power", "values": ["usb"]})
    with platform.db.transaction() as session:
        p = session.get(Project, ident)
        p.state = {**p.state, "proposal": {"summary": "Completed concept", "questions": []},
                   "clarificationReviewedVersion": p.state["clarificationAnswerVersion"]}
    command(platform, ident, "project.answer", {"questionId": "power", "values": []})
    current = view(platform, ident)
    assert [q["id"] for q in current["questions"]] == ["power"]
    assert current["resolvedQuestions"] == []
    assert current["remainingRequired"] == ["power"] and current["requiredCount"] == 1
    assert current["readyToConfirm"] is False
