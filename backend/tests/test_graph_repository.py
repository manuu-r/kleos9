from datetime import UTC, datetime, timedelta

import pytest

from kleos.contracts.models import Actor, DomainError
from kleos.orchestration.engine import RunScope
from kleos.projects.graph_repository import GraphRepository
from kleos.projects.store import ProjectStore


@pytest.fixture
def repository(tmp_path):
    store = ProjectStore(f"sqlite:///{tmp_path}/authority.db")
    store.initialize_for_tests()
    actor = Actor(tenant_id="tenant", user_id="owner")
    project = store.create(actor, "Existing PCB", "create")
    run = store.submit(actor, project["projectId"], "Route only", 1, "submit")
    scope = RunScope(
        actor=actor,
        project_id=project["projectId"],
        run_id=run["runId"],
        revision=2,
        deadline_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    return GraphRepository(store), scope


async def test_receipt_survives_restart_without_repeating_effect(repository):
    repo, scope = repository
    calls = []

    async def effect():
        calls.append(1)
        return {"observed": "result"}

    assert await repo.run_once(scope, "one", effect) == {"observed": "result"}
    reopened = GraphRepository(ProjectStore(str(repo.store.engine.url)))
    assert await reopened.run_once(scope, "one", effect) == {"observed": "result"}
    assert len(calls) == 1


async def test_unknown_effect_blocks_replay(repository):
    repo, scope = repository
    calls = []

    async def effect():
        calls.append(1)
        raise RuntimeError("lost acknowledgement")

    with pytest.raises(RuntimeError):
        await repo.run_once(scope, "one", effect)
    with pytest.raises(DomainError, match="reconciliation"):
        await repo.run_once(scope, "one", effect)
    assert len(calls) == 1


async def test_cancellation_fences_publication_even_after_effect(repository):
    repo, scope = repository

    async def effect():
        repo.cancel(scope.actor, scope.project_id, scope.run_id)
        return {"externalObserved": True}

    with pytest.raises(DomainError, match="no longer active"):
        await repo.run_once(scope, "one", effect)
    with pytest.raises(DomainError):
        await repo.assert_active(scope)
