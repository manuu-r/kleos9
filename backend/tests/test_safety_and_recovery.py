import base64
import json
from datetime import timedelta

import pytest
from sqlalchemy import func, select
from test_control_plane import command, create, start_research

from kleos.auth import Principal
from kleos.contracts import AgentOutput, CheckResult, Command, DomainError, GeneratedFile, JobResult
from kleos.db import (
    Artifact,
    Attempt,
    Invocation,
    NativeJob,
    Outbox,
    Project,
    ProjectMembership,
    Task,
    Validation,
    now,
)
from kleos.storage import artifact_key, digest
from kleos.telemetry import redact


def native_job(platform, purpose="build"):
    project_id, run_id, task_id, worker = start_research(platform)
    attempt_id = worker.begin_attempt(task_id)
    worker.store_candidate(attempt_id, AgentOutput(summary="Fixture", files=[GeneratedFile(path="src/main.c", content="x")]))
    with platform.db.transaction() as session:
        task = session.get(Task, task_id)
        task.kind, task.lane, task.status = "project.generate", "firmware", "waiting"
        attempt = session.get(Attempt, attempt_id)
        attempt.status = "waiting"
        rows = session.scalars(select(Artifact).where(Artifact.project_id == project_id)).all()
        job = NativeJob(id="native-fixture", tenant_id="org-a", task_id=task_id, attempt_id=attempt_id,
                        purpose=purpose, status="submitted", token_hash="test", expires_at=now()+timedelta(hours=1),
                        request={"inputArtifacts": [{"id": a.id, "sha256": a.sha256} for a in rows],
                                 "toolVersion": "independent-fixture:1"})
        session.add(job)
    return project_id, task_id, attempt_id, worker


def test_native_result_commit_is_atomic_and_retryable(platform, monkeypatch):
    project_id, task_id, attempt_id, worker = native_job(platform)
    result = JobResult(status="succeeded", files=[GeneratedFile(path="build/firmware.elf",
                         content=base64.b64encode(b"ELF-fixture").decode(), encoding="base64")])
    original = platform.store.put
    def interrupt(key, *args, **kwargs):
        if key.endswith("firmware.elf"):
            raise OSError("Injected storage interruption")
        return original(key, *args, **kwargs)
    monkeypatch.setattr(platform.store, "put", interrupt)
    with pytest.raises(OSError):
        worker.finish_native("native-fixture", result)
    with platform.db.transaction() as session:
        assert session.get(NativeJob, "native-fixture").status == "submitted"
        assert session.get(Task, task_id).status == "waiting"
    monkeypatch.setattr(platform.store, "put", original)
    worker.finish_native("native-fixture", result)
    worker.finish_native("native-fixture", result)
    with platform.db.transaction() as session:
        assert session.get(Task, task_id).status == "succeeded"
        assert session.scalar(select(func.count(Artifact.id)).where(Artifact.name == "build/firmware.elf")) == 1


def test_validation_rejects_changed_input_closure(platform):
    project_id, task_id, attempt_id, worker = native_job(platform, "validate")
    worker.store_candidate(attempt_id, AgentOutput(summary="Extra candidate", files=[GeneratedFile(path="extra.c", content="new")]))
    with pytest.raises(DomainError) as exc:
        worker.finish_native("native-fixture", JobResult(status="succeeded", checks=[CheckResult(name="interfaces", status="PASS")]))
    assert exc.value.code == "stale_validation"
    with platform.db.transaction() as session:
        assert session.scalar(select(func.count(Validation.id))) == 0


def test_only_engineering_failures_schedule_targeted_repairs(platform):
    _, task_id, attempt_id, worker = native_job(platform)
    worker.finish_native("native-fixture", JobResult(status="failed", logs="Compiler found a missing declaration",
                         measurements={"failureKind": "engineering"}, limitations=["Compile failed"]))
    with platform.db.transaction() as session:
        assert session.get(Task, task_id).status == "queued"
        assert session.get(Attempt, attempt_id).status == "blocked"
        assert session.scalar(select(func.count(Outbox.id)).where(Outbox.kind == "task")) == 1


def test_uploaded_inputs_are_hash_checked_idempotent_and_not_evidence(platform):
    project_id = create(platform)["projectId"]
    body = {"file": {"path": "brief.txt", "content": "user specification"},
            "sha256": digest(b"user specification"), "expectedEngineeringRevision": 1}
    first = platform.client.post(f"/api/v1/projects/{project_id}/artifacts", json=body)
    assert first.status_code == 201
    assert first.json()["status"] == "input"
    again = platform.client.post(f"/api/v1/projects/{project_id}/artifacts", json=body)
    assert first.json() == again.json()
    body["sha256"] = "0" * 64
    assert platform.client.post(f"/api/v1/projects/{project_id}/artifacts", json=body).status_code == 422
    with pytest.raises(DomainError):
        command(platform, project_id, "project.acceptRelease")


def test_project_membership_controls_reads_and_writes(platform):
    project_id = create(platform)["projectId"]
    member = Principal("invited", "org-a", "member")
    assert platform.control.projects(member)["projects"] == []
    with pytest.raises(DomainError):
        platform.control.workspace(member, project_id)
    with platform.db.transaction() as session:
        session.add(ProjectMembership(project_id=project_id, subject="invited", role="viewer"))
    view = platform.control.workspace(member, project_id)
    with pytest.raises(DomainError) as exc:
        platform.control.command(member, Command(type="project.cancel", expected_revision=view["revision"],
                                                payload={"projectId": project_id}), "viewer-cancel")
    assert exc.value.status == 403


def test_uncertain_invocation_conservatively_settles_once(platform):
    project_id, _, task_id, worker = start_research(platform)
    attempt_id = worker.begin_attempt(task_id)
    invocation_id, _ = worker.reserve_invocation(attempt_id)
    worker.block_task(task_id, attempt_id, "Provider response was not recorded")
    endpoint = f"/api/v1/invocations/{invocation_id}/reconcile"
    body = {"note": "Operator checked the provider receipt; unknown call charged conservatively"}
    assert platform.client.post(endpoint, json=body).status_code == 200
    assert platform.client.post(endpoint, json=body).status_code == 200
    with platform.db.transaction() as session:
        project = session.get(Project, project_id)
        assert project.reserved_usd == 0 and project.spent_usd == 1
        assert session.get(Invocation, invocation_id).status == "uncertain_settled"
    with pytest.raises(DomainError):
        worker.store_candidate(attempt_id, AgentOutput(summary="Late response"))


async def test_hosted_capacity_wait_reuses_attempt_and_only_reserves_once(platform, monkeypatch):
    _, run_id, task_id, worker = start_research(platform)
    reserve = worker.reserve_invocation
    probes, sleeps = [], []

    def capacity_then_available(attempt_id):
        probes.append(attempt_id)
        if len(probes) < 3:
            raise DomainError("Hosted capacity reserved", 429, "provider_capacity")
        return reserve(attempt_id)

    async def sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(worker, "reserve_invocation", capacity_then_available)
    monkeypatch.setattr("kleos.engine.asyncio.sleep", sleep)
    await worker.run_task(task_id)
    worker.finish_run(run_id)
    assert len(set(probes)) == 1 and len(probes) == 3 and sleeps == [2, 2]
    assert worker.models.calls == 1
    with platform.db.transaction() as session:
        assert session.scalar(select(func.count(Attempt.id))) == 1
        assert session.scalar(select(func.count(Invocation.id))) == 1
        assert session.get(Task, task_id).status == "succeeded"


def test_transcript_redaction_preserves_usage_and_omits_reasoning():
    safe = redact({"inputTokens": 120, "authorization": "Bearer secret", "parts": [
        {"part_kind": "thinking", "content": "private scratchpad"}],
        "url": "https://user:password@store.example/item?X-Amz-Signature=secret"})
    encoded = json.dumps(safe)
    assert safe["inputTokens"] == 120
    assert "private scratchpad" not in encoded and "password" not in encoded and "Signature" not in encoded


def test_identical_names_in_different_source_directories_have_distinct_keys():
    assert artifact_key("t", "p", "a", "src/shared.h", b"x") != artifact_key("t", "p", "a", "test/shared.h", b"x")


def test_automatic_traces_remove_callback_codes_signed_urls_and_exception_bodies():
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from opentelemetry.trace import Status, StatusCode

    from kleos.telemetry import RedactingSpanExporter

    captured = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(RedactingSpanExporter(captured)))
    with provider.get_tracer("privacy-regression").start_as_current_span("callback") as span:
        span.set_attributes({
            "http.url": "https://api.example/auth/callback?code=private-code",
            "http.target": "/auth/callback?code=private-code",
            "url.query": "code=private-code",
            "artifact.url": "https://s3.example/file?X-Amz-Signature=private-signature",
            "http.request.header.authorization": "opaque credential",
            "gen_ai.input.messages": "private prompt body",
            "db.statement": "SELECT 'private query value'",
            "kleos.run.id": "run-123", "gen_ai.usage.input_tokens": 123,
        })
        span.record_exception(ValueError("private exception body"))
        span.set_status(Status(StatusCode.ERROR, "private provider body"))
    result, = captured.get_finished_spans()
    encoded = result.to_json()
    assert "private" not in encoded and "opaque credential" not in encoded
    assert result.attributes["http.target"] == "/auth/callback"
    assert result.attributes["artifact.url"] == "https://s3.example/file"
    assert result.attributes["kleos.run.id"] == "run-123"
    assert result.attributes["gen_ai.usage.input_tokens"] == 123
    assert result.events[0].attributes["exception.type"] == "ValueError"
    assert result.status.status_code is StatusCode.ERROR
    provider.shutdown()


@pytest.mark.parametrize("policy", [None, [], {"research": {"provider": "invented"}}])
def test_invalid_model_policy_is_a_client_error(platform, policy):
    response = platform.client.post("/api/v1/commands", headers={"Idempotency-Key": "bad-policy"},
        json={"type": "project.create", "payload": {"brief": "Test", "modelPolicy": policy}})
    assert response.status_code == 422
