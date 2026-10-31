"""Engineering streaming through actual OpenAI SSE decoders and Pydantic AI."""
import asyncio
import json

import httpx
import httpx2
import pytest
from model_fixtures import chat_stream, responses_reply, responses_stream
from openai import RateLimitError
from pydantic_ai.exceptions import ModelHTTPError

from kleos.config import Settings
from kleos.contracts import DomainError
from kleos.models import ModelGateway


@pytest.fixture
def gateway(monkeypatch):
    async def no_network(*args, **kwargs):
        raise AssertionError("Streaming tests must never access a provider")
    monkeypatch.setattr(httpx.AsyncClient, "send", no_network)
    monkeypatch.setattr(httpx2.AsyncClient, "send", no_network)
    return ModelGateway(Settings(_env_file=None, meta_api_key="fixture", gemini_api_key=None,
        modal_llm_url="https://fixtures.invalid/modal", modal_llm_key="fixture",
        engineering_streaming=True, modal_fallback_to_meta=True, invocation_reservation_usd=10))


def patch_stream(monkeypatch, gateway, provider, body, *, output_tokens=50, interrupt=None):
    model = gateway.registry.model_for(provider, "mechanics")
    client = gateway.agents[(provider, model)].model.client
    requests = []

    async def respond(**kwargs):
        requests.append(kwargs)
        factory = responses_stream if provider == "meta" else chat_stream
        return factory(client, model, body, output_tokens=output_tokens, interrupt=interrupt)

    monkeypatch.setattr(client.responses if provider == "meta" else client.chat.completions, "create", respond)
    return model, requests


@pytest.mark.parametrize("provider", ["meta", "modal"])
async def test_actual_sdk_stream_reconstructs_long_output_above_prior_ceiling(gateway, monkeypatch, provider):
    source = "# Generated native source fixture\n" * 2000
    body = {"summary": "Long streamed engineering output", "files": [{"path": "enclosure.py", "content": source}]}
    model, requests = patch_stream(monkeypatch, gateway, provider, body, output_tokens=13001)
    output, usage, transcript = await gateway.generate(provider, model, {"lane": "mechanics", "task": "project.generate"})
    assert output.files[0].content == source
    assert usage["outputTokens"] == 13001
    assert usage["outputTokenCeiling"] == 32768
    assert usage["deadlineSeconds"] == 600
    assert usage["streaming"] is True
    assert len(requests) == 1 and requests[0]["stream"] is True
    request = requests[0]
    assert request.get("max_output_tokens", request.get("max_completion_tokens", request.get("max_tokens"))) == 32768
    assert request["timeout"] == 600
    assert json.loads(transcript)


@pytest.mark.parametrize("provider", ["meta", "modal"])
async def test_interrupted_billed_stream_never_retries_or_falls_back(gateway, monkeypatch, provider):
    error = RateLimitError("Simulated capacity error after streamed response", response=httpx2.Response(
        429, request=httpx2.Request("POST", "https://fixtures.invalid/stream")), body={"error": "capacity"})
    model, requests = patch_stream(monkeypatch, gateway, provider, {"summary": "partial response"}, interrupt=error)
    other = "meta" if provider == "modal" else "modal"
    other_model = gateway.registry.model_for(other, "mechanics")
    fallback = []

    async def forbidden(*args, **kwargs):
        fallback.append(True)
        raise AssertionError("An interrupted billed stream cannot move providers")

    monkeypatch.setattr(gateway.agents[(other, other_model)], "run", forbidden)
    with pytest.raises((DomainError, ModelHTTPError)):
        await gateway.generate(provider, model, {"lane": "mechanics"})
    assert len(requests) == 1
    assert fallback == []


async def test_one_total_engineering_deadline_covers_capacity_fallback(gateway, monkeypatch):
    modal = gateway.registry.model_for("modal", "mechanics")
    called, deadlines = [], []
    original_timeout = asyncio.timeout

    def short_deadline(seconds):
        deadlines.append(seconds)
        # Test the real cancellation path in milliseconds while recording the
        # production duration. A second fallback timeout would be observable.
        return original_timeout(.01)

    async def initial_capacity_failure(provider, model, context, retries=None):
        called.append(provider)
        if provider == "modal":
            raise ModelHTTPError(429, model, "Unbilled admission failure")
        await asyncio.Event().wait()

    monkeypatch.setattr("kleos.models.asyncio.timeout", short_deadline)
    monkeypatch.setattr(gateway, "_generate", initial_capacity_failure)
    with pytest.raises(TimeoutError):
        await gateway.generate("modal", modal, {"lane": "mechanics"})
    assert called == ["modal", "meta"]
    assert deadlines == [600]


async def test_engineering_stream_timeout_does_not_restart(gateway, monkeypatch):
    gateway.settings.engineering_model_timeout_seconds = .01
    model = gateway.registry.model_for("modal", "mechanics")
    called = []

    async def stalled(*args, **kwargs):
        called.append(True)
        await asyncio.Event().wait()

    monkeypatch.setattr(gateway.agents[("modal", model)], "run", stalled)
    with pytest.raises(TimeoutError):
        await gateway.generate("modal", model, {"lane": "mechanics"})
    assert called == [True]


@pytest.mark.parametrize("planning", [False, True])
async def test_nonengineering_keeps_short_limits_and_does_not_stream(gateway, monkeypatch, planning):
    from kleos.anatomy_skill import seed_concept

    model = gateway.registry.model_for("meta", "coordination")
    agent = (gateway.planning_agents if planning else gateway.agents)[("meta", model)]
    requests = []

    async def respond(**kwargs):
        requests.append(kwargs)
        body = {"summary": "A concise question turn", "files": []}
        if planning:
            body.pop("files")
            body["conceptScene"] = seed_concept("A smart watch")
        return responses_reply(model, body)

    monkeypatch.setattr(agent.model.client.responses, "create", respond)
    context = {"lane": "coordination"}
    if planning:
        context.update(task="project.capture", planning={"automatic": True})
    _, usage, _ = await gateway.generate("meta", model, context)
    assert usage["streaming"] is False
    assert usage["deadlineSeconds"] == 180
    assert usage["outputTokenCeiling"] == (5000 if planning else 12000)
    assert requests[0]["stream"] is False
    assert requests[0]["timeout"] == 180
