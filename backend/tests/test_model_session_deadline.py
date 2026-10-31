"""A queued/partly consumed native session cannot buy a fresh model deadline."""
import asyncio
from types import SimpleNamespace

import pytest
from pydantic_ai.exceptions import ModelHTTPError

from kleos.config import Settings
from kleos.models import ModelGateway


@pytest.fixture
def gateway():
    result = ModelGateway(Settings(_env_file=None, meta_api_key="fixture", gemini_api_key=None,
        modal_llm_url="https://fixtures.invalid/modal", modal_llm_key="fixture",
        engineering_streaming=False, modal_fallback_to_meta=True))
    result.session_bridge = SimpleNamespace(remaining_seconds=lambda _job: 10)
    return result


def context():
    return {"lane": "mechanics", "task": "project.generate", "nativeSession": {
        "jobId": "native-job", "attemptId": "attempt", "maxSteps": 8, "maxSpendUsd": 1}}


def test_session_deadline_is_remaining_lifetime_without_extending_engineering_cap(gateway):
    assert gateway.deadline(context()) == 10
    gateway.session_bridge.remaining_seconds = lambda _job: 1200
    assert gateway.deadline(context()) == 600
    assert gateway.deadline({"lane": "mechanics"}) == 600
    assert gateway.deadline({"lane": "coordination"}) == 180


async def test_agent_and_provider_timeout_use_fresh_remaining_session_seconds(gateway, monkeypatch):
    seen = []
    gateway.session_bridge.remaining_seconds = lambda _job: 4.5

    class Captured(Exception):
        pass

    async def capture(*args, **kwargs):
        seen.append(kwargs)
        raise Captured

    monkeypatch.setattr(gateway, "run_agent", capture)
    model = gateway.registry.model_for("meta", "mechanics")
    with pytest.raises(Captured):
        await gateway._generate("meta", model, context())
    assert seen[0]["timeout"] == 4.5
    assert seen[0]["model_settings"]["timeout"] == 4.5
    assert seen[0]["model_settings"]["max_tokens"] == 32768
    assert seen[0]["usage_limits"].tool_calls_limit == 7  # pinned8 including reservedfinish, currentsetting32


async def test_stalled_model_is_cancelled_at_remaining_session_not_configured_600_seconds(gateway, monkeypatch):
    model = gateway.registry.model_for("meta", "mechanics")
    gateway.session_bridge.remaining_seconds = lambda _job: .01
    calls = []

    async def stalled(*args, **kwargs):
        calls.append(kwargs["timeout"])
        await asyncio.Event().wait()

    monkeypatch.setattr(gateway.session_agents[("meta", model)], "run", stalled)
    with pytest.raises(TimeoutError):
        await gateway.generate("meta", model, context())
    assert len(calls) == 1 and calls[0] <= .01


async def test_allowed_unbilled_provider_fallback_shares_one_remaining_deadline(gateway, monkeypatch):
    gateway.session_bridge.remaining_seconds = lambda _job: .01
    calls = []

    async def response(provider, model, _context, retries=None):
        calls.append(provider)
        if provider == "modal":
            raise ModelHTTPError(429, model, "Unbilled capacity rejection")
        await asyncio.Event().wait()

    monkeypatch.setattr(gateway, "_generate", response)
    modal = gateway.registry.model_for("modal", "mechanics")
    with pytest.raises(TimeoutError):
        await gateway.generate("modal", modal, context())
    assert calls == ["modal", "meta"]
