import asyncio
from contextlib import asynccontextmanager

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from kleos.contracts.models import Actor, DomainError, TaskResult, TaskSpec
from kleos.orchestration.engine import ObjectiveEngine, PlanDecision
from kleos.orchestration.worker import ObjectiveWorker, PostgresRunLocks
from kleos.projects.graph_repository import GraphRepository, execution_owner
from kleos.projects.store import Membership, Objective, Project, ProjectStore


class FixtureLocks:
    def __init__(self):
        self.active = set()

    def execute(self, _):
        pass

    @asynccontextmanager
    async def hold(self, key):
        if key in self.active:
            yield None
            return
        self.active.add(key)
        try:
            yield self
        finally:
            self.active.remove(key)


class Planner:
    def __init__(self):
        self.calls = []

    async def plan(self, scope, objective, context, tasks, results, answer, **kwargs):
        self.calls.append(answer)
        if results:
            return PlanDecision(done=True, summary="Bracket candidate generated")
        if answer is None:
            return PlanDecision(question="Which material?")
        return PlanDecision(
            tasks=[
                TaskSpec(
                    id="bracket", objective=objective, capability="cad", allowed_paths=["cad/bracket.py"]
                )
            ]
        )


class Specialist:
    def __init__(self):
        self.calls = 0

    async def execute(self, scope, task, context):
        self.calls += 1
        return TaskResult(task_id=task.id, status="succeeded", summary="Fixture candidate, not accepted")


@pytest.fixture
def worker(tmp_path):
    store = ProjectStore(f"sqlite:///{tmp_path}/worker.db")
    store.initialize_for_tests()
    actor = Actor(tenant_id="tenant", user_id="user")
    project = store.create(actor, "Existing hardware", "create")["projectId"]
    run = store.submit(actor, project, "Replace bracket", 1, "submit")["runId"]
    planner, specialist, saver = Planner(), Specialist(), InMemorySaver()
    repo = GraphRepository(store)

    def factory(scope):
        return ObjectiveEngine(repo, planner, specialist, checkpointer=saver)

    worker = ObjectiveWorker(store, factory, locks=FixtureLocks(), heartbeat_seconds=0.01)
    return worker, actor, project, run, planner, specialist


async def test_worker_wait_resume_and_completed_run_is_not_accepted_release(worker):
    worker, actor, project, run, planner, specialist = worker
    with worker.projects.transaction() as session:
        row = session.get(Project, project)
        row.data = {**row.data, 'status': 'queued', 'state': {
            'planning': {'phase': 'engineering', 'runId': run, 'pending': True}}}
    assert await worker.process(run)
    with worker.projects.transaction() as s:
        saved = s.get(Objective, run)
        assert saved.status == "waiting"
        interrupt_id = saved.data["interruptId"]
        initial_scope = saved.data["scope"]
    assert not worker.pending()
    worker.answer(actor, project, run, interrupt_id, "PETG", 2, "answer")
    # A fresh dispatcher uses the same graph checkpoint and persisted answer.
    resumed = ObjectiveWorker(worker.projects, worker.engine_factory, locks=worker.locks)
    assert await resumed.process(run)
    assert not await resumed.process(run)
    assert planner.calls == [None, "PETG", None]
    assert specialist.calls == 1
    with worker.projects.transaction() as s:
        saved = s.get(Objective, run)
        assert saved.status == "succeeded"
        assert saved.data["scope"] == initial_scope
        assert "pendingAnswer" not in saved.data
        assert saved.data["tasks"][0]["id"] == "bracket"
        row = s.get(Project, project)
        assert row.data['status'] == 'review'
        assert row.data['state']['planning']['pending'] is False
        assert row.data.get('acceptedReleaseId') is None
    assert not worker.projects.context(actor, project).approved_design


async def test_answers_are_scoped_and_bound_to_exact_question(worker):
    worker, actor, project, run, _, _ = worker
    await worker.process(run)
    with worker.projects.transaction() as s:
        interrupt_id = s.get(Objective, run).data["interruptId"]
    with pytest.raises(DomainError, match="not found"):
        worker.answer(
            Actor(tenant_id="other", user_id=actor.user_id), project, run, interrupt_id, "PETG", 2, "bad"
        )
    with pytest.raises(DomainError, match="not waiting"):
        worker.answer(actor, project, run, "old-question", "PETG", 2, "bad")
    first = worker.answer(actor, project, run, interrupt_id, "PETG", 2, "ok")
    assert worker.answer(actor, project, run, interrupt_id, "PETG", 2, "ok") == first


async def test_competing_dispatcher_cannot_run_same_objective(worker):
    worker, _, _, run, planner, _ = worker
    async with worker.locks.hold(run):
        assert not await worker.process(run)
    assert not planner.calls
    assert await worker.process(run)


async def test_cancellation_during_model_wait_stops_local_work(worker):
    worker, actor, project, run, _, specialist = worker
    started, stopped = asyncio.Event(), asyncio.Event()

    class WaitingPlanner:
        async def plan(self, *args, **kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

    engine = ObjectiveEngine(
        GraphRepository(worker.projects), WaitingPlanner(), specialist, checkpointer=InMemorySaver()
    )
    worker.engine_factory = lambda _: engine
    processing = asyncio.create_task(worker.process(run))
    await asyncio.wait_for(started.wait(), 1)
    GraphRepository(worker.projects).cancel(actor, project, run)
    await asyncio.wait_for(processing, 1)
    assert stopped.is_set() and specialist.calls == 0
    with worker.projects.transaction() as s:
        assert s.get(Objective, run).status == "cancelled"


def test_worker_cannot_accidentally_use_sqlite_as_production_dispatch_lock(worker):
    worker, *_ = worker
    with pytest.raises(ValueError, match="PostgreSQL"):
        PostgresRunLocks(worker.projects.engine)


async def test_revoked_membership_stops_dispatch_without_crashing_worker(worker):
    worker, actor, project, run, planner, _ = worker
    with worker.projects.transaction() as s:
        s.delete(s.get(Membership, (project, actor.user_id)))
    assert await worker.process(run)
    assert planner.calls == []
    with worker.projects.transaction() as s:
        assert s.get(Objective, run).status == "blocked"


async def test_worker_takeover_fences_older_tool_publication(worker):
    worker, _, _, run, _, _ = worker
    scope, _, _, original = worker._begin(run)
    _, _, _, successor = worker._begin(run)
    repo = GraphRepository(worker.projects)
    token = execution_owner.set((run, original))
    try:
        with pytest.raises(DomainError, match="another worker"):
            await repo.assert_active(scope)
        worker._block(run, "old-worker-error", worker_token=original)
        with worker.projects.transaction() as s:
            assert s.get(Objective, run).status == "running"
        execution_owner.set((run, successor))
        await repo.assert_active(scope)
    finally:
        execution_owner.reset(token)


@pytest.mark.parametrize("same_run", [True, False])
async def test_capture_factory_failure_clears_only_its_own_pending_ui_state(worker, same_run):
    worker, _, project_id, run_id, _, _ = worker
    with worker.projects.transaction() as session:
        project = session.get(Project, project_id)
        project.data = {**project.data, "status": "queued", "state": {"planning": {
            "pending": True, "runId": run_id if same_run else "a-newer-capture",
        }}}

    def unavailable(_):
        raise DomainError("Configure an image model", "model_unavailable")

    worker.engine_factory = unavailable
    assert await worker.process(run_id)
    with worker.projects.transaction() as session:
        run, project = session.get(Objective, run_id), session.get(Project, project_id)
        assert run.status == "blocked" and run.data["blocker"] == "model_unavailable"
        assert project.data["state"]["planning"]["pending"] is not same_run
        assert project.data["status"] == ("blocked" if same_run else "queued")
