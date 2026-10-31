"""Adapter contract tests use SDK response objects; they never make paid requests."""
import asyncio
import json

import httpx
import httpx2
import pytest
from model_fixtures import chat_reply, gemini_reply, patch_replies, responses_reply
from pydantic_ai.exceptions import ModelHTTPError, UnexpectedModelBehavior
from pydantic_ai.usage import RunUsage

from kleos.config import Settings
from kleos.contracts import DomainError, Lane
from kleos.models import DEFAULT_LANE_MODELS, ModelGateway, ModelRegistry

MODAL = {"modal_llm_url": "https://example--muse.modal.run", "modal_llm_key": "wk-fixture.ws-fixture"}


def settings(**overrides):
    return Settings(_env_file=None, meta_api_key="fixture", gemini_api_key="fixture", **{**MODAL, "engineering_streaming": False, **overrides})


@pytest.fixture(autouse=True)
def no_provider_override(monkeypatch):
    for name in ("KLEOS_DEFAULT_MODEL_PROVIDER", "KLEOS_META_API_KEY", "KLEOS_GEMINI_API_KEY",
                 "GEMINI_API_KEY", "KLEOS_MODAL_LLM_URL", "KLEOS_MODAL_LLM_KEY"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def no_provider_network(monkeypatch):
    async def no_network(*args, **kwargs):
        raise AssertionError("Provider contract tests must never access the network")
    monkeypatch.setattr(httpx.AsyncClient, "send", no_network)
    monkeypatch.setattr(httpx2.AsyncClient, "send", no_network)


@pytest.fixture
def gateway():
    return ModelGateway(settings(modal_fallback_to_meta=False))


def test_identical_meta_aliases_register_once_without_duplicate_catalog_choices():
    config = settings(meta_wonder_model="muse-spark-1.3")
    gateway = ModelGateway(config)
    choices = [(row["provider"], row["model"]) for row in gateway.registry.catalog()["models"]]
    assert len(choices) == len(set(choices)) == 3
    assert len(gateway.agents) == len(gateway.planning_agents) == 3
    assert gateway.registry.model_for("meta", "firmware") == "muse-spark-1.3"


@pytest.mark.parametrize("lane,field", [("coordination", "model_timeout_seconds"),
                                        ("mechanics", "engineering_model_timeout_seconds")])
async def test_model_deadline_bounds_the_entire_invocation(gateway, monkeypatch, lane, field):
    setattr(gateway.settings, field, .01)
    model = gateway.registry.models["modal"][0]
    async def stalled(*args, **kwargs):
        await asyncio.Event().wait()
    monkeypatch.setattr(gateway.agents[("modal", model)], "run", stalled)
    with pytest.raises(TimeoutError):
        await gateway.generate("modal", model, {"lane": lane})


def payload():
    return {"summary": "SDK adapter fixture", "questions": [], "requirements": {"screen": "OLED"}, "interfaces": {"voltage": 3.3},
            "files": [], "sources": [], "limitations": ["Fixture response; no engineering qualification"]}


def reply_for(provider, model, body, **kwargs):
    if provider == "meta":
        return lambda _n: responses_reply(model, body, **kwargs)
    if provider == "google":
        return lambda _n: gemini_reply(model, body, **kwargs)
    return lambda _n: chat_reply(model, body, **kwargs)


def provider_lanes():
    lanes = [("meta", lane) for lane in Lane]
    return lanes + [("google", lane) for lane in Lane] + [("modal", lane) for lane in Lane if lane != Lane.research]


@pytest.mark.parametrize(("provider", "lane"), provider_lanes())
async def test_every_lane_uses_selected_direct_provider(gateway, monkeypatch, provider, lane):
    model = gateway.registry.model_for(provider, lane.value)
    requests = patch_replies(monkeypatch, gateway, provider, model, reply_for(provider, model, payload()))
    output, usage, transcript = await gateway.generate(provider, model, {"lane": lane.value, "brief": "Fixture"})
    assert output.summary == "SDK adapter fixture"
    assert output.requirements["screen"] == "OLED" and output.interfaces["voltage"] == 3.3
    assert usage["inputTokens"] == 100 and usage["outputTokens"] == 50
    assert len(requests) == 1
    assert json.loads(transcript)


def test_default_lanes_split_spark_and_wonder_on_the_model_api():
    registry = ModelRegistry(Settings(_env_file=None, meta_api_key="fixture"))
    policy = registry.default_policy()
    assert {lane: choice["model"] for lane, choice in policy.items() if lane in {"firmware", "application"}} == {
        "firmware": "muse-wonder-1.0", "application": "muse-wonder-1.0"}
    assert {choice["model"] for lane, choice in policy.items() if lane not in {"firmware", "application"}} == {
        "muse-spark-1.3"}
    assert {choice["provider"] for choice in policy.values()} == {"meta"}
    assert set(policy) == set(DEFAULT_LANE_MODELS) == {lane.value for lane in Lane}


def test_configured_modal_takes_the_non_search_lanes_and_leaves_research_on_the_model_api():
    policy = ModelRegistry(settings()).default_policy()
    modal_lanes = {lane for lane, choice in policy.items() if choice["provider"] == "modal"}
    assert modal_lanes == {"mechanics", "electronics", "coordination", "review"}
    assert policy["research"] == {"provider": "meta", "model": "muse-spark-1.3"}
    assert policy["mechanics"]["model"] == "meta-models/Muse-Spark-Open-816B-A42B"
    assert policy["firmware"]["model"] == "muse-wonder-1.0"


def test_modal_default_lanes_can_be_disabled():
    policy = ModelRegistry(settings(modal_default_lanes=[])).default_policy()
    assert {choice["provider"] for choice in policy.values()} == {"meta"}


def test_policy_validation_accepts_each_registered_model_and_rejects_others():
    registry = ModelRegistry(settings())
    policy = registry.default_policy()
    assert registry.validate_policy(policy) == policy
    for model in registry.models["meta"]:
        assert registry.validate_policy({**policy, "review": {"provider": "meta", "model": model}})["review"]["model"] == model
    with pytest.raises(DomainError):
        registry.validate_policy({**policy, "review": {"provider": "meta", "model": "muse-unknown"}})
    with pytest.raises(DomainError):
        registry.validate_policy({**policy, "review": {"provider": "meta", "model": registry.models["modal"][0]}})
    with pytest.raises(DomainError):
        registry.validate_policy({**policy, "review": {"provider": "retired-provider", "model": "x"}})


def test_gemini_is_selectable_per_lane_and_never_a_default():
    registry = ModelRegistry(settings())
    assert "google" not in {choice["provider"] for choice in registry.default_policy().values()}
    policy = registry.default_policy()
    for lane in ("research", "firmware", "review"):
        picked = {**policy, lane: {"provider": "google", "model": "gemini-3.8-flash"}}
        assert registry.validate_policy(picked)[lane]["provider"] == "google"
    with pytest.raises(DomainError):
        registry.validate_policy({**policy, "review": {"provider": "google", "model": "gemini-unknown"}})


def test_research_cannot_be_assigned_to_a_provider_without_web_search():
    registry = ModelRegistry(settings())
    policy = {**registry.default_policy(), "research": {"provider": "modal", "model": registry.models["modal"][0]}}
    with pytest.raises(DomainError) as exc:
        registry.validate_policy(policy)
    assert exc.value.status == 422


def test_catalog_lists_every_model_with_its_capabilities():
    catalog = ModelRegistry(settings()).catalog()
    by_model = {item["model"]: item for item in catalog["models"]}
    assert set(by_model) == {"muse-spark-1.3", "muse-wonder-1.0", "meta-models/Muse-Spark-Open-816B-A42B",
                             "gemini-3.8-flash"}
    assert "webSearch" in by_model["muse-wonder-1.0"]["capabilities"]
    assert "webSearch" in by_model["gemini-3.8-flash"]["capabilities"]
    assert "research" in by_model["gemini-3.8-flash"]["enabledLanes"]
    modal = by_model["meta-models/Muse-Spark-Open-816B-A42B"]
    assert "webSearch" not in modal["capabilities"] and "research" not in modal["enabledLanes"]
    assert modal["configured"] is True
    assert catalog["defaultPolicy"] == ModelRegistry(settings()).default_policy()


async def test_missing_provider_and_changed_model_do_not_fall_back(gateway):
    with pytest.raises(DomainError):
        await gateway.generate("meta", "unavailable-model", {"lane": "research"})
    empty = ModelGateway(Settings(_env_file=None))
    with pytest.raises(DomainError) as exc:
        await empty.generate("meta", empty.registry.models["meta"][0], {"lane": "research"})
    assert exc.value.code == "provider_unconfigured"
    with pytest.raises(DomainError) as exc:
        await empty.generate("modal", empty.registry.models["modal"][0], {"lane": "mechanics"})
    assert exc.value.code == "provider_unconfigured"


async def test_research_on_modal_is_refused_not_answered_without_search(gateway):
    with pytest.raises(DomainError) as exc:
        await gateway.generate("modal", gateway.registry.models["modal"][0], {"lane": "research"})
    assert exc.value.code == "provider_unsupported"


async def test_research_attaches_native_web_search_on_the_model_api_only(gateway, monkeypatch):
    model = gateway.registry.models["meta"][0]
    requests = patch_replies(monkeypatch, gateway, "meta", model, lambda _n: responses_reply(model, payload()))
    await gateway.generate("meta", model, {"lane": "research", "brief": "Fixture"})
    assert {"type": "web_search"} in [{"type": tool["type"]} for tool in requests[0]["tools"]]
    requests = patch_replies(monkeypatch, gateway, "meta", model, lambda _n: responses_reply(model, payload()))
    await gateway.generate("meta", model, {"lane": "mechanics", "brief": "Fixture"})
    assert not requests[0].get("tools")


async def test_search_fee_is_added_to_the_estimate(gateway, monkeypatch):
    model = gateway.registry.models["meta"][0]
    patch_replies(monkeypatch, gateway, "meta", model, lambda _n: responses_reply(model, payload(), searches=3))
    _, usage, _ = await gateway.generate("meta", model, {"lane": "research", "brief": "Fixture"})
    assert usage["searchCalls"] == 3
    assert usage["searchUsd"] == pytest.approx(0.0075)
    # Spark 1.3 is not in the price table, so only the search fee is known and settlement keeps the reservation.
    assert usage["estimatedUsd"] is None or usage["estimatedUsd"] >= usage["searchUsd"]


def test_modal_usage_is_priced_from_configured_rates_when_the_price_table_has_no_entry(gateway):
    usage = RunUsage(input_tokens=1_000_000, output_tokens=100_000, cache_read_tokens=400_000, requests=1)
    searches, search_usd, estimated = gateway.estimate_cost("modal", usage, [])
    # 600k fresh * $1.25 + 400k cached * $0.15 + 100k output * $4.25, per million.
    assert estimated == pytest.approx(0.75 + 0.06 + 0.425)
    assert searches == 0 and search_usd == 0


def test_unpriced_meta_usage_stays_unknown_so_settlement_keeps_the_reservation(gateway):
    usage = RunUsage(input_tokens=1000, output_tokens=100, requests=1)
    assert gateway.estimate_cost("meta", usage, []) == (0, 0.0, None)


async def test_modal_replies_report_a_cost(gateway, monkeypatch):
    model = gateway.registry.models["modal"][0]
    patch_replies(monkeypatch, gateway, "modal", model, lambda _n: chat_reply(
        model, payload(), input_tokens=10_000, output_tokens=1_000, cached=4_000))
    _, usage, _ = await gateway.generate("modal", model, {"lane": "mechanics", "brief": "Fixture"})
    assert usage["estimatedUsd"] == pytest.approx((6_000 * 1.25 + 4_000 * 0.15 + 1_000 * 4.25) / 1e6)


@pytest.mark.parametrize("lane", ["research", "coordination", "review"])
async def test_non_generation_lanes_reject_code_and_request_a_bounded_correction(gateway, monkeypatch, lane):
    def reply(n):
        body = payload()
        if n == 1:
            body["files"] = [{"path": "enclosure.py", "content": "unrequested_code = True"}]
        return responses_reply(model, body)
    model = gateway.registry.models["meta"][0]
    requests = patch_replies(monkeypatch, gateway, "meta", model, reply)
    output, usage, _ = await gateway.generate("meta", model, {"lane": lane})
    assert output.files == [] and len(requests) == 2
    assert usage["requests"] == 2


@pytest.mark.parametrize("provider", ["meta", "modal"])
@pytest.mark.parametrize("failure", ["throttle", "timeout", "unavailable"])
async def test_provider_failures_remain_on_selected_provider(gateway, monkeypatch, provider, failure):
    async def no_sleep(_seconds):
        return None
    monkeypatch.setattr("kleos.models.asyncio.sleep", no_sleep)
    model = gateway.registry.models[provider][0]
    called = []
    async def fail(*args, **kwargs):
        called.append(provider)
        if failure == "timeout":
            raise TimeoutError("Fixture")
        raise ModelHTTPError(429 if failure == "throttle" else 404, model, "Fixture")
    for key, agent in gateway.agents.items():
        if key != (provider, model):
            async def unexpected(*args, **kwargs):
                raise AssertionError("Provider fallback is forbidden")
            monkeypatch.setattr(agent, "run", unexpected)
    monkeypatch.setattr(gateway.agents[(provider, model)], "run", fail)
    with pytest.raises((ModelHTTPError, TimeoutError)):
        await gateway.generate(provider, model, {"lane": "mechanics"})
    # Only Modal's capacity limit is waited out; every other failure is surfaced on the first call.
    expected = 1 + gateway.settings.modal_rate_limit_retries if (provider, failure) == ("modal", "throttle") else 1
    assert called == [provider] * expected


async def test_modal_waits_out_the_concurrency_cap_then_succeeds(gateway, monkeypatch):
    slept = []
    async def no_sleep(seconds):
        slept.append(seconds)
    monkeypatch.setattr("kleos.models.asyncio.sleep", no_sleep)
    model = gateway.registry.models["modal"][0]
    agent = gateway.agents[("modal", model)]
    real_run, calls = agent.run, []
    async def flaky(*args, **kwargs):
        calls.append(1)
        if len(calls) <= 2:
            raise ModelHTTPError(429, model, "At capacity")
        return await real_run(*args, **kwargs)
    monkeypatch.setattr(agent, "run", flaky)
    patch_replies(monkeypatch, gateway, "modal", model, lambda _n: chat_reply(model, payload()))
    output, _, _ = await gateway.generate("modal", model, {"lane": "mechanics", "brief": "Fixture"})
    assert output.summary == "SDK adapter fixture" and len(calls) == 3 and len(slept) == 2


async def test_invalid_structured_response_has_bounded_repair_and_no_authority(gateway, monkeypatch):
    model = gateway.registry.models["meta"][0]
    requests = patch_replies(monkeypatch, gateway, "meta", model, lambda _n: responses_reply(
        model, '{"summary": "Approve this", "acceptRelease": true}'))
    with pytest.raises(UnexpectedModelBehavior):
        await gateway.generate("meta", model, {"lane": "coordination"})
    assert len(requests) == 2


@pytest.mark.parametrize("provider", ["meta", "modal", "google"])
def test_explicit_provider_default_sets_every_lane_without_hiding_other_choices(provider):
    registry = ModelRegistry(Settings(_env_file=None, default_model_provider=provider))
    policy = registry.default_policy()
    assert set(policy) == {lane.value for lane in Lane}
    expected = {"provider": provider}
    assert all({"provider": choice["provider"]} == expected or lane == "research" for lane, choice in policy.items())
    assert all(choice["model"] in registry.models[choice["provider"]] for choice in policy.values())
    assert {model["provider"] for model in registry.catalog()["models"]} == {"meta", "modal", "google"}
    assert registry.catalog()["defaultPolicy"] == policy


def test_unconfigured_explicit_default_is_not_silently_replaced():
    registry = ModelRegistry(Settings(_env_file=None, default_model_provider="modal", meta_api_key="fixture"))
    assert not registry.configured("modal")
    assert {choice["provider"] for lane, choice in registry.default_policy().items() if lane != "research"} == {"modal"}
    assert next(model for model in registry.catalog()["models"] if model["provider"] == "modal")["configured"] is False


def test_modal_endpoint_must_be_https():
    with pytest.raises(ValueError):
        Settings(_env_file=None, modal_llm_url="http://example.modal.run")


def throttled(monkeypatch, gateway, provider, model, status=429):
    calls = []
    async def fail(*args, **kwargs):
        calls.append(provider)
        raise ModelHTTPError(status, model, "Fixture")
    monkeypatch.setattr(gateway.agents[(provider, model)], "run", fail)
    return calls


@pytest.fixture
def fallback_gateway(monkeypatch):
    async def no_sleep(_seconds):
        return None
    monkeypatch.setattr("kleos.models.asyncio.sleep", no_sleep)
    return ModelGateway(settings())


async def test_rate_limited_modal_call_is_answered_by_the_model_api_and_recorded(fallback_gateway, monkeypatch):
    gateway = fallback_gateway
    modal = gateway.registry.models["modal"][0]
    spark = gateway.registry.models["meta"][0]
    modal_calls = throttled(monkeypatch, gateway, "modal", modal)
    requests = patch_replies(monkeypatch, gateway, "meta", spark, lambda _n: responses_reply(spark, payload()))
    output, usage, _ = await gateway.generate("modal", modal, {"lane": "mechanics", "brief": "Fixture"})
    assert output.summary == "SDK adapter fixture" and len(requests) == 1
    assert len(modal_calls) == 2  # the first call plus one short wait, not the full retry ladder
    assert (usage["provider"], usage["model"]) == ("meta", spark)
    assert usage["fallbackFrom"] == {"provider": "modal", "model": modal, "reason": "rate_limited"}


@pytest.mark.parametrize("fallback", [True, False])
async def test_rate_limit_after_a_billed_response_cannot_restart_or_switch_provider(monkeypatch, fallback):
    gateway = ModelGateway(settings(modal_fallback_to_meta=fallback))
    model = gateway.registry.models["modal"][0]
    def reply(number):
        if number == 1:
            # A real SDK response is billed, but fails the coordination output guard.
            return chat_reply(model, {**payload(), "files": [{"path": "wrong.py", "content": "x = 1"}]})
        raise ModelHTTPError(429, model, "Later turn is at capacity")
    requests = patch_replies(monkeypatch, gateway, "modal", model, reply)
    async def forbidden(*args, **kwargs):
        raise AssertionError("A partially answered turn cannot change provider")
    monkeypatch.setattr(gateway.agents[("meta", gateway.registry.models["meta"][0])], "run", forbidden)
    with pytest.raises(DomainError) as exc:
        await gateway.generate("modal", model, {"lane": "coordination"})
    assert exc.value.code == "provider_partial_response" and len(requests) == 2


async def test_code_lanes_fall_back_to_the_matching_model_api_model(fallback_gateway, monkeypatch):
    gateway = fallback_gateway
    modal = gateway.registry.models["modal"][0]
    wonder = gateway.registry.models["meta"][1]
    throttled(monkeypatch, gateway, "modal", modal)
    patch_replies(monkeypatch, gateway, "meta", wonder, lambda _n: responses_reply(wonder, payload()))
    _, usage, _ = await gateway.generate("modal", modal, {"lane": "firmware", "brief": "Fixture"})
    assert usage["model"] == wonder


@pytest.mark.parametrize("status", [404, 500])
async def test_only_rate_limits_trigger_the_fallback(fallback_gateway, monkeypatch, status):
    gateway = fallback_gateway
    modal = gateway.registry.models["modal"][0]
    throttled(monkeypatch, gateway, "modal", modal, status=status)
    async def unexpected(*args, **kwargs):
        raise AssertionError("Only a rate limit may move a call to another provider")
    monkeypatch.setattr(gateway.agents[("meta", gateway.registry.models["meta"][0])], "run", unexpected)
    with pytest.raises(ModelHTTPError):
        await gateway.generate("modal", modal, {"lane": "mechanics"})


async def test_model_api_throttling_never_moves_to_modal(fallback_gateway, monkeypatch):
    gateway = fallback_gateway
    spark = gateway.registry.models["meta"][0]
    throttled(monkeypatch, gateway, "meta", spark)
    async def unexpected(*args, **kwargs):
        raise AssertionError("Meta must not fall back to Modal")
    monkeypatch.setattr(gateway.agents[("modal", gateway.registry.models["modal"][0])], "run", unexpected)
    with pytest.raises(ModelHTTPError):
        await gateway.generate("meta", spark, {"lane": "mechanics"})


async def test_fallback_needs_the_model_api_to_be_configured(monkeypatch):
    async def no_sleep(_seconds):
        return None
    monkeypatch.setattr("kleos.models.asyncio.sleep", no_sleep)
    gateway = ModelGateway(Settings(_env_file=None, **MODAL))
    modal = gateway.registry.models["modal"][0]
    calls = throttled(monkeypatch, gateway, "modal", modal)
    with pytest.raises(ModelHTTPError):
        await gateway.generate("modal", modal, {"lane": "mechanics"})
    assert len(calls) == 1 + gateway.settings.modal_rate_limit_retries


async def test_gemini_research_uses_native_search_and_is_priced_by_its_own_table(gateway, monkeypatch):
    model = gateway.registry.models["google"][0]
    requests = patch_replies(monkeypatch, gateway, "google", model, lambda _n: gemini_reply(model, payload()))
    _, usage, _ = await gateway.generate("google", model, {"lane": "research", "brief": "Fixture"})
    assert any("google_search" in str(tool) for tool in requests[0]["config"]["tools"])
    assert usage["provider"] == "google" and usage["searchCalls"] == 0 and usage["searchUsd"] == 0


async def test_gemini_is_not_a_fallback_for_modal_or_meta(monkeypatch):
    async def no_sleep(_seconds):
        return None
    monkeypatch.setattr("kleos.models.asyncio.sleep", no_sleep)
    gateway = ModelGateway(settings())
    modal = gateway.registry.models["modal"][0]
    assert gateway.capacity_fallback("modal", "mechanics") == ("meta", gateway.registry.models["meta"][0])
    assert gateway.capacity_fallback("google", "mechanics") is None
    assert gateway.capacity_fallback("meta", "mechanics") is None
    assert modal
