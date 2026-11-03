"""Versioned installation choice, queued snapshots and actual selected-tool dispatch."""
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from test_design import generate, result
from test_runtime import migrate, model, runtime_actor, settings, test_persistence

from kleos.api.catalog import CatalogFacade
from kleos.api.tool_policy import ToolInstallations, ToolPolicyService, snapshot_project_tool_policy
from kleos.capabilities.design.service import DesignService
from kleos.capabilities.native import ActionInput
from kleos.capabilities.registry import CapabilityRegistry, ToolManifest
from kleos.contracts.models import Actor, DomainError, Observation
from kleos.infrastructure.runtime import Runtime
from kleos.orchestration.engine import RunScope
from kleos.orchestration.specialist import ToolResult
from kleos.projects.engineering import EngineeringService
from kleos.projects.interfaces import InterfaceService
from kleos.projects.store import Objective, Project, ProjectStore


def installations(dispatches=None, *, versions=('1.0.0', '2.0.0')):
    registry = CapabilityRegistry()
    for version in versions:
        for action in ['read', 'patch', 'build', 'analyze', 'render', 'finish']:
            async def authorize(scope, task, args):
                assert task.capability == 'cad'
            async def invoke(scope, task, args, selected=version):
                if dispatches is not None:
                    dispatches.append((scope.run_id, selected))
                return ToolResult(summary='Trusted fixture handler observed', observations=[Observation(name='fixture', status='PASS')])
            registry.register(ToolManifest(id='cad.' + action, version=version, capability='cad', operation=action,
                provider='native', provider_version='fixture-' + version, installation_hash=version[0]*64,
                transport='native', qualified=True, description='Fixture native session binding'),
                ActionInput, invoke, authorize)
    return registry


def choice(version='1.0.0'):
    return {'mechanics': {'id': 'native-cad', 'version': version}}


def command(project, revision, version='1.0.0'):
    return {'type': 'project.setToolPolicy', 'expectedRevision': revision,
            'payload': {'projectId': project, 'toolPolicy': choice(version)}}


@pytest.fixture
def setup(tmp_path):
    store = ProjectStore(f'sqlite:///{tmp_path}/policy.db')
    store.initialize_for_tests()
    actor = Actor(tenant_id='tenant', user_id='owner')
    ident = store.create(actor, 'Only a bracket', 'create')['projectId']
    registry = installations()
    yield store, actor, ident, registry, ToolPolicyService(store, registry)
    store.engine.dispose()


def test_catalog_lists_all_complete_versions_and_exact_selection(setup):
    _, _, _, registry, service = setup
    catalog = ToolInstallations(registry).catalog()
    assert [(row['id'], row['version']) for row in catalog['tools']] == [('native-cad', '1.0.0'), ('native-cad', '2.0.0')]
    assert catalog['defaultPolicy'] == choice('2.0.0')
    assert sum(row['isDefault'] for row in catalog['tools']) == 1
    policy, bindings = service.resolve(choice())
    filtered = service.filter_tools({'policy': policy, 'bindings': bindings}, registry.catalog())
    assert len(filtered) == 6 and {pin['manifest']['version'] for pin in filtered} == {'1.0.0'}
    # Missing a required action cannot be disguised as a configured selectable session.
    registry.revoke('cad.build@1.0.0')
    rows = ToolInstallations(registry).catalog()['tools']
    assert rows[0]['qualified'] is False and rows[0]['isDefault'] is False
    with pytest.raises(DomainError):
        service.filter_tools({'policy': policy, 'bindings': bindings}, registry.catalog())
    remaining = service.filter_tools({}, registry.catalog())
    assert len(remaining) == 6 and {pin['manifest']['version'] for pin in remaining} == {'2.0.0'}


def test_policy_command_is_authorized_idempotent_and_context_preserving(setup):
    store, actor, ident, _, service = setup
    body = command(ident, 1)
    receipt = service.command(actor, body, 'save')
    assert service.command(actor, body, 'save') == receipt
    with store.transaction() as session:
        project = session.get(Project, ident)
        assert project.revision == 2 and project.context_revision == 1
        assert project.data['state']['toolPolicy'] == choice()
        assert project.data['state']['tools'] == {'mechanics': 'native-cad'}
        assert len(project.data['toolPolicyBindings']['mechanics']) == 6
    with pytest.raises(DomainError):
        service.command(actor, body, 'stale')
    with pytest.raises(DomainError):
        service.command(Actor(tenant_id='foreign', user_id='owner'), command(ident, 2), 'foreign')


@pytest.mark.parametrize('value', [
    {'unknown': {'id': 'native-cad', 'version': '1.0.0'}},
    {'electronics': {'id': 'native-cad', 'version': '1.0.0'}},
    {'mechanics': {'id': 'native-cad', 'version': '9.0.0'}},
    {'mechanics': {'id': 'native-cad', 'version': '1.0.0', 'endpoint': 'https://untrusted.example'}},
    {'mechanics': {'id': None, 'version': '1.0.0'}},
    {'mechanics': {'id': 'native-cad', 'version': None}},
    {'unknown': {'id': None, 'version': None}},
])
def test_invalid_lane_version_or_client_binding_fails_before_state_change(setup, value):
    store, actor, ident, _, service = setup
    body = command(ident, 1)
    body['payload']['toolPolicy'] = value
    with pytest.raises(DomainError):
        service.command(actor, body, 'invalid')
    assert store.context(actor, ident).revision == 1


def test_flutter_all_lanes_payload_normalizes_explicitly_empty_uninstalled_lane(setup):
    store, actor, ident, registry, service = setup
    body = command(ident, 1)
    # engineering_tools.dart serializes id and version for every displayed lane.
    body['payload']['toolPolicy']['electronics'] = {'id': None, 'version': None}
    accepted = service.command(actor, body, 'flutter-save')
    assert service.command(actor, body, 'flutter-save') == accepted
    with store.transaction() as session:
        snapshot = snapshot_project_tool_policy(session.get(Project, ident))
    assert snapshot['policy'] == choice() and set(snapshot['bindings']) == {'mechanics'}
    filtered = service.filter_tools(snapshot, registry.catalog())
    assert len(filtered) == 6 and {pin['manifest']['version'] for pin in filtered} == {'1.0.0'}


def test_select_tool_requires_one_installed_version_and_merges_policy(setup):
    _, actor, ident, registry, service = setup
    body = {'type': 'project.selectTool', 'expectedRevision': 1,
            'payload': {'projectId': ident, 'lane': 'mechanics', 'toolId': 'native-cad'}}
    with pytest.raises(DomainError, match='unambiguous'):
        service.command(actor, body, 'ambiguous')
    for entry in registry.catalog():
        if entry['manifest']['version'] == '2.0.0':
            registry.revoke(entry['key'])
    assert service.command(actor, body, 'selected')['revision'] == 2


def test_reinstalled_label_cannot_retarget_saved_exact_bindings(setup):
    _, _, _, registry, service = setup
    policy, bindings = service.resolve(choice())
    changed = registry.catalog()
    changed[0] = {**changed[0], 'digest': 'f'*64}
    with pytest.raises(DomainError, match='installation changed'):
        service.filter_tools({'policy': policy, 'bindings': bindings}, changed)


@pytest.mark.parametrize('kind', ['objective', 'project.generate'])
def test_submission_snapshots_selected_tool_before_later_preference_change(setup, kind):
    store, actor, ident, _, service = setup
    service.command(actor, command(ident, 1), 'v1')
    if kind == 'objective':
        submitted = store.submit(actor, ident, 'Create bracket', 2, 'run')
    else:
        submitted = EngineeringService(store).submit(actor, ident, kind, 2, 'run')
    service.command(actor, command(ident, submitted['revision'], '2.0.0'), 'v2')
    with store.transaction() as session:
        run = session.get(Objective, submitted['runId'])
        assert run.data['toolPolicySnapshot']['policy'] == choice()
        assert all(pin['manifest']['version'] == '1.0.0' for pin in run.data['toolPolicySnapshot']['bindings']['mechanics'])


async def test_capture_confirm_and_interface_objectives_freeze_tool_choice(setup):
    store, actor, ident, _, service = setup
    design = DesignService(store)
    service.command(actor, command(ident, 1), 'v1')
    captured = design.capture(actor, ident, {}, 2, 'capture')
    await generate(design, actor, captured, result())
    approved = design.confirm(actor, ident, captured['revision'], 'confirm')
    service.command(actor, command(ident, approved['revision'], '2.0.0'), 'v2')
    with store.transaction() as session:
        for run_id in [captured['runId'], approved['runId']]:
            run = session.get(Objective, run_id)
            assert run.data['toolPolicySnapshot']['policy'] == choice()
            run.status = 'succeeded'
        revision = session.get(Project, ident).revision
    proposed = InterfaceService(store).solve(actor, ident, revision, 'interface')
    service.command(actor, command(ident, proposed['revision']), 'v1-again')
    with store.transaction() as session:
        assert session.get(Objective, proposed['runId']).data['toolPolicySnapshot']['policy'] == choice('2.0.0')


async def test_actual_runtime_dispatch_uses_queued_binding_then_next_selected_version(tmp_path):
    dispatched, turns = [], {'plan': 0, 'action': 0}
    registry = installations(dispatched)

    def respond(request):
        body = json.loads(request.content)
        name = body['tools'][0]['function']['name']
        if name == 'PlanDecision':
            payload = {'tasks': [{'id': 'bracket', 'capability': 'cad', 'objective': 'Build bracket'}]} if turns['plan'] % 2 == 0 else {'done': True}
            turns['plan'] += 1
        else:
            payload = {'action': 'tool', 'tool': 'cad.build', 'arguments': {}} if turns['action'] % 2 == 0 else {'action': 'finish'}
            turns['action'] += 1
        return httpx.Response(200, json={'choices': [{'finish_reason': 'tool_calls', 'message': {
            'role': 'assistant', 'tool_calls': [{'id': 'fixture', 'type': 'function',
                'function': {'name': name, 'arguments': json.dumps(payload)}}]}}],
            'usage': {'prompt_tokens': 100, 'completion_tokens': 20}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        configured = settings(tmp_path, _env_file=None, model_catalog=[model('text')],
            text_model_key='text', meta_api_key='fixture-key', modal_api_key=None)
        runtime = Runtime(configured, registry=registry, persistence_factory=test_persistence,
            worker_locks=object(), model_client=client)
        migrate(runtime)
        actor = runtime_actor(runtime)
        ident = runtime.projects.create(actor, 'Only a bracket', 'create')['projectId']
        runtime.services.command(actor, command(ident, 1), 'v1')
        first = runtime.projects.submit(actor, ident, 'Build bracket', 2, 'first')
        changed = runtime.services.command(actor, command(ident, first['revision'], '2.0.0'), 'v2')
        async with runtime.start():
            scope = RunScope(actor=actor, project_id=ident, run_id=first['runId'], revision=first['revision'],
                deadline_at=datetime.now(UTC)+timedelta(minutes=5))
            assert (await runtime.engine_factory(scope).start(scope, 'Build bracket'))['done']
            assert dispatched == [(first['runId'], '1.0.0')]
            assert {pin['manifest']['version'] for pin in runtime._run_pins(scope)['tools']} == {'1.0.0'}
            with runtime.projects.transaction() as session:
                session.get(Objective, first['runId']).status = 'succeeded'
            second = runtime.projects.submit(actor, ident, 'Build bracket again', changed['revision'], 'second')
            next_scope = scope.model_copy(update={'run_id': second['runId'], 'revision': second['revision']})
            assert (await runtime.engine_factory(next_scope).start(next_scope, 'Build bracket again'))['done']
            assert dispatched == [(first['runId'], '1.0.0'), (second['runId'], '2.0.0')]
            with runtime.projects.transaction() as session:
                assert snapshot_project_tool_policy(session.get(Project, ident))['policy'] == choice('2.0.0')
            registry.revoke('cad.build@2.0.0')
            with pytest.raises(DomainError, match='unavailable'):
                runtime.engine_factory(next_scope)
        assert len(CatalogFacade(configured, registry).engineering_tools()['tools']) == 2
