"""Composition over actual gateway/publication/plan services; native IO is a data fixture."""
import base64
import copy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select
from test_execution import FakeDocker

from kleos.artifacts.service import Artifact, ArtifactService
from kleos.artifacts.storage import BlobStore
from kleos.contracts.models import Actor, DomainError, TaskResult, TaskSpec
from kleos.execution.docker import NativeSettings
from kleos.execution.gateway import NativeAction, NativeGateway, NativeJob
from kleos.execution.native_runner import atomic_json
from kleos.execution.protocol import PROTOCOL, digest, fingerprint
from kleos.orchestration.engine import RunScope
from kleos.projects.composition import CompositionService
from kleos.projects.graph_repository import GraphRepository
from kleos.projects.store import Membership, Objective, Project, ProjectStore


class ProtocolFixture(FakeDocker):
    """Mock container responses only; no generated/native tool code executes."""
    async def ensure(self, job_id, data, image, control_directory, *, allow_create=True, on_observed=None):
        import json
        self.dispatches += 1
        if job_id not in self.sessions:
            assert allow_create
            self.sessions[job_id] = {item['path']: base64.b64decode(item['contentBase64']) for item in data['sources']}
        sources = self.sessions[job_id]
        if on_observed:
            await on_observed(digest(job_id.encode()))
        root = Path(control_directory)
        for path in sorted((root / 'actions').glob('*.json')):
            if (root / 'results' / path.name).exists():
                continue
            action = json.loads(path.read_bytes())
            args = action['arguments']
            result = {'summary': 'Fixture container response, not engineering evidence'}
            if action['action'] == 'patch':
                old = sources.get(args['path'])
                assert (digest(old) if old else None) == args['expectedSha256']
                sources[args['path']] = args['content'].encode()
                result.update(path=args['path'], sha256=digest(sources[args['path']]))
            if action['action'] == 'finish':
                name = Path(data['allowedPaths'][0]).stem
                outputs = ({f'outputs/{name}.step': b'STEP fixture', f'outputs/{name}.stl': b'STL fixture'}
                           if data['lane'] == 'cad' else {'outputs/companion-web.zip': b'ZIP fixture'})
                result['files'] = [{'path': name, 'role': role, 'sha256': digest(content), 'size': len(content),
                    'contentBase64': base64.b64encode(content).decode()}
                    for role, values in [('source', sources), ('output', outputs)] for name, content in values.items()]
            response = {'status': 'succeeded', 'result': result}
            atomic_json(root / 'results' / path.name, {'protocol': PROTOCOL, 'jobId': job_id,
                'requestHash': fingerprint(data), 'actionId': action['actionId'],
                'actionHash': fingerprint(action), 'response': response})
        return job_id


@pytest.fixture
async def composed(tmp_path, request):
    store = ProjectStore(f'sqlite:///{tmp_path / "composition.db"}')
    store.initialize_for_tests()
    actor = Actor(tenant_id='tenant', user_id='owner')
    project = store.create(actor, 'Compose separate parts and app', 'create')['projectId']
    artifacts = ArtifactService(store, BlobStore(tmp_path / 'blobs'))
    baseline = {}
    for path, content in [('model.py', 'Original CAD source'), ('board.txt', 'Unchanged board context')]:
        result = artifacts.upload(actor, project, {'file': {'path': path, 'content': content},
            'sha256': digest(content.encode()), 'expectedEngineeringRevision': store.context(actor, project).revision}, path)
        baseline[path] = result['artifact']['id']
    submitted = store.submit(actor, project, 'Generate candidates only', store.context(actor, project).revision, 'run')
    scope = RunScope(actor=actor, project_id=project, run_id=submitted['runId'], revision=submitted['revision'],
        deadline_at=datetime.now(UTC)+timedelta(minutes=10))
    repo = GraphRepository(store)
    tasks = [TaskSpec(id='base', capability='cad', objective='Base', allowed_paths=['model.py'],
                     input_artifact_ids=list(baseline.values())),
             TaskSpec(id='lid', capability='cad', objective='Separate lid', allowed_paths=['lid.py'],
                     input_artifact_ids=list(baseline.values())),
             TaskSpec(id='app', capability='application', objective='Companion source', allowed_paths=['lib/main.dart'],
                     input_artifact_ids=list(baseline.values()))]
    if getattr(request, 'param', None) is not None:
        tasks = [task for task in tasks if task.id in request.param]
    await repo.publish_plan(scope, tasks=tasks, superseded={}, turn=0, summary='Separate authored candidates', question=None)
    docker = ProtocolFixture()
    gateway = NativeGateway(store, artifacts, NativeSettings(tmp_path / 'control',
        {'cad': 'sha256:'+'a'*64, 'application': 'sha256:'+'b'*64}), docker, repo)
    refs = {}
    for task in tasks:
        path = task.allowed_paths[0]
        await gateway.invoke(scope, task, 'patch', {'actionId': 'patch', 'path': path, 'content': 'New '+task.id,
            'expectedSha256': digest(b'Original CAD source') if path == 'model.py' else None})
        await gateway.invoke(scope, task, 'build', {'actionId': 'build'})
        result = await gateway.invoke(scope, task, 'finish', {'actionId': 'finish'})
        refs[task.id] = result['artifacts']
        await repo.publish(scope, TaskResult(task_id=task.id, status='succeeded', summary='Fixture exports',
                                           artifacts=result['artifacts']))
    yield CompositionService(repo, artifacts, gateway), scope, tasks, baseline, refs, docker
    store.engine.dispose()


async def test_freezes_exact_union_per_producer_without_accepting_or_executing(composed):
    service, scope, tasks, baseline, refs, docker = composed
    count = docker.dispatches
    grant = service.freeze(scope)
    assert service.freeze(scope) == service.validate(scope, grant['id']) == grant
    assert docker.dispatches == count
    assert len(grant['targets']) == 3 and [target['lane'] for target in grant['targets']].count('cad') == 2
    assert grant['modelCallsAllowed'] == grant['retrievalCallsAllowed'] == 0
    assert not grant['sourceEditsAllowed'] and not grant['releaseAcceptanceAllowed']
    assert grant['deadlineAt'] == scope.deadline_at.isoformat()
    assert grant['baseline'] == baseline
    changed = {ref['path']: ref['id'] for values in refs.values() for ref in values if not ref['path'].startswith('outputs/')}
    assert grant['closure'] == baseline | changed
    assert grant['closure']['board.txt'] == baseline['board.txt']
    with service.repository.store.transaction() as session:
        project = session.get(Project, scope.project_id)
        assert project.data['sourceManifest'] == baseline and not project.data.get('acceptedReleaseId')
        assert project.context_revision == scope.revision
        assert session.get(Objective, scope.run_id).data['sourceComposition'] == grant
    restarted = CompositionService(GraphRepository(service.repository.store), service.artifacts, service.gateway)
    assert restarted.validate(scope, grant['id']) == grant
    grant['closure'].clear()
    assert restarted.validate(scope, grant['id'])['closure']


@pytest.mark.parametrize('change', ['context', 'membership', 'cancel', 'source', 'job', 'action', 'publication', 'late-task', 'authority'])
async def test_revalidation_fences_changed_authority_or_history(composed, change):
    service, scope, _, _, refs, _ = composed
    grant = service.freeze(scope)
    store = service.repository.store
    with store.transaction() as session:
        run = session.get(Objective, scope.run_id)
        if change == 'context':
            session.get(Project, scope.project_id).advance_revision()
        elif change == 'membership':
            session.delete(session.get(Membership, (scope.project_id, scope.actor.user_id)))
        elif change == 'cancel':
            run.status = 'cancelled'
        elif change == 'source':
            row = session.get(Artifact, refs['base'][0]['id'])
            row.sha256 = service.artifacts.blobs.put(b'Changed bytes')
        elif change == 'job':
            job = session.scalar(select(NativeJob).where(NativeJob.run_id == run.id))
            job.request = {**job.request, 'imageDigest': 'sha256:'+'f'*64}
        elif change == 'action':
            job = session.scalar(select(NativeJob).where(NativeJob.run_id == run.id))
            session.get(NativeAction, (job.id, 'finish')).status = 'pending'
        elif change == 'publication':
            from kleos.artifacts.service import NativePublication
            row = session.scalar(select(NativePublication).where(NativePublication.run_id == run.id))
            row.fingerprint = 'f'*64
        elif change == 'late-task':
            run.data = {**run.data, 'tasks': [*run.data['tasks'], TaskSpec(id='late', capability='firmware',
                objective='Late firmware source').model_dump(mode='json')]}
        else:
            project = session.get(Project, scope.project_id)
            project.data = {**project.data, 'interfaces': {'firmware': {'board': 'changed'}}}
    with pytest.raises(DomainError):
        service.validate(scope, grant['id'])


async def test_missing_native_lane_cannot_be_silently_omitted(composed):
    service, scope, tasks, _, _, _ = composed
    await service.repository.publish_plan(scope, tasks=[*tasks, TaskSpec(id='pcb', capability='pcb',
        objective='Required board')], superseded={}, turn=1, summary='PCB still pending', question=None)
    with pytest.raises(DomainError) as error:
        service.freeze(scope)
    assert error.value.code == 'composition_incomplete'


async def test_candidate_without_matching_finished_producer_is_rejected(composed):
    service, scope, _, _, refs, _ = composed
    with service.repository.store.transaction() as session:
        source = session.get(Artifact, refs['base'][0]['id'])
        source.details = {**source.details, 'nativeJobId': 'foreign-job'}
    with pytest.raises(DomainError):
        service.freeze(scope)


async def test_derived_output_dependency_is_explicitly_unsupported(composed):
    service, scope, tasks, _, refs, _ = composed
    output = next(ref for ref in refs['base'] if ref['path'].endswith('.step'))
    task = TaskSpec(id='derived', capability='cad', objective='Read generated output', allowed_paths=['derived.py'],
                    input_artifact_ids=[output['id']], depends_on=['base'])
    await service.repository.publish_plan(scope, tasks=[*tasks, task], superseded={}, turn=1, summary='Derived input', question=None)
    await service.gateway.invoke(scope, task, 'patch', {'actionId': 'patch', 'path': 'derived.py',
        'content': 'Inert fixture', 'expectedSha256': None})
    await service.gateway.invoke(scope, task, 'build', {'actionId': 'build'})
    result = await service.gateway.invoke(scope, task, 'finish', {'actionId': 'finish'})
    await service.repository.publish(scope, TaskResult(task_id=task.id, status='succeeded', summary='Fixture',
                                                     artifacts=result['artifacts']))
    with pytest.raises(DomainError) as error:
        service.freeze(scope)
    assert error.value.code == 'composition_derived_input_unsupported'


async def test_independent_competing_source_edits_cannot_be_overlaid(composed):
    service, scope, tasks, baseline, _, _ = composed
    task = tasks[0].model_copy(update={'id': 'competing'})
    await service.repository.publish_plan(scope, tasks=[*tasks, task], superseded={}, turn=1, summary='Conflicting edits', question=None)
    await service.gateway.invoke(scope, task, 'patch', {'actionId': 'patch', 'path': 'model.py',
        'content': 'Conflicting base', 'expectedSha256': digest(b'Original CAD source')})
    await service.gateway.invoke(scope, task, 'build', {'actionId': 'build'})
    result = await service.gateway.invoke(scope, task, 'finish', {'actionId': 'finish'})
    await service.repository.publish(scope, TaskResult(task_id=task.id, status='succeeded', summary='Fixture',
                                                     artifacts=result['artifacts']))
    with pytest.raises(DomainError, match='ownership'):
        service.freeze(scope)
    with service.repository.store.transaction() as session:
        assert session.get(Project, scope.project_id).data['sourceManifest'] == baseline


async def test_grant_cannot_be_mutated_or_reidentified(composed):
    service, scope, _, _, _, _ = composed
    service.freeze(scope)
    with pytest.raises(DomainError):
        service.validate(scope, 'f'*64)
    with service.repository.store.transaction() as session:
        run = session.get(Objective, scope.run_id)
        changed = copy.deepcopy(run.data['sourceComposition'])
        changed['targets'][0]['maxSeconds'] += 1
        run.data = {**run.data, 'sourceComposition': changed}
    with pytest.raises(DomainError):
        service.freeze(scope)


async def test_validation_tasks_and_changed_operator_defaults_do_not_retarget_grant(composed):
    service, scope, tasks, _, _, _ = composed
    grant = service.freeze(scope)
    await service.repository.publish_plan(scope, tasks=[*tasks, TaskSpec(id='check', capability='validation',
        objective='Independently check the composed outputs', depends_on=[task.id for task in tasks])],
        superseded={}, turn=1, summary='Independent verification', question=None)
    service.max_seconds = 1
    assert service.validate(scope, grant['id']) == service.freeze(scope) == grant


async def test_changed_authoring_gets_new_bounded_immutable_grant(composed):
    service, scope, tasks, _, _, _ = composed
    first = service.freeze(scope)
    # A changed authored acceptance contract is a new plan, never an in-place grant edit.
    changed = [task.model_copy(update={'objective': task.objective+' with final documented constraints'}) for task in tasks]
    # No native work may be retargeted to that altered task: real gateway task hashes fence it.
    await service.repository.publish_plan(scope, tasks=changed, superseded={}, turn=1, summary='Changed task contract', question=None)
    with pytest.raises(DomainError):
        service.freeze(scope)
    await service.repository.publish_plan(scope, tasks=tasks, superseded={}, turn=2, summary='Restore original plan', question=None)
    assert service.validate(scope, first['id']) == first
    # A genuine new native producer is admitted through the same gateway/publication path.
    extra = TaskSpec(id='fastener', capability='cad', objective='Additional fastener', allowed_paths=['fastener.py'])
    await service.repository.publish_plan(scope, tasks=[*tasks, extra], superseded={}, turn=3, summary='New producer', question=None)
    await service.gateway.invoke(scope, extra, 'patch', {'actionId': 'patch', 'path': 'fastener.py',
        'content': 'Fastener fixture', 'expectedSha256': None})
    await service.gateway.invoke(scope, extra, 'build', {'actionId': 'build'})
    result = await service.gateway.invoke(scope, extra, 'finish', {'actionId': 'finish'})
    await service.repository.publish(scope, TaskResult(task_id=extra.id, status='succeeded', summary='Fixture',
                                                     artifacts=result['artifacts']))
    second = service.freeze(scope)
    assert second['id'] != first['id'] and len(second['targets']) == 4
    with pytest.raises(DomainError):
        service.validate(scope, first['id'])
    with service.repository.store.transaction() as session:
        history = session.get(Objective, scope.run_id).data['sourceCompositions']
        assert history == {first['id']: first, second['id']: second}


async def test_dependency_replacement_uses_executed_effective_task_contract(composed):
    service, scope, tasks, _, refs, _ = composed
    failed = TaskSpec(id='old-base', capability='cad', objective='Failed predecessor', allowed_paths=['obsolete.py'])
    consumer = TaskSpec(id='consumer', capability='cad', objective='Consume replacement source',
        allowed_paths=['consumer.py'], depends_on=['old-base'])
    await service.repository.publish_plan(scope, tasks=[*tasks, failed, consumer], superseded={'old-base': 'base'},
        turn=1, summary='Use the replacement dependency', question=None)
    await service.repository.publish(scope, TaskResult(task_id='old-base', status='failed', summary='No candidate produced'))
    source = next(ref for ref in refs['base'] if ref['path'] == 'model.py')
    effective = consumer.model_copy(update={'depends_on': ['base'], 'input_artifact_ids': [source['id']]})
    await service.gateway.invoke(scope, effective, 'patch', {'actionId': 'patch', 'path': 'consumer.py',
        'content': 'Consumer fixture', 'expectedSha256': None})
    await service.gateway.invoke(scope, effective, 'build', {'actionId': 'build'})
    result = await service.gateway.invoke(scope, effective, 'finish', {'actionId': 'finish'})
    await service.repository.publish(scope, TaskResult(task_id='consumer', status='succeeded', summary='Fixture',
                                                     artifacts=result['artifacts']))
    grant = service.freeze(scope)
    assert len(grant['targets']) == 4
    assert {target['taskId'] for target in grant['targets']} == {'base', 'lid', 'app', 'consumer'}


async def test_partial_repair_retains_prior_candidate_read_only_sources(composed):
    from kleos.projects.engineering import EngineeringService
    service, scope, _, baseline, refs, _ = composed
    store = service.repository.store
    with store.transaction() as session:
        session.get(Objective, scope.run_id).status = 'succeeded'
    submitted = EngineeringService(store).submit(scope.actor, scope.project_id, 'project.repair', scope.revision,
        'repair', objective='Repair lid only, keep prior candidate base and app', allowed_paths=['lid.py'])
    repair_scope = scope.model_copy(update={'run_id': submitted['runId'], 'revision': submitted['revision']})
    prior = [ref for values in refs.values() for ref in values if not ref['path'].startswith('outputs/')]
    task = TaskSpec(id='repair-lid', capability='cad', objective='Repair only lid',
                    allowed_paths=['lid.py'], input_artifact_ids=[ref['id'] for ref in prior])
    await service.repository.publish_plan(repair_scope, tasks=[task], superseded={}, turn=0, summary='Repair', question=None)
    old_lid = next(ref for ref in prior if ref['path'] == 'lid.py')
    await service.gateway.invoke(repair_scope, task, 'patch', {'actionId': 'patch', 'path': 'lid.py',
        'content': 'Repaired lid fixture', 'expectedSha256': old_lid['sha256']})
    await service.gateway.invoke(repair_scope, task, 'build', {'actionId': 'build'})
    result = await service.gateway.invoke(repair_scope, task, 'finish', {'actionId': 'finish'})
    await service.repository.publish(repair_scope, TaskResult(task_id=task.id, status='succeeded', summary='Fixture',
                                                            artifacts=result['artifacts']))
    grant = service.freeze(repair_scope)
    retained = {ref['path']: ref['id'] for ref in prior if ref['path'] != 'lid.py'}
    assert all(grant['closure'][path] == ident for path, ident in retained.items())
    assert grant['closure']['model.py'] != baseline['model.py']
    assert grant['closure']['board.txt'] == baseline['board.txt']
    assert grant['repairSourceRunId'] == scope.run_id and len(grant['repairSources']) == 3
    assert grant['closure']['lid.py'] != old_lid['id']
    assert service.validate(repair_scope, grant['id']) == grant


@pytest.mark.parametrize('change', ['actor', 'deadline', 'budget'])
async def test_composition_never_grants_rebound_scope_or_budget(composed, change):
    service, scope, _, _, _, _ = composed
    service.freeze(scope)
    altered = scope.model_copy(update={
        'actor': Actor(tenant_id='foreign', user_id='owner')} if change == 'actor' else {
        'deadline_at': scope.deadline_at+timedelta(minutes=1)} if change == 'deadline' else {'max_plan_calls': 32})
    with pytest.raises(DomainError):
        service.freeze(altered)


async def test_grant_allowance_does_not_reset_when_authority_changes(composed):
    service, scope, _, _, _, _ = composed
    scope = scope.model_copy(update={'max_plan_calls': 1})
    grant = service.freeze(scope)
    with service.repository.store.transaction() as session:
        project = session.get(Project, scope.project_id)
        project.data = {**project.data, 'requirements': {'bounded': 'changed snapshot'}}
    with pytest.raises(DomainError) as error:
        service.freeze(scope)
    assert error.value.code == 'composition_budget_exhausted'
    with service.repository.store.transaction() as session:
        assert session.get(Objective, scope.run_id).data['sourceCompositions'] == {grant['id']: grant}
