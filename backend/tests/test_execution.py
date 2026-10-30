import base64
import hashlib
import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import select

from kleos.config import Settings
from kleos.contracts import DomainError
from kleos.db import Artifact, Attempt, Base, Database, NativeJob, Organization, Project, Run, Task
from kleos.domain import contract_hash
from kleos.execution import ECSExecution
from kleos.native_runner import (
    RENODE_PROFILE,
    RENODE_SCRIPT,
    RENODE_VERSION,
    UnsupportedTool,
    clean_environment,
    prepare_companion,
    run_command,
    run_job,
    safe_relative,
    stage_inputs,
    validate_interfaces,
    validate_protocol,
    validate_renode,
)
from kleos.storage import ArtifactStore, digest


class FakeECS:
    def __init__(self):
        self.calls = []
        self.stops = []
        self.describes = []
        self.lists = []
        self.fail_stop = False

    def run_task(self, **kwargs):
        self.calls.append(kwargs)
        return {"tasks": [{"taskArn": f"arn:task/{kwargs['clientToken']}"}]}

    def stop_task(self, **kwargs):
        self.stops.append(kwargs)
        if self.fail_stop:
            raise ConnectionError("temporary failure")
        return {"task": {"lastStatus": "STOPPED"}}

    def list_tasks(self, **kwargs):
        self.lists.append(kwargs)
        return {"taskArns": [f"arn:task/{kwargs['startedBy']}"]}

    def describe_tasks(self, **kwargs):
        self.describes.append(kwargs)
        return {"tasks": [{"taskArn": kwargs["tasks"][0], "lastStatus": "STOPPED"}]}


@pytest.fixture
def execution(tmp_path):
    settings = Settings(
        environment="test", database_url=f"sqlite:///{tmp_path}/jobs.db",
        local_artifact_dir=tmp_path / "artifacts", session_key="test-job-signing-key",
        execution_backend="ecs", ecs_cluster="cluster", ecs_subnets=["native-subnet"],
        ecs_security_groups=["native-sg"], ecs_task_definitions={"mechanics": "task:mechanics:17"},
        internal_base_url="https://jobs.example.test",
        daily_budget_usd=100, project_budget_usd=10,
    )
    db = Database(settings.database_url)
    Base.metadata.create_all(db.engine)
    with db.transaction() as session:
        session.add(Organization(id="org", name="test"))
        session.add(Project(id="project", tenant_id="org", owner_id="owner", name="watch", brief="watch", budget_usd=10))
        session.add(Run(id="run", tenant_id="org", project_id="project", command_id="command", kind="build", revision=1))
        session.add(Task(id="task", tenant_id="org", project_id="project", run_id="run",
                         lane="mechanics", kind="build", revision=1, fence=1))
        session.add(Attempt(id="attempt", tenant_id="org", task_id="task", number=1, fence=1,
                            policy_revision=1, provider="muse", model="qualified-model", context={}))
    store = ArtifactStore(settings)
    body = b"from build123d import Box\nassembly = Box(40, 30, 10)\n"
    store.put("input/enclosure.py", body)
    with db.transaction() as session:
        session.add(Artifact(id="artifact", tenant_id="org", project_id="project", task_id="task",
                             attempt_id="attempt", revision=1, lane="mechanics", name="enclosure.py",
                             key="input/enclosure.py", sha256=digest(body), size=len(body)))
    backend = ECSExecution(db, settings, store, FakeECS())
    yield backend
    db.close()


def test_native_submission_is_durable_and_does_not_dispatch(execution):
    job_id = execution.submit("attempt", "build")
    assert execution.submit("attempt", "build") == job_id
    assert execution.ecs.calls == []
    with execution.db.transaction() as session:
        assert len(session.scalars(select(NativeJob)).all()) == 1
        job = session.get(NativeJob, job_id)
        assert job.status == "pending"
        assert job.request["toolVersion"] == "task:mechanics:17"
        assert "token" not in str(job.request).lower()
    execution.dispatch_pending()
    call = execution.ecs.calls[0]
    assert call["clientToken"] == job_id
    assert call["networkConfiguration"]["awsvpcConfiguration"]["assignPublicIp"] == "DISABLED"
    assert call["enableExecuteCommand"] is False
    execution.dispatch_pending()
    assert len(execution.ecs.calls) == 1


def test_native_dispatch_and_reconciliation_do_not_cross_execution_backends(execution):
    ident = execution.submit("attempt", "build")
    with execution.db.transaction() as session:
        job = session.get(NativeJob, ident)
        job.request = {**job.request, "dispatch": {**job.request["dispatch"], "backend": "docker"}}
    execution.dispatch_pending()
    assert not execution.ecs.calls
    assert execution.reconcile() == [] and not execution.ecs.describes
    with execution.db.transaction() as session:
        job = session.get(NativeJob, ident)
        job.status, job.external_id = "running", "docker-container-id"
        job.request = {**job.request, "reservation": {**job.request["reservation"], "dispatchAttempted": True}}
    execution.cancel_run("run")
    assert not execution.ecs.stops
    with execution.db.transaction() as session:
        job = session.get(NativeJob, ident)
        assert job.status == "cancelled" and not job.result.get("terminationObservedAt")


def test_legacy_jobs_without_backend_discriminator_still_dispatch_on_ecs(execution):
    ident = execution.submit("attempt", "build")
    with execution.db.transaction() as session:
        job = session.get(NativeJob, ident)
        dispatch = {k: v for k, v in job.request["dispatch"].items() if k != "backend"}
        job.request = {**job.request, "dispatch": dispatch}
    execution.dispatch_pending()
    assert len(execution.ecs.calls) == 1


def prepare_native_retry(execution):
    original = execution.submit("attempt", "build")
    with execution.db.transaction() as session:
        job = session.get(NativeJob, original)
        job.status = "succeeded"
        project = session.get(Project, "project")
        session.add(Task(id="retry-task", tenant_id="org", project_id="project", run_id="run",
                         lane="mechanics", kind="project.retryNative", revision=1, fence=1))
        session.add(Attempt(id="retry-attempt", tenant_id="org", task_id="retry-task", number=1, fence=1,
                            policy_revision=1, provider="muse", model="qualified-model", context={
                                "nativeRetry": {"sourceJobId": original, "sourceAttemptId": "attempt",
                                                "sourceTaskId": "task", "rootSourceAttemptId": "attempt",
                                                "inputArtifacts": job.request["inputArtifacts"],
                                                "contractHash": contract_hash(project.requirements, project.interfaces)}}))
    return original


def test_native_retry_reuses_exact_source_and_pins_new_tool_without_cloning(execution):
    original = prepare_native_retry(execution)
    execution.settings.ecs_task_definitions["mechanics"] = "task:mechanics:18"
    retried = execution.submit("retry-attempt", "build")
    with execution.db.transaction() as session:
        previous, current = session.get(NativeJob, original), session.get(NativeJob, retried)
        assert previous.request["toolVersion"] == "task:mechanics:17"
        assert current.request["toolVersion"] == "task:mechanics:18"
        assert previous.request["inputArtifacts"] == current.request["inputArtifacts"]
        assert current.request["nativeRetry"]["sourceJobId"] == original
        assert len(session.scalars(select(Artifact)).all()) == 1
    payload = execution.payload(retried, execution._token(retried))
    assert digest(base64.b64decode(payload["inputArtifacts"][0]["contentBase64"])) == payload["inputArtifacts"][0]["sha256"]
    assert execution.ecs.calls == []


@pytest.mark.parametrize("mutation", ["artifact", "contract", "cancelled", "tenant", "native_output", "forged_refs"])
def test_native_retry_rejects_mutated_or_unauthorized_source_before_reserving(execution, mutation):
    original = prepare_native_retry(execution)
    with execution.db.transaction() as session:
        artifact = session.get(Artifact, "artifact")
        if mutation == "artifact":
            artifact.sha256 = "0" * 64
        elif mutation == "contract":
            session.get(Project, "project").requirements = {"changed": True}
        elif mutation == "cancelled":
            session.get(NativeJob, original).status = "cancelled"
        elif mutation == "tenant":
            artifact.tenant_id = "other-tenant"
        elif mutation == "native_output":
            artifact.provenance = {"nativeJobId": original}
        else:
            attempt = session.get(Attempt, "retry-attempt")
            attempt.context = {"nativeRetry": {**attempt.context["nativeRetry"], "inputArtifacts": []}}
        reserved = session.get(Project, "project").reserved_usd
    with pytest.raises(DomainError, match="Native retry"):
        execution.submit("retry-attempt", "build")
    with execution.db.transaction() as session:
        assert len(session.scalars(select(NativeJob)).all()) == 1
        assert session.get(Project, "project").reserved_usd == reserved
    assert execution.ecs.calls == []


def test_job_scope_and_input_integrity(execution):
    job_id = execution.submit("attempt", "build")
    with pytest.raises(DomainError, match="credential"):
        execution.payload(job_id, "wrong-token")
    payload = execution.payload(job_id, execution._token(job_id))
    assert base64.b64decode(payload["inputArtifacts"][0]["contentBase64"]).startswith(b"from build123d")
    execution.store._path("input/enclosure.py").write_bytes(b"tampered")
    with pytest.raises(DomainError, match="integrity"):
        execution.payload(job_id, execution._token(job_id))


def test_stale_worker_cannot_read_pinned_artifacts(execution):
    job_id = execution.submit("attempt", "build")
    with execution.db.transaction() as session:
        session.get(Task, "task").fence += 1
    with pytest.raises(DomainError, match="superseded"):
        execution.payload(job_id, execution._token(job_id))


def test_native_capacity_keeps_work_queued(execution):
    execution.settings.max_native_jobs = 0
    job_id = execution.submit("attempt", "build")
    execution.dispatch_pending()
    assert execution.ecs.calls == []
    with execution.db.transaction() as session:
        assert session.get(NativeJob, job_id).status == "pending"


def test_cancellation_retries_uncertain_termination(execution):
    job_id = execution.submit("attempt", "build")
    execution.dispatch_pending()
    execution.ecs.fail_stop = True
    with pytest.raises(ConnectionError):
        execution.cancel_run("run")
    with pytest.raises(DomainError, match="cancelled"):
        execution.payload(job_id, execution._token(job_id))
    execution.ecs.fail_stop = False
    execution.cancel_run("run")
    execution.cancel_run("run")
    assert len(execution.ecs.stops) == 2
    with execution.db.transaction() as session:
        assert session.get(NativeJob, job_id).result["terminationRequested"]


@pytest.mark.parametrize("name", ["../escape", "/root/secret", "a/../../escape", "a\\b", "\x00bad"])
def test_reject_unsafe_native_paths(name):
    with pytest.raises(ValueError):
        safe_relative(name)


def test_stage_inputs_rejects_hash_and_duplicate_names(tmp_path):
    item = {"id": "1", "lane": "mechanics", "name": "enclosure.py",
            "sha256": hashlib.sha256(b"body").hexdigest(), "contentBase64": base64.b64encode(b"body").decode()}
    with pytest.raises(ValueError, match="duplicate"):
        stage_inputs({"inputArtifacts": [item, item]}, tmp_path)
    item["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="hash"):
        stage_inputs({"inputArtifacts": [item]}, tmp_path)


def test_generated_environment_never_inherits_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("KLEOS_JOB_TOKEN", "callback-secret")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "aws-secret")
    monkeypatch.setenv("GEMINI_API_KEY", "provider-secret")
    environment = clean_environment(tmp_path)
    assert not any("SECRET" in key or "TOKEN" in key or "KEY" in key for key in environment)
    assert environment["HOME"].startswith(str(tmp_path))


def test_unknown_tool_support_does_not_become_success(tmp_path):
    result = run_job({"schemaVersion": 1, "purpose": "validate", "inputArtifacts": []}, tmp_path)
    assert result.status == "unsupported"
    assert all(check.status == "UNKNOWN" for check in result.checks)


def test_voltage_failure_is_independent_of_generated_success_claims():
    interfaces = {
        "mechanical": {"internalMm": dict(x=38, y=28, z=8), "externalMm": dict(x=40, y=30, z=10),
                       "minimumWallMm": 1, "components": [
                           {"id": "battery", "sizeMm": dict(x=20, y=15, z=5), "positionMm": dict(x=1, y=1, z=1)}]},
        "power": {"supplyVoltageV": 5, "maxCurrentMa": 500,
                  "loads": [{"id": "mcu", "minVoltageV": 3, "maxVoltageV": 3.6, "currentMa": 50}]},
        "generatedReport": {"allChecks": "PASS"},
    }
    check = validate_interfaces(interfaces)
    assert check.status == "FAIL"
    assert "incompatible supply voltage" in str(check.details)


def test_mechanical_build_requires_tool_outputs(tmp_path):
    source = b"assembly = 'not a shape'"
    request = {"schemaVersion": 1, "purpose": "build", "lane": "mechanics",
               "inputArtifacts": [{"id": "1", "lane": "mechanics", "name": "enclosure.py",
                                   "sha256": digest(source), "contentBase64": base64.b64encode(source).decode()}]}
    result = run_job(request, tmp_path, execute=lambda *_: (0, "Generated report: PASS"))
    assert result.status == "failed"
    assert "no supported outputs" in result.limitations[0]


@pytest.mark.parametrize("lane,tool", [("firmware", "cc"), ("application", "dart")])
def test_actual_independent_protocol_harness_on_starter_fixture(tmp_path, lane, tool):
    if not shutil.which(tool):
        pytest.skip(f"{tool} is not installed on this test machine")
    source = Path(__file__).parents[1] / "docker/templates/watch-telemetry-v1" / lane
    shutil.copytree(source, tmp_path, dirs_exist_ok=True)
    # Trusted, checked-in fixture only. Production always uses the isolated UID runner.
    def execute(args, directory, timeout):
        if args[0] == "dart":
            sdk_binary = Path(shutil.which("dart")).resolve().parent / "cache/dart-sdk/bin/dart"
            if sdk_binary.is_file():
                args = [str(sdk_binary), *args[1:]]
        result = subprocess.run(args, cwd=directory, capture_output=True, text=True, timeout=timeout)
        return result.returncode, result.stdout + result.stderr
    details = validate_protocol(lane, tmp_path, execute)
    assert details["fixtureCount"] == 30


def test_compile_failure_carries_bounded_repair_evidence(tmp_path):
    source = b"broken source"
    result = run_job({
        "schemaVersion": 1, "purpose": "build", "lane": "mechanics",
        "inputArtifacts": [{"id": "a", "lane": "mechanics", "name": "enclosure.py",
                            "sha256": digest(source), "contentBase64": base64.b64encode(source).decode()}],
    }, tmp_path, execute=lambda *_: (1, "SyntaxError: invalid syntax"))
    assert result.measurements["failureKind"] == "engineering"
    assert "SyntaxError" in result.logs


def test_completion_event_is_durable_hint_never_accepted_result(execution):
    job_id = execution.submit("attempt", "build")
    execution.dispatch_pending()
    class Queue:
        def __init__(self):
            self.deleted = []
        def receive_message(self, **_):
            return {"Messages": [{"ReceiptHandle": "receipt", "Body": json.dumps({
                "id": "event", "source": "aws.ecs", "detail-type": "ECS Task State Change",
                "detail": {"clusterArn": "cluster", "taskArn": f"arn:task/{job_id}", "lastStatus": "STOPPED"},
            })}]}
        def delete_message(self, **kwargs):
            self.deleted.append(kwargs)
    execution.settings.sqs_completion_queue = "https://sqs.test/queue"
    execution.sqs = Queue()
    execution.receive_completion_hints()
    execution.receive_completion_hints()
    with execution.db.transaction() as session:
        job = session.get(NativeJob, job_id)
        assert job.status == "submitted"
        assert job.result["infrastructureHint"]["eventId"] == "event"
    assert len(execution.sqs.deleted) == 2


def test_untrusted_upload_is_excluded_from_native_closure(execution):
    with execution.db.transaction() as session:
        session.get(Artifact, "artifact").status = "input"
    with pytest.raises(DomainError, match="No input artifacts"):
        execution.submit("attempt", "build")


def test_declared_component_envelopes_cannot_overlap():
    check = validate_interfaces({
        "mechanical": {"internalMm": dict(x=38, y=28, z=8), "externalMm": dict(x=40, y=30, z=10),
                       "minimumWallMm": 1, "components": [
                           {"id": name, "sizeMm": dict(x=10, y=10, z=4), "positionMm": dict(x=1, y=1, z=1)}
                           for name in ["battery", "board"]]},
        "power": {"supplyVoltageV": 3.3, "maxCurrentMa": 500,
                  "loads": [{"id": "mcu", "minVoltageV": 3, "maxVoltageV": 3.6, "currentMa": 50}]},
    })
    assert check.status == "FAIL"
    assert "component envelopes overlap" in str(check.details)


def test_native_allowance_is_reserved_once_and_never_overspent(execution):
    with execution.db.transaction() as session:
        session.get(Project, "project").budget_usd = 0.25
    with pytest.raises(DomainError, match="exhausted"):
        execution.submit("attempt", "build")
    assert execution.ecs.calls == []
    with execution.db.transaction() as session:
        assert session.scalars(select(NativeJob)).first() is None
        session.get(Project, "project").budget_usd = 10
    first = execution.submit("attempt", "build")
    assert execution.submit("attempt", "build") == first
    with execution.db.transaction() as session:
        assert session.get(Project, "project").reserved_usd == 1


def test_cancelled_queued_job_refunds_reservation_once(execution):
    job_id = execution.submit("attempt", "build")
    execution.cancel_run("run")
    execution.cancel_run("run")
    with execution.db.transaction() as session:
        project = session.get(Project, "project")
        assert project.spent_usd == 0
        assert project.reserved_usd == 0
        assert session.get(NativeJob, job_id).request["reservation"]["settled"]


def test_cancelled_dispatched_job_debits_allowance_once(execution):
    execution.submit("attempt", "build")
    execution.dispatch_pending()
    execution.cancel_run("run")
    execution.cancel_run("run")
    with execution.db.transaction() as session:
        project = session.get(Project, "project")
        assert project.spent_usd == 1
        assert project.reserved_usd == 0


def test_cancellation_reconciles_uncertain_submission_by_stable_identity(execution):
    job_id = execution.submit("attempt", "build")
    with execution.db.transaction() as session:
        job = session.get(NativeJob, job_id)
        job.status = "dispatching"
        job.request = {**job.request, "reservation": {**job.request["reservation"], "dispatchAttempted": True}}
    execution.cancel_run("run")
    assert execution.ecs.stops == []
    execution.reconcile()
    with execution.db.transaction() as session:
        job = session.get(NativeJob, job_id)
        assert job.external_id == f"arn:task/{job_id}"
        assert job.result["terminationObservedAt"]
        assert session.get(Project, "project").spent_usd == 1
    assert len(execution.ecs.stops) == 1


@pytest.mark.parametrize("scope,excluded", [
    ({}, False),
    ({"requiredPeripheralCoverage": []}, False),
    ({"requiredPeripheralCoverage": [], "excludedCoverage": "all"}, False),
    ({"requiredPeripheralCoverage": ["ble"],
      "excludedCoverage": ["ble", "sensor", "display", "radio", "thermal", "physicalOperation"]}, False),
    ({"requiredPeripheralCoverage": [],
      "excludedCoverage": ["ble", "sensor", "display", "radio", "thermal", "physicalOperation"]}, True),
])
def test_omitted_or_conflicting_peripheral_scope_never_waives_checks(tmp_path, scope, excluded):
    result = run_job({"schemaVersion": 1, "purpose": "validate", "inputArtifacts": [],
                      "requirements": {"validationProfile": "watch-telemetry-v1", **scope}}, tmp_path)
    check = next(check for check in result.checks if check.name == "peripheralSimulation")
    assert check.status == "UNKNOWN"
    assert check.details["scopeExcluded"] is excluded
    assert check.details["required"] is not excluded


def test_cluster_migration_preserves_submission_recovery_and_cancellation(execution):
    job_id = execution.submit("attempt", "build")
    execution.settings.ecs_cluster = "new-cluster"
    execution.settings.ecs_subnets = ["new-subnet"]
    execution.dispatch_pending()
    assert execution.ecs.calls[-1]["cluster"] == "cluster"
    assert execution.ecs.calls[-1]["networkConfiguration"]["awsvpcConfiguration"]["subnets"] == ["native-subnet"]
    execution.reconcile()
    assert execution.ecs.describes[-1]["cluster"] == "cluster"
    execution.cancel_run("run")
    assert execution.ecs.stops[-1]["cluster"] == "cluster"
    with execution.db.transaction() as session:
        job = session.get(NativeJob, job_id)
        job.external_id = None
        job.result = {}
    execution.reconcile()
    assert execution.ecs.lists[-1]["cluster"] == "cluster"
    assert execution.ecs.stops[-1]["cluster"] == "cluster"


def test_completion_hint_from_pinned_old_cluster_survives_migration(execution):
    job_id = execution.submit("attempt", "build")
    execution.dispatch_pending()
    execution.settings.ecs_cluster = "new-cluster"
    class Queue:
        def __init__(self):
            self.deleted = []
        def receive_message(self, **_):
            return {"Messages": [
                {"ReceiptHandle": cluster, "Body": json.dumps({
                    "id": cluster, "source": "aws.ecs", "detail-type": "ECS Task State Change",
                    "detail": {"clusterArn": cluster, "taskArn": f"arn:task/{job_id}", "lastStatus": "STOPPED"},
                })} for cluster in ["cluster", "wrong-cluster"]
            ]}
        def delete_message(self, **kwargs):
            self.deleted.append(kwargs["ReceiptHandle"])
    execution.settings.sqs_completion_queue = "https://sqs.test/queue"
    execution.sqs = Queue()
    execution.receive_completion_hints()
    assert execution.sqs.deleted == ["cluster"]
    with execution.db.transaction() as session:
        assert session.get(NativeJob, job_id).result["infrastructureHint"]["eventId"] == "cluster"


@pytest.fixture
def deploy_module():
    path = Path(__file__).parents[1] / "docker/deploy.py"
    spec = importlib.util.spec_from_file_location("kleos_deploy_tests", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_release_definition_pins_version_without_changing_identity_or_secrets(deploy_module):
    original = {
        "taskDefinitionArn": "old", "revision": 7, "status": "ACTIVE", "family": "kleos-engineering",
        "taskRoleArn": "trusted-role", "executionRoleArn": "execution-role", "networkMode": "awsvpc",
        "containerDefinitions": [
            {"name": "control", "image": "old-image", "environment": [{"name": "KLEOS_DEMO_MODE", "value": "true"}],
             "secrets": [{"name": "KLEOS_DATABASE_URL", "valueFrom": "secret-reference"}]},
            {"name": "telemetry", "image": "collector"},
        ],
    }
    updated = deploy_module.release_definition(original, "new@sha256:digest", "commit")
    assert "revision" not in updated and "taskDefinitionArn" not in updated
    assert updated["taskRoleArn"] == original["taskRoleArn"]
    assert updated["containerDefinitions"][0]["secrets"] == original["containerDefinitions"][0]["secrets"]
    assert updated["containerDefinitions"][1] == original["containerDefinitions"][1]
    assert original["containerDefinitions"][0]["image"] == "old-image"
    assert deploy_module.application_version(updated) == "commit"


def test_migration_requires_observed_success_exit(deploy_module):
    assert not deploy_module.completed_task({"lastStatus": "RUNNING"})
    with pytest.raises(RuntimeError):
        deploy_module.completed_task({"taskArn": "migration", "lastStatus": "STOPPED",
                                      "containers": [{"name": "control", "exitCode": 1}]})
    assert deploy_module.completed_task({"taskArn": "migration", "lastStatus": "STOPPED",
                                         "containers": [{"name": "control", "exitCode": 0}]})


@pytest.mark.parametrize("fail_drain", [False, True])
def test_release_never_removes_compatibility_workers_before_post_rollout_drain(
        deploy_module, tmp_path, monkeypatch, fail_drain):
    from types import SimpleNamespace

    import httpx
    events = []
    class ECS:
        def register_task_definition(self, **kwargs):
            return {"taskDefinition": {"taskDefinitionArn": f"new-{kwargs['family']}"}}
        def run_task(self, **kwargs):
            marker = kwargs["tags"][1]["value"]
            events.append(marker)
            return {"tasks": [{"taskArn": marker}]}
        def update_service(self, **kwargs):
            events.append(f"update-{kwargs['service']}")
        def stop_task(self, **kwargs):
            events.append(f"stop-{kwargs['task']}")
    args = SimpleNamespace(region="us-east-1", version="new", cluster="cluster", release_id="test",
                           manifest=str(tmp_path / "release.json"), drain_timeout=30, api_url="https://api.test")
    release = deploy_module.Release(args, ecs=ECS(), ecr=object())
    definitions = {name: {
        "family": name, "containerDefinitions": [{"name": "control", "image": "old",
            "environment": [{"name": "KLEOS_DBOS_APPLICATION_VERSION", "value": "old"}]}]
    } for name in ["api", "engineering", "connectors"]}
    def discover():
        release.services = {name: {"desiredCount": 1, "networkConfiguration": {"awsvpcConfiguration": {}}}
                            for name in definitions}
        release.manifest["previousDefinitions"] = {name: f"old-{name}" for name in definitions}
        return definitions
    def drain(versions, phase):
        assert versions == ["old"]
        events.append(f"drain-{phase}")
        if fail_drain and phase == "after":
            raise TimeoutError("Old workflow still active")
    monkeypatch.setattr(release, "discover", discover)
    monkeypatch.setattr(release, "image", lambda: release.manifest.setdefault("image", "new@sha256:digest"))
    monkeypatch.setattr(release, "wait_task", lambda *_, **__: None)
    monkeypatch.setattr(release, "wait_service", lambda *_, **__: None)
    monkeypatch.setattr(release, "drain", drain)
    monkeypatch.setattr(httpx, "get", lambda *_, **__: SimpleNamespace(
        raise_for_status=lambda: None, json=lambda: {"status": "ok"}))
    if fail_drain:
        with pytest.raises(TimeoutError):
            release.run()
        assert not any(event.startswith("stop-") for event in events)
    else:
        release.run()
        assert events.index("drain-after") < events.index("stop-compat-engineering")
    assert events.index("migration") < events.index("update-engineering")
    assert events.index("drain-before") < events.index("update-engineering")


@pytest.fixture
def renode_contract(tmp_path):
    content = b"test-contract-elf"
    request = {
        "requirements": {"renodeProfile": RENODE_PROFILE},
        "interfaces": {"firmware": {"board": "stm32f4_disco"}},
        "inputArtifacts": [{
            "name": "native/build/zephyr/zephyr.elf", "lane": "firmware",
            "sha256": hashlib.sha256(content).hexdigest(),
            "contentBase64": base64.b64encode(content).decode(),
            "provenance": {"nativeJobId": "build-job"},
        }],
    }
    qualification = tmp_path / "qualification.json"
    qualification.write_text(json.dumps({
        "qualified": True, "profile": RENODE_PROFILE, "renodeVersion": RENODE_VERSION,
        "scenarioSha256": hashlib.sha256(RENODE_SCRIPT.encode()).hexdigest(),
    }))
    return request, qualification, content


def test_checked_in_renode_scenario_matches_runner_and_remains_unqualified():
    root = Path(__file__).parents[1] / "docker/renode"
    assert (root / "stm32f4-uart-v1.resc").read_text() == RENODE_SCRIPT
    qualification = json.loads((root / "qualification.json").read_text())
    assert qualification["qualified"] is False
    assert qualification["scenarioSha256"] == hashlib.sha256(RENODE_SCRIPT.encode()).hexdigest()


@pytest.mark.parametrize("case", ["absent", "unqualified", "changed_script", "unsupported_board", "malformed"])
def test_unqualified_renode_never_executes_or_produces_success(tmp_path, renode_contract, case):
    request, qualification, _ = renode_contract
    if case == "absent":
        qualification.unlink()
    elif case == "unsupported_board":
        request["interfaces"]["firmware"]["board"] = "native_sim"
    elif case == "malformed":
        qualification.write_text("[]")
    else:
        data = json.loads(qualification.read_text())
        data["qualified" if case == "unqualified" else "scenarioSha256"] = False
        qualification.write_text(json.dumps(data))
    def never_execute(*_):
        pytest.fail("An unqualified Renode scenario must not execute")
    result = validate_renode(request, tmp_path, never_execute, qualification)
    assert result.status == "UNKNOWN"
    assert result.details["required"] and not result.details["scopeExcluded"]


@pytest.mark.parametrize("outcome", ["marker", "no_marker", "wrong_version", "missing_tool", "failed"])
def test_renode_process_contract_has_bounded_capture_and_truthful_status(tmp_path, renode_contract, outcome):
    # Fake process results verify the adapter contract only; they do not qualify Renode.
    request, qualification, content = renode_contract
    calls = []
    def execute(args, work, timeout):
        calls.append(args)
        assert timeout <= 60
        assert (work / "firmware.elf").read_bytes() == content
        if outcome == "missing_tool":
            raise UnsupportedTool("Renode is not installed")
        if args == ["renode", "-v"]:
            return 0, "Renode " + ("1.15.2" if outcome == "wrong_version" else RENODE_VERSION)
        assert args[:4] == ["renode", "--console", "--disable-xwt", "--plain"]
        assert "emulation RunFor \"2\"" in (work / "scenario.resc").read_text()
        (work / "uart.log").write_text("KLEOS_UART_SMOKE_V1\n" if outcome == "marker" else "boot failed\n")
        return (1 if outcome == "failed" else 0), "emulator process log"
    result = validate_renode(request, tmp_path, execute, qualification)
    expected = {"marker": "PASS", "no_marker": "FAIL", "wrong_version": "UNKNOWN",
                "missing_tool": "UNKNOWN", "failed": "FAIL"}
    assert result.status == expected[outcome]
    assert result.details["executionMode"] == "digital_simulation"
    assert result.details["required"] is True
    if outcome == "marker":
        assert result.details["elfSha256"] == hashlib.sha256(content).hexdigest()
        assert result.details["simulatedSeconds"] == 2
        assert "USART2 only" in result.details["coverage"]
    assert calls


def test_renode_rejects_generator_supplied_or_mutated_elf(tmp_path, renode_contract):
    request, qualification, _ = renode_contract
    def never_execute(*_):
        pytest.fail("An untrusted ELF must not execute")
    ref = request["inputArtifacts"][0]
    ref["provenance"] = {}
    assert validate_renode(request, tmp_path, never_execute, qualification).status == "UNKNOWN"
    ref["provenance"] = {"nativeJobId": "build-job"}
    ref["contentBase64"] = base64.b64encode(b"mutated").decode()
    assert validate_renode(request, tmp_path, never_execute, qualification).status == "FAIL"


def test_local_docker_dispatch_has_scoped_mount_no_network_and_no_credentials(execution, tmp_path):
    import httpx

    from kleos.execution import DockerExecution
    db, settings, store = execution.db, execution.settings, execution.store
    attempt_id = "attempt"
    settings = settings.model_copy(update={
        "execution_backend": "docker", "docker_jobs_dir": tmp_path / "jobs",
        "docker_native_images": {"mechanics": "local-mechanics"},
    })
    requests, containers = [], {}
    identity = "a" * 64
    def handle(request):
        requests.append(request)
        if "/images/" in request.url.path:
            return httpx.Response(200, json={"Id": "sha256:" + "b" * 64})
        if request.url.path.endswith("/json"):
            name = request.url.path.split("/")[-2]
            item = containers.get(name)
            return httpx.Response(200 if item else 404, json=item or {})
        if request.url.path.endswith("/containers/create"):
            body = json.loads(request.content)
            containers[request.url.params["name"]] = {
                "Id": identity, "Config": body, "State": {"Status": "created", "Running": False},
            }
            return httpx.Response(201, json={"Id": identity})
        if request.url.path.endswith("/start"):
            return httpx.Response(204)
        pytest.fail(str(request.url))
    client = httpx.Client(transport=httpx.MockTransport(handle), base_url="http://docker/v1.45")
    native = DockerExecution(db, settings, store, client)
    job_id = native.submit(attempt_id, "build")
    native.dispatch_pending()
    with db.transaction() as session:
        job = session.get(NativeJob, job_id)
        assert job.external_id == identity
        assert job.request["toolVersion"] == "sha256:" + "b" * 64
    specification = next(json.loads(r.content) for r in requests if r.url.path.endswith("/containers/create"))
    assert specification["Env"] == ["KLEOS_NATIVE_SANDBOX=1"]
    limits = specification["HostConfig"]
    assert limits["NetworkMode"] == "none"
    assert limits["ReadonlyRootfs"] is True
    assert "exec" in limits["Tmpfs"]["/tmp"].split(",")
    assert limits["PidsLimit"] == 256 and limits["Memory"] == 1024**3
    assert limits["Mounts"] == [{
        "Type": "volume", "Source": settings.docker_jobs_volume,
        "Target": "/control", "VolumeOptions": {"Subpath": job_id},
    }]
    assert "docker.sock" not in json.dumps(specification)
    folder = native.control_directory(job_id)
    assert folder.stat().st_mode & 0o777 == 0o700
    payload = json.loads((folder / "request.json").read_bytes())
    assert "token" not in payload and "contentBase64" in payload["inputArtifacts"][0]


def test_local_docker_rejects_cloud_environment_and_symlink_workspaces(execution, tmp_path):
    from kleos.execution import DockerExecution
    db, settings, store = execution.db, execution.settings, execution.store
    with pytest.raises(ValueError, match="cloud"):
        DockerExecution(db, settings.model_copy(update={"environment": "production"}), store)
    native = DockerExecution(db, settings.model_copy(update={"docker_jobs_dir": tmp_path / "jobs"}), store)
    (tmp_path / "jobs").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(DomainError, match="Unsafe"):
        native.control_directory("12345678-1234-1234-1234-123456789012")


def test_local_docker_expired_job_holds_capacity_until_observed_stopped(execution, tmp_path):
    from datetime import timedelta

    from kleos.db import now
    from kleos.execution import DockerExecution
    job_id = execution.submit("attempt", "build")
    with execution.db.transaction() as session:
        job = session.get(NativeJob, job_id)
        job.status, job.external_id = "running", "native-container"
        job.expires_at = now() - timedelta(seconds=1)
        job.request = {**job.request, "dispatch": {"backend": "docker"}}
    native = DockerExecution(execution.db, execution.settings, execution.store)
    class StillRunning:
        def stop_task(self, *, task):
            assert task == "native-container"
            return {"task": {"lastStatus": "RUNNING"}}
    native.ecs = StillRunning()
    assert native.reconcile() == []
    with execution.db.transaction() as session:
        assert session.get(NativeJob, job_id).status == "running"


def test_local_docker_cleanup_conflict_preserves_job_files(execution, tmp_path):
    from types import SimpleNamespace

    from kleos.execution import DockerExecution
    job_id = execution.submit("attempt", "build")
    native = DockerExecution(execution.db, execution.settings.model_copy(
        update={"docker_jobs_dir": tmp_path / "jobs"}), execution.store)
    folder = native.control_directory(job_id)
    folder.mkdir(parents=True)
    (folder / "request.json").write_text("{}")
    native.ecs = SimpleNamespace(request=lambda *_, **__: SimpleNamespace(status_code=409))
    with execution.db.transaction() as session:
        job = session.get(NativeJob, job_id)
        job.status, job.external_id = "failed", "native-container"
    native._cleanup(job)
    assert (folder / "request.json").exists()
    with execution.db.transaction() as session:
        assert not session.get(NativeJob, job_id).result.get("localCleaned")


def test_native_resource_limit_is_not_classified_as_an_engineering_error(tmp_path):
    content = b"assembly = None"
    result = run_job({
        "schemaVersion": 1, "purpose": "build", "lane": "mechanics",
        "inputArtifacts": [{"id": "source", "lane": "mechanics", "name": "enclosure.py",
                            "sha256": digest(content), "contentBase64": base64.b64encode(content).decode()}],
    }, tmp_path, execute=lambda *_: (137, "Killed"))
    assert result.status == "failed"
    assert result.measurements["failureKind"] == "resource"


def test_local_cleanup_does_not_claim_deleted_files_on_filesystem_error(execution, tmp_path, monkeypatch):
    from kleos.execution import DockerExecution
    job_id = execution.submit("attempt", "build")
    execution.settings.docker_jobs_dir = tmp_path / "jobs"
    native = DockerExecution(execution.db, execution.settings, execution.store)
    folder = native.control_directory(job_id)
    folder.mkdir(parents=True)
    (folder / "request.json").write_text("private input")
    with execution.db.transaction() as session:
        job = session.get(NativeJob, job_id)
        job.status = "failed"
    def unavailable(_):
        raise PermissionError("temporary cleanup failure")
    monkeypatch.setattr("kleos.execution.shutil.rmtree", unavailable)
    native._cleanup(job)
    assert (folder / "request.json").is_file()
    with execution.db.transaction() as session:
        assert not session.get(NativeJob, job_id).result.get("localCleaned")


def test_flutter_inner_thread_failure_does_not_trigger_source_repairs(tmp_path, monkeypatch):
    monkeypatch.setattr("kleos.native_runner.prepare_companion", lambda _: {"preparation": "fixture"})
    result = run_job({"schemaVersion": 1, "purpose": "build", "lane": "application", "inputArtifacts": []},
                     tmp_path, execute=lambda *_: (
                         1, "../../runtime/vm/os_thread.cc:348: Could not start thread DartWorker:11: "
                         "Resource temporarily unavailable\nanalysis server exited with -6"))
    assert result.status == "failed"
    assert result.measurements["failureKind"] == "resource"


def test_native_timeout_preserves_bounded_logs_without_source_repair(tmp_path, monkeypatch):
    class FakeProcess:
        pid = 12345
        def __init__(self, args, **kwargs):
            kwargs["stdout"].write(b"compiler reached linking before deadline\n")
        def wait(self, timeout=None):
            if timeout is not None:
                raise subprocess.TimeoutExpired("fixture", timeout)
            return -9
    killed = []
    monkeypatch.setattr("kleos.native_runner.shutil.which", lambda *_, **__: "/fixture")
    monkeypatch.setattr("kleos.native_runner.os.chown", lambda *_: None)
    monkeypatch.setattr("kleos.native_runner.os.killpg", lambda *args: killed.append(args))
    monkeypatch.setattr("kleos.native_runner.reap_generated_processes", lambda: None)
    monkeypatch.setattr("kleos.native_runner.subprocess.Popen", FakeProcess)
    content = b"assembly = None"
    result = run_job({"schemaVersion": 1, "purpose": "build", "lane": "mechanics", "inputArtifacts": [
        {"id": "source", "lane": "mechanics", "name": "enclosure.py", "sha256": digest(content),
         "contentBase64": base64.b64encode(content).decode()}]}, tmp_path, execute=run_command)
    assert result.status == "failed"
    assert result.measurements["failureKind"] == "deadline"
    assert result.logs == "compiler reached linking before deadline\n"
    assert len(killed) == 1


@pytest.mark.parametrize("empty", [False, True])
def test_passing_kicad_checks_cannot_accept_irrelevant_or_empty_circuit(tmp_path, empty, monkeypatch):
    monkeypatch.setattr("kleos.circuit_validation.prepare_kicad_libraries", lambda _, **kwargs: {"source": "test_fixture"})
    artifacts = []
    for suffix in ["kicad_sch", "kicad_pcb"]:
        content = b"native-export-test-fixture"
        artifacts.append({
            "id": suffix, "lane": "electronics", "name": "native/circuit." + suffix,
            "sha256": digest(content), "contentBase64": base64.b64encode(content).decode(),
            "provenance": {"nativeJobId": "real-build-contract"},
        })
    def fake_kicad(args, work, timeout):
        assert args[0] == "kicad-cli"
        if args[1:4] == ["sch", "export", "netlist"]:
            contents = "<export><components/><nets/></export>" if empty else (
                '<export><components><comp ref="R1"/></components><nets><net code="1"/></nets></export>')
            Path(args[args.index("--output") + 1]).write_text(contents)
        return 0, "ERC/DRC process contract passed"
    result = run_job({"schemaVersion": 1, "purpose": "validate", "inputArtifacts": artifacts},
                     tmp_path, fake_kicad)
    checks = {check.name: check for check in result.checks}
    assert checks["electronics"].status == ("FAIL" if empty else "UNKNOWN")
    assert checks["electronics_correspondence"].status == "UNKNOWN"
    assert checks["electronics_correspondence"].details["required"] is True
    assert result.status != "succeeded"


@pytest.mark.parametrize("escape", [False, True])
def test_companion_bundle_is_complete_and_rejects_symlink_output(tmp_path, escape, monkeypatch):
    import io
    import zipfile
    monkeypatch.setattr("kleos.native_runner.prepare_companion", lambda _: {"preparation": "fixture"})
    outside = tmp_path / "outside.txt"
    outside.write_text("must not escape")
    def compile_fixture(args, work, timeout):
        if args[1:3] == ["build", "web"]:
            assert "--no-wasm-dry-run" in args
            web = work / "build/web"
            web.mkdir(parents=True)
            (web / "index.html").write_text("entrypoint")
            (web / "main.dart.js").write_text("compiled fixture")
            if escape:
                (web / "leak.txt").symlink_to(outside)
            else:
                (web / "asset.txt").write_text("bundled asset")
        return 0, "fixture process"
    result = run_job({"schemaVersion": 1, "purpose": "build", "lane": "application", "inputArtifacts": []},
                     tmp_path, compile_fixture)
    assert result.status == ("failed" if escape else "succeeded")
    if not escape:
        artifact = next(item for item in result.files if item.path.endswith("companion-web.zip"))
        with zipfile.ZipFile(io.BytesIO(base64.b64decode(artifact.content))) as bundle:
            assert set(bundle.namelist()) == {"index.html", "main.dart.js", "asset.txt"}


def test_companion_preparation_embeds_verified_font_and_preserves_existing_fonts(tmp_path):
    import yaml
    fonts = tmp_path / "fonts"
    fonts.mkdir()
    hashes = {}
    for name, content in {"Roboto-Regular.ttf": b"font-fixture", "Roboto_LICENSE.txt": b"license-fixture"}.items():
        (fonts / name).write_bytes(content)
        hashes[name] = digest(content)
    work = tmp_path / "application"
    work.mkdir()
    specification = {"name": "companion", "flutter": {"uses-material-design": True, "fonts": [
        {"family": "Existing", "fonts": [{"asset": "existing.ttf"}]}]}}
    (work / "pubspec.yaml").write_text(yaml.safe_dump(specification))
    details = prepare_companion(work, fonts, hashes)
    prepared = yaml.safe_load((work / "pubspec.yaml").read_text())
    assert prepared["flutter"]["fonts"][0] == specification["flutter"]["fonts"][0]
    assert prepared["flutter"]["fonts"][1]["family"] == "Roboto"
    assert (work / "assets/kleos_fonts/Roboto_LICENSE.txt").read_bytes() == b"license-fixture"
    assert details["sourceHashes"]["pubspec.yaml"] == digest((work / "pubspec.yaml").read_bytes())
    assert json.loads((work / "kleos-build.json").read_text())["fontHashes"] == hashes


def test_companion_preparation_rejects_tampered_image_font(tmp_path):
    (tmp_path / "pubspec.yaml").write_text("name: companion\n")
    with pytest.raises(UnsupportedTool, match="pinned offline Roboto"):
        prepare_companion(tmp_path, tmp_path / "missing", {"Roboto-Regular.ttf": "0" * 64})


@pytest.mark.parametrize("modify_source", [False, True])
def test_companion_source_archive_matches_generated_build(tmp_path, monkeypatch, modify_source):
    import io
    import zipfile
    def prepare(work):
        (work / "pubspec.yaml").write_text("original prepared source")
        return {"preparation": "fixture", "sourceHashes": {"pubspec.yaml": digest(b"original prepared source")}}
    monkeypatch.setattr("kleos.native_runner.prepare_companion", prepare)
    def generated_tool(args, work, timeout):
        if modify_source:
            (work / "pubspec.yaml").write_text("modified by generated tool")
        if args[1:3] == ["build", "web"]:
            web = work / "build/web"
            web.mkdir(parents=True)
            (web / "index.html").write_text("entrypoint")
            (web / "main.dart.js").write_text("compiled fixture")
        return 0, "fixture"
    result = run_job({"schemaVersion": 1, "purpose": "build", "lane": "application", "inputArtifacts": []},
                     tmp_path, generated_tool)
    assert result.status == ("failed" if modify_source else "succeeded")
    if modify_source:
        assert "modified the captured" in result.limitations[0]
        return
    archive = next(item for item in result.files if item.path.endswith("companion-source.zip"))
    content = base64.b64decode(archive.content)
    assert result.measurements["preparedSourceSha256"] == digest(content)
    with zipfile.ZipFile(io.BytesIO(content)) as bundle:
        assert bundle.read("pubspec.yaml") == b"original prepared source"
