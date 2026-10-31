"""Independent review regressions for authority boundaries and public inspection."""

import json

import pytest
from sqlalchemy import select
from test_control_plane import command, create, start_research
from test_execution import execution as execution
from test_safety_and_recovery import native_job

from kleos.contracts import CheckResult, DomainError, JobResult
from kleos.db import Attempt, NativeJob, Project, Task, Validation


@pytest.mark.parametrize("status", ["failed", "unsupported"])
def test_unsuccessful_validation_cannot_authorize_release_with_pass_claims(platform, status):
    project_id, _, _, worker = native_job(platform, "validate")
    result = JobResult(status=status, checks=[CheckResult(name=name, status="PASS")
                       for name in ("interfaces", "mechanics", "electronics", "firmware", "application")])
    worker.finish_native("native-fixture", result)
    with platform.db.transaction() as session:
        assert not list(session.scalars(select(Validation).where(Validation.project_id == project_id)))
    with pytest.raises(DomainError):
        command(platform, project_id, "project.acceptRelease")


def test_public_inspector_redacts_outputs_and_excludes_raw_context_bodies(platform):
    project_id, run_id, task_id, worker = start_research(platform)
    attempt_id = worker.begin_attempt(task_id)
    recognizable_key = "sk-user_supplied_secret_123456789"
    opaque_key = "customer-opaque-api-key-value"
    with platform.db.transaction() as session:
        attempt = session.get(Attempt, attempt_id)
        attempt.context = {**attempt.context, "brief": f"Connect using {recognizable_key}",
                           "previousSourceFiles": [{"name": "secret.txt", "sha256": "a", "content": "raw source body"}],
                           "requirements": {"apiKey": opaque_key},
                           "repairHistory": [{"output": {"summary": "private raw model response"}}]}
        attempt.output = {"summary": f"Using {recognizable_key}", "requirements": {"apiKey": opaque_key}}
        session.get(Task, task_id).result = {"accessToken": opaque_key}
        session.get(Project, project_id).requirements = {"apiKey": opaque_key}
    inspector = json.dumps(platform.control.inspect_run(platform.principal, run_id))
    assert recognizable_key not in inspector and opaque_key not in inspector
    assert "raw source body" not in inspector and "private raw model response" not in inspector
    workspace = json.dumps(platform.control.workspace(platform.principal, project_id))
    assert opaque_key not in workspace
    summaries = json.dumps(platform.control.projects(platform.principal))
    assert opaque_key not in summaries


def test_validator_results_must_bind_to_the_pinned_requirement_and_interface_contract(platform):
    project_id, _, _, worker = native_job(platform, "validate")
    with platform.db.transaction() as session:
        job = session.get(NativeJob, "native-fixture")
        job.request = {**job.request, "requirements": {"case": "old"}, "interfaces": {"widthMm": 10}}
        project = session.get(Project, project_id)
        project.requirements, project.interfaces = {"case": "new"}, {"widthMm": 12}
    try:
        worker.finish_native("native-fixture", JobResult(status="succeeded", checks=[
            CheckResult(name=name, status="PASS")
            for name in ("interfaces", "mechanics", "electronics", "firmware", "application")]))
    except DomainError:
        pass
    with platform.db.transaction() as session:
        assert not list(session.scalars(select(Validation).where(Validation.project_id == project_id)))
    with pytest.raises(DomainError):
        command(platform, project_id, "project.acceptRelease")


def test_watch_profile_cannot_silently_exclude_every_unsupported_capability(platform):
    project_id = create(platform)["projectId"]
    with platform.db.transaction() as session:
        project = session.get(Project, project_id)
        project.state = {"proposal": {"requirements": {
            "validationProfile": "watch-telemetry-v1", "requiredPeripheralCoverage": [], "excludedCoverage": []}}}
    with pytest.raises(DomainError):
        command(platform, project_id, "project.confirm")


def test_native_cancellation_uses_the_job_pinned_cluster_after_configuration_changes(execution):
    job_id = execution.submit("attempt", "build")
    execution.dispatch_pending()
    execution.settings.ecs_cluster = "replacement-cluster"
    execution.cancel_run("run")
    assert execution.ecs.stops[-1]["task"] == f"arn:task/{job_id}"
    assert execution.ecs.stops[-1]["cluster"] == "cluster"


def _another_native_attempt(execution):
    from kleos.db import Artifact, Run
    from kleos.storage import digest
    body = b"assembly = None"
    execution.store.put("second/enclosure.py", body)
    with execution.db.transaction() as session:
        # A new edit supersedes the old source path, while cancellation still
        # holds native capacity until actual task termination is observed.
        for artifact in session.scalars(select(Artifact).where(Artifact.project_id == "project")):
            artifact.status = "stale"
        session.add(Run(id="run-2", tenant_id="org", project_id="project", command_id="command-2", kind="build", revision=1))
        session.add(Task(id="task-2", tenant_id="org", project_id="project", run_id="run-2",
                         lane="mechanics", kind="build", revision=1, fence=1))
        session.add(Attempt(id="attempt-2", tenant_id="org", task_id="task-2", number=1, fence=1,
                            policy_revision=1, provider="meta", model="qualified-model", context={}))
        session.add(Artifact(id="artifact-2", tenant_id="org", project_id="project", task_id="task-2",
                             attempt_id="attempt-2", revision=1, lane="mechanics", name="enclosure.py",
                             key="second/enclosure.py", sha256=digest(body), size=len(body)))


def test_cancelled_native_task_holds_capacity_until_termination_is_observed(execution):
    execution.settings.max_native_jobs = 1
    execution.submit("attempt", "build")
    execution.dispatch_pending()
    execution.ecs.fail_stop = True
    with pytest.raises(ConnectionError):
        execution.cancel_run("run")
    _another_native_attempt(execution)
    execution.submit("attempt-2", "build")
    execution.dispatch_pending()
    assert len(execution.ecs.calls) == 1
    execution.ecs.fail_stop = False
    execution.cancel_run("run")
    execution.dispatch_pending()
    assert len(execution.ecs.calls) == 2


def test_explicit_dispatch_failure_after_cancellation_cannot_resurrect_job(execution):
    job_id = execution.submit("attempt", "build")
    def cancel_then_reject(**kwargs):
        execution.cancel_run("run")
        return {"failures": [{"reason": "capacity"}], "tasks": []}
    execution.ecs.run_task = cancel_then_reject
    with pytest.raises(DomainError):
        execution.dispatch_pending()
    with execution.db.transaction() as session:
        assert session.get(NativeJob, job_id).status == "cancelled"


def test_queued_native_job_is_not_dispatched_after_task_is_superseded(execution):
    job_id = execution.submit("attempt", "build")
    with execution.db.transaction() as session:
        session.get(Task, "task").fence += 1
    execution.dispatch_pending()
    assert execution.ecs.calls == []
    with execution.db.transaction() as session:
        assert session.get(NativeJob, job_id).status == "cancelled"


def test_explicit_deletion_preserves_only_native_cancellation_and_budget_metadata(execution):
    from kleos.auth import Principal
    from kleos.maintenance import Maintenance
    job_id = execution.submit("attempt", "build")
    execution.dispatch_pending()
    with execution.db.transaction() as session:
        revision = session.get(Project, "project").revision
    maintenance = Maintenance(execution.db, execution.settings, execution.store)
    maintenance.request_delete(Principal("owner", "org", "owner"), "project", revision)
    maintenance.purge_project("project")
    with execution.db.transaction() as session:
        job = session.get(NativeJob, job_id)
        project = session.get(Project, "project")
        assert job.status == "cancelled" and job.request["reservation"]["settled"]
        assert job.request["reservation"]["dispatchAttempted"]
        assert job.request["dispatch"]["cluster"] == "cluster"
        assert "inputArtifacts" not in job.request and "interfaces" not in job.request
        assert project.reserved_usd == 0 and project.spent_usd == execution.settings.native_job_reservation_usd
    execution.settings.ecs_cluster = "replacement-cluster"
    execution.cancel_run("run")
    assert execution.ecs.stops[-1]["cluster"] == "cluster"


def test_interrupted_native_completion_can_be_finalized_by_infrastructure_reconciliation(platform, monkeypatch):
    from kleos.contracts import GeneratedFile
    _, task_id, _, worker = native_job(platform)
    result = JobResult(status="succeeded", files=[GeneratedFile(path="native/firmware.elf", content="compiled")],
                       logs="Original tool completion")
    original = platform.store.put
    def fail_artifact(key, *args, **kwargs):
        if key.endswith("firmware.elf"):
            raise OSError("Injected storage interruption")
        return original(key, *args, **kwargs)
    monkeypatch.setattr(platform.store, "put", fail_artifact)
    with pytest.raises(OSError):
        worker.finish_native("native-fixture", result)
    monkeypatch.setattr(platform.store, "put", original)
    worker.finish_native("native-fixture", JobResult(status="failed", limitations=["Task stopped before commit"],
                                                     logs="ECS reconciliation observed STOPPED"))
    with platform.db.transaction() as session:
        assert session.get(NativeJob, "native-fixture").status == "failed"
        assert session.get(Task, task_id).status == "blocked"
