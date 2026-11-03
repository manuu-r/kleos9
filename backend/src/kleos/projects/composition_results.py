"""Atomic all-target compilation handoff; immutable authoring results stay intact."""
import base64
import copy
import json
import math
import time
from datetime import datetime
from types import SimpleNamespace

from sqlalchemy import select

from kleos.artifacts.service import Artifact, NativePublication
from kleos.contracts.models import ArtifactRef, DomainError, TaskResult, TaskSpec
from kleos.execution.composition import compilation_job_id, compilation_task, fixed_action, succeeded
from kleos.execution.gateway import NativeAction, NativeJob, artifact_inputs
from kleos.orchestration.specialist import ToolResult
from kleos.projects.composition import (
    CompositionService,
    authoring_plan,
    compilation_targets,
    reference,
    source_successors,
)
from kleos.projects.graph_repository import GraphRepository
from kleos.projects.store import Project, fingerprint
from kleos.validation.policies import authored_native_rows, covered_output

KEY = 'compositionResults'


def reject(message):
    raise DomainError(message, 'composition_result_conflict')


def _closed_job(session, job):
    proof = session.get(NativeAction, (job.id, '_termination'))
    if (session.scalar(select(NativeAction).where(NativeAction.job_id == job.id,
            NativeAction.status != 'completed')) is not None
            or proof is None or proof.request_hash != job.request_hash
            or proof.result != {'kind': 'termination-proof', 'jobId': job.id, 'containerStopped': True}):
        reject('Native producer termination or action history changed')


def authoring_plan_hash(run):
    return fingerprint(authoring_plan(run))


def _current_grant(session, run, authored, *, grant=None, historical=False):
    """Completed-run read validation: no execution scope, lease or deadline refresh."""
    if grant is None:
        _, grant = CompositionService._history(run)
    if grant is None:
        reject('Completed compilation has no source grant')
    project = session.get(Project, run.project_id)
    if (project is None or project.data.get('deletion') is not None or run.status not in {'pending', 'running', 'waiting', 'succeeded'}
            or project.id != grant['projectId'] or run.id != grant['runId']
            or project.tenant_id != grant['actor']['tenantId']
            or run.revision != grant['revision'] or project.context_revision != grant['revision']
            or project.data.get('sourceManifest', {}) != grant['baseline']
            or {key: project.data.get(key) for key in grant['authority']} != grant['authority']
            or not historical and authoring_plan_hash(run) != grant['planHash']
            or fingerprint(run.data.get('runtimePins')) != grant['runtimePinsHash']
            or run.data.get('toolPolicySnapshot', {}) != grant['toolPolicySnapshot']):
        reject('Completed compilation source context or authority changed')
    repairs = [ArtifactRef.model_validate(value).model_dump(mode='json', by_alias=True)
               for value in run.data.get('repairInputs', [])]
    if repairs != grant['repairSources'] or run.data.get('repairSourceRunId') != grant['repairSourceRunId']:
        reject('Retained repair source authority changed')
    pinned = GraphRepository._repair_refs(session, project, run)
    if pinned and run.data.get('repairBaseline') != grant['baseline']:
        reject('Retained repair baseline changed')
    for ident, ref in pinned.items():
        row = session.get(Artifact, ident)
        job = session.get(NativeJob, row.details.get('nativeJobId'))
        publication = session.scalar(select(NativePublication).where(
            NativePublication.run_id == grant['repairSourceRunId'], NativePublication.task_id == row.details.get('taskId'),
            NativePublication.job_id == row.details.get('nativeJobId')))
        if (row.status != 'candidate' or row.details.get('role') != 'source' or job is None
                or job.project_id != project.id or job.run_id != grant['repairSourceRunId']
                or job.task_id != row.details.get('taskId') or job.status != 'finished' or job.active_action is not None
                or job.request_hash != row.details.get('requestHash') or fingerprint(job.request) != job.request_hash
                or job.request.get('imageDigest') != row.details.get('imageDigest')
                or job.source_manifest.get(row.name) != row.sha256 or publication is None
                or ref not in [ArtifactRef.model_validate(value) for value in publication.references]):
            reject('Retained source producer provenance changed')
        _closed_job(session, job)
    closure = {**grant['baseline'], **{ref['path']: ref['id'] for ref in repairs}}
    produced = {}
    for row in authored:
        produced.setdefault(row.details['taskId'], []).append(row)
    winners, lineages = source_successors(session, run, authored)
    closure.update({path: row.id for path, row in winners.items()})
    authors = grant.get('authors', grant['targets'])
    targets, replaced = compilation_targets(authors, authored, winners, lineages)
    if (closure != grant['closure'] or set(produced) != {target['taskId'] for target in authors}
            or targets != grant['targets'] or replaced != grant.get('replacedTargets', {})
            or lineages != grant.get('sourceLineage', lineages)):
        reject('Completed compilation omits or replaces an authored producer')
    for ref in grant['sources']:
        row = session.get(Artifact, ref['id'])
        if row is None or row.project_id != project.id or reference(row) != ref or closure.get(row.name) != row.id:
            reject('Frozen source identity changed')
    if fingerprint(grant['sources']) != grant['sourceClosureHash']:
        reject('Frozen source closure hash changed')
    for target in authors:
        result = TaskResult.model_validate(run.data.get('results', {}).get(target['taskId'], {}))
        if result.task_id != target['taskId'] or result.status != 'succeeded' or result.blockers:
            reject('Original authoring task is no longer complete')
        job = session.get(NativeJob, target['jobId'])
        publication = session.scalar(select(NativePublication).where(NativePublication.run_id == run.id,
            NativePublication.task_id == target['taskId'], NativePublication.job_id == target['jobId']))
        rows = produced[target['taskId']]
        if (job is None or job.project_id != project.id or job.run_id != run.id or job.task_id != target['taskId']
                or job.status != 'finished' or job.active_action is not None
                or job.task_hash != target['taskHash'] or job.request_hash != target['requestHash']
                or fingerprint(job.request) != job.request_hash or fingerprint(job.source_manifest) != target['sourceManifestHash']
                or {key: value for key, value in job.request.items() if key not in {'sources', 'deadline'}} != target['contract']
                or publication is None or publication.fingerprint != target['publicationHash']
                or {ref.id for ref in map(ArtifactRef.model_validate, publication.references)} != {row.id for row in rows}
                or sorted((reference(row) for row in rows if row.details.get('role') == 'output'
                           and covered_output(row.lane, row.name)), key=lambda value: value['path']) != target['coveredOutputs']):
            reject('Original authoring provenance changed after compilation')
        plans = {value['id']: TaskSpec.model_validate(value)
                 for value in grant.get('authoringPlan', authoring_plan(run))['tasks']}
        task = plans.get(target['taskId'])
        if (task is None or fingerprint(task.model_copy(update={
                'input_artifact_ids': [value.get('artifactId') for value in artifact_inputs(session, job)]
                }).model_dump(mode='json')) != job.task_hash):
            reject('Frozen authoring plan differs from its actual producer job')
        _closed_job(session, job)
        finish = list(session.scalars(select(NativeAction).where(NativeAction.job_id == job.id)))
        if not any(succeeded(action, 'finish') and action.result.get('artifacts')
                and sorted((ArtifactRef.model_validate(value) for value in action.result['artifacts']), key=lambda ref: ref.id)
                    == sorted((ArtifactRef.model_validate(reference(row)) for row in rows), key=lambda ref: ref.id)
                    for action in finish):
            reject('Original finish publication receipt changed')
        for row in rows:
            if (row.lane != target['lane'] or any(row.details.get(key) != value for key, value in {
                    'runId': run.id, 'taskId': target['taskId'], 'nativeJobId': job.id,
                    'requestHash': job.request_hash, 'imageDigest': target['contract']['imageDigest']}.items())
                    or row.details.get('role') == 'source' and job.source_manifest.get(row.name) != row.sha256):
                reject('Original published source or output provenance changed')
    return grant


def _target_result(session, run, grant, target):
    job_id, task = compilation_job_id(grant, target), compilation_task(grant, target)
    job = session.get(NativeJob, job_id)
    admission = run.data.get('compositionExecutions', {}).get(grant['id'], {}).get(job_id)
    expected = {**target['contract'], 'jobId': job_id, 'revision': grant['revision'], 'allowedPaths': [],
        'baselines': {}, 'maxActions': target['maxActions'], 'sessionSeconds': target['maxSeconds'],
        'compositionId': grant['id'], 'compositionTarget': target['jobId']}
    if (job is None or job.project_id != run.project_id or job.run_id != run.id or job.task_id != task.id
            or job.status != 'finished' or job.active_action is not None
            or job.task_hash != fingerprint(task.model_dump(mode='json')) or job.request_hash != fingerprint(job.request)
            or {key: value for key, value in job.request.items() if key not in {'sources', 'deadline'}} != expected
            or job.source_manifest != {ref['path']: ref['sha256'] for ref in grant['sources']}):
        reject('Compiled job differs from its output-only grant')
    if (not isinstance(admission, dict) or set(admission) != {'requestHash', 'taskHash', 'admittedAt', 'deadline'}
            or admission['requestHash'] != job.request_hash or admission['taskHash'] != job.task_hash
            or admission['deadline'] != job.request.get('deadline')):
        reject('Compilation admission receipt changed')
    started, deadline = admission['admittedAt'], admission['deadline']
    if (type(started) not in {int, float} or type(deadline) not in {int, float}
            or not math.isfinite(started) or not math.isfinite(deadline)
            or not started < deadline <= started+target['maxSeconds']
            or deadline > datetime.fromisoformat(grant['deadlineAt']).timestamp()):
        reject('Compilation exceeded its original deadline grant')
    staged = job.request.get('sources', [])
    if len(staged) != len(grant['sources']):
        reject('Compiled job omitted frozen sources')
    from kleos.execution.protocol import digest
    for value, ref in zip(staged, grant['sources'], strict=True):
        if (set(value) != {'path', 'artifactId', 'sha256', 'contentBase64'}
                or value['path'] != ref['path'] or value['artifactId'] != ref['id'] or value['sha256'] != ref['sha256']
                or not isinstance(value['contentBase64'], str) or len(value['contentBase64']) > (ref['size']+2)//3*4):
            reject('Compiled job source identity changed')
        try:
            raw = base64.b64decode(value['contentBase64'], validate=True)
        except ValueError:
            reject('Compiled source bytes are malformed')
        if len(raw) != ref['size'] or digest(raw) != ref['sha256']:
            reject('Compiled source bytes changed')
    records = {row.action_id: row for row in session.scalars(select(NativeAction).where(NativeAction.job_id == job_id))}
    expected_ids = {'composition:'+action for action in target['actions']}
    if set(records)-{'_dispatch', '_termination', '_cleanup'} != expected_ids:
        reject('Compilation action set is incomplete or contains source edits')
    for action in target['actions']:
        receipt = records['composition:'+action]
        if receipt.request_hash != fingerprint(fixed_action(action)) or not succeeded(receipt, action):
            reject('Compilation action remains failed or uncertain')
    if any(row.status != 'completed' for row in records.values()):
        reject('Compilation compute outcome remains uncertain')
    termination = records.get('_termination')
    if (termination is None or termination.request_hash != job.request_hash
            or termination.result != {'kind': 'termination-proof', 'jobId': job.id, 'containerStopped': True}):
        reject('Compilation termination is not confirmed')
    result = ToolResult.model_validate(records['composition:finish'].result)
    publication = session.scalar(select(NativePublication).where(NativePublication.run_id == run.id,
        NativePublication.task_id == task.id, NativePublication.job_id == job.id))
    if (publication is None or result.blockers or result.wait_ids or not result.artifacts
            or result.artifacts != [ArtifactRef.model_validate(value) for value in publication.references]):
        reject('Compilation finish differs from its exact published outputs')
    rows = []
    for ref in result.artifacts:
        row = session.get(Artifact, ref.id)
        if (row is None or row.project_id != run.project_id or ArtifactRef.model_validate(reference(row)) != ref
                or row.revision != run.revision or row.status != 'candidate' or row.lane != target['lane']
                or row.details.get('role') != 'output' or row.details.get('origin') != 'native_candidate'
                or row.details.get('sourceClosure') != grant['closure']
                or any(row.details.get(key) != value for key, value in {
                    'runId': run.id, 'taskId': task.id, 'nativeJobId': job.id, 'requestHash': job.request_hash,
                    'imageDigest': target['contract']['imageDigest'], 'compositionId': grant['id'],
                    'compositionTarget': target['jobId']}.items())):
            reject('Compiled output provenance changed')
        rows.append(row)
    if ({row.name for row in rows if covered_output(row.lane, row.name)}
            != {ref['path'] for ref in target['coveredOutputs']}):
        reject('Compilation omitted or expanded its covered outputs')
    return result, rows, publication.fingerprint


class CompositionResults:
    def __init__(self, composition, *, clock=time.time):
        self.composition, self.clock = composition, clock

    def record(self, scope, grant_id, results):
        with self.composition.repository.store.transaction() as session:
            grant = self.composition.validate_in_session(session, scope, grant_id)
            _, run = self.composition.repository._check(session, scope)
            if not isinstance(results, dict) or set(results) != {target['taskId'] for target in grant['targets']}:
                reject('All compilation targets must finish before handoff')
            checked = {}
            for target in grant['targets']:
                result, _, publication_hash = _target_result(session, run, grant, target)
                if ToolResult.model_validate(results[target['taskId']]) != result:
                    reject('Submitted compilation result differs from its durable finish receipt')
                checked[target['taskId']] = {'result': result.model_dump(mode='json'),
                    'publicationHash': publication_hash, 'jobId': compilation_job_id(grant, target)}
            value = {'schemaVersion': 1, 'grantId': grant_id, 'targets': checked}
            if len(json.dumps(value, allow_nan=False).encode()) > 512*1024:
                reject('Completed compilation projection exceeds its bound')
            history = run.data.get(KEY, {})
            prior = history.get(grant_id)
            if prior:
                if {key: val for key, val in prior.items() if key not in {'recordedAt', 'receiptHash'}} != value:
                    reject('Completed compilation handoff cannot be rewritten')
                _projection(session, run, authored_native_rows(session, run))
                return copy.deepcopy(prior)
            now = self.clock()
            admitted = run.data['compositionExecutions'][grant_id]
            if (type(now) not in {int, float} or not math.isfinite(now) or now >= scope.deadline_at.timestamp()
                    or any(now < admitted[compilation_job_id(grant, target)]['admittedAt'] for target in grant['targets'])):
                reject('Compilation handoff exceeded the original run deadline')
            value['recordedAt'] = now
            receipt = {**value, 'receiptHash': fingerprint(value)}
            run.data = {**run.data, KEY: {**history, grant_id: receipt}}
            self.composition.repository.store._emit(session,
                session.get(Project, scope.project_id), 'sources.compilation_completed', {
                    'runId': run.id, 'compositionId': grant_id, 'targetCount': len(checked), 'accepted': False})
            return copy.deepcopy(receipt)


def _historical_rows(session, run, grant):
    frozen, current = grant.get('authoringPlan'), authoring_plan(run)
    if not isinstance(frozen, dict) or fingerprint(frozen) != grant['planHash']:
        reject('Historical compilation lacks a pinned authoring plan')
    for field in ('tasks', 'authoringTasks'):
        now = {task['id']: TaskSpec.model_validate(task) for task in current[field]}
        for saved in frozen[field]:
            if now.get(saved['id']) != TaskSpec.model_validate(saved):
                reject('Historical producer task was removed, superseded or changed')
    if any(current['superseded'].get(key) != value for key, value in frozen['superseded'].items()):
        reject('Historical producer dependency resolution changed')
    authors = {target['taskId'] for target in grant.get('authors', grant['targets'])}
    view = SimpleNamespace(id=run.id, project_id=run.project_id, revision=run.revision, data={**run.data,
        'results': {key: value for key, value in run.data.get('results', {}).items() if key in authors},
        'superseded': frozen['superseded']})
    return authored_native_rows(session, view)


def _projection(session, run, authored, *, grant_id=None, historical=False):
    history, current = CompositionService._history(run)
    grant = history.get(grant_id) if grant_id is not None else current
    receipt = run.data.get(KEY, {}).get(grant.get('id')) if isinstance(grant, dict) else None
    if receipt is None:
        return None
    if historical:
        authored = _historical_rows(session, run, grant)
    grant = _current_grant(session, run, authored, grant=grant, historical=historical)
    if (receipt.get('receiptHash') != fingerprint({key: value for key, value in receipt.items() if key != 'receiptHash'})
            or receipt.get('grantId') != grant['id'] or receipt.get('schemaVersion') != 1
            or set(receipt.get('targets', {})) != {target['taskId'] for target in grant['targets']}
            or type(receipt.get('recordedAt')) not in {int, float} or not math.isfinite(receipt['recordedAt'])
            or receipt['recordedAt'] >= datetime.fromisoformat(grant['deadlineAt']).timestamp()):
        reject('Completed compilation receipt is invalid')
    outputs = {}
    for target in grant['targets']:
        result, rows, publication_hash = _target_result(session, run, grant, target)
        if receipt['recordedAt'] < run.data.get('compositionExecutions', {}).get(grant['id'], {}).get(
                compilation_job_id(grant, target), {}).get('admittedAt', math.inf):
            reject('Completed compilation predates its native admission')
        expected = {'result': result.model_dump(mode='json'), 'publicationHash': publication_hash,
                    'jobId': compilation_job_id(grant, target)}
        if receipt['targets'][target['taskId']] != expected:
            reject('Completed compilation target receipt changed')
        outputs[target['taskId']] = rows
    return grant, outputs


def completed_native_rows(session, run, authored):
    projection = _projection(session, run, authored)
    if projection is None:
        return authored
    grant, outputs = projection
    changed = set(grant['closure'].values())-set(grant['baseline'].values())
    sources = [session.get(Artifact, ref['id']) for ref in grant['sources'] if ref['id'] in changed]
    return sources+[row for rows in outputs.values() for row in rows]


def compiled_carryforward(session, run, row, ref, verified):
    """Recognize a completed output echoed by a later non-producer task.

    This does not select it or establish current applicability. The canonical
    projection performs those checks after the raw authoring scan completes.
    Historical compiled refs may remain in immutable validation results.
    """
    ident = row.details.get('compositionId')
    cache_key = (ident, row.details.get('taskId'))
    if cache_key in verified:
        if ref not in verified[cache_key]:
            reject('Carried compiled output differs from its completed publication')
        return True
    history, _ = CompositionService._history(run)
    grant = history.get(ident)
    receipt = run.data.get(KEY, {}).get(ident)
    if (grant is None or not isinstance(receipt, dict) or row.details.get('role') != 'output'
            or receipt.get('schemaVersion') != 1 or receipt.get('grantId') != ident
            or receipt.get('receiptHash') != fingerprint({key: value for key, value in receipt.items() if key != 'receiptHash'})
            or set(receipt.get('targets', {})) != {target['taskId'] for target in grant['targets']}):
        reject('Carried compiled output lacks its completed handoff')
    targets = [target for target in grant['targets'] if target['taskId'] == row.details.get('taskId')]
    if len(targets) != 1:
        reject('Carried compiled output has no producer target')
    target = targets[0]
    result, _, publication_hash = _target_result(session, run, grant, target)
    expected = {'result': result.model_dump(mode='json'), 'publicationHash': publication_hash,
                'jobId': compilation_job_id(grant, target)}
    if receipt['targets'][target['taskId']] != expected or ref not in result.artifacts:
        reject('Carried compiled output differs from its completed publication')
    verified[cache_key] = result.artifacts
    return True


def effective_artifacts(session, run, task_id):
    """Graph dependencies read replacements without mutating authored TaskResults."""
    original = [ArtifactRef.model_validate(value) for value in run.data.get('results', {}).get(task_id, {}).get('artifacts', [])]
    grant = run.data.get('sourceComposition')
    if not isinstance(grant, dict) or grant.get('id') not in run.data.get(KEY, {}):
        return original
    projection = _projection(session, run, authored_native_rows(session, run))
    if projection is None:
        return original
    if task_id not in projection[1] and task_id not in projection[0].get('replacedTargets', {}):
        return original
    sources = [ref for ref in original if session.get(Artifact, ref.id).details.get('role') != 'output']
    return sources+[ArtifactRef.model_validate(reference(row)) for row in projection[1].get(task_id, [])]


def input_projection(session, run):
    """Read-only dependency context, explicitly distinct from current release inputs.

    Appending tasks can retire current applicability without erasing exact old
    output bytes. Only unchanged original producer plans and a fully verified
    completed receipt permit historical reads; corruption never causes fallback.
    The caller must hold current project/actor authority before using this helper.
    """
    original = {key: TaskResult.model_validate(value).artifacts for key, value in run.data.get('results', {}).items()}
    history, current = CompositionService._history(run)
    receipts = run.data.get(KEY, {})
    if not receipts:
        return {'artifacts': original, 'historical': False, 'compositionId': None, 'historicalTaskIds': []}
    if not isinstance(receipts, dict) or not set(receipts) <= history.keys():
        reject('Compilation read history has no immutable source grant')
    for receipt in receipts.values():
        if (not isinstance(receipt, dict) or type(receipt.get('recordedAt')) not in {int, float}
                or not math.isfinite(receipt['recordedAt'])):
            reject('Compilation read history is malformed')
    ident = (current['id'] if current and current['id'] in receipts else
             max(receipts, key=lambda key: (receipts[key]['recordedAt'], key)))
    grant = history[ident]
    historical = current is None or current['id'] != ident or authoring_plan_hash(run) != grant['planHash']
    projection = _projection(session, run, None if historical else authored_native_rows(session, run),
                             grant_id=ident, historical=historical)
    if projection is None:
        reject('Compilation read receipt is missing')
    _, outputs = projection
    projected = dict(original)
    for task_id, rows in outputs.items():
        projected[task_id] = [ref for ref in original[task_id] if session.get(Artifact, ref.id).details.get('role') != 'output']
        projected[task_id] += [ArtifactRef.model_validate(reference(row)) for row in rows]
    allowed = {task_id: {row.id for row in rows} for task_id, rows in outputs.items()}
    allowed.update({task_id: set() for task_id in grant.get('replacedTargets', {})})
    for task_id, refs in projected.items():
        filtered = []
        for ref in refs:
            row = session.get(Artifact, ref.id)
            if row is None or ArtifactRef.model_validate(reference(row)) != ref or row.project_id != run.project_id:
                reject('Projected task input identity changed')
            producer = row.details.get('taskId')
            if (row.details.get('role') == 'output' and row.details.get('origin') == 'native_candidate'
                    and producer in allowed and ref.id not in allowed[producer]):
                continue  # Remove echoes, never silently retarget the task's artifact IDs.
            filtered.append(ref)
        projected[task_id] = filtered
    historical_tasks = sorted(set(outputs) if historical else set(grant.get('replacedTargets', {})))
    return {'artifacts': projected, 'historical': historical or bool(historical_tasks),
            'compositionId': ident, 'historicalTaskIds': historical_tasks}


def input_artifacts(session, run, task_id):
    return input_projection(session, run)['artifacts'].get(task_id, [])


def historical_input_refs(session, run):
    """Exact permitted read references; never feed these directly to release selection."""
    return {ref.id: ref for refs in input_projection(session, run)['artifacts'].values() for ref in refs}


def retained_repair_sources(session, run):
    projection = _projection(session, run, authored_native_rows(session, run))
    if projection is None:
        return {}
    grant, _ = projection
    return {ref['path']: ArtifactRef.model_validate(ref) for ref in grant['repairSources']
            if grant['closure'].get(ref['path']) == ref['id']}
