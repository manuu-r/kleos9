import json

import httpx
import httpx2
import pytest
from model_fixtures import gemini_reply, patch_replies, responses_reply
from pydantic import SecretStr
from test_control_plane import engine
from test_planning_concepts import admit, start, workspace

from kleos.anatomy_skill import seed_concept
from kleos.config import Settings
from kleos.db import Attempt, Project
from kleos.engine import EngineeringEngine
from kleos.models import ModelGateway
from kleos.planning import CAPTURE_RESEARCH_POLICY


@pytest.fixture(autouse=True)
def no_provider_network(monkeypatch):
    async def forbidden(*_args, **_kwargs):
        raise AssertionError("Capture tests must not call providers")
    monkeypatch.setattr(httpx.AsyncClient, "send", forbidden)
    monkeypatch.setattr(httpx2.AsyncClient, "send", forbidden)


def capture_context():
    return {"lane": "coordination", "task": "project.capture", "brief": "An air monitor",
            "planning": {"automatic": True}, "captureResearch": dict(CAPTURE_RESEARCH_POLICY)}


def output():
    return {"summary": "A desktop monitor with a low sensor vent and accessible USB power.",
            "conceptScene": seed_concept("desktop air monitor"), "sources": ["https://example.org/reference"]}


@pytest.mark.parametrize("provider", ["meta", "google"])
async def test_capture_uses_search_and_low_thinking_in_selected_provider(monkeypatch, provider):
    gateway = ModelGateway(Settings(_env_file=None, meta_api_key="fixture", gemini_api_key="fixture"))
    model = gateway.registry.model_for(provider, "research")
    def reply(_number):
        if provider == "meta":
            return responses_reply(model, output(), searches=1)
        response = gemini_reply(model, output())
        from google.genai.types import GroundingMetadata
        response.candidates[0].grounding_metadata = GroundingMetadata(
            web_search_queries=["manufacturer air quality sensor placement reference"])
        return response
    requests = patch_replies(monkeypatch, gateway, provider, model, reply, planning=True)
    result, usage, _ = await gateway.generate(provider, model, capture_context())
    assert len(requests) == 1
    assert result.sources == ["https://example.org/reference"]
    assert usage["captureResearch"]["grounded"] is True
    request = requests[0]
    if provider == "meta":
        assert request["reasoning"]["effort"] == "low"
        search = next(t for t in request["tools"] if t["type"] == "web_search")
        assert search["search_context_size"] == "low"
    else:
        config = request["config"]
        assert config["thinking_config"]["thinking_level"] == "LOW"
        assert any(tool.get("google_search") is not None for tool in config["tools"])


async def test_unobserved_search_cannot_publish_invented_sources(monkeypatch):
    gateway = ModelGateway(Settings(_env_file=None, meta_api_key="fixture"))
    model = gateway.registry.model_for("meta", "research")
    requests = patch_replies(monkeypatch, gateway, "meta", model,
                             lambda _: responses_reply(model, output()), planning=True)
    result, usage, _ = await gateway.generate("meta", model, capture_context())
    assert len(requests) == 2  # One bounded request to correct missing research.
    assert result.sources == []
    assert "not grounded" in result.limitations[0]
    assert usage["captureResearch"]["grounded"] is False


@pytest.mark.parametrize("capture_complete", [False, True])
def test_capture_research_is_initial_only_and_resume_pins_choice(platform, capture_complete):
    ident, run = start(platform, "An air monitor")
    with platform.db.transaction() as session:
        project = session.get(Project, ident)
        project.model_policy = {**project.model_policy,
            "coordination": {"provider": "modal", "model": platform.settings.modal_llm_model},
            "research": {"provider": "google", "model": platform.settings.gemini_model}}
        project.state = {**project.state, "planning": {**project.state["planning"], "captureComplete": capture_complete}}
    worker = engine(platform)
    task, attempt = admit(platform, worker, run)
    context, provider, model, _ = worker.attempt_input(attempt)
    assert provider == ("modal" if capture_complete else "google")
    assert model == (platform.settings.modal_llm_model if capture_complete else platform.settings.gemini_model)
    if capture_complete:
        assert "captureResearch" not in context
    else:
        assert context["modelPolicyLane"] == "research"
        assert context["manifest"]["captureResearch"] == CAPTURE_RESEARCH_POLICY
    with platform.db.transaction() as session:
        project = session.get(Project, ident)
        project.model_policy = {**project.model_policy, "research": {"provider": "meta", "model": platform.settings.meta_model}}
    assert worker.begin_attempt(task) == attempt
    assert worker.attempt_input(attempt)[1:3] == (provider, model)


async def test_minimal_brief_capture_persists_sources_before_questions(platform, monkeypatch):
    platform.settings.modal_llm_url = "https://fixture.modal.run"
    platform.settings.modal_llm_key = SecretStr("fixture")
    gateway = ModelGateway(platform.settings)
    model = gateway.registry.model_for("meta", "research")
    requests = patch_replies(monkeypatch, gateway, "meta", model,
        lambda _: responses_reply(model, output(), searches=1), planning=True)
    class Collector:
        async def collect(self, principal, project_id, url):
            return {"uri": url, "status": "fixture_collected"}
    worker = EngineeringEngine(platform.db, platform.settings, platform.store, gateway, research=Collector())
    ident, run = start(platform, "An air monitor")
    task, attempt = admit(platform, worker, run)
    await worker.run_task(task)
    assert len(requests) == 1
    saved = workspace(platform, ident)
    assert saved["modelPolicy"]["coordination"]["provider"] == "modal"
    assert saved["state"]["proposal"]["sources"] == output()["sources"]
    assert saved["state"]["proposal"]["sourceRecords"][0]["status"] == "fixture_collected"
    assert saved["state"]["planning"]["feasibilityComplete"] is False
    with platform.db.transaction() as session:
        pinned = session.get(Attempt, attempt)
        assert pinned.provider == "meta" and pinned.context["captureResearch"] == CAPTURE_RESEARCH_POLICY
    assert "An air monitor" in json.dumps(requests[0], default=str)
