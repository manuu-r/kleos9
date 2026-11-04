"""Workbench engineering commands over dynamic objectives, with conservative recovery."""
import copy
import hashlib
import json
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import or_, select

from kleos.artifacts.service import Artifact
from kleos.artifacts.storage import source_path
from kleos.contracts.models import ArtifactRef, DomainError
from kleos.execution.gateway import NativeAction, NativeJob
from kleos.orchestration.engine import RunScope
from kleos.orchestration.models import ModelAttempt
from kleos.orchestration.specialist import ToolResult
from kleos.projects.graph_repository import Effect
from kleos.projects.store import Objective
from kleos.validation.service import Candidate, ValidationCheck

ACTIVE = {'pending', 'running', 'waiting'}


def _ref(row):
    return ArtifactRef(id=row.id, path=row.name, sha256=row.sha256, revision=row.revision,
                       media_type=row.media_type, size=row.size).model_dump(mode='json')


class EngineeringService:
    def __init__(self, projects, *, clock=lambda: datetime.now(UTC)):
        self.projects, self.clock = projects, clock

    def _project(self, session, actor, project_id, revision):
        project = self.projects.authorized(session, actor, project_id, write=True)
        if project.revision != revision:
            raise DomainError('Project changed; refresh before engineering commands', 'revision_conflict')
        return project

    @staticmethod
    def _runs(session, project):
        return list(session.scalars(select(Objective).where(Objective.project_id == project.id)))

    @staticmethod
    def _latest(runs):
        return max(runs, key=lambda run: (run.data.get('createdAt', ''), run.id)) if runs else None

    @staticmethod
    def _set_pending(project, run, phase):
        state = project.data.get('state', {})
        project.data = {**project.data, 'status': 'queued',
                        'state': {**state, 'planning': {'phase': phase, 'pending': True, 'runId': run.id}}}

    def submit(self, actor, project_id, kind, expected_revision, key, *, objective=None, allowed_paths=None):
        if kind not in {'project.generate', 'project.repair'}:
            raise DomainError('Unsupported engineering operation', 'invalid_request', 422)
        if objective is not None and (not objective.strip() or len(objective) > 6000):
            raise DomainError('Provide a bounded engineering objective', 'invalid_request', 422)
        requested_paths = sorted({source_path(path) for path in (allowed_paths or [])})

        def operation(session):
            project = self._project(session, actor, project_id, expected_revision)
            pending_interfaces = project.data.get('state', {}).get('interfaceProposal', {})
            proposed = pending_interfaces.get('candidate', {}).get('interfaces', {})
            if kind == 'project.generate' and objective is None and (
                    pending_interfaces.get('status') == 'needs_input' or
                    (pending_interfaces.get('status') == 'pending' and
                     any(proposed.get(lane) is not None for lane in ('circuit', 'firmware')))):
                raise DomainError('Review or revise the pending interface proposal before generating the full requested design',
                    'approval_required')
            runs = self._runs(session, project)
            active = [run for run in runs if run.status in ACTIVE and run.revision == project.context_revision]
            if active:
                if (len(active) == 1 and kind == 'project.generate' and objective is None and not requested_paths
                        and active[0].data.get('kind') != 'project.capture'):
                    return {'projectId': project.id, 'runId': active[0].id, 'revision': project.revision}
                raise DomainError('An objective is active; finish or cancel it before starting engineering work', 'run_conflict')
            data = {'kind': kind, 'submittedBy': actor.user_id, 'createdAt': self.clock().isoformat(),
                    'tasks': [], 'modelPolicySnapshot': dict(project.data.get('modelPolicy', {})),
                    'toolPolicySnapshot': copy.deepcopy({'policy': project.data.get('state', {}).get('toolPolicy', {}),
                                                        'bindings': project.data.get('toolPolicyBindings', {})})}
            if kind == 'project.generate':
                if objective:
                    text = objective + ('\nChoose only the capabilities needed for the requested outcome; do not force a '
                                        'complete device pipeline.')
                else:
                    text = ('Generate the engineering files for the whole device described by the brief and the accepted design '
                            'context. Plan one task per deliverable the device needs, using only registered capabilities: '
                            'mechanical CAD for its enclosure and parts, electronics (PCB) for its circuit, and a companion '
                            'application when the device has a display, controls or connectivity worth controlling or '
                            'mirroring. Omit a lane only when the brief makes it unnecessary.')
                text += ' Preserve accepted design references and unchanged sources. Outputs remain candidates until independent checking and a separate acceptance command.'
                if requested_paths:
                    data['editAllowedPaths'] = requested_paths
                    text += '\nThe user-authorized source edit paths are exactly: '+json.dumps(requested_paths)
            else:
                prior = self._latest([run for run in runs if run.revision == project.context_revision
                                      and run.data.get('kind') != 'project.capture'])
                refs, paths = {}, set(project.data.get('sourceManifest', {}))
                if prior:
                    for result in prior.data.get('results', {}).values():
                        for encoded in result.get('artifacts', []):
                            reference = ArtifactRef.model_validate(encoded)
                            row = session.get(Artifact, reference.id)
                            if row is None or row.project_id != project.id or _ref(row) != reference.model_dump(mode='json'):
                                raise DomainError('Repair input provenance changed', 'artifact_conflict')
                            if row.details.get('role') != 'source':
                                continue
                            if (row.status != 'candidate' or row.details.get('origin') != 'native_candidate'
                                    or row.details.get('runId') != prior.id or row.revision != project.context_revision):
                                raise DomainError('Repair source is not a current native candidate', 'artifact_scope_conflict')
                            if row.name in refs and refs[row.name]['id'] != row.id:
                                raise DomainError('Repair source path has ambiguous candidates', 'source_closure_conflict')
                            refs[row.name] = _ref(row)
                            paths.add(row.name)
                if not paths:
                    raise DomainError('No current source files are available to repair', 'repair_inputs_missing')
                if requested_paths and not set(requested_paths).issubset(paths):
                    raise DomainError('Repair paths must be current project source paths', 'scope_conflict')
                repair_paths = requested_paths or sorted(paths)
                data.update(repairSourceRunId=prior.id if prior else None,
                            repairInputs=[refs[path] for path in sorted(refs)], repairAllowedPaths=repair_paths,
                            repairBaseline=dict(project.data.get('sourceManifest', {})))
                failures = []
                if prior:
                    for task_id, result in prior.data.get('results', {}).items():
                        checks = [{'name': check['name'], 'status': check['status']}
                                  for check in result.get('observations', []) if check.get('status') != 'PASS']
                        if result.get('status') != 'succeeded' or checks:
                            failures.append({'taskId': task_id, 'summary': result.get('summary', '')[:1000], 'checks': checks[:12]})
                repair_context = {'allowedPaths': repair_paths, 'sourceArtifacts': data['repairInputs'], 'priorFindings': failures[:8]}
                text = (objective or 'Repair the current project candidate sources using the recorded failures.')
                text += '\nRepair only the listed allowedPaths. Preserve all other source bytes, identities and geometry. Use the pinned read-only repair inputs; do not treat them as accepted sources. Select only the capabilities needed and independently check repaired outputs.\n'+json.dumps(repair_context, separators=(',', ':'))
            if len(text) > 12000:
                raise DomainError('Engineering context exceeds the objective bound; select a smaller repair scope', 'context_too_large')
            project.advance_revision()
            run = Objective(id=str(uuid4()), project_id=project.id, revision=project.context_revision,
                            status='pending', data={**data, 'objective': text})
            session.add(run)
            self._set_pending(project, run, 'engineering')
            self.projects._emit(session, project, 'objective.submitted', {'runId': run.id, 'objective': text, 'kind': kind})
            return {'projectId': project.id, 'runId': run.id, 'revision': project.revision}

        return self.projects._command(actor, key, {'type': kind, 'projectId': project_id,
            'expectedRevision': expected_revision, 'objective': objective, 'allowedPaths': requested_paths}, operation)

    def cancel(self, actor, project_id, run_id, expected_revision, key):
        def operation(session):
            project = self._project(session, actor, project_id, expected_revision)
            runs = self._runs(session, project)
            selected = [run for run in runs if run.id == run_id] if run_id else [run for run in runs
                if run.status in ACTIVE and run.revision == project.context_revision]
            if len(selected) != 1:
                raise DomainError('Select one active objective to cancel', 'run_conflict')
            run = selected[0]
            if run.status == 'succeeded':
                raise DomainError('Completed objectives cannot be cancelled', 'run_conflict')
            if run.status != 'cancelled':
                run.status = 'cancelled'
                self.projects.clear_pending(project, run.id, 'cancelled')
                self.projects._emit(session, project, 'objective.cancelled', {'runId': run.id})
            return {'projectId': project.id, 'runId': run.id, 'revision': project.revision}
        return self.projects._command(actor, key, {'type': 'project.cancel', 'projectId': project_id,
            'runId': run_id, 'expectedRevision': expected_revision}, operation)

    @staticmethod
    def _reconcile_native_claims(session, run):
        """Settle tool claims left behind when a run was fenced or its native container was lost.

        A claimed tool effect whose native action is durably completed is simply recorded from that result: the
        work happened exactly once and nothing is repeated. A claim whose container is proven stopped and whose
        action never completed is released when the action only reads or builds (patched bytes reach the blob
        store only after the container acknowledged them), so the retry re-issues it on a continuation job.
        A claim with no native action record never reached the container and is released. Cut-off model calls of
        a blocked run are settled the same way (see the end of this method). `finish` publishes
        files, so a started but unfinished one, and any started claim without proof, stays uncertain.
        """
        jobs = {}
        for job in session.scalars(select(NativeJob).where(NativeJob.run_id == run.id)):
            if job.task_id not in jobs or job.request.get('resumeAttempt', 1) > jobs[job.task_id].request.get('resumeAttempt', 1):
                jobs[job.task_id] = job
        for task_id, latest in jobs.items():
            attempts = [job for job in session.scalars(select(NativeJob).where(
                NativeJob.run_id == run.id, NativeJob.task_id == task_id))]
            for effect in session.scalars(select(Effect).where(
                    Effect.run_id == run.id, Effect.status == 'claimed', Effect.key.like(f'{task_id}:tool:%'))):
                turn = effect.key.rsplit(':', 1)[1]
                model = session.scalar(select(Effect).where(Effect.run_id == run.id, Effect.key == f'{task_id}:model:{turn}'))
                tool = (model.value or {}).get('tool') if model is not None and model.status == 'completed' else None
                if not isinstance(tool, str) or not turn.isdigit():
                    continue
                action_id = hashlib.sha256(json.dumps([run.id, task_id, int(turn), tool]).encode()).hexdigest()
                done = next((record for job in attempts if (record := session.get(NativeAction, (job.id, action_id))) is not None
                             and record.status == 'completed'), None)
                if done is not None:
                    effect.status = 'completed'
                    effect.value = ToolResult.model_validate(done.result).model_dump(mode='json')
                    continue
                if not any(session.get(NativeAction, (job.id, action_id)) is not None for job in attempts):
                    # The native action record is written before dispatch, so none means it never ran.
                    session.delete(effect)
                    continue
                proof = session.get(NativeAction, (latest.id, '_termination'))
                if (tool.endswith('.finish') or latest.status not in {'failed', 'cancelled'} or proof is None
                        or proof.status != 'completed' or proof.request_hash != latest.request_hash
                        or proof.result != {'kind': 'termination-proof', 'jobId': latest.id, 'containerStopped': True}):
                    continue
                pending = session.get(NativeAction, (latest.id, action_id))
                if pending is not None:
                    session.delete(pending)
                session.delete(effect)
                if latest.active_action == action_id:
                    latest.active_action = None
        # A blocked run has no worker, so a still-reserved model attempt is a call that was cut off: its reserved
        # cost stays committed (as a cancellation settles it) and its claim is released so the turn can be asked again.
        for attempt in session.scalars(select(ModelAttempt).where(ModelAttempt.run_id == run.id, ModelAttempt.status == 'reserved')):
            attempt.status = 'uncertain'
        if session.scalar(select(ModelAttempt.id).where(ModelAttempt.run_id == run.id, ModelAttempt.status == 'reserved').limit(1)) is None:
            for effect in session.scalars(select(Effect).where(
                    Effect.run_id == run.id, Effect.status == 'claimed',
                    or_(Effect.key.like('%:model:%'), Effect.key.like('plan:%'),
                        Effect.key.like('%:design:model'), Effect.key.like('%:interfaces:model')))):
                session.delete(effect)
        if run.data.get('blocker') == 'invalid_plan':
            # The engine rejects a plan before publishing or running any of it, so the rejected decision produced
            # nothing; keeping it journaled would replay the same invalid plan on every retry.
            latest = session.scalars(select(Effect).where(Effect.run_id == run.id, Effect.key.like('plan:%'),
                                                          Effect.status == 'completed')).all()
            if latest:
                session.delete(max(latest, key=lambda effect: int(effect.key.split(':')[1])))
        session.flush()

    def retry(self, actor, project_id, run_id, expected_revision, key):
        # Settling claims is committed on its own: it is durable progress even if the retry below is refused.
        with self.projects.transaction() as session:
            project = self._project(session, actor, project_id, expected_revision)
            runs = self._runs(session, project)
            target = next((item for item in runs if item.id == run_id), None) if run_id else self._latest(runs)
            if target is not None and target.status in {'blocked', 'failed'}:
                self._reconcile_native_claims(session, target)

        def operation(session):
            project = self._project(session, actor, project_id, expected_revision)
            runs = self._runs(session, project)
            run = next((item for item in runs if item.id == run_id), None) if run_id else self._latest(runs)
            if run is None or run.status not in {'blocked', 'failed'}:
                raise DomainError('Only an existing blocked objective can be resumed', 'run_conflict')
            if run.revision != project.context_revision:
                raise DomainError('Objective context changed; retry cannot rewrite its inputs', 'stale_context')
            if any(item.status in ACTIVE and item.revision == project.context_revision for item in runs):
                raise DomainError('Another objective is active', 'run_conflict')
            if not run.data.get('scope'):
                raise DomainError('Objective has no pinned recovery scope', 'recovery_scope_missing')
            scope = RunScope.model_validate(run.data['scope'])
            if scope.project_id != project.id or scope.run_id != run.id or scope.revision != run.revision:
                raise DomainError('Objective recovery scope does not match its identity', 'recovery_scope_missing')
            self.projects.authorized(session, scope.actor, project.id, write=True)
            if scope.deadline_at <= self.clock():
                raise DomainError('Original objective deadline expired', 'deadline_exceeded')
            uncertain = session.scalar(select(Effect.id).where(Effect.run_id == run.id, Effect.status != 'completed').limit(1))
            uncertain = uncertain or session.scalar(select(ModelAttempt.id).where(ModelAttempt.run_id == run.id, ModelAttempt.status == 'reserved').limit(1))
            uncertain = uncertain or session.scalar(select(ValidationCheck.candidate_id).join(Candidate, Candidate.id == ValidationCheck.candidate_id).where(
                Candidate.run_id == run.id, ValidationCheck.status == 'running').limit(1))
            uncertain = uncertain or session.scalar(select(NativeAction.job_id).join(NativeJob, NativeJob.id == NativeAction.job_id).where(
                NativeJob.run_id == run.id, NativeAction.status != 'completed').limit(1))
            if uncertain:
                raise DomainError('An earlier effect needs reconciliation before retry', 'effect_uncertain')
            history = list(run.data.get('retryHistory', []))
            if len(history) >= 32:
                raise DomainError('Objective retry allowance exhausted', 'budget_exhausted')
            history.append({'requestedAt': self.clock().isoformat(), 'requestedBy': actor.user_id,
                            'previousStatus': run.status, 'previousBlocker': run.data.get('blocker')})
            # Keep scope, deadline, provider/tool pins, calls, results and checkpoint identity intact.
            run.data = {**run.data, 'retryHistory': history, 'workerToken': str(uuid4())}
            run.status = 'pending'
            self._set_pending(project, run, 'engineering')
            self.projects._emit(session, project, 'objective.retry_requested', {'runId': run.id, 'retryCount': len(history)})
            return {'projectId': project.id, 'runId': run.id, 'revision': project.revision}
        return self.projects._command(actor, key, {'type': 'project.retry', 'projectId': project_id,
            'runId': run_id, 'expectedRevision': expected_revision}, operation)
