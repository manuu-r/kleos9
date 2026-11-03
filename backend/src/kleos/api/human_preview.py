"""Local human-work draft preview; no provider posting, quoting, messaging or funding."""
from copy import deepcopy

from sqlalchemy import select

from kleos.api.external import DraftAction, HumanTask, draft_view, iso
from kleos.capabilities.external import ExternalJob, ExternalRequest, bind_artifacts, bounded_json, job_view
from kleos.contracts.models import DomainError
from kleos.projects.store import fingerprint

ACTIONS = {'human_draft_preview'}
QUERIES = {'human_task_drafts'}
RECORD_KEY = 'humanLocalPreview'


class HumanPreviewFacade:
    """Separate intercepts preserve source-bound existing provider qualification files."""
    def __init__(self, external):
        self.external, self.jobs, self.projects, self.access = external, external.jobs, external.projects, external.access

    def _snapshot(self, session, actor, project_id, ident, *, write=False):
        membership = self.access._member(session, actor)
        if write and membership.role == 'viewer':
            raise DomainError('Organization is read-only', 'forbidden', 403)
        # Mutations follow the existing external service's job-then-project lock order.
        query = select(ExternalJob).where(ExternalJob.id == ident, ExternalJob.tenant_id == actor.tenant_id)
        job = session.scalar(query.with_for_update() if write else query)
        if job is None or job.project_id != project_id or job.kind != 'human_work' or job.presentation.get('type') != 'human_draft':
            raise DomainError('Human draft not found in this project', 'not_found', 404)
        project = self.projects.authorized(session, actor, project_id, write=write)
        if not write:
            # A job mutation may have committed between the initial read and project
            # lock. Reload without taking its lock (mutations lock job then project).
            session.refresh(job)
        if write and job.creator_id != actor.user_id:
            raise DomainError('Preview this draft using its creator account', 'forbidden', 403)
        if (job.presentation.get('_contextRevision', job.revision) != project.context_revision
                or job.presentation.get('engineeringRevision') != project.data.get('engineeringRevision', 1)):
            raise DomainError('Human draft context changed; prepare a current draft', 'stale_context')
        release = job.presentation.get('releaseId')
        if release and release != project.data.get('acceptedReleaseId'):
            raise DomainError('Human draft release is no longer current', 'release_scope')
        if job.status not in {'draft', 'unavailable'} or job.external_id or job.effect_state != 'none':
            raise DomainError('Only an unsubmitted local draft can be previewed', 'job_closed')
        if self.jobs.clock() >= job.deadline_at:
            raise DomainError('Human draft deadline expired', 'job_closed')
        task = HumanTask.model_validate(job.presentation['task'])
        source = {key: job.presentation[key] for key in ('type', 'engineeringRevision', 'releaseId', 'task')}
        if fingerprint(source) != job.presentation.get('draftSha256'):
            raise DomainError('Saved human draft failed integrity verification', 'draft_conflict')
        request = ExternalRequest.model_validate(job.request)
        if (request.kind != 'human_work' or request.project_id != project.id or request.provider != job.provider
                or fingerprint(request.model_dump(mode='json', by_alias=True)) != job.request_hash):
            raise DomainError('Saved human work scope failed integrity verification', 'draft_conflict')
        if set(project.data.get('sourceManifest', {}).values()) != {item.artifact_id for item in request.artifact_bindings}:
            raise DomainError('Human draft source closure changed', 'stale_context')
        bind_artifacts(session, project.id, request.artifact_bindings)
        self.jobs._assert_installation(job, actor)
        provider = self.jobs.providers.get((actor.tenant_id, job.creator_id, job.provider))
        if provider is None:
            if job.provider_version != 'unconfigured':
                raise DomainError('Human draft provider installation changed', 'provider_changed')
        elif (provider.id != job.provider or provider.version != job.provider_version
                or provider.kind != 'human_work' or provider.effect_capable is not False):
            raise DomainError('Human draft provider installation changed', 'provider_changed')
        binding = {'draftSha256': job.presentation['draftSha256'], 'requestHash': job.request_hash,
            'contextRevision': project.context_revision, 'sourceManifestHash': fingerprint(project.data.get('sourceManifest', {})),
            'installationHash': job.presentation.get('_installationHash'), 'providerVersion': job.provider_version,
            'creatorId': job.creator_id}
        return job, project, task, provider, binding

    @staticmethod
    def _view(job, record=None):
        result = draft_view(job_view(job))
        if record:
            result.update(status='local_preview', preview=deepcopy(record['preview']))
        return result

    @staticmethod
    def _record(job, binding):
        record = job.preparations.get(RECORD_KEY)
        if (not isinstance(record, dict) or record.get('binding') != binding
                or not isinstance(record.get('preview'), dict)
                or fingerprint(record['preview']) != record.get('previewSha256')):
            return None
        return record

    async def action(self, actor, operation, params, body, idempotency_key=None):
        if operation not in ACTIONS:
            raise DomainError('Human preview operation is unavailable', 'operation_unavailable', 501)
        parsed = DraftAction.model_validate(body)
        project_id, ident = params.get('projectId'), params.get('ident')
        # Receipt replay also requires current write authority, source and account identity.
        with self.projects.transaction() as session:
            job, _, _, _, _ = self._snapshot(session, actor, project_id, ident, write=True)
            if parsed.draft_sha256 != job.presentation['draftSha256']:
                raise DomainError('Saved task changed; review its current contents', 'draft_conflict')
        key = idempotency_key or 'human-local-preview-'+fingerprint([ident, parsed.draft_sha256])
        def operation_in_transaction(session):
            job, project, task, provider, binding = self._snapshot(session, actor, project_id, ident, write=True)
            if parsed.draft_sha256 != binding['draftSha256']:
                raise DomainError('Saved task changed; review its current contents', 'draft_conflict')
            record = self._record(job, binding)
            if record is None:
                preview = bounded_json({'mode': 'local', 'provider': job.provider, 'providerVersion': job.provider_version,
                    'providerConfigured': provider is not None, 'providerContacted': False,
                    'draftSha256': binding['draftSha256'], 'workerBudgetUsd': task.price, 'currency': 'USD',
                    'feeQuoteAvailable': False, 'submitted': False, 'funded': False, 'physicalAcceptance': False,
                    'previewedAt': iso(self.jobs.clock()), 'limitations': [
                        'This is a local review of the saved task. No task text or files were sent to the provider.',
                        'Worker budget is user supplied; provider fees, availability and total funding are unknown.',
                        'No job was posted, no person was contacted or hired, and no payment or physical acceptance occurred.']})
                record = {'binding': binding, 'preview': preview, 'previewSha256': fingerprint(preview)}
                job.preparations = {**job.preparations, RECORD_KEY: record}
                self.projects._emit(session, project, 'human.draft.local_previewed',
                    {'jobId': job.id, 'draftSha256': binding['draftSha256'], 'providerContacted': False})
            return {'projectId': project.id, 'draftId': job.id, 'previewSha256': record['previewSha256']}
        self.projects._command(actor, key, {'operation': 'human.localPreview', 'projectId': project_id,
            'draftId': ident, 'draftSha256': parsed.draft_sha256}, operation_in_transaction)
        with self.projects.transaction() as session:
            job, _, _, _, binding = self._snapshot(session, actor, project_id, ident, write=True)
            record = self._record(job, binding)
            if record is None:
                raise DomainError('Local preview changed before it could be returned', 'draft_conflict')
            return self._view(job, record)

    async def query(self, actor, operation, params):
        if operation not in QUERIES:
            raise DomainError('Human preview query is unavailable', 'operation_unavailable', 501)
        with self.projects.transaction() as session:
            self.access._member(session, actor)
            self.projects.authorized(session, actor, params.get('projectId'))
        result = await self.external.query(actor, operation, params)
        drafts = []
        for view in result['drafts']:
            try:
                with self.projects.transaction() as session:
                    job, _, _, _, binding = self._snapshot(session, actor, params.get('projectId'), view['id'])
                    drafts.append(self._view(job, self._record(job, binding)))
            except DomainError as exc:
                if exc.code in {'forbidden', 'not_found'}:
                    raise
                # Historical/cancelled drafts remain readable, but stale previews cannot look current.
                drafts.append(view)
        return {'drafts': drafts}
