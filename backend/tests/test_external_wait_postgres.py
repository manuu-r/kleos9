"""Real checkpoint reconnection and durable external-event consumption; fixture provider only."""
from datetime import UTC, datetime

from test_external_waits import ReadProvider
from test_postgres_runtime import database, objective  # noqa: F401

from kleos.capabilities.external import ExternalJobs, ExternalRequest, ProviderObservation
from kleos.contracts.models import TaskResult, TaskSpec
from kleos.orchestration.engine import ObjectiveEngine, PlanDecision
from kleos.orchestration.external_waits import ExternalWaits
from kleos.orchestration.persistence import postgres_runtime
from kleos.orchestration.worker import ObjectiveWorker
from kleos.projects.graph_repository import GraphRepository
from kleos.projects.store import Objective


async def test_external_wait_survives_postgres_reconnection_without_repeating_work(database):  # noqa: F811
    store, url = database
    actor, project, run_id = objective(store)
    provider, clock = ReadProvider(), [datetime.now(UTC).timestamp()]
    jobs = ExternalJobs(store, {('tenant', 'owner', 'fixture'): provider},
        installation_pins={('tenant', 'owner', 'fixture'): 'a'*64}, clock=lambda: clock[0])
    job_id = jobs.create(actor, ExternalRequest(project_id=project, revision=2, kind='sourcing', provider='fixture',
        objective='Observe existing supplier inquiry',
        bom=[{'reference': 'J1', 'supplierPart': 'ABC', 'quantity': 1}]), 'job')['job']['id']
    await jobs.observe_existing(actor, job_id, 'owned', 'link')
    with store.transaction() as session:
        run = session.get(Objective, run_id)
        run.data = {**run.data, 'runtimePins': {'external': [{'provider': 'fixture', 'installationHash': 'a'*64}]}}
    plan_calls, tool_calls = [], []
    class Planner:
        async def plan(self, scope, objective, context, tasks, results, answer, **kwargs):
            plan_calls.append([result.status.value for result in results])
            if not results:
                return PlanDecision(tasks=[TaskSpec(id='observe', capability='sourcing', objective='Observe existing job')])
            if any(result.task_id == 'review' for result in results):
                return PlanDecision(done=True)
            assert results[0].status.value == 'blocked'
            assert all(not observation.independent for observation in results[0].observations)
            return PlanDecision(tasks=[TaskSpec(id='review', capability='sourcing', objective='Summarize supplier claim')],
                supersedes={'observe': 'review'})
    def worker_for(saver, memory):
        repo = GraphRepository(store)
        bridge = ExternalWaits(store, repo, external_jobs=jobs, clock=lambda: clock[0])
        class Specialist:
            async def execute(self, scope, task, context):
                tool_calls.append(task.id)
                if task.id == 'observe':
                    ident = await bridge.bind(scope, task, 'sourcing', job_id)
                    return TaskResult(task_id=task.id, status='waiting', summary='Awaiting supplier claim', wait_ids=[ident])
                return TaskResult(task_id=task.id, status='succeeded', summary='Supplier claim summarized; no physical acceptance')
        worker = ObjectiveWorker(store, lambda _: ObjectiveEngine(repo, Planner(), Specialist(),
            checkpointer=saver, store=memory, external_waits=bridge), max_run_seconds=60,
            max_lifetime_seconds=7*86400)
        return worker, bridge

    async with postgres_runtime(url, setup=True) as (saver, memory):
        worker, bridge = worker_for(saver, memory)
        assert await worker.process(run_id)
        with store.transaction() as session:
            run = session.get(Objective, run_id)
            assert run.status == 'waiting', run.data.get('blocker')
            pinned_scope = run.data['scope']
            committed = run.data['activeBudget']['committedMs']
            assert run.data['activeBudget']['lease'] is None
        assert len(plan_calls) == 1 and tool_calls == ['observe']
        clock[0] += 61
        assert (await bridge.poll_due())['woken'] == 0
        assert not worker.pending() and len(plan_calls) == 1

    # Close every LangGraph connection before delivering an external completion.
    async with postgres_runtime(url) as (saver, memory):
        worker, bridge = worker_for(saver, memory)
        provider.result = ProviderObservation(state='completed', external_id='owned', ownership_verified=True)
        clock[0] += 3600
        assert (await bridge.poll_due())['woken'] == 1
        assert bridge.wake_ready() == 0
        assert await worker.process(run_id)
        with store.transaction() as session:
            run = session.get(Objective, run_id)
            assert run.status == 'succeeded', run.data.get('blocker')
            assert run.data['scope'] == pinned_scope
            assert run.data['results']['observe']['status'] == 'blocked'
            assert committed < run.data['activeBudget']['committedMs'] < 60_000
            assert run.data['activeBudget']['lease'] is None
        assert len(plan_calls) == 3 and tool_calls == ['observe', 'review']
        assert (await bridge.poll_due())['woken'] == 0
