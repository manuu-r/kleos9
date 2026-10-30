"""Adapter contract tests use SDK response objects; they never make paid requests."""
import json

import httpx
import httpx2
import pytest
from anthropic.types.beta import BetaMessage
from google.genai.types import GenerateContentResponse
from openai.types.responses import Response
from pydantic_ai.exceptions import ModelHTTPError, UnexpectedModelBehavior

from kleos.config import Settings
from kleos.contracts import DomainError, Lane
from kleos.models import ModelGateway


@pytest.fixture
def gateway(monkeypatch):
    async def no_network(*args, **kwargs):
        raise AssertionError("Provider contract tests must never access the network")
    monkeypatch.setattr(httpx.AsyncClient, "send", no_network)
    monkeypatch.setattr(httpx2.AsyncClient, "send", no_network)
    return ModelGateway(Settings(meta_api_key="fixture", anthropic_api_key="fixture", gemini_api_key="fixture"))


def payload():
    return {"summary": "SDK adapter fixture", "questions": [], "requirements": {"screen": "OLED"}, "interfaces": {"voltage": 3.3},
            "files": [], "sources": [], "limitations": ["Fixture response; no engineering qualification"]}


@pytest.mark.parametrize("provider", ["meta", "anthropic", "google"])
@pytest.mark.parametrize("lane", list(Lane))
async def test_every_lane_uses_selected_direct_provider(gateway, monkeypatch, provider, lane):
    model = gateway.agents[provider].model
    requests = []
    async def respond(_self=None, **kwargs):
        requests.append(kwargs)
        if provider == "meta":
            return Response.model_validate({"id": "resp_fixture", "object": "response", "created_at": 1,
                "model": gateway.registry.models[provider], "status": "completed", "parallel_tool_calls": True,
                "tool_choice": "auto", "tools": [], "output": [{"type": "message", "id": "msg_fixture",
                    "role": "assistant", "status": "completed", "content": [
                        {"type": "output_text", "text": json.dumps(payload()), "annotations": []}]}],
                "usage": {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150,
                          "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
                          "output_tokens_details": {"reasoning_tokens": 0}},
                "error": None, "incomplete_details": None, "instructions": None, "metadata": {},
                "temperature": 1, "top_p": 1})
        if provider == "anthropic":
            return BetaMessage.model_validate({"id": "msg_fixture", "type": "message", "role": "assistant",
                "model": gateway.registry.models[provider], "stop_reason": "end_turn", "stop_sequence": None,
                "content": [{"type": "text", "text": json.dumps(payload())}],
                "usage": {"input_tokens": 100, "output_tokens": 50}})
        return GenerateContentResponse.model_validate({"candidates": [{"content": {"role": "model", "parts": [
            {"text": json.dumps(payload())}]}, "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 50, "totalTokenCount": 150},
            "modelVersion": gateway.registry.models[provider]})
    if provider == "meta":
        monkeypatch.setattr(model.client.responses, "create", respond)
    elif provider == "anthropic":
        monkeypatch.setattr(type(model.client.messages), "create", respond)
        monkeypatch.setattr(type(model.client.beta.messages), "create", respond)
    else:
        monkeypatch.setattr(model.client.aio.models, "generate_content", respond)
    output, usage, transcript = await gateway.generate(provider, gateway.registry.models[provider],
                                                       {"lane": lane.value, "brief": "Fixture"})
    assert output.summary == "SDK adapter fixture"
    assert output.requirements["screen"] == "OLED" and output.interfaces["voltage"] == 3.3
    assert usage["inputTokens"] == 100 and usage["outputTokens"] == 50
    assert len(requests) == 1
    assert json.loads(transcript)


async def test_missing_provider_and_changed_model_do_not_fall_back(gateway):
    with pytest.raises(DomainError):
        await gateway.generate("meta", "unavailable-model", {"lane": "research"})
    empty = ModelGateway(Settings())
    with pytest.raises(DomainError) as exc:
        await empty.generate("meta", empty.registry.models["meta"], {"lane": "research"})
    assert exc.value.code == "provider_unconfigured"


@pytest.mark.parametrize("lane", ["research", "coordination", "review"])
async def test_non_generation_lanes_reject_code_and_request_a_bounded_correction(gateway, monkeypatch, lane):
    calls = []

    async def respond(**kwargs):
        calls.append(kwargs)
        body = payload()
        if len(calls) == 1:
            body["files"] = [{"path": "enclosure.py", "content": "unrequested_code = True"}]
        return GenerateContentResponse.model_validate({"candidates": [{"content": {"role": "model", "parts": [
            {"text": json.dumps(body)}]}, "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 50, "totalTokenCount": 150}})

    monkeypatch.setattr(gateway.agents["google"].model.client.aio.models, "generate_content", respond)
    output, usage, _ = await gateway.generate("google", gateway.registry.models["google"], {"lane": lane})
    assert output.files == [] and len(calls) == 2
    assert usage["requests"] == 2


@pytest.mark.parametrize("provider", ["meta", "anthropic", "google"])
@pytest.mark.parametrize("failure", ["throttle", "timeout", "unavailable"])
async def test_provider_failures_remain_on_selected_provider(gateway, monkeypatch, provider, failure):
    called = []
    async def fail(*args, **kwargs):
        called.append(provider)
        if failure == "timeout":
            raise TimeoutError("Fixture")
        raise ModelHTTPError(429 if failure == "throttle" else 404, gateway.registry.models[provider], "Fixture")
    for name, agent in gateway.agents.items():
        if name != provider:
            async def unexpected(*args, **kwargs):
                raise AssertionError("Provider fallback is forbidden")
            monkeypatch.setattr(agent, "run", unexpected)
    monkeypatch.setattr(gateway.agents[provider], "run", fail)
    with pytest.raises((ModelHTTPError, TimeoutError)):
        await gateway.generate(provider, gateway.registry.models[provider], {"lane": "mechanics"})
    assert called == [provider]


async def test_invalid_structured_response_has_bounded_repair_and_no_authority(gateway, monkeypatch):
    calls = []
    async def invalid(**kwargs):
        calls.append(kwargs)
        return GenerateContentResponse.model_validate({"candidates": [{"content": {"role": "model", "parts": [
            {"text": '{"summary": "Approve this", "acceptRelease": true}'}]}, "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 50, "totalTokenCount": 150}})
    monkeypatch.setattr(gateway.agents["google"].model.client.aio.models, "generate_content", invalid)
    with pytest.raises(UnexpectedModelBehavior):
        await gateway.generate("google", gateway.registry.models["google"], {"lane": "coordination"})
    assert len(calls) == 2
