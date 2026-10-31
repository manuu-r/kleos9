"""Subprocess entry point for the PostgreSQL/DBOS crash drill, never production."""
import asyncio
import json
import os
import sys
import time
import traceback
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch

from dbos import DBOS, DBOSClient
from pydantic_ai import models as ai_models
from pydantic_ai.models.test import TestModel

import kleos.worker as worker
from kleos.config import Settings
from kleos.db import Database, Run
from kleos.engine import EngineeringEngine
from kleos.models import ModelGateway
from kleos.storage import ArtifactStore


class AuditedTestModel(TestModel):
    def __init__(self, audit_file):
        self.audit_file = audit_file
        super().__init__(call_tools=[], custom_output_text=json.dumps({
            "summary": "Recovered candidate concept",
            "files": [{"path": "concept.txt", "content": "durable candidate\n"}],
            "limitations": ["Test model; no live inference or physical evidence"],
        }))

    def record_request(self):
        # External to either database: this proves provider transport is not
        # called twice even if application accounting is itself idempotent.
        with self.audit_file.open("a") as audit:
            audit.write("model.request\n")
            audit.flush()
            os.fsync(audit.fileno())

    async def request(self, messages, model_settings, model_request_parameters):
        self.record_request()
        return await super().request(messages, model_settings, model_request_parameters)

    @asynccontextmanager
    async def request_stream(self, messages, model_settings, model_request_parameters, run_context=None):
        self.record_request()
        async with super().request_stream(messages, model_settings, model_request_parameters, run_context) as response:
            yield response


class CrashBoundaryEngine(EngineeringEngine):
    def __init__(self, *args, checkpoint, stop_before_candidate, **kwargs):
        super().__init__(*args, **kwargs)
        self.checkpoint = checkpoint
        self.stop_before_candidate = stop_before_candidate

    def block_task(self, *args, **kwargs):
        traceback.print_exc()
        return super().block_task(*args, **kwargs)

    def store_candidate(self, attempt_id, output, _session=None):
        if self.stop_before_candidate:
            # complete_invocation and its DBOS checkpoint have returned. No
            # artifact or project proposal has been published at this point.
            self.checkpoint.write_text(attempt_id)
            while True:
                time.sleep(1)
        return super().store_candidate(attempt_id, output, _session=_session)

    def request_native(self, attempt_id, purpose):
        # This drill isolates the durable model/candidate boundary. Actual native
        # containers have separate integration checks; no validation is fabricated.
        self.finish_attempt(attempt_id, {"summary": "Recovered candidate concept",
                                        "nativeExecution": "not exercised by checkpoint test"})


async def main(config_file, mode):
    config = json.loads(Path(config_file).read_text())
    settings = Settings(
        _env_file=None, environment="test", database_url=config["databaseUrl"],
        dbos_database_url=config["dbosDatabaseUrl"], dbos_application_version="recovery-drill-v1",
        local_artifact_dir=Path(config["artifactDir"]), storage_backend="local",
        project_budget_usd=20, daily_budget_usd=100, execution_backend="disabled",
        meta_api_key="fixture-only-never-sent", gemini_api_key=None,
        invocation_reservation_usd=config["reservationUsd"], meta_model=config["metaModel"],
    )
    ai_models.ALLOW_MODEL_REQUESTS = False
    db = Database(settings.database_url)
    store = ArtifactStore(settings)
    DBOS(config={"name": "kleos", "system_database_url": settings.dbos_database_url,
                 "application_version": settings.dbos_application_version, "executor_id": "crash-drill"})
    # Exercise the real ModelGateway and its DBOSDurability capability; only
    # provider transport is replaced by Pydantic AI's documented TestModel.
    with patch("kleos.models.OpenAIResponsesModel", return_value=AuditedTestModel(Path(config["auditFile"]))):
        gateway = ModelGateway(settings, durable=True)
    worker.engine = CrashBoundaryEngine(db, settings, store, gateway,
        checkpoint=Path(config["checkpoint"]), stop_before_candidate=mode == "pause")
    DBOS.listen_queues(["coordination", "engineering"])
    DBOS.launch()
    await DBOS.register_queue_async("coordination", worker_concurrency=2)
    await DBOS.register_queue_async("engineering", worker_concurrency=2, global_concurrency=2)
    client = DBOSClient(system_database_url=settings.dbos_database_url, application_name="kleos")
    dispatcher = worker.Dispatcher(db, settings, client)
    try:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            await asyncio.to_thread(dispatcher.once)
            with db.transaction() as session:
                run = session.get(Run, config["runId"])
                if run.status == "succeeded":
                    handle = await client.retrieve_workflow_async(f"run:{run.id}")
                    await asyncio.wait_for(handle.get_result(), 15)
                    return
                if run.status in {"blocked", "failed", "cancelled"}:
                    raise RuntimeError(f"Recovery run ended {run.status}: {run.blocker}")
            await asyncio.sleep(0.1)
        raise TimeoutError("Recovery workflow did not finish within 60 seconds")
    finally:
        client.destroy()
        await asyncio.to_thread(DBOS.destroy)
        db.close()


if __name__ == "__main__":
    asyncio.run(main(*sys.argv[1:]))
