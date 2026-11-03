import base64
import copy
import hashlib
from datetime import timedelta

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import select

from kleos.api.engineering import EngineeringFacade
from kleos.artifacts.service import ArtifactService
from kleos.artifacts.storage import BlobStore
from kleos.contracts.models import Actor, DomainError
from kleos.execution.docker import NativeSettings
from kleos.execution.gateway import NativeAction, NativeGateway, NativeJob
from kleos.infrastructure.rebuild import configure_native_rebuild
from kleos.orchestration.engine import RunScope
from kleos.orchestration.models import ModelBudget
from kleos.projects.graph_repository import GraphRepository
from kleos.projects.rebuild import NativeRebuildService
from kleos.projects.store import Objective, Project, ProjectStore, fingerprint


class FixtureGateway:
    """Mock isolated tool calls with actual durable action rows; never runs source code."""
    def __init__(self, projects, artifacts):
        self.projects, self.artifacts = projects, artifacts
        self.calls = []
        self.crash_after_build = False

    def rebuild_snapshot(self, session, actor, job):
        if job.active_action or job.status not in {'finished', 'failed', 'cancelled'}:
            raise DomainError('Parent effect is unresolved', 'effect_uncertain')
        if session.scalar(select(NativeAction.job_id).where(NativeAction.job_id == job.id, NativeAction.status != 'completed')):
            raise DomainError('Parent effect is unresolved', 'effect_uncertain')
        sources = []
        for path, digest in sorted(job.source_manifest.items()):
            content = self.artifacts.blobs.get(digest)
            sources.append({'path': path, 'sha256': digest, 'contentBase64': base64.b64encode(content).decode()})
        result = {'sourceJobId': job.id, 'sourceRequestHash': job.request_hash,
                  'sourceManifest': dict(job.source_manifest), 'originalRequest': copy.deepcopy(job.request), 'sources': sources}
        return {**result, 'snapshotHash': fingerprint(result)}

    async def invoke_rebuild(self, scope, task, snapshot, action, arguments):
        job_id = 'rebuilt-'+scope.run_id
        action_id = arguments['actionId']
        with self.projects.transaction() as session:
            saved = session.get(NativeAction, (job_id, action_id))
            if saved:
                if saved.status != 'completed':
                    raise DomainError('Unknown action', 'effect_uncertain')
                return saved.result
        self.calls.append(action)
        refs = []
        if action == 'finish':
            files = [{'path': item['path'], 'role': 'source', 'content': base64.b64decode(item['contentBase64']),
                      'expectedSha256': snapshot['originalRequest']['baselines'].get(item['path'])} for item in snapshot['sources']]
            files.append({'path': 'outputs/model.step', 'role': 'output', 'content': b'fixture-only export'})
            refs = await self.artifacts.publish_native(scope, task, files,
                {'nativeJobId': job_id, 'imageDigest': snapshot['originalRequest']['imageDigest'], 'requestHash': 'b'*64})
        result = {'summary': 'Mock isolated '+action, 'artifacts': [ref.model_dump(mode='json') for ref in refs],
                  'observations': [{'name': 'native.'+action, 'status': 'UNKNOWN', 'independent': False,
                                    'details': {'executionSucceeded': True, 'candidateOnly': True}}], 'blockers': []}
        with self.projects.transaction() as session:
            session.add(NativeAction(job_id=job_id, action_id=action_id, request_hash='a'*64,
                                     status='completed', result=result))
        if action == 'build' and self.crash_after_build:
            self.crash_after_build = False
            raise RuntimeError('Simulated crash after committed native result')
        return result


@pytest.fixture
def fixture(tmp_path):
    projects = ProjectStore(f'sqlite:///{tmp_path}/rebuild.db')
    projects.initialize_for_tests()
    actor = Actor(tenant_id='tenant', user_id='owner')
    created = projects.create(actor, 'Rebuild this exact source', 'create')
    parent = projects.submit(actor, created['projectId'], 'Original generation', 1, 'generate')
    artifacts = ArtifactService(projects, BlobStore(tmp_path/'blobs'))
    content = b'exact saved candidate code; NEVER executed on host'
    digest = artifacts.blobs.put(content)
    request = {'protocol': 'kleos-native-session-v2', 'jobId': 'parent-job', 'lane': 'cad',
               'imageDigest': 'sha256:'+'a'*64, 'resources': {'cpus': 2, 'memoryBytes': 2*1024**3, 'pids': 64, 'scratchBytes': 1024**3},
               'revision': 2, 'maxActions': 24, 'durationSeconds': 240, 'deadline': 1,
               'sources': [], 'baselines': {'model.py': None}, 'allowedPaths': ['model.py'],
               'networkPolicy': 'docker-desktop-daemon-seccomp'}
    with projects.transaction() as session:
        session.get(Objective, parent['runId']).status = 'blocked'
        session.add(NativeJob(id='parent-job', project_id=created['projectId'], run_id=parent['runId'], task_id='old-task',
                              task_hash='a'*64, request_hash=fingerprint(request), request=request, status='failed',
                              source_manifest={'model.py': digest}, active_action=None))
        session.add(NativeAction(job_id='parent-job', action_id='old-patch', request_hash='b'*64, status='completed', result={}))
        session.add(NativeAction(job_id='parent-job', action_id='_termination', request_hash='b'*64,
                                 status='completed', result={'kind': 'termination-proof'}))
        session.add(ModelBudget(run_id=parent['runId'], policy_hash='c'*64, max_calls=12, max_cost=5000, calls=7, committed_cost=2100))
    gateway = FixtureGateway(projects, artifacts)
    service = NativeRebuildService(projects, gateway)
    yield projects, actor, created['projectId'], parent['runId'], gateway, service
    projects.engine.dispose()


def test_admission_preserves_source_contract_parent_history_and_spend(fixture):
    projects, actor, project, parent, gateway, service = fixture
    result = service.submit(actor, project, 'parent-job', 2, 'rebuild')
    assert service.submit(actor, project, 'parent-job', 2, 'rebuild') == result
    with projects.transaction() as session:
        original = session.get(NativeJob, 'parent-job')
        run = session.get(Objective, result['runId'])
        rebuild = run.data['nativeRebuild']
        assert rebuild['snapshot']['originalRequest'] == original.request
        assert rebuild['snapshot']['sourceManifest'] == original.source_manifest
        assert hashlib.sha256(base64.b64decode(rebuild['snapshot']['sources'][0]['contentBase64'])).hexdigest() == original.source_manifest['model.py']
        assert rebuild['grant']['parentRunId'] == parent and rebuild['grant']['parentActionCount'] == 1
        assert rebuild['grant']['maxDurationSeconds'] == 240 and rebuild['grant']['maxActions'] == 3
        assert rebuild['grant']['modelCallsAllowed'] == rebuild['grant']['retrievalCallsAllowed'] == 0
        assert 'runtimePins' not in run.data and run.data['kind'] == 'project.nativeRebuild'
        budget = session.get(ModelBudget, parent)
        assert (budget.calls, budget.committed_cost) == (7, 2100)
        assert session.get(ModelBudget, run.id) is None
        assert len(list(session.scalars(select(NativeAction)))) == 2
        assert original.status == 'failed'
    assert gateway.calls == []


@pytest.mark.asyncio
async def test_deterministic_graph_builds_only_saved_sources_and_candidate_files(fixture):
    projects, actor, project, _, gateway, service = fixture
    admitted = service.submit(actor, project, 'parent-job', 2, 'rebuild')
    with projects.transaction() as session:
        scope = RunScope.model_validate(session.get(Objective, admitted['runId']).data['scope'])
    engine = configure_native_rebuild(projects, gateway, scope, checkpointer=InMemorySaver())
    result = await engine.start(scope, 'Rebuild only')
    assert result['done']
    assert gateway.calls == ['build', 'render', 'finish']
    assert result['results']['native-rebuild']['status'] == 'succeeded'
    assert all(item['status'] == 'UNKNOWN' and not item['independent']
               for item in result['results']['native-rebuild']['observations'])
    with projects.transaction() as session:
        assert session.get(Project, project).data.get('sourceManifest', {}) == {}


@pytest.mark.asyncio
async def test_committed_action_crash_resumes_without_repeating_native_build(fixture):
    projects, actor, project, _, gateway, service = fixture
    admitted = service.submit(actor, project, 'parent-job', 2, 'rebuild')
    with projects.transaction() as session:
        scope = RunScope.model_validate(session.get(Objective, admitted['runId']).data['scope'])
    saver = InMemorySaver()
    gateway.crash_after_build = True
    first = configure_native_rebuild(projects, gateway, scope, checkpointer=saver)
    with pytest.raises(RuntimeError, match='Simulated crash'):
        await first.start(scope, 'Rebuild')
    restored = configure_native_rebuild(projects, gateway, scope, checkpointer=saver)
    result = await restored.resume(scope)
    assert result['done'] and gateway.calls == ['build', 'render', 'finish']


@pytest.mark.parametrize('failure', ['active', 'uncertain', 'stale', 'missing_blob'])
def test_admission_blocks_unsafe_or_missing_snapshots(fixture, failure):
    projects, actor, project, parent, gateway, service = fixture
    with projects.transaction() as session:
        job = session.get(NativeJob, 'parent-job')
        if failure == 'active':
            session.get(Objective, parent).status = 'running'
        elif failure == 'uncertain':
            job.active_action = 'unknown'
        elif failure == 'stale':
            session.get(Project, project).advance_revision()
        else:
            job.source_manifest = {'model.py': '0'*64}
    with pytest.raises(DomainError):
        service.submit(actor, project, 'parent-job', 3 if failure == 'stale' else 2, 'blocked')
    with projects.transaction() as session:
        assert len(list(session.scalars(select(Objective)))) == 1


def test_native_grant_limit_is_linked_to_root_history(fixture):
    projects, actor, project, _, _, service = fixture
    with projects.transaction() as session:
        for index in range(3):
            session.add(Objective(id=f'prior-rebuild-{index}', project_id=project, revision=1, status='blocked',
                                  data={'nativeRebuild': {'grant': {'rootJobId': 'parent-job'}}}))
    with pytest.raises(DomainError) as error:
        service.submit(actor, project, 'parent-job', 2, 'too-many')
    assert error.value.code == 'native_rebuild_budget_exhausted'


def test_rebuild_does_not_accept_changed_snapshot_or_extended_deadline(fixture):
    projects, actor, project, _, gateway, service = fixture
    admitted = service.submit(actor, project, 'parent-job', 2, 'rebuild')
    with projects.transaction() as session:
        scope = RunScope.model_validate(session.get(Objective, admitted['runId']).data['scope'])
    extended = scope.model_copy(update={'deadline_at': scope.deadline_at+timedelta(hours=1)})
    with pytest.raises(DomainError) as error:
        configure_native_rebuild(projects, gateway, extended, checkpointer=InMemorySaver())
    assert error.value.code == 'compute_budget_conflict'
    with projects.transaction() as session:
        run = session.get(Objective, admitted['runId'])
        data = copy.deepcopy(run.data)
        data['nativeRebuild']['snapshot']['sourceManifest']['model.py'] = '0'*64
        run.data = data
    with pytest.raises(DomainError, match='snapshot changed'):
        configure_native_rebuild(projects, gateway, scope, checkpointer=InMemorySaver())


def test_missing_original_time_contract_cannot_get_new_default_allowance(fixture):
    projects, actor, project, _, _, service = fixture
    with projects.transaction() as session:
        job = session.get(NativeJob, 'parent-job')
        request = dict(job.request)
        request.pop('durationSeconds')
        job.request, job.request_hash = request, fingerprint(request)
    with pytest.raises(DomainError) as error:
        service.submit(actor, project, 'parent-job', 2, 'missing-time')
    assert error.value.code == 'native_history_missing'


def test_rebuild_chain_cannot_expand_a_shorter_previous_native_allowance(fixture):
    projects, actor, project, _, _, service = fixture
    with projects.transaction() as session:
        job = session.get(NativeJob, 'parent-job')
        request = {**job.request, 'sessionSeconds': 60}
        job.request, job.request_hash = request, fingerprint(request)
    admitted = service.submit(actor, project, 'parent-job', 2, 'shorter-allowance')
    with projects.transaction() as session:
        grant = session.get(Objective, admitted['runId']).data['nativeRebuild']['grant']
        assert grant['maxDurationSeconds'] == 60


def test_existing_frontend_command_routes_to_real_rebuild_admission(fixture):
    projects, actor, project, _, _, service = fixture
    facade = EngineeringFacade(None, rebuild=service)
    body = {'type': 'project.retryNative', 'payload': {'projectId': project, 'jobId': 'parent-job'}, 'expectedRevision': 2}
    response = facade.command(actor, body, 'ui-native-rebuild')
    assert facade.command(actor, body, 'ui-native-rebuild') == response
    with projects.transaction() as session:
        assert session.get(Objective, response['runId']).data['kind'] == 'project.nativeRebuild'


@pytest.mark.asyncio
async def test_actual_gateway_and_graph_rebuild_preserve_pins_after_operator_settings_change(fixture, tmp_path):
    # Shared transport fixture writes fixed export bytes; it never runs passed native commands.
    from test_execution import FakeDocker

    projects, actor, project, _, old_gateway, _ = fixture
    with projects.transaction() as session:
        original = session.get(NativeJob, 'parent-job')
        proof = session.get(NativeAction, ('parent-job', '_termination'))
        proof.request_hash = original.request_hash
        proof.result = {'kind': 'termination-proof', 'jobId': original.id, 'containerStopped': True}
        original_request = copy.deepcopy(original.request)
    docker = FakeDocker()
    gateway = NativeGateway(projects, old_gateway.artifacts,
                            NativeSettings(tmp_path/'native', {'cad': 'sha256:'+'b'*64}, session_seconds=600),
                            docker, GraphRepository(projects))
    admitted = NativeRebuildService(projects, gateway).submit(actor, project, 'parent-job', 2, 'real-gateway')
    with projects.transaction() as session:
        scope = RunScope.model_validate(session.get(Objective, admitted['runId']).data['scope'])
    engine = configure_native_rebuild(projects, gateway, scope, checkpointer=InMemorySaver())
    result = await engine.start(scope, 'Exact rebuild')
    assert result['done']
    with projects.transaction() as session:
        rebuilt = session.scalar(select(NativeJob).where(NativeJob.run_id == scope.run_id))
        assert rebuilt.status == 'finished'
        for field in ('imageDigest', 'resources', 'networkPolicy', 'baselines', 'allowedPaths'):
            assert rebuilt.request[field] == original_request[field]
        assert rebuilt.request['imageDigest'] != gateway.settings.images['cad']
        assert rebuilt.source_manifest == session.get(NativeJob, 'parent-job').source_manifest
        assert rebuilt.request['rebuildOf'] == 'parent-job'
        assert rebuilt.request['maxActions'] == 3 and rebuilt.request['sessionSeconds'] == 240
        assert session.get(NativeAction, (rebuilt.id, '_termination')).result['containerStopped']
        assert session.get(ModelBudget, scope.run_id) is None
