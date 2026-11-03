import copy
import hashlib
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import ValidationError
from sqlalchemy import select

from kleos.access.service import AccessService, AccessSettings
from kleos.api.app import create_app
from kleos.api.interfaces import InterfaceFacade
from kleos.api.services import ApplicationServices
from kleos.artifacts.service import ArtifactService
from kleos.artifacts.storage import BlobStore
from kleos.capabilities.design.service import DesignService
from kleos.contracts.models import Actor, ArtifactRef, DomainError, Observation, TaskResult, TaskSpec
from kleos.orchestration.engine import ObjectiveEngine, RunScope
from kleos.projects.engineering import EngineeringService
from kleos.projects.graph_repository import Effect, execution_owner
from kleos.projects.interface_runner import InterfacePlanner, InterfaceRepository, InterfaceRunner
from kleos.projects.interfaces import InterfaceProposal, InterfaceService
from kleos.projects.store import Membership, Objective, Project, ProjectStore
from kleos.projects.views import ProjectViews


def proposal():
    return {'summary': 'A connector circuit and explicit firmware board for review.', 'interfaces': {
        'circuit': {'references': [
            {'reference': 'J1', 'value': 'Power', 'footprint': 'Connector:PinHeader_1x02', 'pins': {'1': 'VCC', '2': 'GND'}},
            {'reference': 'R1', 'value': '1k', 'footprint': 'Resistor:R_0603', 'pins': {'1': 'VCC', '2': 'GND'}}],
            'conceptReferences': {'power-input': 'J1'}, 'constraints': {'maxWidthMm': 40., 'maxHeightMm': 25.,
                'minTrackWidthMm': .3, 'minClearanceMm': .2, 'copperLayers': ['F.Cu', 'B.Cu']}},
        'firmware': {'board': 'nrf52840dk/nrf52840'}}, 'sourceIds': ['brief', 'approvedDesign'],
        'limitations': ['Electrical and physical behavior have not been independently checked.']}


class Model(BaseChatModel):
    output: dict
    calls: int = 0
    packets: list = []
    schema_name: str = ''

    @property
    def _llm_type(self):
        return 'mocked-interface-model'

    def bind_tools(self, tools, **kwargs):
        self.schema_name = tools[0].__name__
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.calls += 1
        self.packets.append(messages[-1].content)
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content='', tool_calls=[
            {'id': 'fixture', 'name': self.schema_name, 'args': self.output}]))])


@pytest.fixture
def setup(tmp_path):
    projects = ProjectStore(f'sqlite:///{tmp_path / "interfaces.db"}')
    projects.initialize_for_tests()
    actor = Actor(tenant_id='tenant', user_id='owner')
    ident = projects.create(actor, 'Repair only the connector and firmware target', 'create')['projectId']
    with projects.transaction() as session:
        project = session.get(Project, ident)
        project.data = {**project.data, 'approvedDesign': {'peripheralConceptIds': ['power-input'],
            'sceneHash': 'a'*64}, 'interfaces': {'mechanical': {'units': 'mm', 'height': 20}}}
    artifacts = ArtifactService(projects, BlobStore(tmp_path / 'files'))
    yield InterfaceService(projects, artifacts), actor, ident
    projects.engine.dispose()


def current(service, actor, ident):
    with service.projects.transaction() as session:
        return service.projects.authorized(session, actor, ident)


def queue(setup):
    service, actor, ident = setup
    receipt = service.solve(actor, ident, current(*setup).revision, 'solve')
    scope = RunScope(actor=actor, project_id=ident, run_id=receipt['runId'], revision=receipt['revision'],
        deadline_at=datetime.now(UTC) + timedelta(minutes=5))
    return receipt, scope


def result(value=None):
    return TaskResult(task_id='interfaces', status='succeeded', summary='Candidate interfaces',
        observations=[Observation(name='interfaces.proposal', status='UNKNOWN', independent=False,
            details={'candidate': value or proposal()})])


def publish(setup, value=None):
    service, _, _ = setup
    _, scope = queue(setup)
    service.publish(scope, result(value))
    with service.projects.transaction() as session:
        session.get(Objective, scope.run_id).status = 'succeeded'
    return scope


def test_solve_is_idempotent_candidate_only_and_preserves_context(setup):
    service, actor, ident = setup
    body = {'type': 'project.solveInterfaces', 'payload': {'projectId': ident}, 'expectedRevision': 1}
    facade = InterfaceFacade(service)
    receipt = facade.command(actor, body, 'solve')
    assert facade.command(actor, body, 'solve') == receipt
    row = current(*setup)
    assert row.data['interfaces'] == {'mechanical': {'units': 'mm', 'height': 20}}
    assert 'acceptedInterfaces' not in row.data
    with pytest.raises(DomainError, match='current objective'):
        service.solve(actor, ident, row.revision, 'duplicate')


def test_publication_visible_in_existing_workspace_never_self_approves(setup):
    service, actor, ident = setup
    scope = publish(setup)
    row = current(*setup)
    state = ProjectViews(service.projects).workspace(actor, ident)['project']['state']
    assert state['interfaceProposal']['status'] == 'pending'
    assert row.revision == scope.revision + 1 and row.context_revision == scope.revision
    assert 'approve interfaces' in state['conversation'][-1]['text']
    assert 'conceptReferences' in state['conversation'][-1]['text']
    assert 'circuit' not in row.data['interfaces'] and 'acceptedInterfaces' not in row.data


def test_explicit_message_accepts_exact_current_candidate_once(setup):
    service, actor, ident = setup
    publish(setup)
    row = current(*setup)
    facade = InterfaceFacade(service)
    body = {'type': 'project.message', 'payload': {'projectId': ident, 'text': 'approve interfaces'},
        'expectedRevision': row.revision}
    assert facade.handles(actor, body)
    receipt = facade.command(actor, body, 'approve')
    assert facade.command(actor, body, 'approve') == receipt
    approved = current(*setup)
    assert approved.data['interfaces']['mechanical'] == row.data['interfaces']['mechanical']
    assert approved.data['interfaces']['circuit']['conceptReferences'] == {'power-input': 'J1'}
    assert approved.data['acceptedInterfaces']['engineeringValidated'] is False
    assert approved.data['acceptedInterfaces']['approvedBy'] == actor.user_id
    assert approved.context_revision == approved.revision == row.revision + 1
    assert len(approved.data['interfaceApprovalHistory']) == 1


def test_direct_approval_requires_exact_identity_and_successful_run(setup):
    service, actor, ident = setup
    _, scope = queue(setup)
    service.publish(scope, result())
    row = current(*setup)
    pending = row.data['state']['interfaceProposal']
    with pytest.raises(DomainError, match='exact reviewed'):
        service.approve(actor, ident, row.revision, 'wrong', proposal_id=pending['id'], proposal_hash='b'*64)
    with pytest.raises(DomainError, match='finish successfully'):
        service.approve(actor, ident, row.revision, 'active', proposal_id=pending['id'], proposal_hash=pending['hash'])
    with service.projects.transaction() as session:
        session.get(Objective, scope.run_id).status = 'succeeded'
    service.approve(actor, ident, row.revision, 'right', proposal_id=pending['id'], proposal_hash=pending['hash'])


@pytest.mark.parametrize('text', ['yes', 'confirm', 'approved', 'please approve interfaces'])
def test_generic_message_never_approves(setup, text):
    service, actor, ident = setup
    publish(setup)
    row = current(*setup)
    facade = InterfaceFacade(service)
    body = {'type': 'project.message', 'payload': {'projectId': ident, 'text': text}, 'expectedRevision': row.revision}
    assert facade.handles(actor, body)
    receipt = facade.command(actor, body, 'respond')
    assert receipt['runId'] and 'acceptedInterfaces' not in current(*setup).data
    assert current(*setup).data['state']['interfaceProposal']['status'] == 'superseded'


@pytest.mark.parametrize('change', ['duplicate', 'missing', 'invented', 'unknown_reference', 'no_approved_ids'])
def test_mapping_requires_exact_approved_ids_and_unique_real_references(setup, change):
    service, _, ident = setup
    candidate = proposal()
    mapping = candidate['interfaces']['circuit']['conceptReferences']
    if change == 'duplicate':
        with service.projects.transaction() as session:
            project = session.get(Project, ident)
            project.data = {**project.data, 'approvedDesign': {'peripheralConceptIds': ['power-input', 'sensor']}}
        mapping['sensor'] = 'J1'
    elif change == 'missing':
        mapping.clear()
    elif change == 'invented':
        mapping['invented'] = 'R1'
    elif change == 'unknown_reference':
        mapping['power-input'] = 'J9'
    else:
        with service.projects.transaction() as session:
            project = session.get(Project, ident)
            project.data = {**project.data, 'approvedDesign': None}
    _, scope = queue(setup)
    with pytest.raises(DomainError) as error:
        service.publish(scope, result(candidate))
    assert error.value.code == 'interface_mapping_invalid'
    assert 'circuit' not in current(*setup).data['interfaces']


@pytest.mark.parametrize('change', ['revision', 'baseline', 'cancelled', 'viewer', 'worker'])
def test_approval_authority_context_and_cancellation_fences(setup, change):
    service, actor, ident = setup
    scope = publish(setup)
    row = current(*setup)
    token = None
    with service.projects.transaction() as session:
        project = session.get(Project, ident)
        if change == 'revision':
            project.advance_revision()
        elif change == 'baseline':
            project.data = {**project.data, 'requirements': {'items': ['Different design']}}
        elif change == 'cancelled':
            session.get(Objective, scope.run_id).status = 'cancelled'
        elif change == 'viewer':
            session.get(Membership, (ident, actor.user_id)).role = 'viewer'
        else:
            token = execution_owner.set((scope.run_id, 'worker'))
    try:
        with pytest.raises(DomainError):
            service.approve(actor, ident, row.revision, 'approval', message=True)
    finally:
        if token:
            execution_owner.reset(token)
    with service.projects.transaction() as session:
        assert 'acceptedInterfaces' not in session.get(Project, ident).data


def test_questions_and_cross_project_sources_cannot_be_accepted(setup):
    service, actor, ident = setup
    scope = publish(setup, {'summary': 'Choose a firmware board', 'questions': ['Which board is installed?']})
    row = current(*setup)
    assert row.data['state']['interfaceProposal']['status'] == 'needs_input'
    with pytest.raises(DomainError, match='complete pending'):
        service.approve(actor, ident, row.revision, 'approve', message=True)
    assert scope.run_id


async def test_real_graph_model_result_and_publication_replay_do_not_rebill(setup):
    service, actor, ident = setup
    _, scope = queue(setup)
    repo = InterfaceRepository(service.projects, service)
    model = Model(output=proposal())
    runner = InterfaceRunner(model, repo, service)
    task = TaskSpec(id='interfaces', objective='Propose shared interfaces', capability='research', max_actions=1)
    context = service.projects.context(actor, ident)
    # Simulate a crash after the inner model receipt, before result publication.
    first = await runner.execute(scope, task, context)
    assert model.calls == 1
    saver = InMemorySaver()
    engine = ObjectiveEngine(repo, InterfacePlanner(), runner, checkpointer=saver)
    state = await engine.start(scope, task.objective)
    assert state['done'] and model.calls == 1
    before = current(*setup).revision
    service.publish(scope, first)
    assert current(*setup).revision == before
    assert 'acceptedInterfaces' not in current(*setup).data


async def test_unknown_model_effect_blocks_without_repeating(setup):
    service, actor, ident = setup
    _, scope = queue(setup)
    repo = InterfaceRepository(service.projects, service)
    model = Model(output=proposal())
    with service.projects.transaction() as session:
        session.add(Effect(id='unknown', run_id=scope.run_id, key='interfaces:interfaces:model', status='claimed', value={}))
    runner = InterfaceRunner(model, repo, service)
    with pytest.raises(DomainError) as error:
        await runner.execute(scope, TaskSpec(id='interfaces', objective='Propose', capability='research'),
            service.projects.context(actor, ident))
    assert error.value.code == 'effect_uncertain' and model.calls == 0


@pytest.mark.parametrize('boundary', ['model_receipt', 'candidate_publication'])
async def test_process_restart_at_inner_boundaries_resumes_without_duplicate_work(setup, monkeypatch, boundary):
    service, _, _ = setup
    _, scope = queue(setup)
    repo = InterfaceRepository(service.projects, service)
    model = Model(output=proposal())
    runner = InterfaceRunner(model, repo, service)
    saver = InMemorySaver()
    if boundary == 'model_receipt':
        original = runner.execute
        async def crash(*args):
            await original(*args)
            raise RuntimeError('Injected process interruption after durable model receipt')
        monkeypatch.setattr(runner, 'execute', crash)
    else:
        original = service.publish
        def crash(*args):
            original(*args)
            raise RuntimeError('Injected process interruption after candidate publication')
        monkeypatch.setattr(service, 'publish', crash)
    engine = ObjectiveEngine(repo, InterfacePlanner(), runner, checkpointer=saver)
    with pytest.raises(RuntimeError, match='Injected'):
        await engine.start(scope, 'Propose scoped interfaces')
    assert model.calls == 1
    before = current(*setup).revision
    monkeypatch.undo()
    restarted = ObjectiveEngine(InterfaceRepository(service.projects, service), InterfacePlanner(),
        InterfaceRunner(model, repo, service), checkpointer=saver)
    state = await restarted.resume(scope)
    assert state['done'] and model.calls == 1
    assert current(*setup).revision == before + (boundary == 'model_receipt')
    with service.projects.transaction() as session:
        assert session.scalar(select(Effect).where(Effect.run_id == scope.run_id, Effect.key == 'task:interfaces')) is None


async def test_missing_vision_loader_fails_without_model_call(setup):
    service, actor, ident = setup
    _, scope = queue(setup)
    context = service.projects.context(actor, ident).model_copy(update={'reference_images': [
        ArtifactRef(id='image', path='reference.png', sha256='a'*64, revision=1, size=10)]})
    repo = InterfaceRepository(service.projects, service)
    model = Model(output=proposal())
    with pytest.raises(DomainError) as error:
        await InterfaceRunner(model, repo, service).execute(scope,
            TaskSpec(id='interfaces', objective='Use the supplied image', capability='research'), context)
    assert error.value.code == 'vision_input_unavailable' and model.calls == 0


def test_other_tenant_cannot_plan_or_approve(setup):
    service, _, ident = setup
    stranger = Actor(tenant_id='other', user_id='owner')
    with pytest.raises(DomainError) as error:
        service.solve(stranger, ident, 1, 'forbidden')
    assert error.value.code == 'not_found'
    publish(setup)
    with pytest.raises(DomainError) as error:
        service.approve(stranger, ident, current(*setup).revision, 'approve', message=True)
    assert error.value.code == 'not_found'


def test_unscoped_evidence_and_cancelled_publication_are_rejected(setup):
    service, _, _ = setup
    _, scope = queue(setup)
    value = proposal()
    value['sourceIds'] = ['other-project-file']
    with pytest.raises(DomainError) as error:
        service.publish(scope, result(value))
    assert error.value.code == 'artifact_scope_conflict'
    with service.projects.transaction() as session:
        session.get(Objective, scope.run_id).status = 'cancelled'
    with pytest.raises(DomainError) as error:
        service.publish(scope, result())
    assert error.value.code == 'run_inactive'


async def test_imported_sources_are_scoped_bounded_text_and_preserved(setup):
    service, actor, ident = setup
    content = 'Existing target: nrf52840dk/nrf52840; retain existing connector pinout.'
    upload = service.artifacts.upload(actor, ident, {'expectedEngineeringRevision': 1,
        'sha256': hashlib.sha256(content.encode()).hexdigest(),
        'file': {'path': 'existing-board.txt', 'content': content, 'mediaType': 'text/plain'}}, 'upload')
    with service.projects.transaction() as session:
        project = session.get(Project, ident)
        project.data = {**project.data, 'approvedDesign': None}
    original_manifest = copy.deepcopy(current(*setup).data['sourceManifest'])
    _, scope = queue(setup)
    repo = InterfaceRepository(service.projects, service)
    model = Model(output={'summary': 'Retain the imported firmware target only.',
        'interfaces': {'firmware': {'board': 'nrf52840dk/nrf52840'}}, 'sourceIds': [upload['artifact']['id']]})
    runner = InterfaceRunner(model, repo, service)
    state = await ObjectiveEngine(repo, InterfacePlanner(), runner, checkpointer=InMemorySaver()).start(scope, 'Only firmware target')
    assert state['done'] and model.calls == 1
    packet = json.loads(model.packets[0])
    assert packet['request']['importedSources'][0]['text'] == content
    assert current(*setup).data['sourceManifest'] == original_manifest
    assert 'circuit' not in current(*setup).data['interfaces']
    with service.projects.transaction() as session:
        session.get(Objective, scope.run_id).status = 'succeeded'
    service.approve(actor, ident, current(*setup).revision, 'approve', message=True)
    assert current(*setup).data['sourceManifest'] == original_manifest


@pytest.mark.parametrize('bad', [True, float('nan'), float('inf'), -1, 10000])
def test_interface_dimensions_are_strict_finite_and_bounded(bad):
    value = proposal()
    value['interfaces']['circuit']['constraints']['maxWidthMm'] = bad
    with pytest.raises(ValidationError):
        InterfaceProposal.model_validate(value)


def test_request_shape_forbids_model_or_client_self_approval(setup):
    service, actor, ident = setup
    facade = InterfaceFacade(service)
    with pytest.raises(ValidationError):
        facade.command(actor, {'type': 'project.solveInterfaces', 'expectedRevision': 1,
            'payload': {'projectId': ident, 'approved': True, 'interfaces': proposal()['interfaces']}}, 'unsafe')
    with service.projects.transaction() as session:
        assert not list(session.scalars(select(Objective)))


def test_concept_confirmation_does_not_fence_unrelated_active_work(setup):
    service, actor, ident = setup
    with service.projects.transaction() as session:
        project = session.get(Project, ident)
        project.data = {**project.data, 'state': {'clarification': {'readyToConfirm': True}}}
        session.add(Objective(id='active', project_id=ident, revision=1, status='running', data={'kind': 'objective'}))
    with pytest.raises(DomainError) as error:
        DesignService(service.projects).confirm(actor, ident, 1, 'confirm')
    assert error.value.code == 'run_conflict'
    with service.projects.transaction() as session:
        assert session.get(Objective, 'active').status == 'running'
        assert session.get(Project, ident).revision == 1


@pytest.mark.parametrize('needs_input', [False, True])
def test_default_generation_waits_for_interface_review_but_scoped_work_is_independent(setup, needs_input):
    service, actor, ident = setup
    publish(setup, {'summary': 'Specify the target', 'questions': ['Which board?']} if needs_input else None)
    before = current(*setup)
    engineering = EngineeringService(service.projects)
    with pytest.raises(DomainError) as error:
        engineering.submit(actor, ident, 'project.generate', before.revision, 'default')
    assert error.value.code == 'approval_required'
    assert current(*setup).revision == before.revision
    scoped = engineering.submit(actor, ident, 'project.generate', before.revision, 'scoped',
        objective='Generate only the mechanical mounting bracket; do not change electronics or firmware.')
    assert scoped['runId']
    assert 'acceptedInterfaces' not in current(*setup).data


async def test_http_existing_solve_and_message_commands_review_before_acceptance(setup):
    service, _, _ = setup
    access = AccessService(service.projects, AccessSettings(secret_key='x'*40,
        environment='test', development_no_login=True))
    actor = access.authenticate({}, {}, 'GET')
    ident = service.projects.create(actor, 'Set only the imported firmware target', 'http-create')['projectId']
    services = ApplicationServices(service.projects, access, interfaces=InterfaceFacade(service))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(services)), base_url='http://local') as client:
        response = await client.post('/api/v1/commands', headers={'Idempotency-Key': 'http-solve'},
            json={'type': 'project.solveInterfaces', 'expectedRevision': 1, 'payload': {'projectId': ident}})
        assert response.status_code == 202, response.text
        receipt = response.json()
        scope = RunScope(actor=actor, project_id=ident, run_id=receipt['runId'], revision=receipt['revision'],
            deadline_at=datetime.now(UTC)+timedelta(minutes=5))
        service.publish(scope, result({'summary': 'Explicit board choice awaiting user review.',
            'interfaces': {'firmware': {'board': 'nrf52840dk/nrf52840'}}, 'sourceIds': ['brief']}))
        with service.projects.transaction() as session:
            session.get(Objective, scope.run_id).status = 'succeeded'
        workspace = (await client.get(f'/api/v1/projects/{ident}/workspace')).json()
        assert workspace['project']['interfaces'] == {}
        assert 'approve interfaces' in workspace['project']['state']['conversation'][-1]['text']
        stale = await client.post('/api/v1/commands', headers={'Idempotency-Key': 'http-stale'},
            json={'type': 'project.message', 'expectedRevision': receipt['revision'],
                'payload': {'projectId': ident, 'text': 'approve interfaces'}})
        assert stale.status_code == 409
        accepted = await client.post('/api/v1/commands', headers={'Idempotency-Key': 'http-approve'},
            json={'type': 'project.message', 'expectedRevision': workspace['revision'],
                'payload': {'projectId': ident, 'text': 'approve interfaces'}})
        assert accepted.status_code == 202, accepted.text
        updated = (await client.get(f'/api/v1/projects/{ident}/workspace')).json()
        assert updated['project']['interfaces'] == {'firmware': {'board': 'nrf52840dk/nrf52840'}}
        assert updated['project']['state']['interfaceProposal']['engineeringValidated'] is False
