"""Explicit runtime registration and cleanup composition; no daemon/model IO."""
import asyncio
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from test_mcp_bridge import config, report
from test_runtime import migrate, runtime_actor, settings
from test_runtime import test_persistence as persistence

from kleos.capabilities.mcp_bridge.runner import McpSession
from kleos.cli import work
from kleos.contracts.models import DomainError, TaskSpec
from kleos.execution.capacity import CapacityReservation
from kleos.infrastructure.mcp import read_installations
from kleos.infrastructure.runtime import Runtime
from kleos.orchestration.engine import RunScope
from kleos.projects.graph_repository import Effect


def installation(tmp_path):
    value = config()
    path = tmp_path / 'mcp-report.json'
    path.write_text(json.dumps(report(value)))
    return {'installation': value.model_dump(mode='json'), 'qualificationReference': str(path)}


@pytest.mark.parametrize('configured', [False, True])
async def test_runtime_mcp_registration_is_explicit_and_startup_never_contacts_daemon(tmp_path, monkeypatch, configured):
    def forbidden(**kwargs):
        pytest.fail('Runtime startup must not contact or construct a Docker transport')
    monkeypatch.setattr('kleos.infrastructure.mcp.DockerTransport', forbidden)
    runtime = Runtime(settings(tmp_path, mcp_installations=[installation(tmp_path)] if configured else []),
        persistence_factory=persistence, worker_locks=object())
    migrate(runtime)
    async with runtime.start():
        assert runtime.mcp_runtime.keys == (['research.mcp_fixture-inspector@1.0.0'] if configured else [])
        assert runtime.mcp_runtime.runner is None
        assert await runtime.mcp_runtime.reconcile() == {'reconciled': 0}
        assert runtime.mcp_runtime.runner is None


@pytest.mark.parametrize('kind', ['duplicate', 'duplicate-id-version', 'unknown-field', 'symlink', 'oversized', 'invalid-json', 'production-fixture'])
def test_invalid_installation_fails_before_transport_creation(tmp_path, kind):
    entry = installation(tmp_path)
    path = tmp_path / 'mcp-report.json'
    entries = [entry]
    if kind == 'duplicate':
        entries.append(entry)
    elif kind == 'duplicate-id-version':
        other = config().model_copy(update={'version': '2.0.0'})
        second = tmp_path / 'second.json'
        second.write_text(json.dumps(report(other)))
        entries.append({'installation': other.model_dump(mode='json'), 'qualificationReference': str(second)})
    elif kind == 'unknown-field':
        entry['command'] = 'must-not-execute'
    elif kind == 'symlink':
        link = tmp_path / 'linked.json'
        link.symlink_to(path)
        entry['qualificationReference'] = str(link)
    elif kind == 'oversized':
        path.write_text(' ' * 262145)
    elif kind == 'invalid-json':
        path.write_text('{private invalid report')
    configured = settings(tmp_path, mcp_installations=entries)
    if kind == 'production-fixture':
        configured.environment = 'development'
    with pytest.raises(DomainError) as caught:
        read_installations(configured)
    assert caught.value.code == 'mcp_unqualified'
    assert 'private' not in str(caught.value)


async def test_cleanup_is_available_after_installation_removal(tmp_path):
    runtime = Runtime(settings(tmp_path), persistence_factory=persistence, worker_locks=object())
    migrate(runtime)
    class Runner:
        calls = 0
        async def reconcile_due(self):
            self.calls += 1
            return {'reconciled': 1}
    runner = Runner()
    runtime.mcp_runtime.runner = runner
    async with runtime.start():
        with runtime.projects.transaction() as session:
            session.add(McpSession(id='a'*32, project_id='retained-project', run_id='retained-run',
                actor_id='revoked', tenant_id='tenant', request_hash='b'*64, status='dispatching',
                request={}, container_id=None, created_at=1, finished_at=None, termination={}))
        assert not runtime.mcp_runtime.keys
        assert await runtime.mcp_runtime.reconcile() == {'reconciled': 1}
        assert runner.calls == 1


async def test_composed_preflight_failure_never_claims_an_effect_or_capacity(tmp_path):
    import hashlib
    runtime = Runtime(settings(tmp_path, mcp_installations=[installation(tmp_path)]),
        persistence_factory=persistence, worker_locks=object())
    migrate(runtime)
    class Runner:
        calls = 0
        async def preflight(self, scope, installation):
            self.calls += 1
            raise DomainError('Pinned image unavailable', 'mcp_installation_conflict')
        def open(self, *args, **kwargs):
            pytest.fail('Failed preflight cannot dispatch')
    runner = Runner()
    runtime.mcp_runtime.runner = runner
    async with runtime.start():
        actor = runtime_actor(runtime)
        project = runtime.projects.create(actor, 'Inspect notes', 'create')['projectId']
        raw = b'notes'
        uploaded = runtime.artifacts.upload(actor, project, {'expectedEngineeringRevision': 1,
            'sha256': hashlib.sha256(raw).hexdigest(), 'file': {'path': 'notes.txt',
                'mediaType': 'text/plain', 'content': raw.decode()}}, 'upload')
        admitted = runtime.projects.submit(actor, project, 'Inspect notes', 2, 'run')
        scope = RunScope(actor=actor, project_id=project, run_id=admitted['runId'],
            revision=3, deadline_at=datetime.now(UTC)+timedelta(minutes=1))
        task = TaskSpec(id='inspect', capability='research', objective='Read notes',
            input_artifact_ids=[uploaded['artifact']['id']])
        tool = runtime.registry.tools(runtime.registry.snapshot(runtime.mcp_runtime.keys))[0]
        for _ in range(2):
            with pytest.raises(DomainError, match='Pinned image unavailable'):
                await tool.invoke(scope, task, {'actionId': 'same', 'artifactIds': task.input_artifact_ids})
        assert runner.calls == 2
        with runtime.projects.transaction() as session:
            for row in (Effect, McpSession, CapacityReservation):
                assert session.scalar(select(func.count()).select_from(row)) == 0


@pytest.mark.parametrize('maintenance_only', [False, True])
async def test_cli_mcp_cleanup_runs_without_a_native_generator(maintenance_only):
    observed = asyncio.Event()
    class Maintenance:
        async def reconcile(self):
            observed.set()
            stop_reference[0].set()
    class Worker:
        async def serve(self, stop):
            stop_reference.append(stop)
            await asyncio.wait_for(observed.wait(), 1)
    class FixtureRuntime:
        native_gateway = None
        mcp_runtime = Maintenance()
        worker = Worker()
        @asynccontextmanager
        async def start(self):
            yield self
    stop_reference = []
    if maintenance_only:
        # Capture the CLI-owned event without changing the maintenance scheduler.
        import kleos.cli
        original = kleos.cli.maintain_native
        async def capture(gateway, stop):
            stop_reference.append(stop)
            await original(gateway, stop)
        from unittest.mock import patch
        with patch('kleos.cli.maintain_native', capture):
            await asyncio.wait_for(work(FixtureRuntime(), native_maintenance=True), 2)
    else:
        await asyncio.wait_for(work(FixtureRuntime()), 2)
    assert observed.is_set()
