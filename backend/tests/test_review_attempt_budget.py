from test_control_plane import command, create, engine

from kleos.contracts import AgentOutput
from kleos.db import Attempt, Project, Task


def test_explicit_review_turns_do_not_share_a_lifetime_attempt_budget(platform):
    ident = create(platform)["projectId"]
    with platform.db.transaction() as session:
        p = session.get(Project, ident)
        p.state = {"proposal": {"summary": "Initial concept", "questions": []}}
    worker = engine(platform)
    attempts = []
    for _ in range(platform.settings.max_attempts + 2):
        receipt = command(platform, ident, "project.requestReview")
        task_id = worker.admit_run(receipt["runId"])[0]
        attempt = worker.begin_attempt(task_id)
        assert attempt is not None
        # Recovery within the same turn returns the already recorded attempt.
        assert worker.begin_attempt(task_id) == attempt
        worker.finish_attempt(attempt, AgentOutput(summary="Updated user requirements").model_dump())
        worker.finish_run(receipt["runId"])
        attempts.append(attempt)
    assert len(set(attempts)) == platform.settings.max_attempts + 2


def test_same_review_retries_remain_bounded_across_provider_switches_and_recovery(platform):
    ident = create(platform)["projectId"]
    receipt = command(platform, ident, "project.requestReview")
    worker = engine(platform)
    task_id = worker.admit_run(receipt["runId"])[0]
    for index in range(platform.settings.max_attempts):
        # Reconstructing the executor must not reset the retry count.
        worker = engine(platform)
        attempt = worker.begin_attempt(task_id)
        assert attempt is not None
        assert worker.begin_attempt(task_id) == attempt
        with platform.db.transaction() as session:
            session.get(Attempt, attempt).status = "failed"
        policy = platform.control.workspace(platform.principal, ident)["project"]["modelPolicy"]
        provider = "google" if index % 2 else "meta"
        policy["coordination"] = {"provider": provider, "model": platform.control.registry.models[provider]}
        command(platform, ident, "project.setModelPolicy", {"modelPolicy": policy})
    assert engine(platform).begin_attempt(task_id) is None
    with platform.db.transaction() as session:
        task = session.get(Task, task_id)
        assert task.status == "blocked"
        assert task.attempt_count == platform.settings.max_attempts
        assert task.result["reason"] == "Attempt budget exhausted"
