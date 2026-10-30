"""Opt-in integration checks against disposable databases on a local PostgreSQL server."""
import asyncio
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import psycopg
import pytest
from dbos import DBOS, DBOSClient
from psycopg import sql
from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from test_control_plane import FakeModels

from kleos.auth import Principal
from kleos.config import Settings
from kleos.contracts import Command
from kleos.db import Base, CommandReceipt, Database, Invocation, Organization, Project, Run
from kleos.domain import ControlPlane
from kleos.engine import EngineeringEngine
from kleos.storage import ArtifactStore

pytestmark = pytest.mark.skipif(not os.getenv("KLEOS_TEST_POSTGRES_URL"), reason="PostgreSQL opt-in required")


@pytest.fixture
def postgres(tmp_path):
    base = make_url(os.environ["KLEOS_TEST_POSTGRES_URL"])
    names = [f"kleos_test_{uuid4().hex[:12]}", f"kleos_dbos_test_{uuid4().hex[:12]}"]
    admin = base.set(drivername="postgresql").render_as_string(hide_password=False)
    with psycopg.connect(admin, autocommit=True) as connection:
        for name in names:
            connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    settings = Settings(environment="test", database_url=base.set(
        drivername="postgresql+psycopg", database=names[0]).render_as_string(hide_password=False),
        dbos_database_url=base.set(drivername="postgresql", database=names[1]).render_as_string(hide_password=False),
        local_artifact_dir=tmp_path, project_budget_usd=20, daily_budget_usd=100)
    db = Database(settings.database_url)
    Base.metadata.create_all(db.engine)
    with db.transaction() as session:
        session.add(Organization(id="org", name="Integration"))
    store = ArtifactStore(settings)
    yield SimpleNamespace(db=db, settings=settings, store=store, principal=Principal("test", "org", "owner"),
                          control=ControlPlane(db, settings, store))
    db.close()
    with psycopg.connect(admin, autocommit=True) as connection:
        for name in names:
            connection.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


def test_concurrent_duplicate_commands_are_one_transaction(postgres):
    request = Command(type="project.create", payload={"brief": "Concurrent retry fixture"})
    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(lambda _: postgres.control.command(postgres.principal, request, "same-key"), range(24)))
    assert all(r == results[0] for r in results)
    with postgres.db.transaction() as session:
        assert session.scalar(select(func.count(Project.id))) == 1
        assert session.scalar(select(func.count(CommandReceipt.id))) == 1


def test_concurrent_migration_is_refused(postgres):
    connection_url = make_url(postgres.settings.database_url).set(drivername="postgresql").render_as_string(hide_password=False)
    with psycopg.connect(connection_url, autocommit=True) as connection:
        connection.execute("SELECT pg_advisory_lock(84922420)")
        result = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=Path(__file__).parents[1], env={**os.environ, "KLEOS_DATABASE_URL": postgres.settings.database_url},
            capture_output=True, text=True, timeout=20)
        assert result.returncode != 0
        assert "Another migration is running" in result.stderr


@pytest.mark.asyncio
async def test_outbox_runs_registered_dbos_workflows(postgres):
    import kleos.worker as worker
    DBOS(config={"name": "kleos", "system_database_url": postgres.settings.dbos_database_url,
                 "application_version": postgres.settings.dbos_application_version})
    models = FakeModels()
    worker.engine = EngineeringEngine(postgres.db, postgres.settings, postgres.store, models)
    DBOS.listen_queues(["coordination", "engineering"])
    DBOS.launch()
    await DBOS.register_queue_async("coordination", worker_concurrency=2)
    await DBOS.register_queue_async("engineering", worker_concurrency=2, global_concurrency=2)
    client = DBOSClient(system_database_url=postgres.settings.dbos_database_url, application_name="kleos")
    try:
        project = postgres.control.command(postgres.principal, Command(
            type="project.create", payload={"brief": "Durable research fixture"}), "create")
        run = postgres.control.command(postgres.principal, Command(type="project.research", payload={
            "projectId": project["projectId"]}, expected_revision=project["revision"]), "research")
        dispatcher = worker.Dispatcher(postgres.db, postgres.settings, client)
        assert await asyncio.to_thread(dispatcher.once) == 1
        handle = await client.retrieve_workflow_async(f"run:{run['runId']}")
        await asyncio.wait_for(handle.get_result(), timeout=30)
        assert await asyncio.to_thread(dispatcher.once) == 0
        with postgres.db.transaction() as session:
            assert session.get(Run, run["runId"]).status == "succeeded"
            assert session.scalar(select(func.count(Invocation.id))) == 1
        steps = await client.list_workflow_steps_async(f"run:{run['runId']}")
        assert steps
        assert models.calls == 1
    finally:
        client.destroy()
        await asyncio.to_thread(DBOS.destroy)
        worker.engine = None
