"""Real LangGraph worker dispatch accounting with a controlled allowance clock."""
import asyncio
import copy
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from kleos.contracts.models import Actor, DomainError, TaskResult, TaskSpec
from kleos.orchestration.engine import ObjectiveEngine, PlanDecision, RunScope
from kleos.orchestration.worker import ObjectiveWorker
from kleos.projects import compute
from kleos.projects.graph_repository import GraphRepository, execution_owner
from kleos.projects.store import Objective, Project, ProjectStore


class Locks:
    def execute(self, _):
        pass

    @asynccontextmanager
    async def hold(self, _):
        yield self


class Planner:
    def __init__(self, clock, *, wait=True):
        self.clock, self.wait, self.calls = clock, wait, 0

    async def plan(self, scope, objective, context, tasks, results, answer, **kwargs):
        self.calls += 1
        if results:
            self.clock[0] += 3000 if self.wait else 0
            return PlanDecision(done=True)
        if self.wait and answer is None:
            self.clock[0] += 10000
            return PlanDecision(question='Which material?')
        self.clock[0] += 5000 if self.wait else 0
        return PlanDecision(tasks=[TaskSpec(id='part', capability='research', objective=objective)])


class Specialist:
    def __init__(self, clock, mode='quick'):
        self.clock, self.mode, self.calls = clock, mode, 0

    async def execute(self, scope, task, context):
        self.calls += 1
        if self.mode == 'renew':
            for elapsed in (16000, 16000, 13000):
                self.clock[0] += elapsed
                await asyncio.sleep(.03)
        else:
            self.clock[0] += 7000 if self.mode == 'quick' else 30001
        return TaskResult(task_id=task.id, status='succeeded', summary='Fixture work, no acceptance')


@pytest.fixture
def setup(tmp_path, monkeypatch):
    projects = ProjectStore(f'sqlite:///{tmp_path / "active.db"}')
    projects.initialize_for_tests()
    actor = Actor(tenant_id='tenant', user_id='owner')
    project = projects.create(actor, 'Work with human input', 'create')['projectId']
    run_id = projects.submit(actor, project, 'Inspect only the requested part', 1, 'objective')['runId']
    clock = [compute.now_ms()]
    monkeypatch.setattr(compute, 'now_ms', lambda: clock[0])
    saver = InMemorySaver()
    planner, specialist = Planner(clock), Specialist(clock)
    repo = GraphRepository(projects)
    def factory(scope):
        return ObjectiveEngine(repo, planner, specialist, checkpointer=saver)
    worker = ObjectiveWorker(projects, factory, locks=Locks(), max_run_seconds=120,
        max_lifetime_seconds=7*86400, heartbeat_seconds=.01)
    yield SimpleNamespace(projects=projects, actor=actor, project=project, run_id=run_id, clock=clock,
        planner=planner, specialist=specialist, worker=worker, repo=repo, factory=factory)
    projects.engine.dispose()


def saved(setup):
    with setup.projects.transaction() as session:
        return session.get(Objective, setup.run_id)


async def test_wait_checkpoint_costs_no_idle_time_and_restart_keeps_original_budget(setup):
    assert await setup.worker.process(setup.run_id)
    first = saved(setup)
    assert first.status == 'waiting' and first.data['activeBudget']['committedMs'] == 10000
    assert first.data['activeBudget']['lease'] is None
    original_scope = copy.deepcopy(first.data['scope'])
    setup.clock[0] += 86400_000
    resumed = ObjectiveWorker(setup.projects, setup.factory, locks=Locks(), max_run_seconds=600,
        max_lifetime_seconds=30*86400, heartbeat_seconds=.01)
    resumed.answer(setup.actor, setup.project, setup.run_id, first.data['interruptId'], 'PETG', 2, 'answer')
    assert await resumed.process(setup.run_id)
    final = saved(setup)
    assert final.status == 'succeeded' and final.data['scope'] == original_scope
    assert final.data['activeBudget']['limitMs'] == 120000
    assert final.data['activeBudget']['committedMs'] == 25000
    assert final.data['activeBudget']['lease'] is None
    assert setup.specialist.calls == 1
    assert not await resumed.process(setup.run_id)
    assert saved(setup).data['activeBudget']['committedMs'] == 25000


async def test_crashed_dispatch_grant_is_not_refunded_by_replacement_or_old_owner(setup):
    old_scope, _, _, lost_owner = setup.worker._begin(setup.run_id)
    assert saved(setup).data['activeBudget']['committedMs'] == 30000
    setup.clock[0] += 5000
    assert await setup.worker.process(setup.run_id)
    final = saved(setup)
    assert final.status == 'waiting' and final.data['activeBudget']['committedMs'] == 40000
    before = copy.deepcopy(final.data)
    setup.worker._finish(old_scope, lost_owner)
    assert saved(setup).data == before
    token = execution_owner.set((setup.run_id, lost_owner))
    try:
        with pytest.raises(DomainError) as error:
            await setup.repo.assert_active(old_scope)
        assert error.value.code == 'worker_fenced'
    finally:
        execution_owner.reset(token)


async def test_heartbeats_extend_grants_and_settle_total_actual_active_time(setup):
    setup.planner.wait = False
    setup.specialist.mode = 'renew'
    assert await setup.worker.process(setup.run_id)
    final = saved(setup)
    assert final.status == 'succeeded', final.data.get('blocker')
    assert final.data['activeBudget']['committedMs'] == 45000
    assert final.data['activeBudget']['lease'] is None


async def test_work_finishing_after_grant_expiry_cannot_publish_or_claim_success(setup):
    setup.planner.wait = False
    setup.specialist.mode = 'expire'
    assert await setup.worker.process(setup.run_id)
    final = saved(setup)
    assert final.status == 'blocked' and final.data['blocker'] == 'compute_budget_exhausted'
    assert final.data['activeBudget']['committedMs'] == 30000
    assert not final.data.get('results')


def test_final_save_also_fences_a_grant_that_expired_after_graph_return(setup):
    scope, _, _, owner = setup.worker._begin(setup.run_id)
    token = execution_owner.set((setup.run_id, owner))
    setup.clock[0] += 30000
    try:
        setup.worker._save(scope, SimpleNamespace(values={'done': True}, interrupts=[], next=()))
    finally:
        execution_owner.reset(token)
    final = saved(setup)
    assert final.status == 'blocked' and final.data['blocker'] == 'compute_budget_exhausted'


def test_final_save_keeps_clock_watermark_for_rollback_safe_settlement(setup):
    scope, _, _, owner = setup.worker._begin(setup.run_id)
    token = execution_owner.set((setup.run_id, owner))
    setup.clock[0] += 20000
    try:
        setup.worker._save(scope, SimpleNamespace(values={'done': True}, interrupts=[], next=()))
    finally:
        execution_owner.reset(token)
    assert saved(setup).data['activeBudget']['lease']['observedAtMs'] == setup.clock[0]
    setup.clock[0] -= 5000
    setup.worker._finish(scope, owner)
    assert saved(setup).data['activeBudget']['committedMs'] == 30000


def test_project_revision_change_cannot_renew_an_old_scope(setup):
    scope, _, _, owner = setup.worker._begin(setup.run_id)
    token = execution_owner.set((setup.run_id, owner))
    setup.clock[0] += 20000
    with setup.projects.transaction() as session:
        session.get(Project, setup.project).advance_revision()
    try:
        with pytest.raises(DomainError) as error:
            setup.worker._renew(scope)
        assert error.value.code == 'stale_context'
    finally:
        execution_owner.reset(token)
    assert saved(setup).data['activeBudget']['committedMs'] == 30000


async def test_budgeted_run_cannot_reenter_repository_with_deadline_only_scope(setup):
    scope, _, _, owner = setup.worker._begin(setup.run_id)
    token = execution_owner.set((setup.run_id, owner))
    try:
        with pytest.raises(DomainError) as error:
            await setup.repo.assert_active(scope.model_copy(update={'compute_budget_seconds': None}))
        assert error.value.code == 'compute_budget_conflict'
    finally:
        execution_owner.reset(token)


async def test_old_serialized_scope_is_not_upgraded_or_extended_on_new_worker(setup):
    old = RunScope(actor=setup.actor, project_id=setup.project, run_id=setup.run_id, revision=2,
        deadline_at=datetime.now(UTC)+timedelta(minutes=30))
    encoded = old.model_dump(mode='json')
    encoded.pop('compute_budget_seconds')
    with setup.projects.transaction() as session:
        run = session.get(Objective, setup.run_id)
        run.data = {**run.data, 'scope': encoded}
    assert await setup.worker.process(setup.run_id)
    final = saved(setup)
    assert final.status == 'waiting' and 'activeBudget' not in final.data
    assert RunScope.model_validate(final.data['scope']) == old


async def test_expired_legacy_scope_cannot_be_extended_by_new_configuration(setup):
    old = RunScope(actor=setup.actor, project_id=setup.project, run_id=setup.run_id, revision=2,
        deadline_at=datetime.now(UTC)-timedelta(seconds=1))
    with setup.projects.transaction() as session:
        run = session.get(Objective, setup.run_id)
        run.data = {**run.data, 'scope': old.model_dump(mode='json')}
    assert await setup.worker.process(setup.run_id)
    final = saved(setup)
    assert final.status == 'blocked' and final.data['blocker'] == 'deadline_exceeded'
    assert 'activeBudget' not in final.data and setup.planner.calls == 0
