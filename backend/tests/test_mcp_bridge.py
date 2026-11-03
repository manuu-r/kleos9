import hashlib
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import anyio
import pytest
from mcp import types
from mcp.server import Server
from mcp.shared.message import SessionMessage

from kleos.artifacts.service import ArtifactService
from kleos.artifacts.storage import BlobStore
from kleos.capabilities.mcp_bridge.bridge import ArtifactInspector, RemoteArguments, register_inspector
from kleos.capabilities.mcp_bridge.installation import (
    REQUIRED_CHECKS,
    Installation,
    qualified_hash,
    source_hashes,
)
from kleos.capabilities.mcp_bridge.transport import frame, session
from kleos.capabilities.registry import CapabilityRegistry
from kleos.contracts.models import Actor, DomainError, TaskSpec
from kleos.orchestration.engine import RunScope
from kleos.orchestration.specialist import ModelSpecialist
from kleos.projects.graph_repository import GraphRepository
from kleos.projects.store import ProjectStore, fingerprint


class FixtureRunner:
    """Trusted test parser, never a production isolation/qualification claim."""
    def __init__(self, *, mode='normal', after_call=None):
        self.calls, self.starts, self.closed = 0, 0, 0
        self.mode, self.after_call = mode, after_call
        self.client_messages = []

    @asynccontextmanager
    async def open(self, scope, task, installation, inputs, *, action_id):
        self.starts += 1
        server = Server(installation.server_name, version=installation.server_version)

        @server.list_tools()
        async def tools():
            schema = RemoteArguments.model_json_schema(by_alias=True)
            if self.mode == 'schema':
                schema = {'type': 'object'}
            return [types.Tool(name=installation.tool_name, inputSchema=schema)]

        @server.call_tool()
        async def call(name, arguments):
            self.calls += 1
            assert name == installation.tool_name
            assert arguments == {'inputs': [{'artifactId': item.reference.id, 'path': item.path,
                'sha256': item.reference.sha256, 'size': len(item.content),
                'mediaType': item.reference.media_type} for item in inputs]}
            if self.after_call:
                self.after_call()
            if self.mode == 'error':
                return types.CallToolResult(content=[types.TextContent(type='text', text='secret filesystem path')],
                                            isError=True)
            records = [{'artifactId': item.reference.id, 'sha256': hashlib.sha256(item.content).hexdigest(),
                'size': len(item.content), 'mediaType': item.reference.media_type,
                'text': item.content.decode(), 'metadata': {'lines': len(item.content.splitlines())}}
                for item in inputs]
            if self.mode == 'wrong_hash':
                records[0]['sha256'] = '0' * 64
            if self.mode == 'oversized':
                records[0]['text'] = 'x' * 300000
            return types.CallToolResult(content=[], structuredContent={'records': records})

        to_server, server_read = anyio.create_memory_object_stream(1)
        server_write, from_server = anyio.create_memory_object_stream(1)
        owner = self

        class Channel:
            pending = b''

            async def send(self, content):
                raw = json.loads(content)
                owner.client_messages.append(raw)
                await to_server.send(SessionMessage(types.JSONRPCMessage.model_validate(raw)))

            async def receive(self, max_bytes):
                if not self.pending:
                    item = await from_server.receive()
                    raw = item.message.model_dump(mode='json', by_alias=True, exclude_none=True)
                    if owner.mode == 'identity' and 'serverInfo' in raw.get('result', {}):
                        raw['result']['serverInfo']['version'] = 'unreviewed'
                    if owner.mode == 'disconnect' and 'structuredContent' in raw.get('result', {}):
                        return b''
                    self.pending = json.dumps(raw).encode() + b'\n'
                result, self.pending = self.pending[:max_bytes], self.pending[max_bytes:]
                return result

        try:
            async with anyio.create_task_group() as group:
                group.start_soon(server.run, server_read, server_write, server.create_initialization_options())
                try:
                    yield Channel()
                finally:
                    group.cancel_scope.cancel()
        except ExceptionGroup as errors:
            # Preserve the application error through the fixture's own task group.
            if len(errors.exceptions) == 1:
                raise errors.exceptions[0]
            raise
        finally:
            self.closed += 1


def config():
    return Installation(id='fixture-inspector', version='1.0.0', image='sha256:' + '1' * 64,
        package_sha256='2' * 64, server_name='fixture-inspector', server_version='1.0.0',
        tool_name='inspect_artifacts')


def report(value):
    hashes = source_hashes()
    return {'qualified': True, 'testOnly': True, 'installation': value.model_dump(mode='json'),
        'sourceSha256': hashes, 'review': {'independent': True, 'reviewer': 'Synthetic fixture reviewer',
            'reference': 'test-only, no image qualification', 'reviewedAt': datetime.now(UTC).isoformat(),
            'sourceHash': fingerprint(hashes)}, 'checks': dict.fromkeys(REQUIRED_CHECKS, 'PASS')}


@pytest.fixture
def authority(tmp_path):
    store = ProjectStore(f'sqlite:///{tmp_path}/state.db')
    store.initialize_for_tests()
    actor = Actor(tenant_id='tenant', user_id='owner')
    created = store.create(actor, 'Inspect existing files', 'create')
    artifacts = ArtifactService(store, BlobStore(tmp_path / 'blobs'))
    body = b'Existing enclosure is 40 mm wide.\n'
    uploaded = artifacts.upload(actor, created['projectId'], {'expectedEngineeringRevision': 1,
        'sha256': hashlib.sha256(body).hexdigest(), 'file': {'path': 'notes.txt', 'mediaType': 'text/plain',
            'content': body.decode()}}, 'upload')
    submitted = store.submit(actor, created['projectId'], 'Inspect uploaded notes', 2, 'objective')
    scope = RunScope(actor=actor, project_id=created['projectId'], run_id=submitted['runId'],
        revision=3, deadline_at=datetime.now(UTC) + timedelta(minutes=5))
    task = TaskSpec(id='inspect', objective='Read notes', capability='research',
        input_artifact_ids=[uploaded['artifact']['id']])
    return GraphRepository(store), artifacts, scope, task


def inspector(authority, runner):
    repository, artifacts, _, _ = authority
    value = config()
    return ArtifactInspector(value, report(value), environment='test', repository=repository,
                             artifacts=artifacts, runner=runner)


def arguments(task, **changes):
    return {'actionId': 'stable-call', 'artifactIds': task.input_artifact_ids} | changes


async def test_actual_sdk_exchange_reads_scoped_bytes_and_replays_after_repository_restart(authority):
    repo, _, scope, task = authority
    runner = FixtureRunner()
    tool = inspector(authority, runner)
    registry = CapabilityRegistry()
    key = register_inspector(registry, tool)
    bound = registry.tools(registry.snapshot([key]))[0]
    result = await bound.invoke(scope, task, arguments(task))
    assert result.observations[0].details['records'][0]['text'] == 'Existing enclosure is 40 mm wide.\n'
    assert not result.observations[0].independent and not result.artifacts
    tool.repository = GraphRepository(ProjectStore(str(repo.store.engine.url)))
    assert await bound.invoke(scope, task, arguments(task)) == result
    assert runner.calls == runner.starts == runner.closed == 1
    initialize = runner.client_messages[0]
    assert initialize['params']['capabilities'] == {}
    assert [item['method'] for item in runner.client_messages] == [
        'initialize', 'notifications/initialized', 'tools/list', 'tools/call']
    registry.revoke(key)
    with pytest.raises(DomainError, match='unavailable'):
        await bound.invoke(scope, task, arguments(task))


@pytest.mark.parametrize('mode,code', [('identity', 'mcp_installation_conflict'),
    ('schema', 'mcp_catalog_conflict'), ('wrong_hash', 'mcp_result_invalid'),
    ('oversized', 'mcp_output_limit'), ('disconnect', 'mcp_disconnected')])
async def test_protocol_output_failures_leave_uncertain_receipt_and_never_repeat(authority, mode, code):
    _, _, scope, task = authority
    runner = FixtureRunner(mode=mode)
    tool = inspector(authority, runner)
    with pytest.raises(DomainError) as failure:
        await tool.invoke(scope, task, arguments(task))
    assert failure.value.code == code
    with pytest.raises(DomainError) as retry:
        await tool.invoke(scope, task, arguments(task))
    assert retry.value.code == 'effect_uncertain'
    assert runner.starts == runner.closed == 1


async def test_scope_conflict_stops_before_start_and_same_action_changed_task_conflicts(authority):
    _, _, scope, task = authority
    runner, tool = FixtureRunner(), None
    tool = inspector(authority, runner)
    with pytest.raises(DomainError):
        await tool.invoke(scope, task, arguments(task, artifactIds=['other-project-artifact']))
    assert runner.starts == 0
    await tool.invoke(scope, task, arguments(task))
    with pytest.raises(DomainError) as failure:
        await tool.invoke(scope, task.model_copy(update={'objective': 'Different intent'}), arguments(task))
    assert failure.value.code == 'idempotency_conflict' and runner.starts == 1


async def test_cancel_during_call_fences_result_and_closes_runner(authority):
    repo, _, scope, task = authority
    runner = FixtureRunner(after_call=lambda: repo.cancel(scope.actor, scope.project_id, scope.run_id))
    with pytest.raises(DomainError) as failure:
        await inspector(authority, runner).invoke(scope, task, arguments(task))
    assert failure.value.code == 'run_inactive'
    assert runner.calls == runner.closed == 1


async def test_server_error_details_are_not_exposed(authority):
    _, _, scope, task = authority
    result = await inspector(authority, FixtureRunner(mode='error')).invoke(scope, task, arguments(task))
    assert result.blockers == ['mcp_tool_error']
    assert 'secret' not in result.model_dump_json() and not result.observations


@pytest.mark.parametrize('raw', [b'{"jsonrpc":"2.0","id":1,"id":2,"result":{}}',
    b'{"jsonrpc":"2.0","id":1,"method":"sampling/createMessage","params":{}}',
    b'{"jsonrpc":"2.0","method":"notifications/tools/list_changed"}',
    b'{"jsonrpc":"2.0","id":1,"result":{"bad":NaN}}', b'[]', b'not-json'])
def test_untrusted_server_messages_fail_closed(raw):
    with pytest.raises(DomainError):
        frame(raw)


@pytest.mark.parametrize('environment', ['development', 'production'])
def test_synthetic_qualification_cannot_enable_installation_outside_tests(environment):
    with pytest.raises(DomainError, match='qualification'):
        qualified_hash(config(), report(config()), environment=environment)


async def test_unterminated_frame_is_bounded_before_sdk_parse():
    class Channel:
        async def send(self, content):
            pass

        async def receive(self, max_bytes):
            return b'x' * max_bytes
    with pytest.raises(DomainError) as failure:
        async with session(Channel(), max_frame_bytes=1024) as client:
            await client.initialize()
    assert failure.value.code == 'mcp_output_limit'


async def test_silent_server_timeout_is_bounded():
    class Channel:
        async def send(self, content):
            pass

        async def receive(self, max_bytes):
            await anyio.sleep_forever()
    with pytest.raises(DomainError) as failure:
        async with session(Channel(), timeout_seconds=1) as client:
            await client.initialize()
    assert failure.value.code in {'mcp_timeout', 'mcp_transport_error'}


async def test_model_specialist_uses_real_registered_protocol_tool_and_observes_content(authority):
    repo, _, scope, task = authority
    runner, registry = FixtureRunner(), CapabilityRegistry()
    key = register_inspector(registry, inspector(authority, runner))

    class Model:
        calls = 0

        def with_structured_output(self, schema):
            return self

        def bind(self, **kwargs):
            return self

        async def ainvoke(self, messages):
            self.calls += 1
            packet = json.loads(messages[-1]['content'])
            if self.calls == 1:
                return {'action': 'tool', 'tool': 'research.mcp_fixture-inspector',
                        'arguments': {'artifactIds': task.input_artifact_ids}}
            text = packet['observations'][0]['result']['observations'][0]['details']['records'][0]['text']
            assert text == 'Existing enclosure is 40 mm wide.\n'
            return {'action': 'finish', 'summary': 'Uploaded notes specify a width of 40 mm; not independently verified.'}

    model = Model()
    specialist = ModelSpecialist(model, registry.tools(registry.snapshot([key])), repo)
    result = await specialist.execute(scope, task, await repo.context(scope))
    assert result.status == 'succeeded' and runner.calls == 1 and model.calls == 2
    assert not result.observations[0].independent
    assert await specialist.execute(scope, task, await repo.context(scope)) == result
    assert runner.calls == 1 and model.calls == 2


@pytest.mark.parametrize('change', ['actor', 'revision', 'input_limit'])
async def test_authority_and_size_fail_before_any_server_start(authority, change):
    _, _, scope, task = authority
    runner = FixtureRunner()
    tool = inspector(authority, runner)
    if change == 'actor':
        scope = scope.model_copy(update={'actor': Actor(tenant_id='tenant', user_id='outsider')})
    elif change == 'revision':
        scope = scope.model_copy(update={'revision': scope.revision + 1})
    else:
        value = config().model_copy(update={'max_input_bytes': 1})
        tool = ArtifactInspector(value, report(value), environment='test', repository=authority[0],
                                 artifacts=authority[1], runner=runner)
    with pytest.raises(DomainError):
        await tool.invoke(scope, task, arguments(task))
    assert runner.starts == 0


@pytest.mark.parametrize('change', ['source', 'image', 'review', 'checks'])
def test_qualification_binds_actual_bridge_image_and_independent_scope(change):
    value = config()
    evidence = report(value)
    if change == 'source':
        evidence['sourceSha256']['transport.py'] = '0' * 64
    elif change == 'image':
        evidence['installation']['image'] = 'sha256:' + '0' * 64
    elif change == 'review':
        evidence['review']['independent'] = False
    else:
        evidence['checks']['isolation'] = 'UNKNOWN'
    with pytest.raises(DomainError) as failure:
        qualified_hash(value, evidence, environment='test')
    assert failure.value.code == 'mcp_unqualified'


async def test_runner_admission_is_inside_wall_clock_bound_and_never_retried(authority):
    repo, artifacts, scope, task = authority

    class StuckRunner:
        starts = 0

        @asynccontextmanager
        async def open(self, *args, **kwargs):
            self.starts += 1
            await anyio.sleep_forever()
            yield  # pragma: no cover

    runner = StuckRunner()
    value = config().model_copy(update={'timeout_seconds': 1})
    tool = ArtifactInspector(value, report(value), environment='test', repository=repo,
                             artifacts=artifacts, runner=runner)
    with pytest.raises(DomainError) as failure:
        await tool.invoke(scope, task, arguments(task))
    assert failure.value.code == 'mcp_timeout'
    with pytest.raises(DomainError) as failure:
        await tool.invoke(scope, task, arguments(task))
    assert failure.value.code == 'effect_uncertain' and runner.starts == 1


async def test_another_authorized_project_editor_cannot_reuse_original_actor_receipt(authority):
    from kleos.projects.store import Membership
    repo, _, scope, task = authority
    runner = FixtureRunner()
    tool = inspector(authority, runner)
    await tool.invoke(scope, task, arguments(task))
    editor = Actor(tenant_id=scope.actor.tenant_id, user_id='second-editor')
    with repo.store.transaction() as session:
        session.add(Membership(project_id=scope.project_id, user_id=editor.user_id, role='editor'))
    editor_scope = scope.model_copy(update={'actor': editor})
    await repo.assert_active(editor_scope)  # Actually authorized; conflict is the receipt identity.
    with pytest.raises(DomainError) as failure:
        await tool.invoke(editor_scope, task, arguments(task))
    assert failure.value.code == 'idempotency_conflict'
    assert runner.starts == runner.calls == 1
