"""Output-only publication fence for server-authorized final compilation."""
import base64
import math
import time

from kleos.contracts.models import DomainError
from kleos.execution.gateway import NativeAction, NativeJob, export_path
from kleos.projects.store import Objective, fingerprint
from kleos.validation.policies import covered_output


def publication_closure(session, scope, task, prepared, provenance, composition, *, prior=None):
    grant = composition.validate_in_session(session, scope, provenance.get('compositionId'))
    targets = [target for target in grant['targets'] if target['taskId'] == task.id]
    if len(targets) != 1:
        raise DomainError('Compilation producer is outside its grant', 'composition_conflict')
    target = targets[0]
    job = session.get(NativeJob, provenance.get('nativeJobId'), with_for_update=True, populate_existing=True)
    expected_id = fingerprint({'compositionId': grant['id'], 'sourceJobId': target['jobId']})
    manifest = {ref['path']: ref['sha256'] for ref in grant['sources']}
    if (task.allowed_paths or task.capability.value != target['lane'] or task.max_actions != target['maxActions']
            or task.input_artifact_ids != [ref['id'] for ref in grant['sources']]
            or job is None or job.id != expected_id or job.project_id != scope.project_id
            or job.run_id != scope.run_id or job.task_id != task.id
            or job.task_hash != fingerprint(task.model_dump(mode='json'))
            or job.source_manifest != manifest or fingerprint(job.request) != job.request_hash
            or provenance.get('requestHash') != job.request_hash
            or provenance.get('imageDigest') != target['contract']['imageDigest']
            or provenance.get('compositionTarget') != target['jobId']):
        raise DomainError('Compilation publication differs from its pinned producer', 'composition_conflict')
    expected = {**target['contract'], 'jobId': job.id, 'revision': scope.revision,
        'allowedPaths': [], 'baselines': {}, 'maxActions': target['maxActions'],
        'sessionSeconds': target['maxSeconds'], 'compositionId': grant['id'],
        'compositionTarget': target['jobId']}
    if {key: value for key, value in job.request.items() if key not in {'sources', 'deadline'}} != expected:
        raise DomainError('Compilation changed its original execution contract', 'composition_conflict')
    run = session.get(Objective, scope.run_id)
    admitted = run.data.get('compositionExecutions', {}).get(grant['id'], {}).get(job.id, {})
    started = admitted.get('admittedAt')
    deadline = job.request.get('deadline')
    if (set(admitted) != {'requestHash', 'taskHash', 'admittedAt', 'deadline'}
            or admitted.get('requestHash') != job.request_hash or admitted.get('taskHash') != job.task_hash
            or type(started) not in {int, float} or not math.isfinite(started) or started > time.time()
            or type(deadline) not in {int, float} or not math.isfinite(deadline)
            or admitted.get('deadline') != deadline or not started < deadline <= started + target['maxSeconds']
            or deadline > scope.deadline_at.timestamp()):
        raise DomainError('Compilation exceeded its original objective deadline', 'composition_conflict')
    if prior is None:
        finish = session.get(NativeAction, (job.id, 'composition:finish'), with_for_update=True, populate_existing=True)
        action_hash = fingerprint({'actionId': 'composition:finish', 'action': 'finish', 'arguments': {}})
        if (job.status != 'running' or job.active_action != 'composition:finish'
                or finish is None or finish.status != 'pending' or finish.request_hash != action_hash
                or time.time() >= deadline):
            raise DomainError('Compilation has no authorized in-flight finish', 'composition_conflict')
    staged = job.request.get('sources', [])
    if len(staged) != len(grant['sources']):
        raise DomainError('Compilation omitted composed sources', 'composition_conflict')
    for item, ref in zip(staged, grant['sources'], strict=True):
        expected_source = {'path': ref['path'], 'artifactId': ref['id'], 'sha256': ref['sha256'],
            'contentBase64': base64.b64encode(composition.artifacts.blobs.get(ref['sha256'])).decode()}
        if item != expected_source:
            raise DomainError('Compilation input bytes or identities changed', 'composition_conflict')
    if not prepared or any(file['role'] != 'output' for file, _, _ in prepared):
        raise DomainError('Final compilation may publish outputs only', 'composition_conflict')
    names = {name for _, name, _ in prepared}
    if {ref['path'] for ref in target['coveredOutputs']} != {name for name in names if covered_output(target['lane'], name)}:
        raise DomainError('Final compilation changed its covered output scope', 'composition_conflict')
    for name in names:
        export_path(target['lane'], name)
    return grant['closure']
