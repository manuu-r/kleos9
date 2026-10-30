"""Versioned, user-facing requirements questions and durable answer validation."""
import hashlib
import json
import math

from pydantic import ValidationError

from .contracts import ClarificationQuestion, DomainError

MAX_NEW_QUESTIONS_PER_TURN = 3
MAX_PLANNING_QUESTIONS = 16


def question_hash(question):
    # Cosmetic help text does not invalidate a saved engineering choice.
    semantics = {k: question.get(k) for k in ("prompt", "type", "options", "required", "allowCustom", "min", "max", "unit")}
    return hashlib.sha256(json.dumps(semantics, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def get_clarification_questions(proposal):
    raw = proposal.get("clarificationQuestions", proposal.get("clarification_questions", []))
    if raw:
        try:
            questions = [ClarificationQuestion.model_validate(q).model_dump(mode="json", by_alias=True) for q in raw]
        except ValidationError as exc:
            raise DomainError("The clarification questions need to be regenerated", 409, "invalid_questions") from exc
        if len({q["id"] for q in questions}) != len(questions):
            raise DomainError("The clarification questions contain repeated IDs", 409, "invalid_questions")
        return questions
    result = []
    seen = set()
    for prompt in proposal.get("questions", []):
        if not isinstance(prompt, str) or not prompt.strip():
            continue
        prompt = prompt.strip()
        ident = "legacy-" + hashlib.sha256(prompt.encode()).hexdigest()[:16]
        if ident in seen:
            continue
        seen.add(ident)
        result.append({"id": ident, "prompt": prompt, "helpText": "", "type": "text", "options": [],
                       "required": True, "allowCustom": True, "min": None, "max": None, "unit": ""})
    return result


def validate_answer(question, values):
    if not isinstance(values, list) or len(values) > 12:
        raise DomainError("Choose or enter a valid answer", 422, "invalid_answer")
    if not values:
        return []
    if question["type"] != "multi_select" and len(values) != 1:
        raise DomainError("This question accepts one answer", 422, "invalid_answer")
    if any(isinstance(v, bool) or not isinstance(v, (str, int, float)) for v in values):
        raise DomainError("Answers must contain text or a number", 422, "invalid_answer")
    normalized = [str(v).strip() for v in values]
    if any(not v or len(v) > 2000 for v in normalized) or len(set(normalized)) != len(normalized):
        raise DomainError("Provide a nonempty answer without repeated choices", 422, "invalid_answer")
    if question["type"] in {"single_select", "multi_select"} and not question.get("allowCustom"):
        allowed = {option["value"] for option in question["options"]}
        if any(v not in allowed for v in normalized):
            raise DomainError("Choose one of the available options", 422, "invalid_answer")
    if question["type"] == "number":
        try:
            number = float(normalized[0])
        except ValueError as exc:
            raise DomainError("Enter a valid number", 422, "invalid_answer") from exc
        if (not math.isfinite(number) or (question.get("min") is not None and number < question["min"])
                or (question.get("max") is not None and number > question["max"])):
            raise DomainError("Enter a number within the requested range", 422, "invalid_answer")
    return normalized


def clarification_view(project):
    state = project.state or {}
    questions = get_clarification_questions(state.get("proposal", {}))
    records = state.get("clarificationAnswers", {})
    answers = {}
    for question in questions:
        record = records.get(question["id"])
        values = None
        if isinstance(record, dict) and record.get("questionHash") == question_hash(question):
            values = record.get("values")
        # Existing legacy projects may have answers stored under their full prompt.
        elif question["id"].startswith("legacy-"):
            values = state.get("answers", {}).get(question["prompt"], project.requirements.get(question["prompt"]))
        if values is not None and (values or not question["required"]):
            try:
                answers[question["id"]] = validate_answer(question, values)
            except DomainError:
                pass
    # Completed decisions remain inspectable and editable after the model stops
    # asking them. Active definitions always take precedence over captured ones.
    resolved = []
    active_ids = {q["id"] for q in questions}
    for ident, record in records.items():
        if ident in active_ids or not isinstance(record, dict):
            continue
        question = record.get("question")
        if not isinstance(question, dict) or question.get("id") != ident:
            continue
        try:
            question = ClarificationQuestion.model_validate(question).model_dump(mode="json", by_alias=True)
            if record.get("questionHash") != question_hash(question):
                continue
            values = validate_answer(question, record.get("values"))
        except (ValidationError, DomainError):
            continue
        if not values and question["required"]:
            questions.append(question)
            continue
        resolved.append(question)
        answers[ident] = values
    required = [q["id"] for q in questions if q["required"]]
    remaining = [ident for ident in required if ident not in answers]
    planning = state.get("planning", {})
    needs_review = (state.get("clarificationAnswerVersion", 0) > state.get("clarificationReviewedVersion", 0)
                    or planning.get("inputVersion", 0) > planning.get("appliedInputVersion", 0)
                    or planning.get("pendingResearch", False))
    feasibility_ready = not planning.get("autoResume") or planning.get("feasibilityComplete", False)
    questions = [{**q, "questionHash": question_hash(q)} for q in questions]
    resolved = [{**q, "questionHash": question_hash(q)} for q in resolved]
    return {"questions": questions, "resolvedQuestions": resolved, "answers": answers, "answeredCount": len(answers),
            "requiredCount": len(required), "remainingRequired": remaining, "needsReview": needs_review,
            "readyToConfirm": bool(state.get("proposal")) and not remaining and not needs_review and feasibility_ready}


def answer_context(project):
    view = clarification_view(project)
    questions = {q["id"]: q for q in view["questions"]}
    answers = []
    for q in view["questions"]:
        if q["id"] in view["answers"]:
            answers.append(_context_answer(q, view["answers"][q["id"]], "current"))
    # Resolved questions can disappear from the next proposal; their explicit
    # user choices must remain available to all later review and generation work.
    for ident, record in project.state.get("clarificationAnswers", {}).items():
        if ident in questions or not isinstance(record, dict):
            continue
        question = record.get("question")
        if not isinstance(question, dict) or record.get("questionHash") != question_hash(question):
            continue
        try:
            values = validate_answer(question, record.get("values"))
        except DomainError:
            continue
        answers.append(_context_answer(question, values, "resolved"))
    return {"version": project.state.get("clarificationAnswerVersion", 0), "answers": answers,
            "questions": [{k: v for k, v in q.items() if k != "questionHash"}
                          for q in view["questions"] + view["resolvedQuestions"]], "remainingRequired": view["remainingRequired"]}


def _context_answer(question, values, status):
    return {"questionId": question["id"], "prompt": question["prompt"], "values": values, "status": status,
            "labels": [next((o["label"] for o in question["options"] if o["value"] == v), v) for v in values]}


def retain_unanswered_questions(output, context):
    """Model output cannot remove or downgrade a still-required user decision.

    Accept a friendlier prompt/choice presentation under the same canonical ID.
    Resolved answers may disappear from the active questions; answer_context keeps
    their original user choices available in later reviews.
    """
    incoming = get_clarification_questions(output)
    clarification = context.get("clarification", {})
    previous = clarification.get("questions")
    if previous is None:
        previous = get_clarification_questions(context.get("proposal", {}))
    answered = {a["questionId"] for a in clarification.get("answers", [])
                if a.get("values")}
    pending = [q for q in previous if q.get("required", True) and q["id"] not in answered]
    pending_ids = {q["id"] for q in pending}
    supplied = {q["id"]: q for q in incoming}
    # Existing open decisions stay first and required even if omitted by the AI.
    retained = [{**supplied.get(q["id"], q), "required": True} for q in pending]
    # A deeper design conversation stays finite: only genuinely new questions are
    # limited, per turn and over the project, and never any outstanding decision.
    known = {q["id"] for q in previous} | {a["questionId"] for a in clarification.get("answers", [])}
    room = min(MAX_NEW_QUESTIONS_PER_TURN, max(0, MAX_PLANNING_QUESTIONS - len(known)))
    fresh = [q for q in incoming if q["id"] not in pending_ids and q["id"] not in known]
    carried = [q for q in incoming if q["id"] not in pending_ids and q["id"] in known]
    return retained + carried + fresh[:room]
