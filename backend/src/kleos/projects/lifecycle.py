"""User-visible deletion fences work while retaining source and audit history."""
from datetime import UTC, datetime

from sqlalchemy import select

from kleos.capabilities.external import ExternalJob
from kleos.capabilities.fabrication.slicing import PrintPreparation
from kleos.contracts.models import DomainError
from kleos.projects.graph_repository import execution_owner
from kleos.projects.store import Membership, Objective, Project
from kleos.projects.verification import VerificationJob


def _owner(projects, session, actor, project_id, *, lock=True):
    query = select(Project).where(Project.id == project_id, Project.tenant_id == actor.tenant_id)
    project = session.scalar((query.with_for_update() if lock else query).execution_options(populate_existing=True))
    membership = session.get(Membership, (project_id, actor.user_id), populate_existing=True) if project else None
    if project is None or membership is None or project.data.get('internalPurpose') is not None:
        raise DomainError('Project not found', 'not_found', 404)
    if membership.role != 'owner':
        raise DomainError('Only a project owner may delete this project', 'forbidden', 403)
    if lock:
        projects.authorize_account(session, actor, write=True)
    return project


def delete_project(projects, actor, params, body, key=None):
    if execution_owner.get() is not None:
        raise DomainError('Project deletion requires a user command', 'approval_required')
    revision = params.get('expectedRevision')
    if isinstance(revision, str) and revision.isascii() and revision.isdecimal() and len(revision) <= 12:
        revision = int(revision)
    if type(revision) is not int or revision < 1 or body:
        raise DomainError('Provide the current expectedRevision to delete a project', 'invalid_request', 422)
    project_id = params.get('projectId')
    if not isinstance(project_id, str) or not 1 <= len(project_id) <= 36:
        raise DomainError('Choose a project', 'invalid_request', 422)
    def operation(session):
        # External observation locks job -> project. Take existing external rows
        # in that order too; never wait for an external row while holding Project.
        _owner(projects, session, actor, project_id, lock=False)
        locked_jobs = set(session.scalars(select(ExternalJob.id).where(
            ExternalJob.project_id == project_id).order_by(ExternalJob.id).with_for_update()))
        project = _owner(projects, session, actor, project_id)
        current_jobs = set(session.scalars(select(ExternalJob.id).where(ExternalJob.project_id == project_id)))
        if current_jobs != locked_jobs:
            # A job created between the two locks could itself be waiting for
            # Project. Roll back instead of taking its lock in the reverse order.
            raise DomainError('Project jobs changed; refresh before deletion', 'revision_conflict')
        deletion = project.data.get('deletion')
        if deletion is not None:
            if deletion['expectedRevision'] != revision:
                raise DomainError('Project changed before deletion', 'revision_conflict')
            return deletion['result']
        if project.revision != revision:
            raise DomainError('Project changed before deletion', 'revision_conflict')
        cancelled = {'objectives': 0, 'verifications': 0, 'preparations': 0, 'externalJobs': 0}
        for run in session.scalars(select(Objective).where(Objective.project_id == project_id,
                Objective.status.in_(['pending', 'running', 'waiting']))):
            run.status = 'cancelled'
            projects.clear_pending(project, run.id, 'cancelled')
            cancelled['objectives'] += 1
            projects._emit(session, project, 'objective.cancelled', {'runId': run.id, 'reason': 'project_deleted'})
        for job in session.scalars(select(VerificationJob).where(VerificationJob.project_id == project_id,
                VerificationJob.status.in_(['queued', 'running']))):
            job.status = 'cancelled'
            cancelled['verifications'] += 1
        for job in session.scalars(select(PrintPreparation).where(PrintPreparation.project_id == project_id,
                PrintPreparation.status.in_(['queued', 'running']))):
            job.status, job.generation = 'cancelled', job.generation + 1
            cancelled['preparations'] += 1
        for job in session.scalars(select(ExternalJob).where(ExternalJob.project_id == project_id,
                ExternalJob.status.not_in(['completed', 'failed', 'cancelled', 'expired']))):
            job.status, job.generation = 'cancelled', job.generation + 1
            cancelled['externalJobs'] += 1
        project.advance_revision()
        result = {'id': project.id, 'revision': project.revision, 'deleted': True,
            'dataRetained': True, 'externalCancellationConfirmed': False}
        project.data = {**project.data, 'status': 'deleted', 'deletion': {
            'expectedRevision': revision, 'deletedBy': actor.user_id,
            'deletedAt': datetime.now(UTC).isoformat(), 'result': result}}
        projects._emit(session, project, 'project.deleted', {**result, 'cancelled': cancelled,
            'computeTerminationPending': True})
        # Deliberately use id, not projectId: generic receipt replay authorizes
        # live projects only. This entry point supplies its own owner replay gate.
        return result

    return projects._command(actor, key or f'delete-project:{project_id}:{revision}',
        {'operation': 'project.delete', 'projectId': project_id, 'expectedRevision': revision}, operation,
        replay_authorizer=lambda session, result: _owner(projects, session, actor, project_id))
