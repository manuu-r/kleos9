import json

import pytest
from sqlalchemy import func, select

from kleos.auth import Principal
from kleos.contracts import AgentOutput, Command, DomainError, GeneratedFile
from kleos.db import (
    Attempt,
    CommandReceipt,
    Invocation,
    Project,
    Run,
    Task,
    Validation,
)
from kleos.engine import EngineeringEngine


def create(platform, brief="A smartwatch"):
    return platform.control.command(platform.principal, Command(type="project.create", payload={"brief": brief}), brief)


def command(platform, project_id, kind, payload=None, key=None):
    revision = platform.control.workspace(platform.principal, project_id)["revision"]
    return platform.control.command(platform.principal, Command(
        type=kind, payload={"projectId": project_id, **(payload or {})}, expected_revision=revision),
        key or f"{kind}:{revision}")


class FakeModels:
    class Registry:
        @staticmethod
        def configured(_provider):
            return True
    registry = Registry()
    calls = 0

    async def generate(self, provider, model, context):
        self.calls += 1
        return AgentOutput(summary="A sourced candidate concept", requirements={"screen": "small"},
                           sources=["https://example.com/datasheet"]), {
            "inputTokens": 100, "outputTokens": 50}, json.dumps({"provider": provider, "content": "candidate"})


def engine(platform, models=None):
    return EngineeringEngine(platform.db, platform.settings, platform.store, models or FakeModels())


def start_research(platform):
    project = create(platform)
    result = command(platform, project["projectId"], "project.research")
    worker = engine(platform)
    task_ids = worker.admit_run(result["runId"])
    return project["projectId"], result["runId"], task_ids[0], worker


def test_api_requires_auth_and_persists_idempotency(platform):
    platform.client.headers.pop("Authorization")
    assert platform.client.get("/api/v1/projects").status_code == 401
    platform.client.headers["Authorization"] = "Bearer test-token-not-for-deployment"
    request = {"type": "project.create", "payload": {"brief": "A smartwatch"}}
    first = platform.client.post("/api/v1/commands", json=request, headers={"Idempotency-Key": "one"})
    repeated = platform.client.post("/api/v1/commands", json=request, headers={"Idempotency-Key": "one"})
    assert first.status_code == 202
    assert first.json() == repeated.json()
    request["payload"]["brief"] = "Different"
    assert platform.client.post("/api/v1/commands", json=request, headers={"Idempotency-Key": "one"}).status_code == 409
    with platform.db.transaction() as session:
        assert session.scalar(select(func.count(Project.id))) == 1
        assert session.scalar(select(func.count(CommandReceipt.id))) == 1


def test_deleted_command_receipt_is_a_tombstone_not_a_schema_error_or_replay(platform):
    body = {"type": "project.create", "payload": {"brief": "Deleted fixture"}}
    headers = {"Idempotency-Key": "deleted-fixture"}
    first = platform.client.post("/api/v1/commands", json=body, headers=headers)
    with platform.db.transaction() as session:
        receipt = session.scalar(select(CommandReceipt))
        receipt.response = {"projectId": first.json()["projectId"], "deleted": True}
    replay = platform.client.post("/api/v1/commands", json=body, headers=headers)
    assert replay.status_code == 410 and replay.json()["code"] == "project_deleted"
    with platform.db.transaction() as session:
        assert session.scalar(select(func.count(Project.id))) == 1


def test_cross_tenant_reads_writes_and_runs_rejected(platform):
    project, run_id, task_id, worker = start_research(platform)
    outsider = Principal("other", "org-b", "owner")
    assert platform.control.projects(outsider)["projects"] == []
    for fn in (
        lambda: platform.control.workspace(outsider, project),
        lambda: platform.control.events(outsider, project),
        lambda: platform.control.inspect_run(outsider, run_id),
        lambda: platform.control.command(outsider, Command(type="project.cancel",
                    payload={"projectId": project}, expected_revision=1), "cancel"),
    ):
        with pytest.raises(DomainError) as exc:
            fn()
        assert exc.value.status == 404


def test_viewer_cannot_write(platform):
    with pytest.raises(DomainError) as exc:
        platform.control.command(Principal("viewer", "org-a", "viewer"),
                                 Command(type="project.create", payload={"brief": "A"}), "one")
    assert exc.value.status == 403


def test_generate_one_lane_keeps_other_lanes_out_of_the_run(platform):
    project_id = create(platform)["projectId"]
    with platform.db.transaction() as session:
        project = session.get(Project, project_id)
        project.state, project.interfaces = {"confirmed": True}, {"firmware": {"board": "native_sim"}}
    receipt = command(platform, project_id, "project.generate", {"lane": "mechanics"})
    with platform.db.transaction() as session:
        assert list(session.scalars(select(Task.lane).where(Task.run_id == receipt["runId"]))) == ["mechanics"]


def test_misspelled_lane_cannot_accidentally_start_every_worker(platform):
    project_id = create(platform)["projectId"]
    with platform.db.transaction() as session:
        project = session.get(Project, project_id)
        project.state, project.interfaces = {"confirmed": True}, {"firmware": {"board": "native_sim"}}
    with pytest.raises(DomainError) as exc:
        command(platform, project_id, "project.generate", {"lane": "mechanical-typo"})
    assert exc.value.code == "invalid_lane"
    with platform.db.transaction() as session:
        assert session.scalar(select(func.count(Task.id))) == 0


def attempt_model(platform, attempt_id):
    with platform.db.transaction() as session:
        return session.get(Attempt, attempt_id).model


def test_policy_switch_pins_current_attempt(platform):
    project_id, run_id, task_id, worker = start_research(platform)
    attempt_id = worker.begin_attempt(task_id)
    policy = platform.control.registry.default_policy()
    policy["research"] = policy["firmware"]
    assert policy["research"]["model"] != attempt_model(platform, attempt_id)
    command(platform, project_id, "project.setModelPolicy", {"modelPolicy": policy})
    with platform.db.transaction() as session:
        attempt = session.get(Attempt, attempt_id)
        assert attempt.provider == "meta" and attempt.model == platform.control.registry.models["meta"][0]
        assert attempt.policy_revision == 1
        assert session.get(Project, project_id).policy_revision == 2
    assert worker.begin_attempt(task_id) == attempt_id


@pytest.mark.asyncio
async def test_model_result_recovery_does_not_repeat_charge(platform):
    project_id, run_id, task_id, worker = start_research(platform)
    await worker.run_task(task_id)
    await worker.run_task(task_id)
    worker.finish_run(run_id)
    assert worker.models.calls == 1
    with platform.db.transaction() as session:
        assert session.get(Run, run_id).status == "succeeded"
        assert session.scalar(select(func.count(Invocation.id))) == 1
        project = session.get(Project, project_id)
        assert project.reserved_usd == 0
        assert project.spent_usd == 1
        assert project.state["proposal"]["summary"]


def test_stale_revision_and_fenced_worker_cannot_publish(platform):
    project_id, run_id, task_id, worker = start_research(platform)
    attempt_id = worker.begin_attempt(task_id)
    old_revision = platform.control.workspace(platform.principal, project_id)["revision"]
    command(platform, project_id, "project.cancel")
    with pytest.raises(DomainError) as exc:
        platform.control.command(platform.principal, Command(
            type="project.confirm", payload={"projectId": project_id}, expected_revision=old_revision), "stale")
    assert exc.value.code == "stale_revision"
    with pytest.raises(DomainError) as exc:
        worker.store_candidate(attempt_id, AgentOutput(summary="late", files=[
            GeneratedFile(path="test.py", content="late result")]))
    assert exc.value.code == "stale_attempt"


def test_synthetic_physical_results_never_approve_digital_release(platform):
    project_id = create(platform)["projectId"]
    with platform.db.transaction() as session:
        for name in ["interfaces", "mechanics", "electronics", "firmware", "application"]:
            session.add(Validation(tenant_id="org-a", project_id=project_id, task_id="synthetic",
                                   revision=1, name=name, status="PASS", evidence_kind="synthetic",
                                   validator="fake", artifact_hashes={}))
    with pytest.raises(DomainError) as exc:
        command(platform, project_id, "project.acceptRelease")
    assert exc.value.code == "gate_failed"
    with pytest.raises(DomainError):
        command(platform, project_id, "project.ship")


def test_provider_keys_never_in_catalog(platform):
    platform.settings.meta_api_key = "sensitive"
    body = json.dumps(platform.control.registry.catalog())
    assert "sensitive" not in body
    assert '"creditBalance": null' in body


@pytest.mark.asyncio
async def test_budget_must_be_configured_before_inference(platform):
    project_id, run_id, task_id, worker = start_research(platform)
    platform.settings.daily_budget_usd = 0
    await worker.run_task(task_id)
    worker.finish_run(run_id)
    assert worker.models.calls == 0
    assert platform.control.inspect_run(platform.principal, run_id)["status"] == "blocked"


def test_event_replay_and_expired_cursor(platform):
    project_id = create(platform)["projectId"]
    events = platform.control.events(platform.principal, project_id)
    assert events[0]["sequence"] == 1
    response = platform.client.get(f"/api/v1/projects/{project_id}/events?after=0&once=true")
    assert response.status_code == 200
    assert "event: activity" in response.text
    assert platform.control.events(platform.principal, project_id, events[-1]["sequence"]) == []
    with platform.db.transaction() as session:
        session.get(Project, project_id).event_floor = 1
    assert platform.client.get(f"/api/v1/projects/{project_id}/events?after=0&once=true").status_code == 410


@pytest.mark.parametrize("path", ["../secret", "/etc/passwd", "a/../../b", r"a\..\b"])
def test_artifact_path_traversal_rejected(path):
    with pytest.raises(ValueError):
        GeneratedFile(path=path, content="x")


def test_outbox_enqueue_is_retry_safe(platform):
    project_id = create(platform)["projectId"]
    command(platform, project_id, "project.research")
    class Client:
        def __init__(self):
            self.ids = set()
        def enqueue(self, options, *args):
            self.ids.add(options["workflow_id"])
    from kleos.worker import Dispatcher
    client = Client()
    dispatcher = Dispatcher(platform.db, platform.settings, client)
    assert dispatcher.once() == 1
    assert dispatcher.once() == 0
    assert len(client.ids) == 1
