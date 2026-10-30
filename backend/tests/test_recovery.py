"""Real process-kill recovery with PostgreSQL and Pydantic AI DBOS checkpoints."""
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
from dbos import DBOSClient
from sqlalchemy import func, select
from test_postgres import postgres as postgres

from kleos.contracts import Command
from kleos.db import Artifact, Attempt, DailyBudget, Event, Invocation, Project, Run, Task
from kleos.storage import digest

pytestmark = pytest.mark.skipif(not os.getenv("KLEOS_TEST_POSTGRES_URL"), reason="PostgreSQL opt-in required")


def wait_until(predicate, process, log, timeout=45):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        if process.poll() is not None:
            pytest.fail(f"Worker exited before its checkpoint ({process.returncode}):\n{log.read_text()[-8000:]}")
        time.sleep(0.05)
    pytest.fail(f"Worker did not reach checkpoint within {timeout}s:\n{log.read_text()[-8000:]}")


def test_sigkill_after_model_checkpoint_recovers_without_repeat_call_or_publication(postgres, tmp_path):
    project = postgres.control.command(postgres.principal, Command(
        type="project.create", payload={"brief": "Crash recovery fixture"}), "create")
    # Source publication belongs to an engineering lane. Research/coordination
    # deliberately prohibit generated code after the live scope regression fix.
    with postgres.db.transaction() as session:
        fixture = session.get(Project, project["projectId"])
        fixture.state, fixture.interfaces = {"confirmed": True}, {"firmware": {"board": "native_sim"}}
    run = postgres.control.command(postgres.principal, Command(type="project.generate", payload={
        "projectId": project["projectId"], "lane": "mechanics"}, expected_revision=project["revision"]), "generate")
    with postgres.db.transaction() as session:
        task_id = session.scalar(select(Task.id).where(Task.run_id == run["runId"]))
    checkpoint, audit = tmp_path / "checkpoint", tmp_path / "provider-calls"
    config = tmp_path / "worker.json"
    config.write_text(json.dumps({
        "databaseUrl": postgres.settings.database_url,
        "dbosDatabaseUrl": postgres.settings.dbos_database_url,
        "artifactDir": str(postgres.settings.local_artifact_dir), "runId": run["runId"],
        "reservationUsd": postgres.settings.invocation_reservation_usd,
        "metaModel": postgres.settings.meta_model,
        "checkpoint": str(checkpoint), "auditFile": str(audit),
    }))
    helper = Path(__file__).with_name("_recovery_worker.py")
    children = []
    client = None
    try:
        first_log = tmp_path / "first-worker.log"
        with first_log.open("w") as output:
            first = subprocess.Popen([sys.executable, str(helper), str(config), "pause"],
                cwd=Path(__file__).parents[1], stdout=output, stderr=subprocess.STDOUT)
        children.append(first)
        wait_until(checkpoint.exists, first, first_log)
        attempt_id = checkpoint.read_text()
        client = DBOSClient(system_database_url=postgres.settings.dbos_database_url, application_name="kleos")
        before = client.list_workflow_steps(f"task:{task_id}", load_output=False)
        assert any("kleos_meta" in step["function_name"] and "request" in step["function_name"] for step in before)
        assert any(step["function_name"].endswith("complete_invocation") for step in before)
        assert not any(step["function_name"].endswith("store_candidate") for step in before)
        with postgres.db.transaction() as session:
            assert session.scalar(select(func.count(Artifact.id))) == 0
            assert session.get(Attempt, attempt_id).status == "running"
            assert session.get(Run, run["runId"]).status == "running"
            assert "proposal" not in session.get(Project, project["projectId"]).state
            invocation = session.scalar(select(Invocation))
            assert invocation.status == "succeeded"
            invocation_id = invocation.id

        first.kill()  # SIGKILL: no Python cleanup or graceful DBOS shutdown.
        assert first.wait(timeout=10) == -signal.SIGKILL
        second_log = tmp_path / "second-worker.log"
        with second_log.open("w") as output:
            second = subprocess.Popen([sys.executable, str(helper), str(config), "resume"],
                cwd=Path(__file__).parents[1], stdout=output, stderr=subprocess.STDOUT)
        children.append(second)
        wait_until(lambda: second.poll() is not None, second, second_log, timeout=60)
        assert second.returncode == 0, second_log.read_text()
        assert audit.read_text().splitlines() == ["model.request"]

        after = client.list_workflow_steps(f"task:{task_id}", load_output=False)
        assert {(s["function_id"], s["completed_at_epoch_ms"]) for s in before}.issubset(
            {(s["function_id"], s["completed_at_epoch_ms"]) for s in after})
        with postgres.db.transaction() as session:
            assert session.get(Run, run["runId"]).status == "succeeded"
            assert session.get(Attempt, attempt_id).status == "succeeded"
            assert session.scalar(select(func.count(Attempt.id))) == 1
            assert session.scalar(select(func.count(Invocation.id))) == 1
            assert session.get(Invocation, invocation_id).status == "succeeded"
            artifacts = session.scalars(select(Artifact)).all()
            assert len(artifacts) == 1
            artifact = artifacts[0]
            assert artifact.attempt_id == attempt_id
            assert artifact.status == "candidate"
            assert postgres.store.get(artifact.key) == b"durable candidate\n"
            assert artifact.sha256 == digest(b"durable candidate\n")
            current = session.get(Project, project["projectId"])
            assert current.stage == 5
            assert session.get(Task, task_id).result["summary"] == "Recovered candidate concept"
            assert current.accepted_release_id is None  # Generators cannot approve releases.
            assert current.spent_usd == pytest.approx(postgres.settings.invocation_reservation_usd)
            assert current.reserved_usd == 0
            daily = session.scalar(select(DailyBudget))
            assert daily.spent == pytest.approx(current.spent_usd)
            assert daily.reserved == 0
            for kind in ["model.completed", "artifact.created", "task.completed", "run.succeeded"]:
                assert session.scalar(select(func.count(Event.id)).where(Event.type == kind)) == 1
    finally:
        for process in children:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)
        if client:
            client.destroy()
