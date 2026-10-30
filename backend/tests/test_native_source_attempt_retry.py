import pytest
from sqlalchemy import select
from test_execution import execution as execution  # noqa: F401
from test_native_retry import rebuild as rebuild  # noqa: F401

from kleos.contracts import Command, DomainError, JobResult
from kleos.db import Artifact, Attempt, Invocation, NativeJob, Project, Run, Task


@pytest.fixture
def unsubmitted(rebuild):
    executor, worker, control, user, job_id, _, _ = rebuild
    with executor.db.transaction() as session:
        session.delete(session.get(NativeJob, job_id))
        attempt = session.get(Attempt, "attempt")
        files = session.scalars(select(Artifact).where(Artifact.attempt_id == attempt.id,
            ~Artifact.name.startswith("native/"))).all()
        attempt.output = {"summary": "Sources generated; native admission was blocked",
                          "files": [{"path": f.name, "mediaType": f.media_type} for f in files]}
        attempt.status = "blocked"
        session.get(Task, "task").status = "blocked"
        session.get(Task, "task").kind = "project.generate"
        session.get(Run, "run").status = "blocked"
        session.add(Invocation(id="recorded-model", tenant_id="org", project_id="project", attempt_id="attempt",
                               operation="generate", provider="google", model="pinned-source-model", status="succeeded"))
    return executor, worker, control, user


def request(unsubmitted, source="attempt", key="native-source-retry"):
    executor, _, control, user = unsubmitted
    with executor.db.transaction() as session:
        revision = session.get(Project, "project").revision
    return control.command(user, Command(type="project.retryNative", expected_revision=revision,
        payload={"projectId": "project", "sourceAttemptId": source}), key)


async def test_recorded_latest_source_can_build_without_an_existing_native_job_or_model_call(unsubmitted):
    executor, worker, control, user = unsubmitted
    receipt = request(unsubmitted)
    tasks = worker.admit_run(receipt["runId"])
    assert len(tasks) == 1
    await worker.run_task(tasks[0])
    with executor.db.transaction() as session:
        attempt = session.scalar(select(Attempt).where(Attempt.task_id == tasks[0]))
        job = session.scalar(select(NativeJob).where(NativeJob.attempt_id == attempt.id))
        assert job is not None
        retry = job.request["nativeRetry"]
        assert retry["sourceKind"] == "attempt" and retry["sourceJobId"] is None
        assert retry["sourceAttemptId"] == retry["rootSourceAttemptId"] == "attempt"
        assert job.request["model"] == {"provider": "google", "id": "pinned-source-model"}
        assert len(job.request["inputArtifacts"]) == 1
        assert job.request["inputArtifacts"][0]["name"] == "enclosure.py"
        assert session.get(Task, "task").fence == 2
        assert session.query(Invocation).count() == 1
    worker.finish_native(job.id, JobResult(status="succeeded"))
    with executor.db.transaction() as session:
        assert session.get(Run, receipt["runId"]).status == "succeeded"
        assert not session.get(Project, "project").accepted_release_id


@pytest.mark.parametrize("mutation", ["unrecorded", "stale_contract", "incomplete_closure", "stale_file", "other_tenant", "active"])
def test_source_attempt_retry_rejects_invalid_or_incomplete_sources(unsubmitted, mutation):
    executor, _, _, _ = unsubmitted
    with executor.db.transaction() as session:
        attempt = session.get(Attempt, "attempt")
        if mutation == "unrecorded":
            session.get(Invocation, "recorded-model").status = "uncertain"
        elif mutation == "stale_contract":
            attempt.context = {**attempt.context, "requirements": {"changed": True}}
        elif mutation == "incomplete_closure":
            attempt.output = {**attempt.output, "files": [*attempt.output["files"], {"path": "missing-footprint.kicad_mod"}]}
        elif mutation == "stale_file":
            source = session.scalar(select(Artifact).where(Artifact.name == "enclosure.py"))
            source.status = "stale"
        elif mutation == "other_tenant":
            attempt.tenant_id = "other"
        elif mutation == "active":
            session.get(Task, "task").status = "running"
    with pytest.raises(DomainError):
        request(unsubmitted)
    with executor.db.transaction() as session:
        assert session.query(NativeJob).count() == 0
        assert session.query(Invocation).count() == 1


async def test_source_closure_is_rechecked_before_native_dispatch(unsubmitted):
    executor, worker, _, _ = unsubmitted
    receipt = request(unsubmitted)
    tasks = worker.admit_run(receipt["runId"])
    with executor.db.transaction() as session:
        source = session.scalar(select(Artifact).where(Artifact.name == "enclosure.py"))
        source.sha256 = "modified-after-command"
    await worker.run_task(tasks[0])
    with executor.db.transaction() as session:
        assert session.get(Task, tasks[0]).status == "blocked"
        assert session.query(NativeJob).count() == 0
