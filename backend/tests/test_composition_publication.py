"""Output-only final publication against a real frozen authoring grant."""
import base64
import time

import pytest
from sqlalchemy import func, select
from test_composition import composed as composed

from kleos.artifacts.service import Artifact, NativePublication
from kleos.contracts.models import DomainError, TaskSpec
from kleos.execution.gateway import NativeAction, NativeJob
from kleos.projects.store import Objective, Project, fingerprint


def compiled(service, scope):
    grant = service.freeze(scope)
    target = next(target for target in grant['targets'] if target['taskId'] == 'base')
    task = TaskSpec(id=target['taskId'], capability=target['lane'], objective='Compile frozen source set',
        input_artifact_ids=[ref['id'] for ref in grant['sources']], max_actions=target['maxActions'])
    ident = fingerprint({'compositionId': grant['id'], 'sourceJobId': target['jobId']})
    started = time.time()
    request = {**target['contract'], 'jobId': ident, 'revision': scope.revision,
        'allowedPaths': [], 'baselines': {}, 'maxActions': target['maxActions'],
        'sessionSeconds': target['maxSeconds'], 'compositionId': grant['id'],
        'compositionTarget': target['jobId'], 'deadline': min(scope.deadline_at.timestamp(), started+target['maxSeconds']),
        'sources': [{'path': ref['path'], 'artifactId': ref['id'], 'sha256': ref['sha256'],
            'contentBase64': base64.b64encode(service.artifacts.blobs.get(ref['sha256'])).decode()}
            for ref in grant['sources']]}
    with service.repository.store.transaction() as session:
        session.add(NativeJob(id=ident, project_id=scope.project_id, run_id=scope.run_id, task_id=task.id,
            task_hash=fingerprint(task.model_dump(mode='json')), request_hash=fingerprint(request), request=request,
            source_manifest={ref['path']: ref['sha256'] for ref in grant['sources']},
            status='running', active_action='composition:finish'))
        session.add(NativeAction(job_id=ident, action_id='composition:finish', status='pending', result={},
            request_hash=fingerprint({'actionId': 'composition:finish', 'action': 'finish', 'arguments': {}})))
        run = session.get(Objective, scope.run_id)
        run.data = {**run.data, 'compositionExecutions': {grant['id']: {ident: {
            'requestHash': fingerprint(request), 'taskHash': fingerprint(task.model_dump(mode='json')),
            'admittedAt': started, 'deadline': request['deadline']}}}}
    provenance = {'nativeJobId': ident, 'requestHash': fingerprint(request),
        'imageDigest': target['contract']['imageDigest'], 'compositionId': grant['id'],
        'compositionTarget': target['jobId']}
    files = [{'path': ref['path'], 'role': 'output', 'content': b'Fixed fixture output bytes'}
             for ref in target['coveredOutputs']]
    return grant, task, provenance, files


async def test_compiled_outputs_keep_original_source_ids_and_replay_atomically(composed):
    service, scope, _, baseline, _, _ = composed
    grant, task, provenance, files = compiled(service, scope)
    store = service.repository.store
    with store.transaction() as session:
        source_count = session.scalar(select(func.count()).select_from(Artifact).where(Artifact.name.not_like('outputs/%')))
    refs = await service.artifacts.publish_compilation(scope, task, files, provenance, service)
    with store.transaction() as session:
        job = session.get(NativeJob, provenance['nativeJobId'])
        job.status, job.active_action = 'finished', None
    assert refs == await service.artifacts.publish_compilation(scope, task, files, provenance, service)
    assert {ref.path for ref in refs} == {file['path'] for file in files}
    with store.transaction() as session:
        assert session.get(Project, scope.project_id).data['sourceManifest'] == baseline
        assert session.scalar(select(func.count()).select_from(Artifact).where(Artifact.name.not_like('outputs/%'))) == source_count
        for ref in refs:
            row = session.get(Artifact, ref.id)
            assert row.details['sourceClosure'] == grant['closure']
            assert row.details['compositionId'] == grant['id'] and row.status == 'candidate'
        assert session.scalar(select(func.count()).select_from(NativePublication).where(
            NativePublication.job_id == provenance['nativeJobId'])) == 1
    assert service.validate(scope, grant['id']) == grant
    changed = [dict(file, content=b'different output') for file in files]
    with pytest.raises(DomainError, match='changed during replay'):
        await service.artifacts.publish_compilation(scope, task, changed, provenance, service)
    with store.transaction() as session:
        session.get(Objective, scope.run_id).status = 'cancelled'
    with pytest.raises(DomainError):
        await service.artifacts.publish_compilation(scope, task, files, provenance, service)


@pytest.mark.parametrize('mutation', ['source', 'omitted-output', 'source-bytes', 'image', 'resources',
    'deadline', 'identity', 'closure', 'target', 'native-bypass', 'failed', 'cancelled', 'pending',
    'no-finish', 'wrong-finish', 'long-deadline', 'extra-output'])
async def test_compilation_publication_rejects_changed_contract_without_partial_files(composed, mutation):
    service, scope, _, _, _, _ = composed
    grant, task, provenance, files = compiled(service, scope)
    store = service.repository.store
    if mutation == 'source':
        files.append({'path': 'model.py', 'role': 'source', 'content': b'changed', 'expectedSha256': None})
    elif mutation == 'omitted-output':
        files.pop()
    elif mutation == 'extra-output':
        files.append({'path': 'outputs/unrequested.step', 'role': 'output', 'content': b'extra'})
    elif mutation == 'target':
        provenance['compositionTarget'] = 'different-target'
    elif mutation not in {'native-bypass'}:
        with store.transaction() as session:
            job = session.get(NativeJob, provenance['nativeJobId'])
            request = dict(job.request)
            if mutation == 'source-bytes':
                request['sources'] = [dict(item, contentBase64='dGFtcGVyZWQ=') for item in request['sources']]
            elif mutation == 'image':
                request['imageDigest'] = 'sha256:'+'f'*64
            elif mutation == 'resources':
                request['resources'] = {**request['resources'], 'cpus': 99}
            elif mutation == 'deadline':
                request['deadline'] += 1000
            elif mutation == 'identity':
                job.task_id = 'another-task'
            elif mutation == 'closure':
                job.source_manifest = {}
            elif mutation in {'failed', 'cancelled', 'pending'}:
                job.status, job.active_action = mutation, None
            elif mutation == 'no-finish':
                session.delete(session.get(NativeAction, (job.id, 'composition:finish')))
            elif mutation == 'wrong-finish':
                session.get(NativeAction, (job.id, 'composition:finish')).request_hash = 'f'*64
            if mutation == 'long-deadline':
                run = session.get(Objective, scope.run_id)
                entries = run.data['compositionExecutions'][grant['id']]
                run.data = {**run.data, 'compositionExecutions': {grant['id']: {job.id: {
                    **entries[job.id], 'admittedAt': request['deadline']-10000,
                    'deadline': request['deadline'], 'requestHash': fingerprint(request)}}}}
            job.request, job.request_hash = request, fingerprint(request)
            provenance['requestHash'] = job.request_hash
            run = session.get(Objective, scope.run_id)
            admission = run.data['compositionExecutions'][grant['id']][job.id]
            run.data = {**run.data, 'compositionExecutions': {grant['id']: {job.id: {
                **admission, 'requestHash': job.request_hash, 'deadline': request['deadline']}}}}
    with store.transaction() as session:
        before = session.scalar(select(func.count()).select_from(Artifact))
    with pytest.raises(DomainError):
        if mutation == 'native-bypass':
            await service.artifacts.publish_native(scope, task, files, provenance)
        else:
            await service.artifacts.publish_compilation(scope, task, files, provenance, service)
    with store.transaction() as session:
        assert session.scalar(select(func.count()).select_from(Artifact)) == before
        assert session.scalar(select(func.count()).select_from(NativePublication).where(
            NativePublication.job_id == provenance['nativeJobId'])) == 0
