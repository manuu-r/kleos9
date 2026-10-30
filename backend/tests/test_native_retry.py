from copy import deepcopy
from datetime import timedelta

import pytest
from sqlalchemy import select
from test_execution import execution as execution

from kleos.auth import Principal
from kleos.contracts import Command, DomainError, GeneratedFile, JobResult
from kleos.db import (
    Artifact,
    Attempt,
    DailyBudget,
    Invocation,
    NativeJob,
    Outbox,
    Project,
    Run,
    Task,
    Validation,
    now,
)
from kleos.domain import ControlPlane, contract_hash
from kleos.engine import EngineeringEngine
from kleos.storage import digest


@pytest.fixture
def rebuild(execution):
    with execution.db.transaction() as session:
        project = session.get(Project, "project")
        project.state, project.interfaces = {"confirmed": True}, {"mechanical": {"units": "mm"}}
        project.requirements, project.policy_revision = {"fixture": "unchanged"}, 9
        attempt = session.get(Attempt, "attempt")
        attempt.provider, attempt.model, attempt.policy_revision = "google", "pinned-source-model", 3
        attempt.context = {"requirements": project.requirements, "interfaces": project.interfaces,
                           "inputArtifacts": [], "brief": "Original source context", "manifest": {"sources": []}}
    source_job_id = execution.submit("attempt", "build")
    with execution.db.transaction() as session:
        job = session.get(NativeJob, source_job_id)
        job.status = "succeeded"
        job.request = {**job.request, "reservation": {**job.request["reservation"], "settled": True, "dispatchAttempted": True}}
        project = session.get(Project, "project")
        project.reserved_usd, project.spent_usd = 0, 1
        daily = session.get(DailyBudget, job.request["reservation"]["day"])
        daily.reserved, daily.spent = 0, 1
        session.get(Attempt, "attempt").status = "succeeded"
        session.get(Task, "task").status = "succeeded"
        session.get(Run, "run").status = "succeeded"
        source_request = deepcopy(job.request)
        old_context = deepcopy(session.get(Attempt, "attempt").context)
        execution.store.put("old/native.stl", b"old native output")
        session.add(Artifact(id="old-native", tenant_id="org", project_id="project", task_id="task",
            attempt_id="attempt", revision=1, lane="mechanics", name="native/model.stl", key="old/native.stl",
            sha256=digest(b"old native output"), size=17, provenance={"nativeJobId": source_job_id}))
    class NoModels:
        async def generate(self, *args, **kwargs):
            raise AssertionError("A native retry must never invoke a model")
    engine = EngineeringEngine(execution.db, execution.settings, execution.store, NoModels(), execution)
    async def no_retrieval(*args, **kwargs):
        raise AssertionError("A native retry must never perform retrieval or embeddings")
    engine.retrieve_context = no_retrieval
    control = ControlPlane(execution.db, execution.settings, execution.store)
    user = Principal("owner", "org", "owner")
    return execution, engine, control, user, source_job_id, source_request, old_context


def request(rebuild, key="native-rebuild", job_id=None):
    execution, _, control, user, source_job_id, _, _ = rebuild
    with execution.db.transaction() as session:
        revision = session.get(Project, "project").revision
    command = Command(type="project.retryNative", expected_revision=revision,
                      payload={"projectId": "project", "jobId": job_id or source_job_id})
    return control.command(user, command, key), command


async def start(rebuild, key="native-rebuild", job_id=None):
    execution, engine, _, _, _, _, _ = rebuild
    accepted, command = request(rebuild, key, job_id)
    tasks = engine.admit_run(accepted["runId"])
    assert len(tasks) == 1
    await engine.run_task(tasks[0])
    with execution.db.transaction() as session:
        task = session.get(Task, tasks[0])
        attempt = session.scalar(select(Attempt).where(Attempt.task_id == task.id))
        job = session.scalar(select(NativeJob).where(NativeJob.attempt_id == attempt.id))
        return accepted, command, task, attempt, job


async def test_native_rebuild_pins_source_skips_model_and_retrieval_and_uses_new_tool(rebuild):
    execution, engine, control, user, source_job_id, source_request, old_context = rebuild
    execution.settings.ecs_task_definitions["mechanics"] = "task:mechanics:18"
    accepted, command, task, attempt, job = await start(rebuild)
    assert control.command(user, command, "native-rebuild") == accepted
    assert job.request["toolVersion"] == "task:mechanics:18"
    assert job.request["inputArtifacts"] == source_request["inputArtifacts"]
    assert attempt.provider == "google" and attempt.model == "pinned-source-model" and attempt.policy_revision == 3
    assert {k: v for k, v in attempt.context.items() if k != "nativeRetry"} == old_context
    assert job.request["nativeRetry"]["rootSourceAttemptId"] == "attempt"
    await engine.run_task(task.id)  # Crash/replay of the native request reuses the persisted job/reservation.
    with execution.db.transaction() as session:
        assert session.get(NativeJob, source_job_id).request == source_request
        assert session.get(Artifact, "artifact").status == "candidate"
        assert session.get(Artifact, "artifact").attempt_id == "attempt"
        assert session.get(Artifact, "artifact").provenance == {}
        assert session.get(Artifact, "old-native").status == "stale"
        assert session.query(Invocation).count() == 0 and session.query(NativeJob).count() == 2
        assert session.get(Project, "project").reserved_usd == 1
        persisted = session.get(NativeJob, job.id)
        persisted.request = {**persisted.request, "reservation": {
            **persisted.request["reservation"], "dispatchAttempted": True}}
    engine.finish_native(job.id, JobResult(status="succeeded", files=[GeneratedFile(path="native/model.step", content="new output")]))
    engine.finish_native(job.id, JobResult(status="succeeded"))
    with execution.db.transaction() as session:
        native = session.scalar(select(Artifact).where(Artifact.attempt_id == attempt.id))
        assert native.provenance["nativeJobId"] == job.id
        assert native.provenance["toolVersion"] == "task:mechanics:18"
        assert native.dependencies == source_request["inputArtifacts"]
        assert session.get(Artifact, "artifact").status == "candidate"
        assert session.query(Validation).count() == 0
        assert session.query(Invocation).count() == 0
        assert session.get(Run, accepted["runId"]).status == "succeeded"
        assert session.get(Project, "project").spent_usd == 2
        assert session.get(Project, "project").reserved_usd == 0


async def test_native_retry_failure_cannot_schedule_paid_model_repairs_and_is_bounded(rebuild):
    execution, engine, _, _, _, _, _ = rebuild
    for number in (1, 2):
        _, _, _, _, job = await start(rebuild, f"rebuild-{number}")
        engine.finish_native(job.id, JobResult(status="failed", measurements={"failureKind": "engineering"},
                                              limitations=["Explicit native failure fixture"]))
    with pytest.raises(DomainError) as exhausted:
        request(rebuild, "third")
    assert exhausted.value.code == "native_retry_exhausted"
    with execution.db.transaction() as session:
        assert session.query(Invocation).count() == 0
        assert not session.scalars(select(Outbox).where(Outbox.key.like("repair:%"))).all()


async def test_rebuild_cancellation_and_old_source_fence_prevent_publication(rebuild):
    execution, engine, control, user, source_job_id, _, _ = rebuild
    _, _, _, _, job = await start(rebuild)
    with pytest.raises(DomainError) as stale:
        engine.finish_native(source_job_id, JobResult(status="succeeded", files=[GeneratedFile(path="late.txt", content="late")]))
    assert stale.value.code == "stale_attempt"
    with execution.db.transaction() as session:
        revision = session.get(Project, "project").revision
    control.command(user, Command(type="project.cancel", payload={"projectId": "project"}, expected_revision=revision), "cancel")
    with pytest.raises(DomainError):
        engine.finish_native(job.id, JobResult(status="succeeded", files=[GeneratedFile(path="new-late.txt", content="late")]))
    with execution.db.transaction() as session:
        assert not session.scalars(select(Artifact).where(Artifact.name.in_(["late.txt", "new-late.txt"]))).all()


@pytest.mark.parametrize("mutation", ["contract", "source_hash", "revision", "active", "wrong_tenant"])
def test_native_retry_rejects_inapplicable_or_unauthorized_source_before_queueing(rebuild, mutation):
    execution, _, _, _, source_job_id, _, _ = rebuild
    with execution.db.transaction() as session:
        if mutation == "contract":
            session.get(Project, "project").requirements = {"changed": True}
        elif mutation == "source_hash":
            session.get(Artifact, "artifact").sha256 = "changed"
        elif mutation == "revision":
            session.get(Project, "project").engineering_revision = 2
        elif mutation == "active":
            session.get(NativeJob, source_job_id).status = "running"
        else:
            session.get(NativeJob, source_job_id).tenant_id = "other"
    with pytest.raises(DomainError):
        request(rebuild)
    with execution.db.transaction() as session:
        assert session.query(Run).count() == 1 and session.query(Attempt).count() == 1


async def test_native_retry_requires_fresh_independent_validation_even_for_same_closure(rebuild):
    execution, engine, control, user, _, _, _ = rebuild
    accepted, _, _, _, job = await start(rebuild)
    engine.finish_native(job.id, JobResult(status="succeeded"))
    with execution.db.transaction() as session:
        project = session.get(Project, "project")
        hashes = {a.id: a.sha256 for a in session.scalars(select(Artifact).where(Artifact.status == "candidate"))}
        for lane in ("interfaces", "mechanics", "electronics", "firmware", "application"):
            session.add(Validation(tenant_id="org", project_id="project", task_id="fixture", revision=1, name=lane,
                status="PASS", evidence_kind="digital", artifact_hashes=hashes, validator="independent-fixture",
                details={"contractHash": contract_hash(project.requirements, project.interfaces)},
                created_at=now() - timedelta(days=1)))
        revision = project.revision
    with pytest.raises(DomainError) as gate:
        control.command(user, Command(type="project.acceptRelease", payload={"projectId": "project"},
                                     expected_revision=revision), "accept-old-checks")
    assert gate.value.code == "gate_failed"
    with execution.db.transaction() as session:
        assert session.get(Run, accepted["runId"]).status == "succeeded"
        assert session.get(Project, "project").accepted_release_id is None


async def test_independent_native_validation_also_skips_retrieval_and_model_calls(rebuild):
    execution, engine, control, user, _, _, _ = rebuild
    execution.settings.ecs_task_definitions["validate"] = "task:independent-validator:1"
    with execution.db.transaction() as session:
        project = session.get(Project, "project")
        project.model_policy = {"review": {"provider": "google", "model": "pinned-validator-label"}}
        revision = project.revision
    accepted = control.command(user, Command(type="project.verify", expected_revision=revision,
                                            payload={"projectId": "project"}), "independent-native-only")
    tasks = engine.admit_run(accepted["runId"])
    await engine.run_task(tasks[0])
    with execution.db.transaction() as session:
        job = session.scalar(select(NativeJob).where(NativeJob.task_id == tasks[0]))
        assert job and job.purpose == "validate"
        assert session.query(Invocation).count() == 0


async def test_native_rebuild_budget_denial_never_falls_back_to_inference(rebuild):
    execution, engine, _, _, _, _, _ = rebuild
    with execution.db.transaction() as session:
        session.get(Project, "project").budget_usd = 1
    accepted, _ = request(rebuild)
    tasks = engine.admit_run(accepted["runId"])
    await engine.run_task(tasks[0])
    engine.finish_run(accepted["runId"])
    with execution.db.transaction() as session:
        assert session.query(NativeJob).count() == 1 and session.query(Invocation).count() == 0
        assert session.get(Project, "project").reserved_usd == 0
        assert session.get(Run, accepted["runId"]).status == "blocked"
