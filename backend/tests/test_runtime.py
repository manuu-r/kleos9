import base64
import io
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from alembic import command
from function_calls import function_call, provider_name
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from PIL import Image
from pydantic import SecretStr
from sqlalchemy import inspect

from kleos.capabilities.registry import CapabilityRegistry, ToolManifest
from kleos.capabilities.services import SearchInput
from kleos.cli import main
from kleos.contracts.models import Actor, DomainError, Observation
from kleos.execution.protocol import PROTOCOL
from kleos.infrastructure.runtime import (
    NativeToolConfiguration,
    Runtime,
    migration_config,
    native_qualification,
)
from kleos.infrastructure.settings import Settings
from kleos.orchestration.engine import RunScope
from kleos.orchestration.specialist import ToolResult
from kleos.projects.store import Objective


@asynccontextmanager
async def test_persistence(url, *, setup):
    assert setup is False
    yield InMemorySaver(), InMemoryStore()


test_persistence.__test__ = False


def settings(tmp_path, **changes):
    return Settings(environment='test', database_url=f'sqlite:///{tmp_path / "runtime.db"}',
        artifact_root=tmp_path / 'blobs', native_control_root=tmp_path / 'native',
        slicer_control_root=tmp_path / 'slicing', checker_control_root=tmp_path / 'checkers',
        session_secret=SecretStr('s' * 40), development_no_login=True, **changes)


def migrate(runtime):
    with runtime.projects.engine.begin() as connection:
        config = migration_config()
        config.attributes['connection'] = connection
        command.upgrade(config, 'head')


def runtime_actor(runtime, *, tenant='tenant', user='owner'):
    from kleos.access.service import Account, Organization, OrganizationMember
    with runtime.projects.transaction() as session:
        session.add(Account(id=user, email=user+'@fixture.invalid', password_hash='fixture-login-disabled'))
        if session.get(Organization, tenant) is None:
            session.add(Organization(id=tenant, name='Fixture organization'))
        session.add(OrganizationMember(tenant_id=tenant, user_id=user, role='owner'))
    return Actor(tenant_id=tenant, user_id=user)


def model(key, vision=False):
    return dict(key=key, model=key + '-pinned', provider='meta', protocol='openai_chat', version='1',
        baseUrl='https://meta.example/v1', qualified=True, vision=vision, maxInputTokens=50000,
        inputPriceMicroUsdPerMillion=1000000, outputPriceMicroUsdPerMillion=2000000)


def installed_registry():
    registry = CapabilityRegistry()
    async def authorize(scope, task, arguments):
        assert task.capability == 'research'
    async def invoke(scope, task, arguments):
        return ToolResult(summary='Fixture tool observed', observations=[Observation(name='fixture', status='PASS')])
    registry.register(ToolManifest(id='research.inspect', version='1.0.0', capability='research', operation='inspect',
        provider='fixture', provider_version='1', installation_hash='a' * 64, transport='internal',
        qualified=True, description='Inspect fixture'), SearchInput, invoke, authorize)
    return registry


async def test_runtime_does_not_create_schema_or_connect_models_on_start(tmp_path):
    runtime = Runtime(settings(tmp_path), persistence_factory=test_persistence, worker_locks=object())
    assert inspect(runtime.projects.engine).get_table_names() == []
    with pytest.raises(DomainError, match='migrate'):
        async with runtime.start():
            pytest.fail('Unmigrated runtime cannot start')
    assert inspect(runtime.projects.engine).get_table_names() == []


async def test_runtime_composes_real_api_and_reports_partial_readiness(tmp_path):
    runtime = Runtime(settings(tmp_path), persistence_factory=test_persistence, worker_locks=object())
    migrate(runtime)
    assert runtime.readiness().status_code == 503
    async with runtime.start():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=runtime.app), base_url='http://localhost') as client:
            assert (await client.get('/api/v1/health')).status_code == 200
            readiness = (await client.get('/api/v1/ready')).json()
            assert readiness['status'] == 'partial' and readiness['frontendContractComplete'] is False
            assert readiness['capabilities'] == ['research'] and readiness['physicalActions'] is False
            created = await client.post('/api/v1/commands', headers={'Idempotency-Key': 'create'},
                json={'type': 'project.create', 'payload': {'brief': 'Only bracket'}})
            assert created.status_code == 202
            workspace = await client.get('/api/v1/projects/' + created.json()['projectId'] + '/workspace')
            assert workspace.status_code == 200
            assert (await client.get('/api/v1/engineering-tools')).status_code == 200
            preflight = await client.options('/api/v1/commands', headers={
                'Origin': 'http://localhost:5180', 'Access-Control-Request-Method': 'POST'})
            assert preflight.headers['access-control-allow-origin'] == 'http://localhost:5180'
            foreign = await client.options('/api/v1/commands', headers={
                'Origin': 'https://preview.example', 'Access-Control-Request-Method': 'POST'})
            assert 'access-control-allow-origin' not in foreign.headers
    assert not runtime.ready


async def test_runtime_vision_selection_actual_graph_and_model_policy_survive_settings_change(tmp_path):
    calls, plan_count, action_count = [], 0, 0
    def provider(request):
        nonlocal plan_count, action_count
        body = json.loads(request.content)
        calls.append(body)
        name = provider_name(body)
        if name == 'PlanDecision':
            payload = {'tasks': [{'id': 'inspect', 'objective': 'Inspect reference', 'capability': 'research',
                                 'maxActions': 4}]} if plan_count == 0 else {'done': True}
            plan_count += 1
        else:
            payload = {'action': 'tool', 'tool': 'research.inspect', 'arguments': {'query': 'Shape'}} if action_count == 0 else {'action': 'finish'}
            action_count += 1
        name, payload = function_call(name, payload)
        return httpx.Response(200, json={'id': 'test-request', 'choices': [{'finish_reason': 'tool_calls', 'message': {
            'role': 'assistant', 'tool_calls': [{'id': 'call', 'type': 'function', 'function': {
                'name': name, 'arguments': json.dumps(payload)}}]}}],
            'usage': {'prompt_tokens': 100, 'completion_tokens': 20}})
    client = httpx.AsyncClient(transport=httpx.MockTransport(provider))
    configured = settings(tmp_path, model_catalog=[model('text'), model('vision', True)],
        text_model_key='text', vision_model_key='vision', meta_api_key=SecretStr('not-a-real-key'))
    runtime = Runtime(configured, registry=installed_registry(), persistence_factory=test_persistence,
                      worker_locks=object(), model_client=client)
    migrate(runtime)
    actor = runtime_actor(runtime)
    project = runtime.projects.create(actor, 'Inspect an uploaded sketch', 'create')['projectId']
    raw = io.BytesIO()
    Image.new('RGB', (4, 4), 'red').save(raw, format='PNG')
    runtime.artifacts.reference_image(actor, project,
        {'name': 'sketch.png', 'contentBase64': base64.b64encode(raw.getvalue()).decode()})
    run = runtime.projects.submit(actor, project, 'Inspect reference only', runtime.projects.context(actor, project).revision, 'run')
    scope = RunScope(actor=actor, project_id=project, run_id=run['runId'], revision=run['revision'],
                    deadline_at=datetime.now(UTC) + timedelta(minutes=5))
    async with client, runtime.start():
        engine = runtime.engine_factory(scope)
        assert (await engine.start(scope, 'Inspect reference'))['done']
        assert calls[0]['model'] == calls[-1]['model'] == 'vision-pinned'
        assert calls[1]['model'] == 'text-pinned'
        assert any(part['type'] == 'image_url' for part in calls[0]['messages'][1]['content'])
        configured.model_catalog = [model('replacement')]
        configured.text_model_key, configured.vision_model_key = 'replacement', None
        configured.max_model_calls = 999
        restored = runtime.engine_factory(scope)
        assert restored is not None
        pins = runtime._run_pins(scope)
        assert pins['modelPolicy']['text_model_key'] == 'text'
        assert pins['modelPolicy']['vision_model_key'] == 'vision'
        assert pins['modelPolicy']['max_calls'] == 100
        with runtime.projects.transaction() as session:
            encoded = json.dumps(session.get(Objective, scope.run_id).data['runtimePins'])
            assert 'not-a-real-key' not in encoded and 'session_secret' not in encoded
        assert runtime.memory_for_run(scope).extractor is not None
        seen, original = {}, runtime._gateway

        class Spy:
            def __init__(self, inner):
                self.inner = inner

            def chat_model(self, chat_scope, **kwargs):
                seen.update(kwargs)
                return self.inner.chat_model(chat_scope, **kwargs)

        runtime._gateway = lambda gateway_scope, gateway_pins: Spy(original(gateway_scope, gateway_pins))
        runtime.memory_for_run(scope)
        # Background memory calls use the project's pinned coordination model, never the server default provider.
        assert seen == {'model_key': pins['laneModels'].get('coordination')}


async def test_missing_tools_block_engine_without_provider_requests(tmp_path):
    runtime = Runtime(settings(tmp_path, model_catalog=[model('text')], text_model_key='text',
        meta_api_key=SecretStr('key')), persistence_factory=test_persistence, worker_locks=object())
    migrate(runtime)
    for installed in runtime.registry.catalog():
        runtime.registry.revoke(installed['key'])
    actor = runtime_actor(runtime)
    project = runtime.projects.create(actor, 'Bracket', 'create')['projectId']
    run = runtime.projects.submit(actor, project, 'Bracket', 1, 'run')
    scope = RunScope(actor=actor, project_id=project, run_id=run['runId'], revision=run['revision'],
                    deadline_at=datetime.now(UTC) + timedelta(minutes=5))
    async with runtime.start():
        with pytest.raises(DomainError, match='No qualified'):
            runtime.engine_factory(scope)


def test_native_registration_requires_exact_image_policy_and_success_evidence(tmp_path):
    report = tmp_path / 'qualification.json'
    entry = NativeToolConfiguration(capability='cad', image_digest='sha256:' + 'a' * 64,
        provider_version='1', qualified=True, qualification_reference=report)
    evidence = {'qualified': True, 'imageId': entry.image_digest, 'protocol': PROTOCOL, 'lane': 'cad',
        'sourceOverride': False, 'networkPolicy': 'in-process-seccomp', 'scope': 'Fixture only',
        'checks': [{'action': action, 'response': {'blockers': [], 'observations': [],
                    'artifacts': [{'id': 'fixture'}] if action == 'finish' else []}}
                   for action in ['patch', 'build', 'analyze', 'render', 'finish']],
        'containers': [{'image': entry.image_digest, 'readonlyRootfs': True, 'networkMode': 'none',
                        'memory': 4294967296, 'nanoCpus': 2000000000, 'pidsLimit': 128}]}
    report.write_text(json.dumps(evidence))
    assert native_qualification(entry)['qualificationScope'] == 'Fixture only'
    with pytest.raises(DomainError, match='exact image'):
        native_qualification(entry, desktop=True)
    evidence['sourceOverride'] = True
    report.write_text(json.dumps(evidence))
    with pytest.raises(DomainError, match='exact image'):
        native_qualification(entry)
    evidence['sourceOverride'], evidence['qualified'] = False, False
    report.write_text(json.dumps(evidence))
    with pytest.raises(DomainError, match='exact image'):
        native_qualification(entry)


def test_cli_export_is_offline_and_does_not_overwrite_frozen_target(tmp_path, monkeypatch):
    monkeypatch.setenv('KLEOS_DATABASE_URL', 'invalid')
    output = tmp_path / 'runtime-openapi.json'
    assert main(['export-runtime-contracts', '--output', str(output)]) == 0
    document = json.loads(output.read_text())
    assert '/api/v1/projects/{project_id}/workspace' in document['paths']
    assert '/api/v1/ready' in document['paths']
    protected = tmp_path / 'openapi.json'
    protected.write_text('preserve')
    assert main(['export-runtime-contracts', '--output', str(protected)]) == 2
    assert protected.read_text() == 'preserve'


async def test_runtime_installs_only_explicit_independent_checker_and_scoped_slicer(tmp_path):
    root = Path(__file__).parents[1] / 'src/kleos'
    report = root / 'validation/qualification/cad-check-exact-image.json'
    slicing = root / 'capabilities/fabrication/qualification/slicer-qualified-2026-11-02.json'
    evidence, slicer_evidence = json.loads(report.read_text()), json.loads(slicing.read_text())
    configured = settings(tmp_path, cad_checker_installation={
        'imageDigest': evidence['imageId'], 'qualificationReference': str(report)},
        slicer_installation={'image': slicer_evidence['image'], 'report': str(slicing), 'dockerDesktop': True})
    runtime = Runtime(configured, persistence_factory=test_persistence, worker_locks=object())
    migrate(runtime)
    async with runtime.start():
        assert runtime.registry.available() == ['fabrication', 'research', 'validation']
        wait = next(item for item in runtime.registry.catalog() if item['manifest']['operation'] == 'wait')
        assert wait['key'] == 'fabrication.wait.orca@1.0.0'
        assert wait['manifest']['effect'] != 'external_action'
        assert runtime.services.releases.validation is runtime.validation
        assert runtime.validation.registry.snapshot('cad.export-integrity')['policy']['kind'] == 'digital'
        assert runtime.fabrication_runtime.enabled
        assert runtime.services.fabrication is runtime.fabrication_runtime.facade
        assert runtime.bambu.preparations is runtime.fabrication_runtime.preparations
    assert runtime.fabrication_runtime._closed


def test_firmware_qualification_pins_board_and_rejects_implicit_default(tmp_path):
    report = Path(__file__).parents[1] / 'src/kleos/execution/qualification/firmware-exact-image.json'
    evidence = json.loads(report.read_text())
    local = tmp_path / 'firmware.json'
    local.write_text(json.dumps(evidence))
    config = NativeToolConfiguration(capability='firmware', image_digest=evidence['imageId'],
        provider_version='zephyr-4.2.0', qualified=True, qualification_reference=local)
    assert native_qualification(config, desktop=True)['firmwareBoard'] == 'nrf52840dk/nrf52840'
    del evidence['firmwareBoard']
    local.write_text(json.dumps(evidence))
    with pytest.raises(DomainError, match='explicit supported board'):
        native_qualification(config, desktop=True)


def test_web_qualification_requires_exact_target_and_hard_scratch_bounds(tmp_path):
    report = Path(__file__).parents[1] / 'src/kleos/execution/qualification/web-exact-image.json'
    original = json.loads(report.read_text())
    local = tmp_path / 'web.json'
    local.write_text(json.dumps(original))
    config = NativeToolConfiguration(capability='application', image_digest=original['imageId'],
        provider_version='flutter-web-3.35.4', qualified=True, qualification_reference=local)
    pin = native_qualification(config, desktop=True)
    assert pin['applicationTarget'] == 'web' and pin['resources']['scratchBytes'] == 4 * 1024**3
    for failure in ['target', 'scratch', 'host_mount']:
        changed = json.loads(json.dumps(original))
        if failure == 'target':
            changed['applicationTarget'] = 'apk'
        elif failure == 'scratch':
            changed['containers'][0]['scratch'] = 'rw,exec,size=8589934592'
        else:
            changed['containers'][0]['writableHostMounts'] = True
        local.write_text(json.dumps(changed))
        with pytest.raises(DomainError, match='hard resource limits'):
            native_qualification(config, desktop=True)


def test_run_tool_pins_are_account_scoped_and_account_rotation_fences_recovery(tmp_path):
    configured = settings(tmp_path, model_catalog=[model('text')], text_model_key='text',
        meta_api_key=SecretStr('fixture-key'))
    runtime = Runtime(configured, registry=installed_registry(), persistence_factory=test_persistence)
    migrate(runtime)
    key = 'research.inspect@1.0.0'
    assert key in {item['key'] for item in runtime.registry.catalog()}
    accounts = {'owner': {'provider': 'fixture', 'installationHash': 'a' * 64}}
    runtime.external_runtime.tool_keys = [key]
    runtime.external_runtime.pins_for = lambda actor: [accounts[actor.user_id]] if actor.user_id in accounts else []
    runtime.external_runtime.tool_keys_for = lambda actor: [key] if actor.user_id in accounts else []
    scopes = []
    for user in ['owner', 'other']:
        actor = runtime_actor(runtime, tenant=user + '-tenant', user=user)
        project = runtime.projects.create(actor, 'Inspect only', 'create')['projectId']
        admitted = runtime.projects.submit(actor, project, 'Inspect', 1, 'run')
        scopes.append(RunScope(actor=actor, project_id=project, run_id=admitted['runId'],
            revision=admitted['revision'], deadline_at=datetime.now(UTC) + timedelta(minutes=5)))
    first = runtime._run_pins(scopes[0])
    internal_tools = {'research.project_sources@1.0.0', 'repository.context_read@1.0.0'}
    assert {tool['key'] for tool in first['tools']} == {key, *internal_tools}
    assert {tool['key'] for tool in runtime._run_pins(scopes[1])['tools']} == internal_tools
    accounts['other'] = {'provider': 'fixture', 'installationHash': 'b' * 64}
    assert runtime._run_pins(scopes[0]) == first
    accounts['owner'] = {'provider': 'fixture', 'installationHash': 'c' * 64}
    with pytest.raises(DomainError, match='account installation changed'):
        runtime._run_pins(scopes[0])


async def test_final_memory_hook_excludes_raw_tools_and_reasoning(tmp_path, monkeypatch):
    runtime = Runtime(settings(tmp_path), persistence_factory=test_persistence, worker_locks=object())
    migrate(runtime)
    actor = runtime_actor(runtime)
    project = runtime.projects.create(actor, 'Public brief', 'create')['projectId']
    admitted = runtime.projects.submit(actor, project, 'Public objective', 1, 'run')
    received = []
    class Memory:
        async def extract(self, actor, project_id, messages):
            received.extend(messages)
            return [{'id': 'advisory-fact'}]
    monkeypatch.setattr(runtime, 'memory_for_run', lambda scope: Memory())
    scope = RunScope(actor=actor, project_id=project, run_id=admitted['runId'],
        revision=admitted['revision'], deadline_at=datetime.now(UTC) + timedelta(minutes=5))
    result = await runtime.observe_memory(scope, {'objective': 'Public objective', 'summary': 'Public summary',
        'private_reasoning': 'never send', 'rawTools': [{'credentials': 'never send'}]})
    assert result == {'factIds': ['advisory-fact']}
    assert received == [{'role': 'user', 'content': 'Public objective'}, {'role': 'assistant', 'content': 'Public summary'}]
    with runtime.projects.transaction() as session:
        run = session.get(Objective, scope.run_id)
        run.data = {**run.data, 'kind': 'project.nativeRebuild'}
    assert (await runtime.observe_memory(scope, {'objective': 'Rebuild'}))['reason'] == 'native_only_rebuild'
    assert len(received) == 2


def test_runtime_requires_persistent_session_and_printer_secrets(tmp_path):
    configured = settings(tmp_path)
    configured.session_secret = None
    with pytest.raises(DomainError, match='persistent session secret'):
        Runtime(configured, persistence_factory=test_persistence)
    configured = settings(tmp_path, bambu_local_enabled=True)
    with pytest.raises(DomainError, match='printer encryption key'):
        Runtime(configured, persistence_factory=test_persistence)


async def test_queued_lane_selection_remains_pinned_when_project_policy_changes(tmp_path):
    calls, turns = [], {'plan': 0, 'action': 0}

    def provider(request):
        body = json.loads(request.content)
        name = provider_name(body)
        calls.append((name, body['model']))
        if name == 'PlanDecision':
            payload = {'tasks': [{'id': 'research', 'objective': 'Inspect', 'capability': 'research'}]} if not turns['plan'] else {'done': True}
            turns['plan'] += 1
        else:
            payload = {'action': 'tool', 'tool': 'research.inspect', 'arguments': {'query': 'Inspect'}} if not turns['action'] else {'action': 'finish'}
            turns['action'] += 1
        name, payload = function_call(name, payload)
        return httpx.Response(200, json={'choices': [{'finish_reason': 'tool_calls', 'message': {
            'role': 'assistant', 'tool_calls': [{'id': str(len(calls)), 'type': 'function', 'function': {
                'name': name, 'arguments': json.dumps(payload)}}]}}],
            'usage': {'prompt_tokens': 100, 'completion_tokens': 20}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(provider))
    configured = settings(tmp_path, model_catalog=[model('text'), model('vision', True)],
        text_model_key='text', vision_model_key='vision', meta_api_key='fixture-key')
    runtime = Runtime(configured, registry=installed_registry(), persistence_factory=test_persistence,
                      worker_locks=object(), model_client=client)
    migrate(runtime)
    actor = runtime_actor(runtime)
    project = runtime.projects.create(actor, 'Research only', 'create', model_policy={
        'research': {'provider': 'meta', 'model': 'vision-pinned'}})['projectId']
    run = runtime.projects.submit(actor, project, 'Research only', 1, 'run')
    runtime.policies.update(actor, project, {'research': {'provider': 'meta', 'model': 'text-pinned'}}, 2, 'change')
    scope = RunScope(actor=actor, project_id=project, run_id=run['runId'], revision=2,
                     deadline_at=datetime.now(UTC) + timedelta(minutes=5))
    async with client, runtime.start():
        assert (await runtime.engine_factory(scope).start(scope, 'Research only'))['done']
        assert [model for name, model in calls if name == 'Action'] == ['vision-pinned', 'vision-pinned']
        assert [model for name, model in calls if name == 'PlanDecision'] == ['text-pinned', 'text-pinned']
