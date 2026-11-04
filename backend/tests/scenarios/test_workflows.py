"""Phase 4 workflows: the orchestrator chooses capabilities per objective, using fake capabilities.

Each capability is a scripted fake. These tests prove task selection, dependency order,
result-driven expansion, approval gating and exactly-once external effects in the actual
LangGraph engine. They do not prove CAD/PCB/firmware quality or any physical outcome;
native-tool and provider behavior has its own qualification.

Workflow coverage map (the other two named workflows run through the real runtime elsewhere):
  1. Missing part in an imported project -> tests/test_dynamic_journeys.py
  6. Resume without duplicating external actions -> tests/test_external_wait_graph.py
"""
from datetime import UTC, datetime, timedelta

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from test_orchestration import Repo

from kleos.contracts.models import (
    Actor,
    ArtifactRef,
    CheckStatus,
    Observation,
    TaskResult,
    TaskSpec,
    TaskStatus,
)
from kleos.orchestration.engine import ObjectiveEngine, PlanDecision, RunScope


def scope():
    return RunScope(actor=Actor(tenant_id='tenant', user_id='owner'), project_id='project',
                    run_id='run', revision=1, deadline_at=datetime.now(UTC) + timedelta(minutes=5))


def spec(id, capability, objective, depends_on=(), inputs=(), paths=()):
    return TaskSpec(id=id, capability=capability, objective=objective, depends_on=list(depends_on),
                    input_artifact_ids=list(inputs), allowed_paths=list(paths))


class Planner:
    """Reactive planner: a function of the objective and actual results, as a model would be."""

    def __init__(self, decide):
        self.decide, self.calls = decide, []

    async def plan(self, scope, objective, context, tasks, results, answer, *, superseded=None):
        self.calls.append({'tasks': [t.id for t in tasks], 'results': [r.task_id for r in results], 'answer': answer})
        return self.decide(objective, tasks, {r.task_id: r for r in results}, answer)


class Capabilities:
    """Fake lanes keyed by capability. Records execution order and every external effect."""

    def __init__(self, handlers=None):
        self.handlers, self.order, self.effects = handlers or {}, [], []

    async def execute(self, scope, task, context):
        self.order.append((task.capability.value, task.id))
        handler = self.handlers.get(task.capability.value)
        if handler:
            return handler(self, task)
        return TaskResult(task_id=task.id, status=TaskStatus.SUCCEEDED, summary='Candidate only; unchecked.')

    def lanes(self):
        return [lane for lane, _ in self.order]

    def position(self, task_id):
        return [ident for _, ident in self.order].index(task_id)


def engine(planner, capabilities, saver=None):
    return ObjectiveEngine(Repo(), planner, capabilities, checkpointer=saver or InMemorySaver())


async def test_route_existing_pcb_selects_only_the_pcb_lane_and_keeps_the_source_closure():
    sources = ['board', 'schematic', 'libs']

    def decide(objective, tasks, results, answer):
        if not tasks:
            return PlanDecision(tasks=[spec('route-board', 'pcb', objective, inputs=sources)])
        return PlanDecision(done=True, summary='Routing candidate produced; ERC/DRC remain independent checks.')

    seen = []
    repo = Repo()
    repo.artifacts = {name: ArtifactRef(id=name, path=name + '.txt', sha256=sha, revision=1, size=1)
                      for name, sha in zip(sources, ['a' * 64, 'b' * 64, 'c' * 64])}
    capabilities = Capabilities()
    original = capabilities.execute

    async def spy(scope, task, context):
        seen.append(task)
        return await original(scope, task, context)

    capabilities.execute = spy
    result = await ObjectiveEngine(repo, Planner(decide), capabilities, checkpointer=InMemorySaver()).start(
        scope(), 'Route the existing board; do not redesign it')
    assert result['done'] and capabilities.lanes() == ['pcb']
    assert seen[0].input_artifact_ids == sources and not seen[0].allowed_paths
    assert not any(lane in capabilities.lanes() for lane in ('cad', 'firmware', 'application', 'sourcing'))


@pytest.mark.parametrize('footprint_changed', [True, False], ids=['footprint-changes', 'drop-in'])
async def test_replacement_components_assess_and_scope_only_the_affected_work(footprint_changed):
    def sourcing(self, task):
        candidates = [{'part': 'ALT-3V3', 'footprintChanged': footprint_changed, 'quotedUsd': 0.42}]
        return TaskResult(task_id=task.id, status=TaskStatus.SUCCEEDED, summary='Two nonbinding alternates found.',
            observations=[Observation(name='replacement-candidates', status=CheckStatus.UNKNOWN,
                                      details={'candidates': candidates, 'binding': False})])

    def decide(objective, tasks, results, answer):
        if not tasks:
            return PlanDecision(tasks=[spec('find-alternate', 'sourcing', 'Find replacements for U3, discontinued')])
        if 'find-alternate' in results and len(tasks) == 1:
            found = results['find-alternate'].observations[0].details['candidates']
            if any(item['footprintChanged'] for item in found):
                return PlanDecision(tasks=[
                    spec('update-footprint', 'pcb', 'Update U3 footprint and re-route', ['find-alternate']),
                    spec('recheck', 'validation', 'Independently re-check the board', ['update-footprint'])])
            return PlanDecision(done=True, summary='Drop-in alternate: no dependent work is affected.')
        if len(results) < len(tasks):
            return PlanDecision()
        return PlanDecision(done=True, summary='Affected work assessed.')

    capabilities = Capabilities({'sourcing': sourcing})
    result = await engine(Planner(decide), capabilities).start(scope(), 'U3 is discontinued; find a replacement')
    assert result['done']
    if footprint_changed:
        assert capabilities.lanes() == ['sourcing', 'pcb', 'validation']
    else:
        assert capabilities.lanes() == ['sourcing']
    assert 'cad' not in capabilities.lanes() and 'firmware' not in capabilities.lanes()
    # A quote or search never becomes an order.
    assert not capabilities.effects


async def test_human_verification_needs_explicit_approval_and_dispatches_once_across_resume():
    def human(self, task):
        if task.id == 'draft-check':
            return TaskResult(task_id=task.id, status=TaskStatus.SUCCEEDED, summary='Local draft prepared; provider not contacted.',
                observations=[Observation(name='draft', status=CheckStatus.UNKNOWN, details={'fee': None})])
        key = ('dispatch', task.id)
        if key not in self.effects:  # the fake provider's own idempotency key
            self.effects.append(key)
        return TaskResult(task_id=task.id, status=TaskStatus.SUCCEEDED, summary='Scoped task posted once.')

    def decide(objective, tasks, results, answer):
        if not tasks:
            return PlanDecision(tasks=[spec('draft-check', 'human_work', 'Draft a connector-fit inspection task')])
        if answer is None and 'draft-check' in results and len(tasks) == 1:
            return PlanDecision(question='Approve posting this inspection task, scope and fee as drafted?')
        if answer == 'approve' and len(tasks) == 1:
            return PlanDecision(tasks=[spec('post-check', 'human_work', 'Post the approved task', ['draft-check'])])
        if answer == 'decline':
            return PlanDecision(done=True, summary='Draft kept; nothing was posted.')
        return PlanDecision(done=True, summary='Posted; physical acceptance still requires independent evidence.')

    saver, capabilities = InMemorySaver(), Capabilities({'human_work': human})
    planner = Planner(decide)
    run = scope()
    first = await engine(planner, capabilities, saver).start(run, 'Arrange a human fit check of the connector')
    assert '__interrupt__' in first and capabilities.effects == []
    # A new process resumes from the checkpoint; the approved action runs exactly once.
    resumed = await engine(planner, capabilities, saver).resume(run, answer='approve')
    assert resumed['done'] and capabilities.effects == [('dispatch', 'post-check')]
    assert [ident for _, ident in capabilities.order].count('draft-check') == 1


async def test_declining_the_human_task_posts_nothing():
    def decide(objective, tasks, results, answer):
        if not tasks:
            return PlanDecision(tasks=[spec('draft-check', 'human_work', 'Draft a connector-fit inspection task')])
        if answer is None:
            return PlanDecision(question='Approve posting?')
        return PlanDecision(done=True, summary='Nothing posted.')

    saver, capabilities = InMemorySaver(), Capabilities()
    planner = Planner(decide)
    run = scope()
    await engine(planner, capabilities, saver).start(run, 'Arrange a human fit check')
    result = await engine(planner, capabilities, saver).resume(run, answer='decline')
    assert result['done'] and capabilities.lanes() == ['human_work'] and not capabilities.effects


@pytest.mark.parametrize('pcb_fails', [False, True], ids=['clean-build', 'failed-pcb-then-repair'])
async def test_complete_device_runs_dynamically_selected_tasks_in_dependency_order(pcb_fails):
    def pcb(self, task):
        status = TaskStatus.FAILED if (pcb_fails and task.id == 'board') else TaskStatus.SUCCEEDED
        return TaskResult(task_id=task.id, status=status, summary='DRC reported a clearance violation.' if status == TaskStatus.FAILED
                          else 'Board candidate produced.')

    def decide(objective, tasks, results, answer):
        ids = {t.id for t in tasks}
        if not tasks:
            return PlanDecision(tasks=[
                spec('enclosure', 'cad', 'Enclosure sized to the approved board outline'),
                spec('board', 'pcb', 'Two-layer sensor board'),
                spec('firmware', 'firmware', 'Sensor firmware for the selected board', ['board']),
                spec('app', 'application', 'Companion app reading the sensor', ['firmware']),
                spec('bom', 'sourcing', 'Nonbinding BOM pricing', ['board']),
                spec('checks', 'validation', 'Independent checks of every candidate',
                     ['enclosure', 'board', 'firmware', 'app', 'bom'])])
        failed = [r for r in results.values() if r.status == TaskStatus.FAILED]
        if failed and 'board-v2' not in ids:
            return PlanDecision(tasks=[spec('board-v2', 'pcb', 'Repair the clearance violation')],
                                supersedes={'board': 'board-v2'})
        return PlanDecision() if len(results) < len(ids) else PlanDecision(done=True, summary='Candidates complete; acceptance pending.')

    capabilities = Capabilities({'pcb': pcb})
    result = await engine(Planner(decide), capabilities).start(scope(), 'Build a complete sensor device')
    assert result['done']
    assert set(capabilities.lanes()) == {'cad', 'pcb', 'firmware', 'application', 'sourcing', 'validation'}
    board = 'board-v2' if pcb_fails else 'board'
    assert capabilities.position(board) < capabilities.position('firmware') < capabilities.position('app')
    assert capabilities.position(board) < capabilities.position('bom')
    assert capabilities.position('checks') == len(capabilities.order) - 1
    if pcb_fails:
        assert result['results']['board']['status'] == 'failed' and result['superseded'] == {'board': 'board-v2'}
        assert result['results']['board-v2']['status'] == 'succeeded'
    # No generator result is acceptance: nothing here marks a release or an independent check.
    assert not any(obs.get('independent') for res in result['results'].values() for obs in res.get('observations', []))
