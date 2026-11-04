from types import SimpleNamespace

import pytest
from langgraph.store.memory import InMemoryStore
from langmem.knowledge.extraction import ExtractedMemory

from kleos.contracts.models import Actor, DomainError, ProjectContext
from kleos.memory.service import Knowledge, MemoryFact


class Extractor:
    def __init__(self, sources=None):
        self.sources = sources or ["project:p:revision:1"]
        self.inputs = []

    async def ainvoke(self, value):
        self.inputs.append(value)
        return [
            ExtractedMemory(
                id="candidate",
                content=MemoryFact(text="User prefers PETG", category="preference", source_ids=self.sources),
            )
        ]


async def authorize(actor, project, *, write=False):
    if actor.tenant_id != "t" or project not in {"p", "other"}:
        raise DomainError("Forbidden", "forbidden", 403)
    return ProjectContext(tenant_id="t", project_id=project, revision=1)


async def test_memory_is_evidence_linked_advisory_and_project_scoped():
    extractor, store = Extractor(), InMemoryStore()
    memory = Knowledge(store, authorize, extractor=extractor)
    actor = Actor(tenant_id="t", user_id="u")
    facts = await memory.extract(actor, "p", [{"role": "user", "content": "Use PETG"}])
    assert facts[0]["authority"] == "advisory" and facts[0]["revision"] == 1
    assert extractor.inputs[0]["messages"][0]["role"] == "system"
    found = await memory.search(actor, "p", "material")
    assert found["mode"] == "recent_project_records" and len(found["items"]) == 1
    assert not (await memory.search(actor, "other", "material"))["items"]
    with pytest.raises(DomainError):
        await memory.search(Actor(tenant_id="evil", user_id="u"), "p", "material")
    await memory.delete_project(actor, "p")
    assert not (await memory.search(actor, "p", "material"))["items"]


async def test_memory_cannot_invent_evidence_or_publish_after_revision_change():
    actor, store = Actor(tenant_id="t", user_id="u"), InMemoryStore()
    memory = Knowledge(store, authorize, extractor=Extractor(["invented"]))
    with pytest.raises(DomainError, match="unknown evidence"):
        await memory.extract(actor, "p", [{"role": "user", "content": "Use PETG"}])
    calls = []

    async def changed(actor, project, *, write=False):
        calls.append(1)
        return ProjectContext(tenant_id="t", project_id="p", revision=len(calls))

    memory = Knowledge(store, changed, extractor=Extractor())
    with pytest.raises(DomainError, match="changed"):
        await memory.extract(actor, "p", [{"role": "user", "content": "Use PETG"}])
    assert not await store.asearch(("kleos", "t", "p", "knowledge"))


async def test_extraction_requires_configured_model_and_public_bounded_messages():
    actor = Actor(tenant_id="t", user_id="u")
    memory = Knowledge(InMemoryStore(), authorize)
    with pytest.raises(DomainError, match="not configured"):
        await memory.extract(actor, "p", [{"role": "user", "content": "Hi"}])
    memory = Knowledge(InMemoryStore(), authorize, extractor=Extractor())
    with pytest.raises(DomainError, match="public messages"):
        await memory.extract(actor, "p", [{"role": "reasoning", "content": "private"}])


def test_actual_langmem_factory_receives_explicit_memory_policy(monkeypatch):
    captured = {}

    def factory(model, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(ainvoke=None)

    monkeypatch.setattr("kleos.memory.service.create_memory_manager", factory)
    Knowledge(InMemoryStore(), authorize, model=object())
    assert captured["schemas"] == [MemoryFact]
    assert captured["enable_updates"] is False and captured["enable_deletes"] is False
    assert "private reasoning" in captured["instructions"]


async def test_actual_langmem_extraction_runs_with_injected_chat_model():
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, ChatResult

    class FixtureChat(BaseChatModel):
        @property
        def _llm_type(self):
            return "fixture-no-network"

        def bind_tools(self, tools, **kwargs):
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            return ChatResult(
                generations=[
                    ChatGeneration(
                        message=AIMessage(
                            content="",
                            tool_calls=[
                                {
                                    "id": "memory-fixture",
                                    "name": "MemoryFact",
                                    "args": {
                                        "text": "User prefers PETG",
                                        "category": "preference",
                                        "source_ids": ["project:p:revision:1"],
                                    },
                                }
                            ],
                        )
                    )
                ]
            )

    memory = Knowledge(InMemoryStore(), authorize, model=FixtureChat())
    result = await memory.extract(
        Actor(tenant_id="t", user_id="u"), "p", [{"role": "user", "content": "Use PETG"}]
    )
    assert result[0]["text"] == "User prefers PETG"
    assert result[0]["authority"] == "advisory"


async def test_viewer_can_retrieve_but_cannot_write_or_delete_memory():
    async def permissions(actor, project, *, write=False):
        if write:
            raise DomainError("Project is read-only", "forbidden", 403)
        return await authorize(actor, project)

    actor = Actor(tenant_id="t", user_id="viewer")
    memory = Knowledge(InMemoryStore(), permissions, extractor=Extractor())
    assert not (await memory.search(actor, "p", "material"))["items"]
    with pytest.raises(DomainError, match="read-only"):
        await memory.extract(actor, "p", [{"role": "user", "content": "Use PETG"}])
    with pytest.raises(DomainError, match="read-only"):
        await memory.delete_project(actor, "p")


async def test_preferences_survive_later_objectives_without_becoming_current_evidence():
    revision = 1

    async def current(actor, project, *, write=False):
        return ProjectContext(tenant_id=actor.tenant_id, project_id=project, revision=revision)

    actor = Actor(tenant_id="t", user_id="u")
    memory = Knowledge(InMemoryStore(), current, extractor=Extractor())
    await memory.extract(actor, "p", [{"role": "user", "content": "Use PETG"}])
    revision = 2
    found = await memory.search(actor, "p", "material")
    assert found["items"][0]["text"] == "User prefers PETG"
    assert found["items"][0]["sourceStatus"] == "historical"
    assert found["items"][0]["authority"] == "advisory"
    assert found["items"][0]["revision"] == 1 and found["items"][0]["currentRevision"] == 2
