"""A cancelled SDK response remains observable without regaining tool authority."""
from types import SimpleNamespace

import httpx
import httpx2
import pytest
from model_fixtures import responses_reply
from test_native_sessions import ledger as ledger

from kleos.config import Settings
from kleos.contracts import DomainError
from kleos.db import Artifact, Invocation, NativeJob, NativeSessionStep, Task, Validation
from kleos.models import ModelGateway
from kleos.session_bridge import SessionBridge


def bridge_for(ledger):
    with ledger.db.transaction() as session:
        session.get(Invocation, "i").operation = "generate"
    return SessionBridge(ledger.db, ledger.settings, SimpleNamespace())


async def test_actual_sdk_response_after_cancel_records_tokens_but_cannot_continue(ledger, monkeypatch):
    async def no_network(*args, **kwargs):
        raise AssertionError("No paid calls in cancellation accounting test")
    monkeypatch.setattr(httpx.AsyncClient, "send", no_network)
    monkeypatch.setattr(httpx2.AsyncClient, "send", no_network)
    gateway = ModelGateway(Settings(_env_file=None, meta_api_key="fixture", gemini_api_key=None,
        modal_llm_url=None, modal_llm_key=None, engineering_streaming=False))
    gateway.session_bridge = bridge_for(ledger)
    model = gateway.settings.meta_model
    calls = []

    async def response(**kwargs):
        calls.append(kwargs)
        with ledger.db.transaction() as session:
            session.get(NativeJob, "j").status = "cancelled"
            session.get(Task, "t").fence += 1
        return responses_reply(model, {"summary": "A response arrived after cancellation", "files": []},
                               input_tokens=100, output_tokens=50)

    monkeypatch.setattr(gateway.session_agents[("meta", model)].model.client.responses, "create", response)
    with pytest.raises(DomainError, match="no longer active"):
        await gateway.generate("meta", model, {"lane": "mechanics", "nativeSession": {
            "jobId": "j", "attemptId": "a", "maxSteps": 4, "maxSpendUsd": 1}})
    assert len(calls) == 1
    with ledger.db.transaction() as session:
        invocation = session.get(Invocation, "i")
        observed = invocation.usage["sessionObserved"]
        assert observed["inputTokens"] == 100 and observed["outputTokens"] == 50
        assert observed["requests"] == 1 and observed["estimatedUsd"] > 0
        assert invocation.status == "pending" and invocation.reserved_usd == 1
        assert session.get(NativeJob, "j").status == "cancelled"
        assert session.query(Artifact).count() == session.query(Validation).count() == session.query(NativeSessionStep).count() == 0


def test_numeric_observer_can_record_uncertain_fenced_response_without_tool_authority(ledger):
    bridge = bridge_for(ledger)
    with ledger.db.transaction() as session:
        session.get(Invocation, "i").status = "uncertain"
        session.get(NativeJob, "j").status = "cancelled"
        session.get(Task, "t").fence += 1
    bridge.record_observed_usage("a", "j", "meta", "fixture", {
        "inputTokens": 10, "outputTokens": 20, "estimatedUsd": .02, "apiKey": "never-store", "content": "never-store"})
    with ledger.db.transaction() as session:
        call = session.get(Invocation, "i")
        assert call.usage == {"sessionObserved": {"provider": "meta", "model": "fixture",
                                                 "inputTokens": 10, "outputTokens": 20, "estimatedUsd": .02}}
        assert call.status == "uncertain" and call.reserved_usd == 1
    with pytest.raises(DomainError):
        bridge.record_usage("a", "j", "meta", "fixture", {"outputTokens": 30})
    with pytest.raises(DomainError):
        bridge.assert_live("j")


@pytest.mark.parametrize("mutation", ["attempt", "tenant", "purpose", "invocation_project"])
def test_postresponse_accounting_identity_checks_are_strict(ledger, mutation):
    bridge = bridge_for(ledger)
    with ledger.db.transaction() as session:
        if mutation == "attempt":
            session.get(NativeJob, "j").attempt_id = "other"
        elif mutation == "tenant":
            session.get(NativeJob, "j").tenant_id = "other"
        elif mutation == "purpose":
            session.get(NativeJob, "j").purpose = "build"
        else:
            session.get(Invocation, "i").project_id = "other"
    with pytest.raises(DomainError):
        bridge.record_observed_usage("a", "j", "meta", "fixture", {"outputTokens": 10})
    with ledger.db.transaction() as session:
        assert session.get(Invocation, "i").usage == {}


@pytest.mark.parametrize("settled", ["succeeded", "failed", "reconciled"])
def test_observer_never_reopens_or_changes_settled_financial_records(ledger, settled):
    bridge = bridge_for(ledger)
    with ledger.db.transaction() as session:
        call = session.get(Invocation, "i")
        call.status, call.usage, call.estimated_usd = settled, {"settled": True}, .1
    bridge.record_observed_usage("a", "j", "meta", "fixture", {"outputTokens": 100, "estimatedUsd": .2})
    with ledger.db.transaction() as session:
        call = session.get(Invocation, "i")
        assert call.status == settled and call.usage == {"settled": True} and call.estimated_usd == .1
