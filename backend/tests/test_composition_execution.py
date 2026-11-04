"""Actual durable gateway/publication flow with inert container response fixtures."""
import asyncio
import base64
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from test_composition import composed as composed
from test_execution import FakeDocker
from test_execution import gateway as gateway

from kleos.artifacts.service import Artifact, NativePublication
from kleos.contracts.models import DomainError, TaskResult
from kleos.execution.capacity import CapacityReservation, CapacityService
from kleos.execution.composition import NativeComposition, compilation_job_id
from kleos.execution.gateway import NativeAction, NativeGateway, NativeJob
from kleos.execution.native_runner import atomic_json
from kleos.execution.protocol import PROTOCOL, digest, fingerprint
from kleos.orchestration.engine import RunScope
from kleos.projects.composition import CompositionService
from kleos.projects.store import Membership, Objective, Project


class CompilationDocker(FakeDocker):
    """Writes only fixed data; never runs source files or native commands."""
    def __init__(self, grant):
        super().__init__()
        self.targets = {target['jobId']: target for target in grant['targets']}
        self.calls, self.lost_create, self.fail_action = [], False, None

    async def ensure(self, job_id, data, image, control_directory, *, allow_create=True, on_observed=None):
        self.dispatches += 1
        assert image == data['imageDigest'] and not data['allowedPaths']
        if self.lost_create:
            raise RuntimeError('Creation acknowledgement lost')
        if job_id not in self.sessions:
            if not allow_create:
                raise RuntimeError('Container missing after original dispatch')
            self.sessions[job_id] = True
        if on_observed:
            await on_observed(digest(job_id.encode()))
        directory = Path(control_directory)
        for path in sorted((directory/'actions').glob('*.json')):
            if (directory/'results'/path.name).exists():
                continue
            item = json.loads(path.read_bytes())
            self.calls.append(item['action'])
            assert item['actionId'] == 'composition:'+item['action']
            assert item['action'] in self.targets[data['compositionTarget']]['actions']
            result = {'summary': 'Inert compilation fixture'}
            if item['action'] == 'finish':
                result['files'] = [{'path': entry['path'], 'role': 'source', 'sha256': entry['sha256'],
                    'size': len(base64.b64decode(entry['contentBase64'])), 'contentBase64': entry['contentBase64']}
                    for entry in data['sources']]
                result['files'] += [{'path': ref['path'], 'role': 'output', 'sha256': digest(b'compiled fixture'),
                    'size': len(b'compiled fixture'), 'contentBase64': base64.b64encode(b'compiled fixture').decode()}
                    for ref in self.targets[data['compositionTarget']]['coveredOutputs']]
            response = {'status': 'failed', 'error': 'Fixture build failed'} if self.fail_action == item['action'] else {
                'status': 'succeeded', 'result': result}
            atomic_json(directory/'results'/path.name, {'protocol': PROTOCOL, 'jobId': job_id,
                'requestHash': fingerprint(data), 'actionId': item['actionId'],
                'actionHash': fingerprint(item), 'response': response})
        if self.on_dispatch:
            self.on_dispatch()
        return digest(job_id.encode())


def adapter(service, scope):
    grant = service.freeze(scope)
    docker = CompilationDocker(grant)
    service.gateway.docker = docker
    return NativeComposition(service.gateway, service), grant, docker


async def test_fixed_execution_keeps_source_ids_and_uses_original_contract_after_settings_change(composed):
    service, scope, _, baseline, _, _ = composed
    executor, grant, docker = adapter(service, scope)
    target = next(target for target in grant['targets'] if target['taskId'] == 'base')
    # Current operator resources/images do not reinterpret this frozen grant.
    from dataclasses import replace
    service.gateway.settings = replace(service.gateway.settings, images={'cad': 'sha256:'+'e'*64}, session_seconds=1)
    with service.repository.store.transaction() as session:
        before = session.scalar(select(func.count()).select_from(Artifact).where(Artifact.name.not_like('outputs/%')))
    for action in target['actions']:
        result = await executor.invoke(scope, grant['id'], target['taskId'], action)
        assert result['observations'][0]['status'] == 'UNKNOWN'
    assert docker.calls == ['build', 'render', 'finish']
    ident = compilation_job_id(grant, target)
    with service.repository.store.transaction() as session:
        job = session.get(NativeJob, ident)
        assert job.status == 'finished' and job.active_action is None
        assert job.request['imageDigest'] == target['contract']['imageDigest']
        assert job.request['resources'] == target['contract']['resources']
        assert job.request['sessionSeconds'] == target['maxSeconds'] and not job.request['allowedPaths']
        assert job.source_manifest == {ref['path']: ref['sha256'] for ref in grant['sources']}
        assert session.get(Project, scope.project_id).data['sourceManifest'] == baseline
        assert session.scalar(select(func.count()).select_from(Artifact).where(Artifact.name.not_like('outputs/%'))) == before
        for ref in result['artifacts']:
            row = session.get(Artifact, ref['id'])
            assert row.details['sourceClosure'] == grant['closure'] and row.details['compositionId'] == grant['id']
        receipt = copy.deepcopy(session.get(Objective, scope.run_id).data['compositionExecutions'])
    restarted = NativeComposition(NativeGateway(service.gateway.projects, service.gateway.artifacts,
        service.gateway.settings, docker, service.gateway.repository), service)
    for action in target['actions']:
        replay = await restarted.invoke(scope, grant['id'], target['taskId'], action)
    assert replay == result and docker.calls == target['actions']
    with service.repository.store.transaction() as session:
        assert session.get(Objective, scope.run_id).data['compositionExecutions'] == receipt


async def test_application_uses_only_build_and_finish(composed):
    service, scope, *_ = composed
    executor, grant, docker = adapter(service, scope)
    with pytest.raises(DomainError, match='fixed target'):
        await executor.invoke(scope, grant['id'], 'app', 'render')
    for action in ['build', 'finish']:
        result = await executor.invoke(scope, grant['id'], 'app', action)
    assert docker.calls == ['build', 'finish'] and len(result['artifacts']) == 1


@pytest.mark.parametrize('action', ['patch', 'read', 'analyze', 'shell', 'render', 'finish'])
async def test_edits_and_out_of_order_actions_do_not_dispatch(composed, action):
    service, scope, *_ = composed
    executor, grant, docker = adapter(service, scope)
    with pytest.raises(DomainError):
        await executor.invoke(scope, grant['id'], 'base', action)
    assert docker.dispatches == 0
    with service.repository.store.transaction() as session:
        assert not session.get(Objective, scope.run_id).data.get('compositionExecutions')


@pytest.mark.parametrize('change', ['cancel', 'revision', 'member', 'grant'])
async def test_authority_change_fences_dispatch_and_cached_replay(composed, change):
    service, scope, *_ = composed
    executor, grant, docker = adapter(service, scope)
    await executor.invoke(scope, grant['id'], 'base', 'build')
    count = docker.dispatches
    with service.repository.store.transaction() as session:
        if change == 'cancel':
            session.get(Objective, scope.run_id).status = 'cancelled'
        elif change == 'member':
            session.delete(session.get(Membership, (scope.project_id, scope.actor.user_id)))
        else:
            project = session.get(Project, scope.project_id)
            if change == 'revision':
                project.advance_revision()
            else:
                project.data = {**project.data, 'requirements': {'changed': True}}
    for action in ['build', 'render']:
        with pytest.raises(DomainError):
            await executor.invoke(scope, grant['id'], 'base', action)
    assert docker.dispatches == count


async def test_unknown_creation_never_gets_another_job_or_action_identity(composed):
    service, scope, *_ = composed
    executor, grant, docker = adapter(service, scope)
    docker.lost_create = True
    with pytest.raises(RuntimeError, match='acknowledgement lost'):
        await executor.invoke(scope, grant['id'], 'base', 'build')
    with pytest.raises(DomainError, match='reconciliation'):
        await executor.invoke(scope, grant['id'], 'base', 'build')
    assert docker.dispatches == 1
    target = next(target for target in grant['targets'] if target['taskId'] == 'base')
    with service.repository.store.transaction() as session:
        ident = compilation_job_id(grant, target)
        job = session.get(NativeJob, ident)
        assert job.status == 'failed' and job.active_action == 'composition:build'
        assert session.get(NativeAction, (ident, 'composition:build')).status == 'pending'
        assert session.get(NativeAction, (ident, '_termination')) is None
        assert len(session.get(Objective, scope.run_id).data['compositionExecutions'][grant['id']]) == 1


async def test_completed_failure_is_replayed_but_cannot_proceed_to_render(composed):
    service, scope, *_ = composed
    executor, grant, docker = adapter(service, scope)
    docker.fail_action = 'build'
    result = await executor.invoke(scope, grant['id'], 'base', 'build')
    assert result['observations'][0]['status'] == 'ERROR'
    assert await executor.invoke(scope, grant['id'], 'base', 'build') == result
    with pytest.raises(DomainError, match='prerequisite'):
        await executor.invoke(scope, grant['id'], 'base', 'render')
    assert docker.calls == ['build']


async def test_deadline_is_not_refreshed_for_later_actions(composed, monkeypatch):
    import kleos.execution.composition as module
    service, scope, *_ = composed
    executor, grant, docker = adapter(service, scope)
    observed = await executor.invoke(scope, grant['id'], 'base', 'build')
    with service.repository.store.transaction() as session:
        admissions = copy.deepcopy(session.get(Objective, scope.run_id).data['compositionExecutions'])
    admission = next(iter(admissions[grant['id']].values()))
    assert admission['deadline']-admission['admittedAt'] <= grant['targets'][0]['maxSeconds']
    monkeypatch.setattr(module, 'time', SimpleNamespace(time=lambda: admission['deadline']+1))
    assert await executor.invoke(scope, grant['id'], 'base', 'build') == observed
    with pytest.raises(DomainError, match='original deadline'):
        await executor.invoke(scope, grant['id'], 'base', 'render')
    with service.repository.store.transaction() as session:
        assert session.get(Objective, scope.run_id).data['compositionExecutions'] == admissions
    assert docker.calls == ['build']


async def test_cancellation_after_native_response_prevents_publication(composed):
    service, scope, *_ = composed
    executor, grant, docker = adapter(service, scope)
    await executor.invoke(scope, grant['id'], 'base', 'build')
    await executor.invoke(scope, grant['id'], 'base', 'render')
    def cancel():
        with service.repository.store.transaction() as session:
            session.get(Objective, scope.run_id).status = 'cancelled'
    docker.on_dispatch = cancel
    with pytest.raises(DomainError):
        await executor.invoke(scope, grant['id'], 'base', 'finish')
    target = next(target for target in grant['targets'] if target['taskId'] == 'base')
    with service.repository.store.transaction() as session:
        assert session.scalar(select(NativePublication).where(
            NativePublication.job_id == compilation_job_id(grant, target))) is None


@pytest.fixture
async def pooled(gateway):
    native, old_scope, task = gateway
    scope = RunScope(actor=old_scope.actor, project_id=old_scope.project_id, run_id=old_scope.run_id,
        revision=old_scope.revision, deadline_at=old_scope.deadline_at)
    native.capacity = CapacityService(native.projects)
    await native.repository.publish_plan(scope, tasks=[task], superseded={}, turn=0, summary='Fixture author', question=None)
    await native.invoke(scope, task, 'patch', {'actionId': 'patch', 'path': 'model.py', 'content': 'Inert fixture', 'expectedSha256': None})
    await native.invoke(scope, task, 'build', {'actionId': 'build'})
    result = await native.invoke(scope, task, 'finish', {'actionId': 'finish'})
    await native.repository.publish(scope, TaskResult(task_id=task.id, status='succeeded', summary='Fixture', artifacts=result['artifacts']))
    await native.reconcile()
    service = CompositionService(native.repository, native.artifacts, native)
    return service, scope


async def test_real_capacity_reservation_and_cleanup_use_original_pool(pooled):
    service, scope = pooled
    native = service.gateway
    executor, grant, docker = adapter(service, scope)
    target = grant['targets'][0]
    ident = compilation_job_id(grant, target)
    for action in target['actions']:
        await executor.invoke(scope, grant['id'], target['taskId'], action)
    with native.projects.transaction() as session:
        reservation = session.get(CapacityReservation, 'native:'+ident)
        assert reservation.status == 'reserved'
        assert reservation.resources == target['contract']['capacityResources']
        assert reservation.pool_id == target['contract']['capacityPoolId']
    await native.reconcile()
    with native.projects.transaction() as session:
        assert session.get(CapacityReservation, 'native:'+ident).status == 'released'
    assert ident in docker.cleaned


async def test_new_capacity_pool_cannot_reinterpret_unpooled_original_contract(composed):
    service, scope, *_ = composed
    executor, grant, docker = adapter(service, scope)
    service.gateway.capacity = CapacityService(service.repository.store)
    with pytest.raises(DomainError, match='capacity pool'):
        await executor.invoke(scope, grant['id'], 'base', 'build')
    assert docker.dispatches == 0


async def test_pending_action_recovers_its_existing_result_without_native_reexecution(composed):
    service, scope, *_ = composed
    executor, grant, docker = adapter(service, scope)
    result = await executor.invoke(scope, grant['id'], 'base', 'build')
    target = next(target for target in grant['targets'] if target['taskId'] == 'base')
    ident = compilation_job_id(grant, target)
    # The supervisor result is already durable; simulate worker loss before the
    # separate action-result commit, without modifying the result envelope.
    with service.repository.store.transaction() as session:
        row = session.get(NativeAction, (ident, 'composition:build'))
        row.status, row.result = 'pending', {}
        session.get(NativeJob, ident).active_action = 'composition:build'
    reopened = NativeComposition(service.gateway, service)
    assert await reopened.invoke(scope, grant['id'], 'base', 'build') == result
    assert docker.calls == ['build'] and docker.dispatches == 1


@pytest.mark.parametrize('change', ['release', 'cancel', 'grant'])
async def test_capacity_wait_is_before_action_claim_and_rechecks_authority(pooled, monkeypatch, change):
    service, scope = pooled
    native = service.gateway
    executor, grant, docker = adapter(service, scope)
    capacity = native.capacity
    assert capacity.reserve('checker:blocker', scope.project_id, 'c'*64,
        {'cpus': 4, 'memoryBytes': 6*1024**3, 'scratchBytes': 5*1024**3},
        details={'containerName': 'capacity-fixture'})
    waiting = asyncio.Event()
    reserve = capacity.reserve
    def observed(*args, **kwargs):
        result = reserve(*args, **kwargs)
        if not result:
            waiting.set()
        return result
    monkeypatch.setattr(capacity, 'reserve', observed)
    pending = asyncio.create_task(executor.invoke(scope, grant['id'], 'cad-one', 'build'))
    try:
        await asyncio.wait_for(waiting.wait(), timeout=2)
        assert docker.dispatches == 0
        ident = compilation_job_id(grant, grant['targets'][0])
        with native.projects.transaction() as session:
            deadline = session.get(NativeJob, ident).request['deadline']
            assert session.get(NativeAction, (ident, 'composition:build')) is None
            if change == 'cancel':
                session.get(Objective, scope.run_id).status = 'cancelled'
            elif change == 'grant':
                project = session.get(Project, scope.project_id)
                project.data = {**project.data, 'requirements': {'changed': True}}
        if change == 'release':
            capacity.release('checker:blocker', 'c'*64, {'kind': 'container-removed', 'identity': 'capacity-fixture'})
            result = await asyncio.wait_for(pending, timeout=2)
            assert result['observations'][0]['details']['executionSucceeded']
            assert docker.calls == ['build']
        else:
            with pytest.raises(DomainError):
                await asyncio.wait_for(pending, timeout=2)
            assert docker.dispatches == 0
        with native.projects.transaction() as session:
            assert session.get(NativeJob, ident).request['deadline'] == deadline
    finally:
        if not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
