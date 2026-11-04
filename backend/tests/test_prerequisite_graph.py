"""Host adapter with real effect receipts; model and approval service are fixtures."""
import copy
import json

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import select
from test_graph_repository import repository
from test_interface_runtime import Locks
from test_interfaces import Model
from test_orchestration import Planner

from kleos.contracts.models import ArtifactRef, DomainError, TaskResult, TaskSpec
from kleos.orchestration.engine import ObjectiveEngine, PlanDecision
from kleos.orchestration.prerequisites import InterfacePrerequisites
from kleos.orchestration.worker import ObjectiveWorker
from kleos.projects.graph_repository import Effect, GraphRepository
from kleos.projects.interfaces import InterfaceService
from kleos.projects.prerequisites import PrerequisiteService
from kleos.projects.store import Objective, Project

__all__ = ['repository']


class Gate:
    """Minimal authoritative-service double; domain approval is tested separately."""
    def __init__(self, scope):
        self.scope, self.requests, self.publications = scope, {}, {}
        self.pending = self.add('round-one')
        self.inspected = None
        self.decision = None

    def add(self, request_id):
        self.requests[request_id] = {'objective': 'Firmware only, use the existing explicit board.',
            'baselineHash': 'a'*64, 'previousCandidate': None, 'importedSources': []}
        return {'requestId': request_id, 'runId': self.scope.run_id, 'round': len(self.requests),
                'missing': ['firmware']}

    def inspect(self, scope, tasks, results, task_inputs):
        assert scope == self.scope
        self.inspected = (tasks, results, task_inputs)
        return self.pending

    def request(self, scope, request_id):
        if scope != self.scope or request_id not in self.requests:
            raise DomainError('Unrecognized request', 'prerequisite_conflict')
        return copy.deepcopy(self.requests[request_id])

    def publish(self, scope, request_id, result):
        self.request(scope, request_id)
        assert result.status == 'succeeded' and not result.artifacts
        assert len(result.observations) == 1 and not result.observations[0].independent
        assert result.observations[0].status == 'UNKNOWN'
        encoded = result.model_dump(mode='json')
        if request_id in self.publications:
            assert self.publications[request_id] == encoded
        self.publications[request_id] = encoded
        return {'kind': 'interface_prerequisite', 'requestId': request_id, 'runId': scope.run_id,
                'proposal': encoded['observations'][0]['details']['candidate']}

    def resolve(self, scope, payload, value):
        if value != self.decision or self.decision is None:
            raise DomainError('Explicit user decision receipt required', 'approval_required')
        return {'status': 'approved'}

    def validate_resume(self, old, new, answer, interrupts):
        if (old != self.scope or new != old.model_copy(update={'revision': old.revision+1})
                or self.decision is None or answer != {'interrupt': self.decision} or interrupts != ['interrupt']):
            raise DomainError('Only canonical approval amendment is permitted', 'prerequisite_conflict')
        return True


def candidate():
    return {'summary': 'Review the explicit board before firmware generation.',
        'interfaces': {'firmware': {'board': 'nrf52840dk/nrf52840'}}, 'sourceIds': ['brief'],
        'limitations': ['Board behavior is unverified.']}


def configured(repo, scope, gate=None, model=None, **kwargs):
    gate = gate or Gate(scope)
    model = model or Model(output=candidate())
    resolved = []
    def resolve(selected_scope, context):
        resolved.append((selected_scope, bool(context.reference_images)))
        return model
    return InterfacePrerequisites(repo, gate, resolve, **kwargs), gate, model, resolved


async def test_proposal_replays_real_receipt_without_new_task_or_native_work(repository):
    repo, scope = repository
    adapter, gate, model, resolved = configured(repo, scope)
    task_plan, results, prepared = {'firmware': {'capability': 'firmware'}}, {}, {}
    pending = await adapter.inspect(scope, task_plan, results, prepared)
    assert gate.inspected == (task_plan, results, prepared)
    first = await adapter.proposal(scope, pending)
    reopened, _, _, _ = configured(GraphRepository(repo.store), scope, gate=gate, model=model)
    assert await reopened.proposal(scope, pending) == first
    assert model.calls == 1 and resolved == [(scope, False)]
    assert json.loads(model.packets[0])['request'] == gate.requests[pending['requestId']]
    with repo.store.transaction() as session:
        run = session.get(Objective, scope.run_id)
        assert run.revision == scope.revision and not run.data.get('results')
        assert not run.data.get('acceptedInterfaces') and not run.data.get('tasks')
        effects = list(session.scalars(select(Effect).where(Effect.run_id == scope.run_id)))
        assert [(row.key, row.status) for row in effects] == [('prerequisite:round-one:interfaces:model', 'completed')]


async def test_refinement_uses_new_request_receipt_without_overwriting_previous(repository):
    repo, scope = repository
    adapter, gate, model, _ = configured(repo, scope)
    await adapter.proposal(scope, gate.pending)
    before = copy.deepcopy(gate.publications['round-one'])
    next_request = gate.add('round-two')
    gate.requests['round-two']['previousCandidate'] = candidate()
    await adapter.proposal(scope, next_request)
    assert model.calls == 2 and gate.publications['round-one'] == before
    with repo.store.transaction() as session:
        assert len(list(session.scalars(select(Effect).where(Effect.run_id == scope.run_id)))) == 2


async def test_images_use_injected_pinned_vision_model_and_existing_loader(repository):
    repo, scope = repository
    context = await repo.context(scope)
    context.reference_images = [ArtifactRef(id='fixture-image', path='board.png', sha256='b'*64,
                                           revision=scope.revision, size=4, media_type='image/png')]
    async def current(_):
        return context
    repo.context = current
    loaded = []
    async def loader(selected_scope, selected_context):
        loaded.append((selected_scope, selected_context))
        return [{'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,ZGF0YQ=='}}]
    adapter, gate, model, resolved = configured(repo, scope, image_loader=loader)
    await adapter.proposal(scope, gate.pending)
    assert resolved == [(scope, True)] and loaded == [(scope, context)]
    assert model.packets[0][1]['type'] == 'image_url'
    assert json.loads(model.packets[0][0]['text'])['sourceIds'] == ['brief', 'fixture-image']


async def test_uncertain_model_receipt_never_blindly_retries(repository):
    repo, scope = repository
    adapter, gate, model, _ = configured(repo, scope)
    with repo.store.transaction() as session:
        session.add(Effect(id='uncertain', run_id=scope.run_id,
            key='prerequisite:round-one:interfaces:model', status='claimed', value={}))
    with pytest.raises(DomainError, match='reconciliation'):
        await adapter.proposal(scope, gate.pending)
    assert model.calls == 0 and not gate.publications


@pytest.mark.parametrize('change', ['cancel', 'foreign', 'unknown', 'context', 'oversized'])
async def test_invalid_authority_or_identity_fails_before_inference(repository, change):
    repo, scope = repository
    adapter, gate, model, _ = configured(repo, scope)
    pending = dict(gate.pending)
    if change == 'cancel':
        repo.cancel(scope.actor, scope.project_id, scope.run_id)
    elif change == 'foreign':
        pending['runId'] = 'other-run'
    elif change == 'unknown':
        pending['requestId'] = 'not-issued'
    elif change == 'oversized':
        pending['requestId'] = 'x'*81
    else:
        context = await repo.context(scope)
        async def wrong(_):
            return context.model_copy(update={'revision': scope.revision+1})
        repo.context = wrong
    with pytest.raises(DomainError):
        await adapter.proposal(scope, pending)
    assert model.calls == 0 and not gate.publications


async def test_resume_text_has_no_approval_authority_and_only_service_amendment_passes(repository):
    repo, scope = repository
    adapter, gate, _, _ = configured(repo, scope)
    payload = await adapter.proposal(scope, gate.pending)
    with pytest.raises(DomainError, match='Explicit user decision'):
        await adapter.resolve(scope, payload, 'approve interfaces')
    gate.decision = {'decisionId': 'server-committed-explicit-user-approval'}
    assert await adapter.resolve(scope, payload, gate.decision) == {'status': 'approved'}
    amended = scope.model_copy(update={'revision': scope.revision+1})
    assert await adapter.validate_resume(scope, amended, {'interrupt': gate.decision}, ['interrupt'])
    with pytest.raises(DomainError):
        await adapter.validate_resume(scope, amended.model_copy(update={'max_plan_calls': 32}),
                                      {'interrupt': gate.decision}, ['interrupt'])


class GraphGate(Gate):
    """Decision double only; tests exercise actual checkpoint and effect behavior."""
    def __init__(self, scope):
        super().__init__(scope)
        self.original = scope
        self.outcome = None

    def resolve(self, scope, payload, value):
        super().resolve(scope, payload, value)
        return self.outcome

    def validate_resume(self, old, new, answer, interrupts):
        if (old != self.original or new != self.scope or self.outcome != {'status': 'approved'}
                or len(interrupts) != 1 or answer != {interrupts[0].id: self.decision}):
            raise DomainError('Canonical amendment required', 'prerequisite_conflict')
        return True


async def test_actual_graph_wait_restart_refine_then_approved_revision_continuation(repository):
    repo, scope = repository
    gate, saver = GraphGate(scope), InMemorySaver()
    adapter, _, model, _ = configured(repo, scope, gate=gate)
    planned = TaskSpec(id='firmware', capability='firmware', objective='Build only the firmware', allowed_paths=['main.c'])
    planner = Planner([PlanDecision(tasks=[planned]), PlanDecision(done=True)])
    calls = []
    class Specialist:
        async def execute(self, selected_scope, task, context):
            calls.append((selected_scope, context.interfaces))
            assert gate.outcome == {'status': 'approved'}
            return TaskResult(task_id=task.id, status='succeeded', summary='Inert execution fixture')
    def engine():
        return ObjectiveEngine(repo, planner, Specialist(), checkpointer=saver, prerequisites=adapter)
    first = await engine().start(scope, 'Build the firmware for the supplied project')
    assert first['__interrupt__'] and not calls and model.calls == 1
    assert first['task_inputs'] == {} and first['turns'] == 1
    # Reentry at the wait node cannot run the proposal model a second time.
    repeated = await engine().resume(scope)
    assert repeated['__interrupt__'][0].id == first['__interrupt__'][0].id
    assert model.calls == 1 and not calls
    gate.decision = {'decisionId': 'explicit-user-refinement'}
    gate.outcome = {'status': 'revise', 'pending': gate.add('round-two')}
    second = await engine().resume(scope, answer={first['__interrupt__'][0].id: gate.decision})
    assert second['__interrupt__'] and model.calls == 2 and not calls
    assert second['turns'] == 1 and second['task_inputs'] == {}
    # Simulate the domain service's explicit command transaction only. Domain
    # authority/budget amendment is covered by the real service tests.
    with repo.store.transaction() as session:
        project = session.get(Project, scope.project_id)
        project.advance_revision()
        project.data = {**project.data, 'interfaces': {'firmware': {'board': 'nrf52840dk/nrf52840'}}}
        session.get(Objective, scope.run_id).revision = project.context_revision
        amended = scope.model_copy(update={'revision': project.context_revision})
    gate.scope, gate.decision, gate.outcome = amended, {'decisionId': 'explicit-user-approval'}, {'status': 'approved'}
    final = await engine().resume(amended, answer={second['__interrupt__'][0].id: gate.decision})
    assert final['done'] and final['turns'] == 2 and model.calls == 2
    assert calls == [(amended, {'firmware': {'board': 'nrf52840dk/nrf52840'}})]
    assert final['scope'] == amended.model_dump(mode='json') and amended.thread_id == scope.thread_id
    assert final['task_inputs']['firmware']['context']['revision'] == amended.revision
    with repo.store.transaction() as session:
        keys = set(session.scalars(select(Effect.key).where(Effect.run_id == scope.run_id)))
        assert {'plan:0', 'plan:1', 'prerequisite:round-one:interfaces:model',
                'prerequisite:round-two:interfaces:model'} <= keys


async def test_actual_graph_cannot_dispatch_on_forged_resume(repository):
    repo, scope = repository
    adapter, gate, model, _ = configured(repo, scope)
    saver = InMemorySaver()
    planner = Planner([PlanDecision(tasks=[TaskSpec(id='board', capability='pcb', objective='Route board')])])
    class NoDispatch:
        async def execute(self, *args):
            pytest.fail('Unapproved native work must not dispatch')
    engine = ObjectiveEngine(repo, planner, NoDispatch(), checkpointer=saver, prerequisites=adapter)
    state = await engine.start(scope, 'Route board')
    with pytest.raises(DomainError, match='Explicit user decision'):
        await engine.resume(scope, answer={state['__interrupt__'][0].id: 'approve interfaces'})
    assert model.calls == 1 and len(gate.publications) == 1


async def test_real_service_worker_and_graph_keep_same_run_through_refinement_and_approval(repository):
    repo, initial = repository
    gate = PrerequisiteService(repo.store, InterfaceService(repo.store), repo)
    model = Model(output=candidate())
    adapter = InterfacePrerequisites(repo, gate, lambda scope, context: model)
    saver = InMemorySaver()
    task = TaskSpec(id='firmware', capability='firmware', objective='Build only firmware', allowed_paths=['main.c'])
    planner = Planner([PlanDecision(tasks=[task]), PlanDecision(done=True)])
    dispatched = []
    class Specialist:
        async def execute(self, scope, task, context):
            dispatched.append((scope, context.interfaces))
            return TaskResult(task_id=task.id, status='succeeded', summary='Inert specialist fixture')
    def factory(scope):
        return ObjectiveEngine(repo, planner, Specialist(), checkpointer=saver, prerequisites=adapter)
    pins = {'native': [{'capability': 'firmware', 'firmwareBoard': 'nrf52840dk/nrf52840'}],
            'models': ['fixture-original-model'], 'tools': ['fixture-original-tool']}
    with repo.store.transaction() as session:
        run = session.get(Objective, initial.run_id)
        run.data = {**run.data, 'scope': initial.model_dump(mode='json'), 'runtimePins': pins}
    worker = ObjectiveWorker(repo.store, factory, locks=Locks())
    assert await worker.process(initial.run_id)
    with repo.store.transaction() as session:
        run, project = session.get(Objective, initial.run_id), session.get(Project, initial.project_id)
        assert run.status == 'waiting', run.data.get('blocker')
        revision, first_request = project.revision, run.data['prerequisiteCurrent']
        assert not dispatched and model.calls == 1 and project.data['interfaces'] == {}
    refined = gate.reply(initial.actor, initial.project_id, revision, 'clarify', text='Keep the exact qualified board.')
    assert refined['runId'] == initial.run_id
    assert await worker.process(initial.run_id)
    with repo.store.transaction() as session:
        run, project = session.get(Objective, initial.run_id), session.get(Project, initial.project_id)
        assert run.status == 'waiting', run.data.get('blocker')
        revision, second_request = project.revision, run.data['prerequisiteCurrent']
        assert second_request != first_request and not dispatched and model.calls == 2
        old_effects = {row.key: row.value for row in session.scalars(select(Effect).where(Effect.run_id == run.id))}
    approved = gate.reply(initial.actor, initial.project_id, revision, 'approve', text='approve interfaces')
    assert approved['runId'] == initial.run_id
    assert gate.reply(initial.actor, initial.project_id, revision, 'approve', text='approve interfaces') == approved
    assert await worker.process(initial.run_id)
    with repo.store.transaction() as session:
        run, project = session.get(Objective, initial.run_id), session.get(Project, initial.project_id)
        assert run.status == 'succeeded', run.data.get('blocker')
        assert run.data['runtimePins'] == pins and len(run.data['scopeAmendments']) == 1
        assert run.data['initialScope'] == initial.model_dump(mode='json')
        amended = initial.model_copy(update={'revision': project.context_revision})
        assert run.data['scope'] == amended.model_dump(mode='json')
        effects = {row.key: row.value for row in session.scalars(select(Effect).where(Effect.run_id == run.id))}
        assert all(effects[key] == value for key, value in old_effects.items())
        assert project.data['acceptedInterfaces']['engineeringValidated'] is False
        assert len(list(session.scalars(select(Objective)))) == 1
    assert model.calls == 2 and len(dispatched) == 1 and len(planner.calls) == 2
    assert dispatched == [(amended, {'firmware': {'board': 'nrf52840dk/nrf52840'}})]
