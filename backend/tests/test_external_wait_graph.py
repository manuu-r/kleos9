"""Real LangGraph checkpoint/worker integration with fixture external GETs only."""
from datetime import UTC, datetime

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from test_external_waits import ReadProvider
from test_worker import FixtureLocks

from kleos.capabilities.external import ExternalJobs, ExternalRequest, ProviderObservation
from kleos.contracts.models import Actor, DomainError, TaskResult, TaskSpec
from kleos.infrastructure.schema import metadata
from kleos.orchestration.engine import ObjectiveEngine, PlanDecision
from kleos.orchestration.external_waits import ExternalWaits
from kleos.orchestration.worker import ObjectiveWorker
from kleos.projects.graph_repository import GraphRepository
from kleos.projects.store import Objective, ProjectStore


@pytest.fixture
async def graph_waits(tmp_path):
    store = ProjectStore('sqlite:///'+str(tmp_path/'graph.db'))
    metadata().create_all(store.engine)
    actor = Actor(tenant_id='tenant', user_id='owner')
    project = store.create(actor, 'Observe two existing scoped jobs', 'create')['projectId']
    submitted = store.submit(actor, project, 'Continue once observations arrive', 1, 'submit')
    clock = [datetime.now(UTC).timestamp()]
    provider = ReadProvider()
    jobs = ExternalJobs(store, {('tenant', 'owner', 'fixture'): provider},
        installation_pins={('tenant', 'owner', 'fixture'): 'a'*64}, clock=lambda: clock[0])
    job_ids = {}
    for task_id in ('first', 'second'):
        request = ExternalRequest(project_id=project, revision=2, kind='sourcing', provider='fixture',
            objective='Observe '+task_id, bom=[{'reference': task_id, 'supplierPart': 'ABC', 'quantity': 1}])
        job_ids[task_id] = jobs.create(actor, request, 'job-'+task_id)['job']['id']
        await jobs.observe_existing(actor, job_ids[task_id], 'owned', 'link-'+task_id)
    provider.calls = 0
    with store.transaction() as session:
        run = session.get(Objective, submitted['runId'])
        run.data = {**run.data, 'runtimePins': {'external': [{'provider': 'fixture', 'installationHash': 'a'*64}],
                    'modelPolicy': {'maxCalls': 10, 'maxCostMicroUsd': 100}}}
    repo, saver, locks = GraphRepository(store), InMemorySaver(), FixtureLocks()
    bridge = ExternalWaits(store, repo, external_jobs=jobs, clock=lambda: clock[0])
    class Planner:
        calls = 0
        async def plan(self, scope, objective, context, tasks, results, answer, *, superseded):
            self.calls += 1
            if not tasks:
                return PlanDecision(tasks=[TaskSpec(id=key, capability='sourcing', objective='Observe '+key) for key in job_ids])
            replacements = [item for item in results if item.status.value == 'blocked' and item.task_id not in superseded]
            if replacements:
                return PlanDecision(tasks=[TaskSpec(id=item.task_id+'-continued', capability='sourcing',
                    objective='Review the available observation') for item in replacements],
                    supersedes={item.task_id: item.task_id+'-continued' for item in replacements})
            return PlanDecision(done=True, summary='Fixture observation review complete; no physical work')
    class Specialist:
        calls = []
        async def execute(self, scope, task, context):
            self.calls.append(task.id)
            if task.id in job_ids:
                ident = await bridge.bind(scope, task, 'sourcing', job_ids[task.id])
                return TaskResult(task_id=task.id, status='waiting', summary='Wait for scoped existing work', wait_ids=[ident])
            return TaskResult(task_id=task.id, status='succeeded', summary='Fixture reviewed the external observation')
    planner, specialist = Planner(), Specialist()
    def worker(current_bridge):
        def factory(scope):
            return ObjectiveEngine(repo, planner, specialist, checkpointer=saver, external_waits=current_bridge)
        return ObjectiveWorker(store, factory, locks=locks, heartbeat_seconds=.01,
            max_run_seconds=120, max_lifetime_seconds=7*86400)
    yield store, repo, bridge, jobs, provider, actor, project, submitted['runId'], clock, planner, specialist, worker, locks
    store.engine.dispose()


async def test_actual_graph_partial_waits_restart_noop_polls_and_duplicate_events(graph_waits):
    store, repo, bridge, jobs, provider, actor, project, run_id, clock, planner, specialist, make_worker, locks = graph_waits
    assert await make_worker(bridge).process(run_id)
    assert planner.calls == 1 and sorted(specialist.calls) == ['first', 'second']
    assert not locks.active
    with store.transaction() as session:
        run = session.get(Objective, run_id)
        assert run.status == 'waiting' and run.data['question']['kind'] == 'external_wait'
        original_scope, pins, budget = run.data['scope'], run.data['runtimePins'], run.data['activeBudget']
        assert budget['lease'] is None and budget['committedMs'] > 0
        interrupt = run.data['interruptId']
    with pytest.raises(DomainError):
        make_worker(bridge).answer(actor, project, run_id, interrupt, 'pretend complete', 2, 'forged')
    for _ in range(2):
        clock[0] += 61
        assert (await bridge.poll_due())['woken'] == 0
        assert not await make_worker(bridge).process(run_id)
    assert planner.calls == 1
    with store.transaction() as session:
        assert session.get(Objective, run_id).data['activeBudget'] == budget
    # Each job gets its own actual saved observation; first completion must not complete second.
    ids = [row['id'] for row in jobs.list(actor, project)]
    from kleos.capabilities.external import ExternalJob
    with store.transaction() as session:
        job = session.get(ExternalJob, ids[0])
        generation = job.generation
    jobs._record(actor, ids[0], generation, ProviderObservation(state='completed', external_id='owned', ownership_verified=True))
    clock[0] += 61
    fresh = ExternalWaits(store, repo, external_jobs=jobs, clock=lambda: clock[0])
    assert (await fresh.poll_due())['woken'] == 1
    assert fresh.wake_ready() == 0
    assert await make_worker(fresh).process(run_id)
    assert planner.calls == 2 and not locks.active
    with store.transaction() as session:
        run = session.get(Objective, run_id)
        assert run.status == 'waiting'
        assert run.data['scope'] == original_scope and run.data['runtimePins'] == pins
        assert run.data['activeBudget']['committedMs'] >= budget['committedMs']
    with store.transaction() as session:
        generation = session.get(ExternalJob, ids[1]).generation
    jobs._record(actor, ids[1], generation, ProviderObservation(state='completed', external_id='owned', ownership_verified=True))
    clock[0] += 61
    assert (await fresh.poll_due())['woken'] == 1
    assert await make_worker(fresh).process(run_id)
    with store.transaction() as session:
        run = session.get(Objective, run_id)
        assert run.status == 'succeeded', run.data.get('blocker')
        assert run.data['scope'] == original_scope and run.data['activeBudget']['lease'] is None
    assert planner.calls == 4 and len(specialist.calls) == 4
    assert (await fresh.poll_due())['woken'] == 0
    assert not await make_worker(fresh).process(run_id)


async def test_crash_after_atomic_resolve_before_checkpoint_replays_without_duplicate_publication(graph_waits):
    import asyncio

    from sqlalchemy import select

    from kleos.capabilities.external import ExternalJob
    from kleos.projects.store import Event
    store, repo, bridge, jobs, _, actor, project, run_id, clock, planner, specialist, make_worker, locks = graph_waits
    await make_worker(bridge).process(run_id)
    for job in jobs.list(actor, project):
        with store.transaction() as session:
            generation = session.get(ExternalJob, job['id']).generation
        jobs._record(actor, job['id'], generation, ProviderObservation(state='completed', external_id='owned', ownership_verified=True))
    clock[0] += 61
    await bridge.poll_due()
    first = ExternalWaits(store, repo, external_jobs=jobs, clock=lambda: clock[0])
    original = first.resolve
    async def crash(*args):
        await original(*args)
        raise asyncio.CancelledError()
    first.resolve = crash
    lost_worker = make_worker(first)
    def lose_before_worker_save(*args, **kwargs):
        raise asyncio.CancelledError()
    lost_worker._save = lose_before_worker_save
    with pytest.raises(asyncio.CancelledError):
        await lost_worker.process(run_id)
    with store.transaction() as session:
        run = session.get(Objective, run_id)
        assert run.status == 'running', (run.status, run.data.get('blocker'))
    assert planner.calls == 1 and not locks.active
    restarted = ExternalWaits(store, repo, external_jobs=jobs, clock=lambda: clock[0])
    assert await make_worker(restarted).process(run_id)
    with store.transaction() as session:
        run = session.get(Objective, run_id)
        assert run.status == 'succeeded', run.data.get('blocker')
        publications = [e for e in session.scalars(select(Event).where(Event.project_id == project))
                        if e.data['type'] == 'task.external_observed']
        assert len(publications) == 2
    assert planner.calls == 3 and len(specialist.calls) == 4


async def test_completion_before_interrupt_is_saved_does_not_lose_wake(graph_waits):
    from kleos.capabilities.external import ExternalJob
    store, _, bridge, jobs, _, actor, project, run_id, _, planner, specialist, make_worker, _ = graph_waits
    original = specialist.execute
    async def already_ready(scope, task, context):
        result = await original(scope, task, context)
        if result.wait_ids:
            from kleos.orchestration.external_waits import ExternalWait
            with store.transaction() as session:
                wait = session.get(ExternalWait, result.wait_ids[0])
                row = session.get(ExternalJob, wait.job_id)
                ident, generation = row.id, row.generation
            jobs._record(actor, ident, generation, ProviderObservation(state='completed', external_id='owned', ownership_verified=True))
            bridge._refresh(result.wait_ids[0])
            assert bridge.wake_ready() == 0  # Running graph has not saved its interrupt yet.
        return result
    specialist.execute = already_ready
    await make_worker(bridge).process(run_id)
    assert planner.calls == 1
    assert (await bridge.poll_due())['woken'] == 1  # Ready receipts survive the checkpoint race.
    assert await make_worker(bridge).process(run_id)
    with store.transaction() as session:
        assert session.get(Objective, run_id).status == 'succeeded'
    assert planner.calls == 3
