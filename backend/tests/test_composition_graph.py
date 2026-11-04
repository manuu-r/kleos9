"""Final compilation is a resumable LangGraph node, never another planner."""
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from test_orchestration import Planner, Repo, scope, task

from kleos.contracts.models import DomainError, TaskResult
from kleos.orchestration.engine import ObjectiveEngine, PlanDecision


class Compilation:
    def __init__(self, events, repository, *, fail=False, cancel=False):
        self.events, self.repository = events, repository
        self.fail, self.cancel = fail, cancel

    async def needed(self, scope, tasks, results):
        return 'cad' in results and 'compose' not in self.events

    async def run(self, scope):
        self.events.append('compose')
        if self.fail:
            raise DomainError('Fixture compilation failed', 'compilation_failed')
        if self.cancel:
            self.repository.active = False


class Specialist:
    def __init__(self, events):
        self.events = events

    async def execute(self, scope, task, context):
        self.events.append(task.id)
        return TaskResult(task_id=task.id, status='succeeded', summary='Fixture candidate')


@pytest.mark.parametrize('verify', [False, True])
async def test_final_compilation_precedes_validation_or_completion_without_an_extra_plan(verify):
    repository, events = Repo(), []
    tasks = [task('cad')]+([task('verify', 'validation', ['cad'])] if verify else [])
    decisions = [PlanDecision(tasks=tasks)]+([PlanDecision()] if verify else [])+[PlanDecision(done=True)]
    planner = Planner(decisions)
    engine = ObjectiveEngine(repository, planner, Specialist(events), checkpointer=InMemorySaver(),
        composition=Compilation(events, repository))
    result = await engine.start(scope(), 'Produce exact final candidates')
    assert result['done'] and not result['compilation_requested']
    assert events == ['cad', 'compose']+(['verify'] if verify else [])
    assert len(planner.calls) == len(decisions)


@pytest.mark.parametrize('failure', ['failed', 'cancelled'])
async def test_failed_or_cancelled_compilation_cannot_dispatch_validation(failure):
    repository, events = Repo(), []
    planner = Planner([PlanDecision(tasks=[task('cad'), task('verify', 'validation', ['cad'])]), PlanDecision()])
    engine = ObjectiveEngine(repository, planner, Specialist(events), checkpointer=InMemorySaver(),
        composition=Compilation(events, repository, fail=failure == 'failed', cancel=failure == 'cancelled'))
    with pytest.raises(DomainError):
        await engine.start(scope(), 'Validate only final compiled candidates')
    assert events == ['cad', 'compose'] and 'verify' not in repository.published


async def test_restart_at_compilation_checkpoint_does_not_repeat_authoring_or_planning():
    repository, events, saver = Repo(), [], InMemorySaver()
    current = scope()
    planner = Planner([PlanDecision(tasks=[task('cad')]), PlanDecision(done=True)])
    engine = ObjectiveEngine(repository, planner, Specialist(events), checkpointer=saver,
        composition=Compilation(events, repository))
    initial = {'scope': current.model_dump(mode='json'), 'objective': 'Compile after authoring',
        'tasks': {}, 'results': {}, 'turns': 0, 'done': False}
    await engine.graph.ainvoke(initial, engine._config(current), interrupt_before=['compose'], durability='sync')
    snapshot = await engine.graph.aget_state(engine._config(current))
    assert snapshot.next == ('compose',) and events == ['cad']
    resumed = ObjectiveEngine(repository, Planner([]), Specialist(events), checkpointer=saver,
        composition=Compilation(events, repository))
    result = await resumed.resume(current)
    assert result['done'] and events == ['cad', 'compose']
    assert len(planner.calls) == 2
