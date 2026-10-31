import asyncio
import json
import stat
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from test_native_sessions import ledger as ledger

from kleos import session_bridge
from kleos.contracts import DomainError
from kleos.db import Invocation, NativeJob, NativeSessionStep, Task, now
from kleos.session_bridge import SessionBridge


def bridge_for(ledger, tmp_path, backend="docker"):
    ledger.settings.execution_backend = backend
    execution = SimpleNamespace(control_directory=lambda job_id: tmp_path / "control" / job_id)
    return SessionBridge(ledger.db, ledger.settings, execution)


def test_local_action_roundtrip_atomic_permissions_and_failed_diagnostics(ledger, tmp_path):
    bridge = bridge_for(ledger, tmp_path)

    async def supervisor():
        step = bridge.sessions.next_local("j")["step"]
        folder = tmp_path / "control/j/steps"
        path = folder / f"{step['id']}.request.json"
        assert json.loads(path.read_bytes()) == step
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(folder.stat().st_mode) == 0o700
        assert stat.S_IMODE(folder.parent.stat().st_mode) == 0o700
        assert not list(folder.glob("*.tmp"))
        (folder / f"{step['id']}.result.json").write_text(json.dumps({
            "status": "failed", "result": {"exitCode": 1, "logs": "Fix this compiler error"}}))

    bridge._sleep = supervisor
    response = asyncio.run(bridge.execute("a", "j", "call", "build", {}))
    assert response == {"status": "failed", "result": {"exitCode": 1, "logs": "Fix this compiler error"}}
    # Same durable tool call reuses its result; no second supervisor/build call.
    bridge._sleep = AsyncMock(side_effect=AssertionError("Must not rerun completed step"))
    assert asyncio.run(bridge.execute("a", "j", "call", "build", {})) == response


def test_ecs_poll_uses_ledger_without_local_transport(ledger, tmp_path):
    bridge = bridge_for(ledger, tmp_path, "ecs")
    bridge.execution.control_directory = lambda _: pytest.fail("ECS must not access local files")

    async def callback():
        step = bridge.sessions.next_local("j")["step"]
        bridge.sessions.complete_local("j", step["id"], {"status": "succeeded", "result": {"text": "source"}})

    bridge._sleep = callback
    assert asyncio.run(bridge.execute("a", "j", "call", "read", {"path": "design.py"})) == {
        "status": "succeeded", "result": {"text": "source"}}


def test_capacity_wait_is_fresh_and_never_dispatches(ledger, tmp_path):
    bridge = bridge_for(ledger, tmp_path)
    with ledger.db.transaction() as session:
        session.get(NativeJob, "j").status = "pending"
    calls = []

    async def dispatch_elsewhere():
        calls.append("poll")
        with ledger.db.transaction() as session:
            session.get(NativeJob, "j").status = "submitted"

    bridge._sleep = dispatch_elsewhere
    assert asyncio.run(bridge.await_ready("j")) is None
    assert calls == ["poll"]


def test_finish_only_trusted_and_does_not_wait(ledger, tmp_path):
    bridge = bridge_for(ledger, tmp_path)
    with pytest.raises(DomainError, match="trusted"):
        asyncio.run(bridge.execute("a", "j", "bad", "finish", {}))
    with ledger.db.transaction() as session:
        session.get(Invocation, "i").status = "succeeded"
    bridge._sleep = AsyncMock(side_effect=AssertionError("Finish must not wait"))
    step_id = bridge.finish("a", "j")
    assert bridge.finish("a", "j") == step_id
    assert bridge.sessions.read(step_id)["status"] == "queued"
    assert json.loads((tmp_path / f"control/j/steps/{step_id}.request.json").read_text()) == {
        "id": step_id, "action": "finish", "arguments": {}}


@pytest.mark.parametrize("method", ["execute", "await_ready"])
def test_cancellation_during_poll_stops_without_replaying_paid_work(ledger, tmp_path, method):
    bridge = bridge_for(ledger, tmp_path, "ecs")
    with ledger.db.transaction() as session:
        session.get(NativeJob, "j").status = "pending"

    async def cancel():
        with ledger.db.transaction() as session:
            session.get(Task, "t").fence += 1

    bridge._sleep = cancel
    with pytest.raises(DomainError, match="no longer active"):
        asyncio.run(bridge.execute("a", "j", "call", "read", {}) if method == "execute" else bridge.await_ready("j"))


def test_deadline_during_poll_stops(ledger, tmp_path):
    bridge = bridge_for(ledger, tmp_path, "ecs")

    async def expire():
        with ledger.db.transaction() as session:
            session.get(NativeJob, "j").expires_at = now() - timedelta(seconds=1)

    bridge._sleep = expire
    with pytest.raises(DomainError, match="deadline"):
        asyncio.run(bridge.execute("a", "j", "call", "build", {}))


def test_fence_checked_after_result_before_return(ledger, tmp_path):
    bridge = bridge_for(ledger, tmp_path, "ecs")
    original_read = bridge.sessions.read

    def malicious_race(step_id):
        result = original_read(step_id)
        with ledger.db.transaction() as session:
            session.get(Task, "t").fence += 1
        return {**result, "status": "succeeded", "result": {"text": "old"}}

    bridge.sessions.read = malicious_race
    with pytest.raises(DomainError, match="no longer active"):
        asyncio.run(bridge.execute("a", "j", "call", "read", {}))


def test_request_write_replay_rejects_changed_or_symlink_files(ledger, tmp_path):
    bridge = bridge_for(ledger, tmp_path)
    step = bridge.sessions.queue("a", "j", "call", "read", {})
    bridge._flush_local("j", step)
    path = tmp_path / f"control/j/steps/{step['id']}.request.json"
    path.write_text("different immutable request")
    with pytest.raises(DomainError, match="changed"):
        bridge._flush_local("j", step)
    path.unlink()
    target = tmp_path / "secret"
    target.write_text("secret")
    path.symlink_to(target)
    with pytest.raises(DomainError, match="Unsafe"):
        bridge._flush_local("j", step)
    assert target.read_text() == "secret"


@pytest.mark.parametrize("kind", ["oversize", "symlink", "fifo", "invalid_json", "authority"])
def test_result_file_bounded_regular_and_non_authoritative(ledger, tmp_path, kind):
    bridge = bridge_for(ledger, tmp_path)
    step = bridge.sessions.queue("a", "j", "call", "read", {})
    bridge._flush_local("j", step)
    path = tmp_path / f"control/j/steps/{step['id']}.result.json"
    if kind == "oversize":
        path.write_bytes(b"x" * (2 * 1024 * 1024 + 1))
    elif kind == "symlink":
        secret = tmp_path / "secret"
        secret.write_text("secret")
        path.symlink_to(secret)
    elif kind == "fifo":
        import os
        os.mkfifo(path)
    elif kind == "invalid_json":
        path.write_text("not JSON")
    else:
        path.write_text(json.dumps({"status": "succeeded", "result": {"checks": [{"status": "PASS"}]}}))
    with pytest.raises(DomainError):
        bridge._local_result("j", step["id"])
    assert bridge.sessions.read(step["id"])["status"] == "queued"


def test_unsafe_directory_and_step_ids_rejected(ledger, tmp_path):
    bridge = bridge_for(ledger, tmp_path)
    step = bridge.sessions.queue("a", "j", "call", "read", {})
    folder = tmp_path / "control/j"
    folder.mkdir(parents=True)
    external = tmp_path / "elsewhere"
    external.mkdir()
    (folder / "steps").symlink_to(external, target_is_directory=True)
    with pytest.raises(DomainError, match="Unsafe"):
        bridge._flush_local("j", step)
    with pytest.raises(DomainError, match="identifier"):
        bridge._flush_local("j", {**step, "id": "../../escape"})
    assert not list(external.iterdir())


def test_sleep_uses_durable_workflow_wait_only_inside_workflow(ledger, tmp_path, monkeypatch):
    bridge = bridge_for(ledger, tmp_path)
    durable = AsyncMock()
    ordinary = AsyncMock()
    monkeypatch.setattr(session_bridge, "DBOS", SimpleNamespace(workflow_id="workflow", sleep_async=durable))
    monkeypatch.setattr(session_bridge.asyncio, "sleep", ordinary)
    asyncio.run(bridge._sleep())
    durable.assert_awaited_once_with(.25)
    ordinary.assert_not_awaited()
    session_bridge.DBOS.workflow_id = None
    asyncio.run(bridge._sleep())
    ordinary.assert_awaited_once_with(.25)


def test_plain_live_check_never_reuses_cached_status(ledger, tmp_path):
    bridge = bridge_for(ledger, tmp_path)
    assert bridge.assert_live("j").status == "running"
    with ledger.db.transaction() as session:
        session.get(NativeJob, "j").status = "cancelled"
    with pytest.raises(DomainError):
        bridge.assert_live("j")
    with ledger.db.transaction() as session:
        assert session.query(NativeSessionStep).count() == 0


def test_partial_session_usage_preserved_without_financial_settlement(ledger, tmp_path):
    from kleos.db import Project

    bridge = bridge_for(ledger, tmp_path)
    bridge.record_usage("a", "j", "google", "fixture", {
        "inputTokens": 10, "outputTokens": 20, "requests": 1, "estimatedUsd": .12,
        "apiKey": "must-not-store", "transcript": "must-not-store"})
    bridge.record_usage("a", "j", "google", "fixture", {"inputTokens": 15, "outputTokens": 18,
                                                               "requests": 2, "estimatedUsd": .1})
    with ledger.db.transaction() as s:
        call = s.get(Invocation, "i")
        assert call.usage["sessionObserved"] == {
            "provider": "google", "model": "fixture", "inputTokens": 15,
            "outputTokens": 20, "requests": 2, "estimatedUsd": .12}
        assert call.status == "pending"
        assert call.reserved_usd == 1 and call.estimated_usd == 0
        project = s.get(Project, "p")
        assert project.spent_usd == 0 and project.reserved_usd == 0
        s.get(Task, "t").fence += 1
    with pytest.raises(DomainError):
        bridge.record_usage("a", "j", "google", "fixture", {"outputTokens": 99})


@pytest.mark.parametrize("value", [-1, True, float("nan"), float("inf"), "12", 10 ** 500])
def test_partial_usage_rejects_invalid_numeric_records(ledger, tmp_path, value):
    bridge = bridge_for(ledger, tmp_path)
    with pytest.raises(DomainError, match="finite nonnegative"):
        bridge.record_usage("a", "j", "google", "fixture", {"estimatedUsd": value})


def test_partial_usage_rejects_completed_invocation(ledger, tmp_path):
    bridge = bridge_for(ledger, tmp_path)
    with ledger.db.transaction() as s:
        s.get(Invocation, "i").status = "succeeded"
    with pytest.raises(DomainError, match="active reserved"):
        bridge.record_usage("a", "j", "google", "fixture", {"outputTokens": 10})


def test_remaining_seconds_uses_actual_expiry_and_elapsed_session_age(ledger, tmp_path):
    bridge = bridge_for(ledger, tmp_path)
    with ledger.db.transaction() as s:
        s.get(NativeJob, "j").expires_at = now() + timedelta(seconds=12)
    assert 0 < bridge.remaining_seconds("j") <= 12
    with ledger.db.transaction() as s:
        job = s.get(NativeJob, "j")
        job.expires_at = now() + timedelta(hours=1)
        job.created_at = now() - timedelta(seconds=890)
    assert 0 < bridge.remaining_seconds("j") <= 10
    with ledger.db.transaction() as s:
        s.get(NativeJob, "j").expires_at = now() - timedelta(seconds=1)
    with pytest.raises(DomainError, match="deadline"):
        bridge.remaining_seconds("j")


def test_remaining_lifetime_keeps_original_pin_when_settings_increase(ledger, tmp_path):
    bridge = bridge_for(ledger, tmp_path)
    with ledger.db.transaction() as s:
        job = s.get(NativeJob, "j")
        job.request = {"sessionLimits": {"maxSteps": 32, "maxSeconds": 60}}
        job.created_at = now() - timedelta(seconds=50)
    ledger.settings.native_session_max_seconds = 1800
    assert 0 < bridge.remaining_seconds("j") <= 10
