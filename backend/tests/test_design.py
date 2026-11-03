import base64
import io
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from alembic import command
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from PIL import Image
from pydantic import SecretStr, ValidationError

from kleos.api.app import create_app
from kleos.api.design import DesignFacade
from kleos.api.services import ApplicationServices
from kleos.capabilities.design.models import CaptureResult, ConceptScene
from kleos.capabilities.design.runner import DesignPlanner, DesignRepository, DesignRunner
from kleos.capabilities.design.service import DesignService
from kleos.contracts.models import Actor, DomainError, Observation, TaskResult
from kleos.orchestration.engine import ObjectiveEngine, RunScope
from kleos.projects.graph_repository import execution_owner
from kleos.projects.store import Objective, Project, ProjectStore
from kleos.projects.views import ProjectViews


def part(ident='body', **changes):
    return dict(id=ident, label=ident.title(), role='shell' if ident == 'body' else 'cover',
        shape='rounded_box', size={'x': 4, 'y': 3, 'z': 1}, color='#697080',
        position={'x': 0, 'y': 0, 'z': 0 if ident == 'body' else 1},
        explode={'x': 0, 'y': 0, 'z': 0 if ident == 'body' else 3}) | changes


def result(parts=None, **changes):
    return dict(message='A two-part enclosure candidate with separate lid.', readyToReview=True,
        requirements=['Separate lid and body'], scene={'schemaVersion': 'concept-scene-v2', 'title': 'Enclosure',
            'summary': 'Separate physical parts', 'deviceKind': 'enclosure', 'parts': parts or [part(), part('lid')]}) | changes


class Model(BaseChatModel):
    answers: list[dict]
    packets: list = []
    schema_name: str = ''
    calls: int = 0

    @property
    def _llm_type(self):
        return 'mocked-design-model'

    def bind_tools(self, tools, **kwargs):
        self.schema_name = tools[0].__name__
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.packets.append(messages[-1].content)
        self.calls += 1
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content='', tool_calls=[
            {'id': 'fixture', 'name': self.schema_name, 'args': self.answers.pop(0)}]))])


@pytest.fixture
def design(tmp_path):
    store = ProjectStore(f'sqlite:///{tmp_path / "design.db"}')
    store.initialize_for_tests()
    actor = Actor(tenant_id='tenant', user_id='owner')
    service = DesignService(store)
    yield service, actor
    store.engine.dispose()


def run_scope(service, actor, receipt):
    return RunScope(actor=actor, project_id=receipt['projectId'], run_id=receipt['runId'], revision=receipt['revision'],
                    deadline_at=datetime.now(UTC) + timedelta(minutes=5))


async def generate(service, actor, receipt, answer, *, model=None, image_loader=None):
    scope = run_scope(service, actor, receipt)
    repository = DesignRepository(service.projects, service)
    model = model or Model(answers=[answer])
    engine = ObjectiveEngine(repository, DesignPlanner(), DesignRunner(model, repository, service, image_loader),
                             checkpointer=InMemorySaver())
    state = await engine.start(scope, 'Design only the requested concept')
    return state, model, scope


async def test_capture_produces_existing_frontend_projection_without_approving_it(design):
    service, actor = design
    facade = DesignFacade(service)
    body = {'type': 'project.create', 'payload': {'brief': 'A case with removable lid', 'autoStart': True}}
    receipt = facade.command(actor, body, 'create')
    assert facade.command(actor, body, 'create') == receipt
    assert facade.handles(actor, body)
    state, model, _ = await generate(service, actor, receipt, result())
    assert state['done'] and model.calls == 1
    workspace = ProjectViews(service.projects).workspace(actor, receipt['projectId'])
    view = workspace['project']['state']
    assert workspace['runs'][0]['kind'] == 'project.capture'
    assert view['concept']['scene']['schemaVersion'] == 'concept-scene-v2'
    assert len(view['concept']['scene']['parts']) == 2
    assert view['concept']['scene']['parts'][1]['explode']['z'] == 3
    assert view['concept']['scene']['parts'][1]['position']['z'] == 1
    assert view['clarification']['readyToConfirm'] and not view['planning']['pending']
    assert view['conversation'][0]['role'] == 'user' and view['conversation'][-1]['role'] == 'assistant'
    assert service.projects.context(actor, receipt['projectId']).approved_design is None
    assert not service.projects.context(actor, receipt['projectId']).requirements


async def test_selected_part_is_generated_from_scratch_and_neighbors_remain_exact(design):
    service, actor = design
    initial = service.create(actor, 'Case', 'create')
    await generate(service, actor, initial, result())
    before = ProjectViews(service.projects).workspace(actor, initial['projectId'])['project']['state']['concept']
    request = {'text': 'Replace only the lid from scratch with a vented cap', 'selectedPartId': 'lid',
               'conceptVersion': before['version'], 'clientMessageId': 'redo-message'}
    receipt = service.capture(actor, initial['projectId'], request, initial['revision'], 'redo')
    assert service.capture(actor, initial['projectId'], request, initial['revision'], 'redo-another-key')['runId'] == receipt['runId']
    replacement = part('lid', shape='feature', geometry={'kind': 'extrusion',
        'outer': [[-.5, -.5], [.5, -.5], [.5, .5], [-.5, .5]],
        'holes': [[[-.2, -.2], [.2, -.2], [.2, .2], [-.2, .2]]], 'floor': 0})
    _, model, _ = await generate(service, actor, receipt, result([replacement], requirements=[]))
    packet = json.loads(model.packets[0])
    assert packet['replaceFromScratch']['id'] == 'lid'
    assert 'geometry' not in packet['replaceFromScratch'] and 'size' not in packet['replaceFromScratch']
    assert [p['id'] for p in packet['previousSceneWithoutSelectedGeometry']['parts']] == ['body']
    after = ProjectViews(service.projects).workspace(actor, receipt['projectId'])['project']['state']['concept']
    assert after['version'] == before['version'] + 1
    assert after['scene']['parts'][0] == before['scene']['parts'][0]
    assert after['scene']['parts'][1]['id'] == 'lid' and after['scene']['parts'][1]['shape'] == 'feature'


async def test_redo_cannot_return_changed_neighbors_or_reuse_stale_version(design):
    service, actor = design
    initial = service.create(actor, 'Case', 'create')
    await generate(service, actor, initial, result())
    with pytest.raises(DomainError, match='Concept changed'):
        service.capture(actor, initial['projectId'], {'text': 'Redo', 'selectedPartId': 'lid', 'conceptVersion': 77},
                        initial['revision'], 'stale')
    redo = service.capture(actor, initial['projectId'], {'text': 'Redo', 'selectedPartId': 'lid', 'conceptVersion': 1},
                           initial['revision'], 'redo')
    with pytest.raises(DomainError, match='exactly the selected'):
        await generate(service, actor, redo, result([part(), part('lid')], requirements=[]))
    saved = ProjectViews(service.projects).workspace(actor, initial['projectId'])['project']['state']['concept']
    assert saved['version'] == 1


async def test_user_confirmation_freezes_peripheral_ids_but_is_not_manufacturing_acceptance(design):
    service, actor = design
    initial = service.create(actor, 'Sensor case', 'create')
    await generate(service, actor, initial, result([part(), part('sensor', circuitRole='peripheral'),
                                                   part('board', circuitRole='board')]))
    token = execution_owner.set((initial['runId'], 'worker'))
    try:
        with pytest.raises(DomainError, match='Only a user'):
            service.confirm(actor, initial['projectId'], initial['revision'], 'worker-confirm')
    finally:
        execution_owner.reset(token)
    approved = service.confirm(actor, initial['projectId'], initial['revision'], 'confirm')
    assert service.confirm(actor, initial['projectId'], initial['revision'], 'confirm') == approved
    context = service.projects.context(actor, initial['projectId'])
    assert context.approved_design['peripheralConceptIds'] == ['sensor']
    assert context.approved_design['explodeAffectsManufacturing'] is False
    assert context.approved_design['units'] == 'relative-illustration'
    with service.projects.transaction() as session:
        run = session.get(Objective, approved['runId'])
        assert run.data['kind'] == 'project.solveInterfaces'
        assert session.get(Project, initial['projectId']).data.get('acceptedReleaseId') is None
    with pytest.raises(DomainError, match='already confirmed'):
        service.confirm(actor, initial['projectId'], approved['revision'], 'again')


async def test_new_draft_does_not_overwrite_previous_user_approved_reference(design):
    service, actor = design
    initial = service.create(actor, 'Case', 'create')
    await generate(service, actor, initial, result())
    approved = service.confirm(actor, initial['projectId'], initial['revision'], 'confirm')
    before = service.projects.context(actor, initial['projectId']).approved_design
    draft = service.capture(actor, initial['projectId'], {'text': 'Different lid', 'selectedPartId': 'lid', 'conceptVersion': 1},
                            approved['revision'], 'new-draft')
    await generate(service, actor, draft, result([part('lid', shape='box')], requirements=[]))
    assert service.projects.context(actor, initial['projectId']).approved_design == before
    state = ProjectViews(service.projects).workspace(actor, initial['projectId'])['project']['state']
    assert state['approvedDesignStatus'] == 'superseded' and state['confirmed'] is False


async def test_question_answers_are_saved_and_bound_to_current_hash(design):
    service, actor = design
    initial = service.create(actor, 'Case', 'create')
    question = {'id': 'width', 'type': 'number', 'prompt': 'What is the required width?', 'unit': 'mm', 'min': 1, 'max': 300}
    await generate(service, actor, initial, result(scene=None, questions=[question], readyToReview=False))
    state = ProjectViews(service.projects).workspace(actor, initial['projectId'])['project']['state']
    q = state['clarification']['questions'][0]
    with pytest.raises(DomainError, match='Question changed'):
        service.answer(actor, initial['projectId'], {'questionId': 'width', 'questionHash': 'stale', 'values': ['42']},
                       initial['revision'], 'bad')
    answer = service.answer(actor, initial['projectId'], {'questionId': 'width', 'questionHash': q['questionHash'], 'values': ['42']},
                            initial['revision'], 'answer')
    _, model, _ = await generate(service, actor, answer, result())
    assert json.loads(model.packets[0])['answers']['width'] == ['42']
    state = ProjectViews(service.projects).workspace(actor, initial['projectId'])['project']['state']
    assert state['clarification']['readyToConfirm'] and state['clarification']['resolvedQuestions'][0]['id'] == 'width'


async def test_publication_crash_recovers_from_model_receipt_without_another_call(design, monkeypatch):
    service, actor = design
    initial = service.create(actor, 'Case', 'create')
    scope = run_scope(service, actor, initial)
    model, saver = Model(answers=[result()]), InMemorySaver()
    repository = DesignRepository(service.projects, service)
    engine = ObjectiveEngine(repository, DesignPlanner(), DesignRunner(model, repository, service, None), checkpointer=saver)
    original = service.publish
    def crash(scope, result):
        original(scope, result)
        raise RuntimeError('Process stopped after candidate publication')
    monkeypatch.setattr(service, 'publish', crash)
    with pytest.raises(RuntimeError):
        await engine.start(scope, 'Case')
    monkeypatch.setattr(service, 'publish', original)
    new_model = Model(answers=[])
    new_repo = DesignRepository(service.projects, service)
    restarted = ObjectiveEngine(new_repo, DesignPlanner(), DesignRunner(new_model, new_repo, service, None), checkpointer=saver)
    assert (await restarted.resume(scope))['done']
    assert model.calls == 1 and new_model.calls == 0
    state = ProjectViews(service.projects).workspace(actor, initial['projectId'])['project']['state']
    assert state['concept']['version'] == 1
    assert len([m for m in state['conversation'] if m['role'] == 'assistant']) == 1


async def test_new_message_cancels_old_capture_and_prevents_stale_publication(design):
    service, actor = design
    initial = service.create(actor, 'Case', 'create')
    old_scope = run_scope(service, actor, initial)
    next_run = service.capture(actor, initial['projectId'], {'text': 'Changed requested shape'}, initial['revision'], 'new')
    assert next_run['revision'] == initial['revision'] + 1
    observed = TaskResult(task_id='capture', status='succeeded', summary='Old', observations=[
        Observation(name='design.capture', status='UNKNOWN', details={'candidate': result()})])
    with pytest.raises(DomainError, match='no longer active'):
        service.publish(old_scope, observed)
    with service.projects.transaction() as session:
        assert session.get(Objective, initial['runId']).status == 'cancelled'


@pytest.mark.parametrize('geometry', [
    {'kind': 'extrusion', 'outer': [[-.5, -.5], [.5, .5], [.5, -.5], [-.5, .5]]},
    {'kind': 'extrusion', 'outer': [[-.5, -.5], [.5, -.5], [.5, .5], [-.5, .5]],
     'holes': [[[-.6, 0], [0, 0], [0, .1]]]},
    {'kind': 'loft', 'sections': [{'z': .4, 'outer': [[0, 0], [.3, 0], [0, .3]]},
                                {'z': -.4, 'outer': [[0, 0], [.3, 0], [0, .3]]}]},
    {'kind': 'sweep', 'path': [[0, 0, 0], [0, 0, 0]], 'radius': .1},
    {'kind': 'revolution', 'profile': [[-.2, -.2], [.2, 0], [.2, .2]]},
])
def test_invalid_feature_geometry_is_rejected_before_viewer(geometry):
    with pytest.raises(ValidationError):
        CaptureResult.model_validate(result([part(shape='feature', geometry=geometry)]))


def test_scene_rejects_duplicate_ids_nonfinite_dimensions_and_unknown_code():
    for parts in ([part(), part()], [part(size={'x': float('nan'), 'y': 1, 'z': 1})],
                  [part(shape='feature', geometry={'kind': 'python', 'code': 'run()'})]):
        with pytest.raises(ValidationError):
            ConceptScene.model_validate(result(parts)['scene'])


async def test_design_facade_uses_real_existing_command_endpoint(design):
    service, actor = design
    class Access:
        def authenticate(self, *args):
            return actor
    services = ApplicationServices(service.projects, Access(), design=DesignFacade(service))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(services)), base_url='http://test') as client:
        created = await client.post('/api/v1/commands', headers={'Idempotency-Key': 'capture-create'},
            json={'type': 'project.create', 'payload': {'brief': 'Case', 'autoStart': True}})
        assert created.status_code == 202 and created.json()['runId']
        value = created.json()
        message = await client.post('/api/v1/commands', headers={'Idempotency-Key': 'capture-message'},
            json={'type': 'project.message', 'expectedRevision': value['revision'],
                  'payload': {'projectId': value['projectId'], 'text': 'Add a removable lid', 'clientMessageId': 'm1', 'autoResume': True}})
        assert message.status_code == 202
        assert message.json()['revision'] > value['revision']
        with service.projects.transaction() as session:
            assert session.get(Objective, message.json()['runId']).data['kind'] == 'project.capture'


@pytest.mark.parametrize('vision_enabled', [True, False])
async def test_runtime_capture_sends_actual_authorized_image_only_to_qualified_vision_model(tmp_path, vision_enabled):
    from kleos.infrastructure.runtime import Runtime, migration_config
    from kleos.infrastructure.settings import Settings

    @asynccontextmanager
    async def persistence(*args, **kwargs):
        yield InMemorySaver(), InMemoryStore()

    calls = []
    def respond(request):
        body = json.loads(request.content)
        calls.append(body)
        assert body['model'] == 'vision-pinned'
        assert body['tools'][0]['function']['name'] == 'CaptureResult'
        assert any(part['type'] == 'image_url' for part in body['messages'][1]['content'])
        return httpx.Response(200, json={'id': 'capture-fixture', 'choices': [{'finish_reason': 'tool_calls', 'message': {
            'role': 'assistant', 'tool_calls': [{'id': 'capture', 'type': 'function', 'function': {
                'name': 'CaptureResult', 'arguments': json.dumps(result())}}]}}],
            'usage': {'prompt_tokens': 100, 'completion_tokens': 100}})

    common = {'provider': 'meta', 'protocol': 'openai_chat', 'version': '1', 'baseUrl': 'https://meta.example/v1',
              'qualified': True, 'maxInputTokens': 100000, 'inputPriceMicroUsdPerMillion': 1000000,
              'outputPriceMicroUsdPerMillion': 1000000}
    settings = Settings(environment='test', database_url=f'sqlite:///{tmp_path / "capture.db"}',
        artifact_root=tmp_path / 'blobs', session_secret=SecretStr('s' * 40),
        model_catalog=[{**common, 'key': 'text', 'model': 'text-pinned'},
                       {**common, 'key': 'vision', 'model': 'vision-pinned', 'vision': True}],
        text_model_key='text', vision_model_key='vision' if vision_enabled else None, meta_api_key=SecretStr('mock-key'))
    client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    runtime = Runtime(settings, persistence_factory=persistence, worker_locks=object(), model_client=client)
    with runtime.projects.engine.begin() as connection:
        migration = migration_config()
        migration.attributes['connection'] = connection
        command.upgrade(migration, 'head')
    from test_runtime import runtime_actor
    actor = runtime_actor(runtime)
    project = runtime.projects.create(actor, 'Design from sketch', 'create')['projectId']
    raw = io.BytesIO()
    Image.new('RGB', (4, 4), 'blue').save(raw, format='PNG')
    image = runtime.artifacts.reference_image(actor, project,
        {'name': 'sketch.png', 'contentBase64': base64.b64encode(raw.getvalue()).decode()})
    current = runtime.projects.context(actor, project)
    submitted = runtime.design.capture(actor, project, {'referenceImageIds': [image['id']]}, current.revision, 'capture')
    scope = run_scope(runtime.design, actor, submitted)
    async with client, runtime.start():
        if vision_enabled:
            assert (await runtime.engine_factory(scope).start(scope, 'Design from sketch'))['done']
            assert len(calls) == 1
        else:
            with pytest.raises(DomainError, match='not configured'):
                runtime.engine_factory(scope)
            assert calls == []


def test_complex_feature_profiles_fit_existing_viewer_contract():
    square = [[-.5, -.5], [.5, -.5], [.5, .5], [-.5, .5]]
    features = [
        {'kind': 'extrusion', 'outer': square, 'holes': [[[-.3, -.3], [.3, -.3], [.3, .3], [-.3, .3]]], 'floor': .15},
        {'kind': 'revolution', 'profile': [[0, -.5], [.5, -.5], [.4, .5], [0, .5]]},
        {'kind': 'loft', 'sections': [{'z': -.5, 'outer': square},
                                    {'z': .5, 'outer': [[x*.6, y*.6] for x, y in square]}]},
        {'kind': 'sweep', 'path': [[-.3, 0, 0], [0, 0, 0], [.3, .2, 0]], 'radius': .04, 'innerRadius': .02},
    ]
    scene = ConceptScene.model_validate(result([part(f'part{i}', shape='feature', geometry=geometry)
                                               for i, geometry in enumerate(features)])['scene'])
    encoded = scene.model_dump(mode='json', by_alias=True)
    assert [p['geometry']['kind'] for p in encoded['parts']] == ['extrusion', 'revolution', 'loft', 'sweep']
    assert encoded['parts'][-1]['geometry']['innerRadius'] == .02
    assert all(p.triangles() <= 4096 for p in scene.parts)


async def test_critical_unknown_dimensions_and_untrusted_evidence_block_confirmation(design):
    service, actor = design
    initial = service.create(actor, 'Bracket for an unknown mounting hole', 'create')
    constraint = {'id': 'mount', 'partIds': ['body'], 'kind': 'dimension',
        'description': 'Mounting hole diameter still needs measurement', 'unit': 'mm',
        'certainty': 'unknown', 'critical': True}
    await generate(service, actor, initial, result(constraints=[constraint]))
    with pytest.raises(DomainError, match='Resolve or review'):
        service.confirm(actor, initial['projectId'], initial['revision'], 'confirm')
    update = service.capture(actor, initial['projectId'], {'text': 'Try again'}, initial['revision'], 'update')
    constraint.update(certainty='known', value=5, sourceIds=['invented-source'])
    with pytest.raises(DomainError, match='unknown parts or evidence'):
        await generate(service, actor, update, result(constraints=[constraint]))
