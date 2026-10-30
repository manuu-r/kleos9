"""DBOS workflow registrations and the durable application outbox dispatcher."""
import argparse
import asyncio
import logging
import signal

from dbos import DBOS, DBOSClient
from sqlalchemy import select

from .config import get_settings
from .contracts import DomainError
from .db import Database, NativeJob, Outbox, Run, Task, now
from .engine import EngineeringEngine
from .models import ModelGateway
from .storage import ArtifactStore
from .telemetry import configure, linked_span, metric

log = logging.getLogger(__name__)
engine: EngineeringEngine | None = None


@DBOS.workflow()
async def engineering_task(task_id: str):
    run_id, trace_id = task_run(task_id)
    with linked_span("engineering.task", trace_id, **{"kleos.task.id": task_id, "kleos.run.id": run_id}):
        await engine.run_task(task_id)
        engine.finish_run(run_id)


@DBOS.step()
def task_run(task_id):
    with engine.db.transaction() as session:
        task = session.get(Task, task_id)
        run = session.get(Run, task.run_id)
        return run.id, run.trace_id


@DBOS.workflow()
async def engineering_run(run_id: str):
    task_ids = engine.admit_run(run_id)
    while task_ids is None:
        await DBOS.sleep_async(5)
        task_ids = engine.admit_run(run_id)
    if not task_ids:
        return
    from dbos import SetWorkflowID
    handles = []
    for task_id in task_ids:
        with SetWorkflowID(f"task:{task_id}"):
            handles.append(await DBOS.enqueue_workflow_async("engineering", engineering_task, task_id))
    for handle in handles:
        await handle.get_result()
    engine.finish_run(run_id)


@DBOS.workflow()
async def index_source(source_id: str):
    await index_with_capacity(index_source_once, source_id, DBOS.sleep_async)


@DBOS.step()
async def index_source_once(source_id: str):
    from .memory import MemoryIndex
    await asyncio.to_thread(MemoryIndex(engine.db, engine.settings).index, source_id)


async def index_with_capacity(index, source_id, sleep, polls=150):
    for attempt in range(polls + 1):
        try:
            return await index(source_id)
        except DomainError as exc:
            # Admission refused before a paid call. Uncertain provider outcomes
            # remain operator-reconciled and must never enter this retry loop.
            if exc.code != "model_capacity" or attempt == polls:
                raise
            await sleep(2)


@DBOS.workflow()
async def delete_source(source_id: str):
    from .memory import MemoryIndex
    await asyncio.to_thread(MemoryIndex(engine.db, engine.settings).delete_points, [source_id])


@DBOS.step()
async def delete_project_once(project_id: str):
    from .maintenance import Maintenance
    def purge():
        maintenance = Maintenance(engine.db, engine.settings, engine.store)
        maintenance.mark_deletion_started(project_id)
        return maintenance.purge_project(project_id)
    return await asyncio.to_thread(purge)


@DBOS.step()
async def record_deletion_failure(project_id, error_type, attempt, exhausted):
    from .maintenance import Maintenance
    await asyncio.to_thread(Maintenance(engine.db, engine.settings, engine.store).record_deletion_failure,
                            project_id, error_type, attempt, exhausted)


async def delete_with_retry(project_id, purge, sleep, record_failure, attempts=5):
    # Deletions are idempotent. Durable bounded retries handle brief outages;
    # the tombstone schedules a fresh workflow after a longer outage.
    for attempt in range(attempts):
        try:
            return await purge(project_id)
        except Exception as exc:
            exhausted = attempt == attempts - 1
            await record_failure(project_id, type(exc).__name__, attempt + 1, exhausted)
            if exhausted:
                return {"status": "retry_pending"}
            await sleep(2 ** attempt)


@DBOS.workflow()
async def delete_project(project_id: str):
    return await delete_with_retry(project_id, delete_project_once, DBOS.sleep_async, record_deletion_failure)


@DBOS.workflow()
async def retention():
    from .maintenance import Maintenance
    from .training_consent import reconcile_training_cancellations
    result = await asyncio.to_thread(Maintenance(engine.db, engine.settings, engine.store).retention)
    result["trainingCancellationsQueued"] = await asyncio.to_thread(reconcile_training_cancellations, engine.db)
    return result


@DBOS.step()
async def cancel_training_once(intent_id: str):
    from .learning import ModalCompute
    await ModalCompute(engine.db, engine.settings, engine.store).cancel_training(intent_id)


@DBOS.workflow()
async def cancel_training(intent_id: str):
    for attempt in range(4):
        try:
            await cancel_training_once(intent_id)
            return
        except Exception:
            if attempt == 3:
                raise
            await DBOS.sleep_async(2 ** attempt)


def finish_native_with_trace(job_id, result):
    # Native jobs outlive their submitting workflow step. Rejoin the persisted
    # run trace when their independent completion is reconciled after a restart.
    with engine.db.transaction() as session:
        job = session.get(NativeJob, job_id)
        task = session.get(Task, job.task_id)
        run = session.get(Run, task.run_id)
        trace_id, run_id, task_id = run.trace_id, run.id, task.id
    with linked_span("native.reconcile", trace_id, **{
        "kleos.run.id": run_id, "kleos.task.id": task_id, "kleos.native_job.id": job_id,
    }):
        return engine.finish_native(job_id, result)


@DBOS.workflow()
async def process_webhook(inbox_id: str):
    from .connectors import ConnectorService
    await asyncio.to_thread(ConnectorService(engine.db, engine.settings).process_webhook, inbox_id)


class Dispatcher:
    def __init__(self, db, settings, client, execution=None):
        self.db, self.settings, self.client, self.execution = db, settings, client, execution

    def once(self, kinds=None):
        from .maintenance import Maintenance
        maintenance = Maintenance(self.db, self.settings, None)
        maintenance.schedule_deletion_retries()
        maintenance.reclassify_failed_invocations()
        count = 0
        with self.db.transaction() as session:
            query = select(Outbox).where(Outbox.delivered_at.is_(None)).order_by(Outbox.created_at).limit(25)
            if kinds:
                query = query.where(Outbox.kind.in_(kinds))
            rows = session.scalars(query.with_for_update(skip_locked=True)).all()
            for item in rows:
                try:
                    if item.kind == "run":
                        self.client.enqueue({
                            "workflow_name": "engineering_run", "queue_name": "coordination",
                            "workflow_id": item.key, "app_version": self.settings.dbos_application_version,
                        }, item.payload["runId"])
                    elif item.kind == "task":
                        self.client.enqueue({
                            "workflow_name": "engineering_task", "queue_name": "engineering",
                            "workflow_id": item.key, "app_version": self.settings.dbos_application_version,
                        }, item.payload["taskId"])
                    elif item.kind in {"index_source", "delete_source"}:
                        self.client.enqueue({
                            "workflow_name": item.kind, "queue_name": "connectors",
                            "workflow_id": item.key, "app_version": self.settings.dbos_application_version,
                        }, item.payload["sourceId"])
                    elif item.kind == "webhook":
                        self.client.enqueue({
                            "workflow_name": "process_webhook", "queue_name": "connectors",
                            "workflow_id": item.key, "app_version": self.settings.dbos_application_version,
                        }, item.payload["inboxId"])
                    elif item.kind == "delete_project":
                        self.client.enqueue({
                            "workflow_name": "delete_project", "queue_name": "connectors",
                            "workflow_id": item.key, "app_version": self.settings.dbos_application_version,
                        }, item.payload["projectId"])
                    elif item.kind == "cancel_training":
                        self.client.enqueue({
                            "workflow_name": "cancel_training", "queue_name": "connectors",
                            "workflow_id": item.key, "app_version": self.settings.dbos_application_version,
                        }, item.payload["intentId"])
                    elif item.kind == "cancel":
                        if self.execution:
                            self.execution.cancel_run(item.payload["runId"])
                    else:
                        continue
                    item.delivered_at = now()
                    count += 1
                except Exception as exc:
                    item.failures += 1
                    log.error("Outbox %s failed (%s)", item.id, type(exc).__name__)
        return count


async def serve(kind="all", drain=False):
    global engine
    settings = get_settings()
    configure(settings.otlp_endpoint, settings)
    db, store = Database(settings.database_url), ArtifactStore(settings)
    DBOS(config={"name": "kleos", "system_database_url": settings.dbos_database_url,
                 "application_version": settings.dbos_application_version},
         conductor_url=settings.conductor_url or None,
         conductor_key=settings.conductor_key.get_secret_value() if settings.conductor_key else None)
    models = ModelGateway(settings, durable=True)
    from .connectors import ResearchService
    from .execution import execution_factory
    from .memory import MemoryIndex
    execution = execution_factory(db, settings, store)
    engine = EngineeringEngine(db, settings, store, models, execution, ResearchService(db), MemoryIndex(db, settings))
    queues = ["coordination", "engineering"] if kind == "engineering" else ["connectors"] if kind == "connectors" else [
        "coordination", "engineering", "connectors"]
    DBOS.listen_queues(queues)
    DBOS.launch()
    await DBOS.register_queue_async("coordination", worker_concurrency=100)
    await DBOS.register_queue_async("engineering", worker_concurrency=min(10, settings.max_model_calls),
                                    global_concurrency=settings.max_model_calls)
    await DBOS.register_queue_async("connectors", worker_concurrency=4, global_concurrency=4)
    client = DBOSClient(system_database_url=settings.dbos_database_url, application_name="kleos")
    dispatcher = Dispatcher(db, settings, client, execution)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    try:
        ticks = 0
        while not stop.is_set():
            ticks += 1
            if ticks % 15 == 1:
                metric("WorkerHeartbeat", 1, WorkerKind=kind, Environment=settings.environment)
            try:
                if kind in {"engineering", "all"} and ticks % 15 == 1:
                    from .operations import report
                    await asyncio.to_thread(report, db, settings)
                if not drain:
                    await asyncio.to_thread(dispatcher.once)
                if not drain and kind in {"connectors", "all"} and ticks % 1800 == 1:
                    from datetime import UTC, datetime
                    await client.enqueue_async({
                        "workflow_name": "retention", "queue_name": "connectors",
                        "workflow_id": f"retention:{datetime.now(UTC).strftime('%Y-%m-%d-%H')}",
                        "app_version": settings.dbos_application_version,
                    })
                if kind != "connectors" and settings.execution_backend in {"ecs", "docker"}:
                    await asyncio.to_thread(execution.dispatch_pending)
                    completions = await asyncio.to_thread(execution.reconcile)
                    for job_id, result in completions:
                        try:
                            await asyncio.to_thread(finish_native_with_trace, job_id, result)
                        except Exception as exc:
                            metric("ReconciliationFailure", 1, Environment=settings.environment)
                            log.error("Job %s reconciliation deferred (%s)", job_id, type(exc).__name__)
            except Exception as exc:
                metric("ReconciliationFailure", 1, Environment=settings.environment)
                log.error("Worker reconciliation failed (%s); retrying", type(exc).__name__)
            try:
                await asyncio.wait_for(stop.wait(), timeout=2)
            except TimeoutError:
                pass
    finally:
        await asyncio.to_thread(DBOS.destroy)
        client.destroy()
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--kind", choices=["all", "engineering", "connectors"], default="all")
    parser.add_argument("--drain", action="store_true", help="Finish pinned workflows without dispatching new commands")
    args = parser.parse_args()
    asyncio.run(serve(args.kind, args.drain))
