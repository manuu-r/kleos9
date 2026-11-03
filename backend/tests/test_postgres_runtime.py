"""Explicit opt-in real PostgreSQL checks, isolated in disposable schemas."""

import asyncio
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from psycopg import sql
from sqlalchemy.engine import make_url

from kleos.contracts.models import Actor, DomainError, TaskResult, TaskSpec
from kleos.orchestration.engine import ObjectiveEngine, PlanDecision, RunScope
from kleos.orchestration.persistence import postgres_runtime
from kleos.orchestration.worker import ObjectiveWorker, PostgresRunLocks
from kleos.projects.graph_repository import GraphRepository
from kleos.projects.store import Objective, ProjectStore


@pytest.fixture
def database():
    raw = os.environ.get("KLEOS_TEST_POSTGRES_URL")
    if not raw:
        pytest.skip("KLEOS_TEST_POSTGRES_URL is an explicit disposable database opt-in")
    parsed = make_url(raw)
    if not parsed.database.startswith(("kleos_rewrite_verify_", "kleos_test_")):
        pytest.fail("PostgreSQL verification requires an explicitly named disposable test database")
    schema = "verify_" + uuid4().hex
    with psycopg.connect(raw, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    scoped = parsed.update_query_dict({"options": "-csearch_path=" + schema})
    store = ProjectStore(scoped.set(drivername="postgresql+psycopg").render_as_string(hide_password=False))
    with store.engine.begin() as connection:
        migration = Config(str(Path(__file__).parents[1] / "alembic.ini"))
        migration.attributes["connection"] = connection
        command.upgrade(migration, "head")
        command.check(migration)
    try:
        yield store, scoped.render_as_string(hide_password=False)
    finally:
        store.engine.dispose()
        with psycopg.connect(raw, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def objective(store):
    actor = Actor(tenant_id="tenant", user_id="owner")
    project = store.create(actor, "Imported bracket", "create")["projectId"]
    run = store.submit(actor, project, "Replace bracket", 1, "submit")["runId"]
    return actor, project, run


async def test_postgres_run_lock_is_exclusive_and_released(database):
    store, _ = database
    first, second = PostgresRunLocks(store.engine), PostgresRunLocks(store.engine)
    async with first.hold("same-run") as held:
        assert held is not None
        async with second.hold("same-run") as contender:
            assert contender is None
    async with second.hold("same-run") as successor:
        assert successor is not None


async def test_postgres_effect_claim_excludes_parallel_duplicate_and_replays_receipt(database):
    store, _ = database
    actor, project, run = objective(store)
    scope = RunScope(
        actor=actor,
        project_id=project,
        run_id=run,
        revision=2,
        deadline_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    repo = GraphRepository(store)
    started, finish = asyncio.Event(), asyncio.Event()
    calls = []

    async def effect():
        calls.append(1)
        started.set()
        await finish.wait()
        return {"observed": True}

    operation = asyncio.create_task(repo.run_once(scope, "effect", effect))
    await asyncio.wait_for(started.wait(), 2)
    with pytest.raises(DomainError, match="reconciliation"):
        await repo.run_once(scope, "effect", effect)
    finish.set()
    assert await operation == {"observed": True}
    reopened = ProjectStore(store.engine.url)
    try:
        assert await GraphRepository(reopened).run_once(scope, "effect", effect) == {"observed": True}
        assert calls == [1]
    finally:
        reopened.engine.dispose()


async def test_postgres_graph_and_memory_survive_connection_restart(database):
    store, url = database
    actor, project, run = objective(store)
    repo, calls = GraphRepository(store), []

    class Planner:
        async def plan(self, scope, objective, context, tasks, results, answer, **kwargs):
            calls.append(answer)
            if results:
                return PlanDecision(done=True)
            if answer is None:
                return PlanDecision(question="Material?")
            return PlanDecision(
                tasks=[
                    TaskSpec(
                        id="bracket", objective=objective, capability="cad", allowed_paths=["bracket.py"]
                    )
                ]
            )

    class Specialist:
        async def execute(self, scope, task, context):
            return TaskResult(task_id=task.id, status="succeeded", summary="Fixture candidate")

    async with postgres_runtime(url, setup=True) as (saver, memory):
        await memory.aput(("tenant", project), "fact", {"material": "PETG", "source": "fixture"})
        worker = ObjectiveWorker(
            store, lambda _: ObjectiveEngine(repo, Planner(), Specialist(), checkpointer=saver, store=memory)
        )
        assert await worker.process(run)
        with store.transaction() as session:
            saved = session.get(Objective, run)
            assert saved.status == "waiting", saved.data.get("blocker")
            interrupt_id, pinned_scope = saved.data["interruptId"], saved.data["scope"]
        worker.answer(actor, project, run, interrupt_id, "PETG", 2, "answer")
    # Every checkpoint and memory connection is closed before reconstruction.
    async with postgres_runtime(url) as (saver, memory):
        assert (await memory.aget(("tenant", project), "fact")).value["material"] == "PETG"
        assert await memory.aget(("other-tenant", project), "fact") is None
        worker = ObjectiveWorker(
            store, lambda _: ObjectiveEngine(repo, Planner(), Specialist(), checkpointer=saver, store=memory)
        )
        assert await worker.process(run)
        with store.transaction() as session:
            saved = session.get(Objective, run)
            assert saved.status == "succeeded", saved.data.get("blocker")
            assert saved.data["scope"] == pinned_scope
        assert calls == [None, "PETG", None]
    with store.engine.begin() as connection:
        migration = Config(str(Path(__file__).parents[1] / "alembic.ini"))
        migration.attributes["connection"] = connection
        command.check(migration)  # LangGraph-owned tables must not become Alembic drops.
