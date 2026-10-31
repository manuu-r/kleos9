"""Engine/session integration with real ledgers and fake transport/model responses."""
import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from test_execution import execution as execution

from kleos.contracts import AgentOutput, CheckResult, DomainError, GeneratedFile, JobResult
from kleos.db import (
    Artifact,
    Attempt,
    Invocation,
    NativeJob,
    NativeSessionStep,
    Project,
    Run,
    Task,
    Validation,
    now,
)
from kleos.engine import EngineeringEngine


class Models:
    def __init__(self, callback=None):
        self.registry = SimpleNamespace(configured=lambda provider: True)
        self.session_agents = {}
        self.calls = []
        self.callback = callback

    async def generate(self, provider, model, context):
        self.calls.append((provider, model, context))
        if self.callback:
            await self.callback(context, self.session_bridge)
        return AgentOutput(summary="Native candidate, independently unvalidated"), {
            "provider": provider, "model": model, "inputTokens": 25, "outputTokens": 10,
            "requests": 1, "estimatedUsd": .01}, []


@pytest.fixture
def engineering(execution):
    execution.settings.native_sessions_enabled = True
    execution.settings.max_native_jobs = 4
    # Markers are fixture secrets; assertions must prove native payloads exclude them.
    execution.settings.gemini_api_key = "fixture-google-api-secret"
    execution.settings.meta_api_key = "fixture-meta-api-secret"
    with execution.db.transaction() as session:
        project = session.get(Project, "project")
        project.state, project.interfaces = {"confirmed": True}, {"mechanical": {"units": "mm"}}
        task = session.get(Task, "task")
        task.kind, task.status = "project.generate", "running"
        session.get(Run, "run").status = "running"
        attempt = session.get(Attempt, "attempt")
        attempt.context = {"requirements": {}, "interfaces": project.interfaces, "inputArtifacts": []}
        attempt.provider, attempt.model = "google", "fixture-model"
    models = Models()
    engine = EngineeringEngine(execution.db, execution.settings, execution.store, models, execution)
    ready = engine.session_bridge.await_ready

    async def dispatcher_ready(job_id):
        # A capacity wait must precede any hosted inference reservation/call.
        with execution.db.transaction() as session:
            assert session.query(Invocation).count() == 0
            assert session.get(NativeJob, job_id).status == "pending"
        assert models.calls == []
        execution.dispatch_pending()
        await ready(job_id)

    engine.session_bridge.await_ready = dispatcher_ready
    return execution, engine, models


async def test_session_opens_before_model_reservation_then_persists_finish(engineering):
    execution, engine, models = engineering
    job_id = await engine.run_task("task")
    assert len(models.calls) == 1
    _, _, context = models.calls[0]
    assert context["nativeSession"]["jobId"] == job_id
    with execution.db.transaction() as session:
        job = session.get(NativeJob, job_id)
        assert job.purpose == "session" and job.status == "submitted"
        assert session.get(Task, "task").status == session.get(Attempt, "attempt").status == "waiting"
        call = session.scalar(select(Invocation).where(Invocation.attempt_id == "attempt"))
        assert call.status == "succeeded"
        steps = session.scalars(select(NativeSessionStep).where(NativeSessionStep.job_id == job_id)).all()
        assert [(step.action, step.status, step.request_key) for step in steps] == [
            ("finish", "queued", "finish:attempt")]
    native_request = execution.payload(job_id, execution._token(job_id))
    encoded = json.dumps(native_request)
    assert "fixture-google-api-secret" not in encoded and "fixture-meta-api-secret" not in encoded
    assert "apiKey" not in encoded and "authorization" not in encoded.lower()
    assert "sessionLimits" in native_request
    assert len(execution.ecs.calls) == 1


async def test_model_native_tool_diagnostics_roundtrip_before_finish(engineering):
    execution, engine, models = engineering

    async def exercise(context, bridge):
        info = context["nativeSession"]

        async def supervisor():
            step = bridge.sessions.next_local(info["jobId"])["step"]
            assert step["action"] == "build"
            bridge.sessions.complete_local(info["jobId"], step["id"], {
                "status": "failed", "result": {"logs": "A genuine compiler diagnostic", "exitCode": 1}})

        bridge._sleep = supervisor
        result = await bridge.execute("attempt", info["jobId"], "tool-build-1", "build", {})
        assert result == {"status": "failed", "result": {"logs": "A genuine compiler diagnostic", "exitCode": 1}}
        bridge.record_usage("attempt", info["jobId"], "google", "fixture-model", {
            "inputTokens": 25, "outputTokens": 10, "estimatedUsd": .01})

    models.callback = exercise
    job_id = await engine.run_task("task")
    with execution.db.transaction() as session:
        steps = session.scalars(select(NativeSessionStep).where(NativeSessionStep.job_id == job_id)
                                .order_by(NativeSessionStep.sequence)).all()
        assert [(step.action, step.status) for step in steps] == [("build", "failed"), ("finish", "queued")]
        call = session.scalar(select(Invocation))
        assert call.status == "succeeded" and call.usage["sessionObserved"]["outputTokens"] == 10
        assert session.query(Validation).count() == 0


async def test_session_candidate_success_cannot_create_validation_pass(engineering):
    execution, engine, _models = engineering
    job_id = await engine.run_task("task")
    engine.finish_native(job_id, JobResult(status="succeeded", files=[
        GeneratedFile(path="native/model.step", content="candidate STEP bytes, not qualified evidence")],
        checks=[CheckResult(name="approved_enclosure_form", status="PASS", details={"untrusted": True})]))
    with execution.db.transaction() as session:
        assert session.query(Validation).count() == 0
        assert session.get(Project, "project").accepted_release_id is None
        assert session.get(Task, "task").status == "succeeded"
        candidate = session.scalar(select(Artifact).where(Artifact.name == "native/model.step"))
        assert candidate.status == "candidate"
        assert candidate.provenance["nativeJobId"] == job_id
    # A duplicate final callback is inert; it cannot publish a new candidate or PASS.
    engine.finish_native(job_id, JobResult(status="succeeded"))
    with execution.db.transaction() as session:
        assert session.query(Validation).count() == 0
        assert session.query(Artifact).filter(Artifact.name == "native/model.step").count() == 1


def add_other_session(execution):
    with execution.db.transaction() as session:
        session.add(Task(id="other-task", tenant_id="org", project_id="project", run_id="run",
                         lane="application", kind="project.generate", revision=1, fence=1, status="running"))
        session.add(Attempt(id="other-attempt", tenant_id="org", task_id="other-task", number=1,
                            fence=1, policy_revision=1, provider="google", model="fixture", context={}))
        session.add(NativeJob(id="other-job", tenant_id="org", task_id="other-task", attempt_id="other-attempt",
                              purpose="session", status="running", token_hash="fixture", request={},
                              expires_at=now() + timedelta(hours=1)))


async def test_model_failure_cancels_only_scoped_session_preserves_uncertain_usage(engineering):
    execution, engine, models = engineering
    add_other_session(execution)

    async def fail(context, bridge):
        bridge.record_usage("attempt", context["nativeSession"]["jobId"], "google", "fixture-model", {
            "inputTokens": 30, "outputTokens": 12, "estimatedUsd": .02})
        raise RuntimeError("Simulated model transport interruption")

    models.callback = fail
    assert await engine.run_task("task") is None
    with execution.db.transaction() as session:
        job = session.scalar(select(NativeJob).where(NativeJob.attempt_id == "attempt"))
        assert job.status == "cancelled"
        assert session.get(NativeJob, "other-job").status == "running"
        assert session.get(Task, "task").status == "blocked"
        call = session.scalar(select(Invocation).where(Invocation.attempt_id == "attempt"))
        assert call.status == "uncertain" and call.reserved_usd > 0
        assert call.usage["sessionObserved"]["outputTokens"] == 12
        assert session.query(Validation).count() == 0


async def test_cancellation_while_waiting_for_capacity_prevents_model_reservation(engineering):
    execution, engine, models = engineering
    add_other_session(execution)

    async def cancel(job_id):
        with execution.db.transaction() as session:
            session.get(Task, "task").status = "cancelled"
            session.get(Attempt, "attempt").status = "cancelled"
        engine.session_bridge.assert_live(job_id)

    engine.session_bridge.await_ready = cancel
    assert await engine.run_task("task") is None
    assert models.calls == []
    with execution.db.transaction() as session:
        assert session.query(Invocation).count() == 0
        assert session.scalar(select(NativeJob).where(NativeJob.attempt_id == "attempt")).status == "cancelled"
        assert session.get(NativeJob, "other-job").status == "running"
        assert session.get(Task, "task").status == "cancelled"


async def test_failed_native_session_never_schedules_paid_automatic_repair(engineering):
    execution, engine, models = engineering
    job_id = await engine.run_task("task")
    engine.finish_native(job_id, JobResult(status="failed", limitations=["Actual native compile failed"],
                                        measurements={"failureKind": "engineering"}))
    with execution.db.transaction() as session:
        assert session.get(Task, "task").status == "blocked"
        assert session.get(NativeJob, job_id).status == "failed"
        assert session.query(Invocation).count() == 1
        assert session.query(Validation).count() == 0
    assert len(models.calls) == 1


async def test_cancelled_final_native_result_is_fenced(engineering):
    execution, engine, _models = engineering
    job_id = await engine.run_task("task")
    execution.cancel_session(job_id)
    # Cancellation of the scoped job must itself fence final publication, without
    # depending on a separate task-cancellation transaction arriving first.
    with pytest.raises(DomainError):
        engine.finish_native(job_id, JobResult(status="succeeded", files=[
            GeneratedFile(path="late.step", content="late candidate")]))
    with execution.db.transaction() as session:
        assert session.get(NativeJob, job_id).status == "cancelled"
        assert not session.scalar(select(Artifact.id).where(Artifact.name == "late.step"))


async def test_waiting_session_replay_reuses_job_model_output_and_finish_step(engineering):
    from kleos.session_bridge import SessionBridge

    execution, engine, models = engineering
    job_id = await engine.run_task("task")
    engine.session_bridge.await_ready = SessionBridge.await_ready.__get__(engine.session_bridge)
    assert await engine.run_task("task") == job_id
    assert len(models.calls) == 1
    assert len(execution.ecs.calls) == 1
    with execution.db.transaction() as session:
        assert session.query(NativeJob).count() == 1
        assert session.query(Invocation).count() == 1
        assert session.query(NativeSessionStep).count() == 1
        assert session.get(Task, "task").status == "waiting"


def test_recovered_session_context_preserves_original_limits_and_spend_cap(engineering):
    execution, engine, _models = engineering
    execution.settings.native_session_max_steps = 32
    execution.settings.native_session_max_seconds = 900
    execution.settings.native_session_max_spend_usd = 1
    execution.settings.invocation_reservation_usd = 1
    job_id = engine.open_session("attempt")
    execution.settings.native_session_max_steps = 64
    execution.settings.native_session_max_seconds = 1800
    execution.settings.native_session_max_spend_usd = 2
    execution.settings.invocation_reservation_usd = 2
    assert engine.open_session("attempt") == job_id
    with execution.db.transaction() as s:
        context = s.get(Attempt, "attempt").context["nativeSession"]
        assert context["maxSteps"] == 32 and context["maxSeconds"] == 900
        assert context["maxSpendUsd"] == 1
        assert s.get(NativeJob, job_id).request["sessionLimits"] == {"maxSteps": 32, "maxSeconds": 900}
    payload = execution.payload(job_id, execution._token(job_id))
    assert payload["sessionLimits"] == {"maxSteps": 32, "maxSeconds": 900}


@pytest.mark.parametrize("lane,tool,expected", [
    ("mechanics", None, True),
    ("mechanics", {"id": "build123d", "version": "1"}, True),
    ("mechanics", {"id": "build123d", "version": "next"}, False),
    ("mechanics", {"id": "custom-cad", "version": "1", "qualified": True}, False),
    ("electronics", {"id": "atopile-kicad", "version": "1"}, False),
    ("electronics", {"id": "atopile-kicad", "version": "2"}, False),
    ("electronics", {"id": "custom-pcb", "version": "1", "qualified": True}, False),
    ("firmware", None, True), ("application", None, True),
    ("application", {"id": "custom-app", "version": "1"}, False),
])
def test_session_transport_requires_its_own_runtime_qualification(lane, tool, expected):
    from kleos.engine import _session_runtime_supported

    assert _session_runtime_supported(lane, {"engineeringTool": tool}) is expected


async def test_enabled_session_flag_preserves_custom_tool_classic_build_protocol(engineering):
    from test_engineering_tools import cad_tool

    from kleos.engineering_tools import EngineeringToolRegistry

    execution, engine, models = engineering
    execution.settings.engineering_tools = [cad_tool()]
    execution.settings.ecs_task_definitions["example-cad@1"] = "tool:example-cad:4"
    with execution.db.transaction() as session:
        attempt = session.get(Attempt, "attempt")
        attempt.context = {**attempt.context, "engineeringTool": EngineeringToolRegistry(execution.settings).describe(cad_tool())}
    output = AgentOutput(summary="Custom adapter candidate", files=[
        GeneratedFile(path="design.json", content='{"widthMm":40}')])

    async def classic_response(provider, model, context):
        models.calls.append((provider, model, context))
        assert "nativeSession" not in context
        return output, {"estimatedUsd": .01, "inputTokens": 20, "outputTokens": 10}, []

    async def forbidden_session_wait(_job):
        raise AssertionError("Classic adapter cannot be sent session actions")

    models.generate = classic_response
    engine.session_bridge.await_ready = forbidden_session_wait
    await engine.run_task("task")
    with execution.db.transaction() as session:
        job = session.scalar(select(NativeJob).where(NativeJob.attempt_id == "attempt"))
        job_id = job.id
        assert job.purpose == "build" and job.status == "pending"
        assert job.request["toolVersion"] == "tool:example-cad:4"
        assert job.request["engineeringTool"]["id"] == "example-cad"
        assert session.query(NativeSessionStep).count() == 0
        assert session.get(Task, "task").status == "waiting"
    assert len(models.calls) == 1
    execution.dispatch_pending()
    engine.finish_native(job_id, JobResult(status="succeeded", files=[
        GeneratedFile(path="native/outputs/model.step", content="custom adapter export fixture")]))
    with execution.db.transaction() as session:
        assert session.get(Task, "task").status == "succeeded"
        assert session.get(NativeJob, job_id).status == "succeeded"
        assert session.query(Validation).count() == 0
        candidate = session.scalar(select(Artifact).where(Artifact.name == "native/outputs/model.step"))
        assert candidate.status == "candidate" and candidate.provenance["nativeJobId"] == job_id


async def test_recovering_already_open_unqualified_session_cancels_before_paid_sdk(engineering):
    from test_engineering_tools import cad_tool

    from kleos.engineering_tools import EngineeringToolRegistry

    execution, engine, models = engineering
    execution.settings.engineering_tools = [cad_tool()]
    execution.settings.ecs_task_definitions["example-cad@1"] = "tool:example-cad:4"
    with execution.db.transaction() as session:
        attempt = session.get(Attempt, "attempt")
        attempt.context = {**attempt.context, "engineeringTool": EngineeringToolRegistry(execution.settings).describe(cad_tool())}
    # Simulate a persisted session opened by the earlier global-flag behavior.
    job_id = engine.open_session("attempt")
    assert await engine.run_task("task") is None
    assert models.calls == []
    with execution.db.transaction() as session:
        assert session.get(NativeJob, job_id).status == "cancelled"
        assert session.get(Task, "task").status == "blocked"
        assert session.get(Attempt, "attempt").status == "blocked"
        assert "not qualified" in session.get(Task, "task").result["reason"]
        assert session.query(NativeJob).count() == 1
        assert session.query(Invocation).count() == 0
        assert session.query(NativeSessionStep).count() == 0
