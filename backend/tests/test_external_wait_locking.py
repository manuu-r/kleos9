"""Real PostgreSQL lock-order regression; all external provider IO is a local fixture."""
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from test_external_waits import ReadProvider
from test_postgres_runtime import database, objective  # noqa: F401

from kleos.capabilities.external import ExternalJob, ExternalJobs, ExternalRequest
from kleos.contracts.models import TaskResult, TaskSpec
from kleos.orchestration.engine import RunScope
from kleos.orchestration.external_waits import ExternalWait, ExternalWaits
from kleos.projects.graph_repository import GraphRepository
from kleos.projects.store import Objective


async def test_project_locked_bridge_reads_without_waiting_on_job_locked_mutator(database, monkeypatch):  # noqa: F811
    store, _ = database
    actor, project_id, run_id = objective(store)
    scope = RunScope(actor=actor, project_id=project_id, run_id=run_id, revision=2,
        deadline_at=datetime.now(UTC)+timedelta(days=7))
    task = TaskSpec(id='observe', capability='sourcing', objective='Observe the linked supplier inquiry')
    repo = GraphRepository(store)
    await repo.publish_plan(scope, tasks=[task], superseded={}, turn=0, summary='Observe only', question=None)
    provider = ReadProvider()
    jobs = ExternalJobs(store, {('tenant', 'owner', 'fixture'): provider},
        installation_pins={('tenant', 'owner', 'fixture'): 'a'*64})
    job_id = jobs.create(actor, ExternalRequest(project_id=project_id, revision=2, kind='sourcing', provider='fixture',
        objective='Observe an already owned supplier inquiry',
        bom=[{'reference': 'J1', 'supplierPart': 'ABC', 'quantity': 1}]), 'job')['job']['id']
    await jobs.observe_existing(actor, job_id, 'owned', 'link')
    with store.transaction() as session:
        run = session.get(Objective, run_id)
        run.data = {**run.data, 'scope': scope.model_dump(mode='json'),
            'runtimePins': {'external': [{'provider': 'fixture', 'installationHash': 'a'*64}]}}
    bridge = ExternalWaits(store, repo, external_jobs=jobs)
    wait_id = await bridge.bind(scope, task, 'sourcing', job_id)
    await repo.publish(scope, TaskResult(task_id=task.id, status='waiting', summary='Awaiting supplier observation', wait_ids=[wait_id]))
    with store.transaction() as session:
        run = session.get(Objective, run_id)
        run.status = 'waiting'
        run.data = {**run.data, 'interruptId': 'test-interrupt',
            'question': {'kind': 'external_wait', 'waitIds': [wait_id]}}

    about_to_lock_project = threading.Event()
    mutator_pid = []
    original_authorize = store.authorized
    def authorize(session, *args, **kwargs):
        if threading.current_thread().name.startswith('external-mutator'):
            # ExternalJobs._load has already acquired the job's FOR UPDATE lock.
            session.execute(text("SET LOCAL lock_timeout = '3s'"))
            session.execute(text("SET LOCAL statement_timeout = '5s'"))
            mutator_pid.append(session.scalar(text('SELECT pg_backend_pid()')))
            about_to_lock_project.set()
        return original_authorize(session, *args, **kwargs)
    monkeypatch.setattr(store, 'authorized', authorize)
    calls = provider.calls
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix='external-mutator') as pool:
        with store.transaction() as session:
            session.execute(text("SET LOCAL lock_timeout = '750ms'"))
            session.execute(text("SET LOCAL statement_timeout = '2s'"))
            store.authorized(session, actor, project_id, write=True)
            bridge_pid = session.scalar(text('SELECT pg_backend_pid()'))
            mutation = pool.submit(jobs.cancel, actor, job_id, 'cancel-concurrently')
            assert about_to_lock_project.wait(2), 'Mutator never reached its project lock'
            # Verify actual PostgreSQL blocking, rather than inferring it from scheduling.
            with store.engine.connect() as observer:
                deadline = time.monotonic()+2
                while bridge_pid not in observer.scalar(text('SELECT pg_blocking_pids(:pid)'), {'pid': mutator_pid[0]}):
                    assert time.monotonic() < deadline, 'Mutator did not block on the bridge project lock'
                    time.sleep(.01)
            assert not mutation.done()
            wait = session.get(ExternalWait, wait_id)
            _, _, material, _ = bridge._guard(session, wait)
            assert material['status'] == 'waiting'  # committed snapshot, not the uncommitted cancellation
            assert not mutation.done()  # project lock is still held; no job-lock inversion occurred
        cancelled = mutation.result(timeout=5)
    assert cancelled['job']['status'] == 'cancelled'
    assert cancelled['externalCancellationConfirmed'] is False
    assert provider.calls == calls  # no remote cancellation or observation request
    with store.transaction() as session:
        assert session.get(ExternalJob, job_id).status == 'cancelled'
    assert bridge._refresh(wait_id) is True
    with store.transaction() as session:
        assert session.get(ExternalWait, wait_id).status == 'ready'
