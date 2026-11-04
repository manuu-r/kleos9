"""Deterministic final compilation over an existing frozen source grant.

This adapter is server-only, not a model tool or workflow scheduler. The native
gateway retains execution, action recovery, capacity and termination ownership.
"""
import base64
import copy
import math
import time

from sqlalchemy import select

from kleos.contracts.models import DomainError, TaskSpec
from kleos.execution.gateway import NativeAction, NativeJob
from kleos.execution.protocol import action_request
from kleos.projects.store import Objective, fingerprint


def compilation_task(grant, target):
    return TaskSpec(id=target['taskId'], capability=target['lane'], objective='Compile frozen source set',
        input_artifact_ids=[ref['id'] for ref in grant['sources']], allowed_paths=[], max_actions=target['maxActions'])


def compilation_job_id(grant, target):
    return fingerprint({'compositionId': grant['id'], 'sourceJobId': target['jobId']})


def fixed_action(action):
    if action not in {'build', 'render', 'finish'}:
        raise DomainError('Compilation cannot edit sources or invoke arbitrary actions', 'composition_scope_conflict')
    return action_request({'actionId': 'composition:'+action, 'action': action,
                           'arguments': {'view': 'isometric'} if action == 'render' else {}})


def succeeded(record, action):
    return (record.status == 'completed' and not record.result.get('blockers')
        and any(item.get('name') == 'native.'+action and item.get('details', {}).get('executionSucceeded') is True
                for item in record.result.get('observations', [])))


class NativeComposition:
    def __init__(self, gateway, grants):
        if gateway.projects is not grants.repository.store or gateway.artifacts is not grants.artifacts:
            raise ValueError('Compilation requires the same authoritative project and artifact services')
        self.gateway, self.grants = gateway, grants

    def _job(self, scope, grant_id, target_task_id, action):
        """Atomically pin the original duration and job identity before any IO."""
        with self.gateway.projects.transaction() as session:
            grant = self.grants.validate_in_session(session, scope, grant_id)
            targets = [target for target in grant['targets'] if target['taskId'] == target_task_id]
            if len(targets) != 1:
                raise DomainError('Compilation target is outside its grant', 'composition_scope_conflict')
            target = targets[0]
            actions = ['build', 'render', 'finish'] if target['lane'] == 'cad' else ['build', 'finish']
            if target['actions'] != actions or action not in actions or target['maxActions'] != len(actions):
                raise DomainError('Compilation action differs from its fixed target contract', 'composition_scope_conflict')
            task = compilation_task(grant, target)
            ident = compilation_job_id(grant, target)
            task_hash = fingerprint(task.model_dump(mode='json'))
            run = session.get(Objective, scope.run_id)
            history = run.data.get('compositionExecutions', {})
            if (not isinstance(history, dict) or not isinstance(history.get(grant_id, {}), dict)):
                raise DomainError('Compilation execution history changed', 'composition_conflict')
            admitted = history.get(grant_id, {}).get(ident)
            job = session.get(NativeJob, ident, with_for_update=True)
            now = time.time()
            if (admitted is None) != (job is None):
                raise DomainError('Compilation admission or durable job is missing', 'composition_conflict')
            if admitted is None:
                if action != 'build':
                    raise DomainError('Compilation must start with its fixed build action', 'composition_action_order')
                started, deadline = now, min(scope.deadline_at.timestamp(), now+target['maxSeconds'])
            else:
                if (not isinstance(admitted, dict)
                        or set(admitted) != {'requestHash', 'taskHash', 'admittedAt', 'deadline'}):
                    raise DomainError('Compilation admission changed', 'composition_conflict')
                started, deadline = admitted['admittedAt'], admitted['deadline']
            if (type(started) not in {int, float} or not math.isfinite(started) or started > now
                    or type(deadline) not in {int, float} or not math.isfinite(deadline)
                    or not started < deadline <= min(scope.deadline_at.timestamp(), started+target['maxSeconds'])):
                raise DomainError('Compilation time allowance changed', 'composition_conflict')
            sources = [{'path': ref['path'], 'artifactId': ref['id'], 'sha256': ref['sha256'],
                        'contentBase64': base64.b64encode(self.grants.artifacts.blobs.get(ref['sha256'])).decode()}
                       for ref in grant['sources']]
            request = {**copy.deepcopy(target['contract']), 'jobId': ident, 'revision': scope.revision,
                'allowedPaths': [], 'baselines': {}, 'maxActions': target['maxActions'],
                'sessionSeconds': target['maxSeconds'], 'compositionId': grant_id,
                'compositionTarget': target['jobId'], 'sources': sources, 'deadline': deadline}
            pool = self.gateway.capacity.pool_id if self.gateway.capacity is not None else None
            if request.get('capacityPoolId') != pool:
                raise DomainError('Compilation capacity pool differs from its original contract', 'capacity_identity_conflict')
            checksum = fingerprint(request)
            manifest = {ref['path']: ref['sha256'] for ref in grant['sources']}
            receipt = {'requestHash': checksum, 'taskHash': task_hash, 'admittedAt': started, 'deadline': deadline}
            if job is not None:
                if (admitted != receipt or job.project_id != scope.project_id or job.run_id != scope.run_id
                        or job.task_id != task.id or job.task_hash != task_hash or job.request != request
                        or job.request_hash != checksum or job.source_manifest != manifest):
                    raise DomainError('Compilation durable contract changed', 'composition_conflict')
            else:
                job = NativeJob(id=ident, project_id=scope.project_id, run_id=scope.run_id, task_id=task.id,
                    task_hash=task_hash, request_hash=checksum, request=request, source_manifest=manifest,
                    status='pending', active_action=None)
                session.add(job)
                run.data = {**run.data, 'compositionExecutions': {**history,
                    grant_id: {**history.get(grant_id, {}), ident: receipt}}}
            records = {record.action_id: record for record in session.scalars(
                select(NativeAction).where(NativeAction.job_id == ident))}
            for name, record in records.items():
                if name in {'_dispatch', '_termination', '_cleanup'}:
                    continue
                if name not in {'composition:'+value for value in actions}:
                    raise DomainError('Compilation contains an unauthorized action', 'composition_conflict')
                if record.request_hash != fingerprint(fixed_action(name.removeprefix('composition:'))):
                    raise DomainError('Compilation action identity changed', 'composition_conflict')
            for previous in actions[:actions.index(action)]:
                record = records.get('composition:'+previous)
                if record is None or not succeeded(record, previous):
                    raise DomainError('Compilation prerequisite is incomplete or uncertain', 'composition_action_order')
            record = records.get('composition:'+action)
            if record is None or record.status != 'completed':
                if job.status in {'finished', 'failed', 'cancelled'}:
                    raise DomainError('Compilation is closed; unresolved actions require reconciliation', 'native_uncertain')
                if now >= deadline:
                    raise DomainError('Compilation exceeded its original deadline', 'deadline_exceeded')
            # Reading a committed observation consumes no capacity or new time;
            # unresolved actions still use the gateway's original recovery path.
            cached = copy.deepcopy(record.result) if record is not None and record.status == 'completed' else None
            return task, job, cached

    async def invoke(self, scope, grant_id, target_task_id, action):
        item = fixed_action(action)
        task, job, cached = self._job(scope, grant_id, target_task_id, action)
        if cached is not None:
            return cached
        return await self.gateway._invoke_job(scope, task, item, job, composition=self.grants)
