"""Agent tools for durable read-only sourcing/human jobs, never purchase or hire."""
import json

from pydantic import Field
from sqlalchemy import select

from kleos.capabilities.external import ArtifactBinding, BOMItem, ExternalRequest, WorkOrder
from kleos.capabilities.registry import CapabilityRegistry, ToolManifest
from kleos.contracts.models import Capability, DomainError, Observation, WireModel
from kleos.orchestration.specialist import ToolResult
from kleos.projects.store import Receipt, fingerprint


class JobAction(WireModel):
    action_id: str = Field(min_length=1, max_length=100)
    job_id: str = Field(min_length=1, max_length=36)


class CreateSourcing(WireModel):
    action_id: str = Field(min_length=1, max_length=100)
    bom: list[BOMItem] = Field(min_length=1, max_length=20)


class CreateHumanWork(WireModel):
    action_id: str = Field(min_length=1, max_length=100)
    work_order: WorkOrder


class ExternalJobTools:
    def __init__(self, jobs, repository, capability, provider, provider_version):
        self.jobs, self.repository = jobs, repository
        self.capability, self.provider, self.provider_version = capability, provider, provider_version

    def _provider(self, actor, run):
        provider = self.jobs.providers.get((actor.tenant_id, actor.user_id, self.provider))
        if provider is None:
            raise DomainError('No external provider is installed for this account', 'provider_unavailable', 503)
        if (provider.id != self.provider or provider.version != self.provider_version
                or provider.kind != self.capability.value or provider.effect_capable is not False):
            raise DomainError('Pinned read-only external installation changed', 'provider_changed')
        current = self.jobs.installation_pin(actor, self.provider)
        if current is not None:
            pins = run.data.get('runtimePins', {}).get('external', [])
            matching = [pin for pin in pins if pin.get('provider') == self.provider]
            if len(matching) != 1 or matching[0].get('installationHash') != current:
                raise DomainError('Run external account installation changed or is not pinned', 'installation_conflict')
        return provider

    def _job(self, session, scope, task, job_id):
        job, project = self.jobs._load(session, scope.actor, job_id, write=True, current=True)
        if (job.project_id != scope.project_id or job.creator_id != scope.actor.user_id
                or job.kind != self.capability.value or job.provider != self.provider
                or job.provider_version != self.provider_version):
            raise DomainError('External job is outside this task/account scope', 'artifact_scope_conflict', 403)
        bindings = ExternalRequest.model_validate(job.request).artifact_bindings
        if not {binding.artifact_id for binding in bindings}.issubset(task.input_artifact_ids):
            raise DomainError('External job inputs are outside this task scope', 'artifact_scope_conflict', 403)
        return job, project

    async def authorize(self, scope, task, operation, arguments):
        if len(task.input_artifact_ids) > 100:
            raise DomainError('External jobs support at most 100 scoped artifacts', 'artifact_limit', 422)
        if task.capability != self.capability:
            raise DomainError('External capability differs from task', 'tool_unavailable')
        with self.jobs.projects.transaction() as session:
            _, run = self.repository._check(session, scope)
            self._provider(scope.actor, run)
            if operation != 'create':
                self._job(session, scope, task, arguments['jobId'])
        # Resolve authoritative hashes, not IDs or hashes supplied by a model.
        references = await self.repository.resolve_artifacts(scope, task.input_artifact_ids)
        if operation != 'create':
            expected = {reference.id: (reference.sha256, reference.revision) for reference in references}
            with self.jobs.projects.transaction() as session:
                job, _ = self._job(session, scope, task, arguments['jobId'])
                for binding in ExternalRequest.model_validate(job.request).artifact_bindings:
                    if expected.get(binding.artifact_id) != (binding.sha256, binding.revision):
                        raise DomainError('External job input binding changed', 'stale_context')

    async def _create(self, scope, task, arguments):
        key = 'external-tool-' + fingerprint([scope.run_id, task.id, arguments['actionId'], self.provider])
        input_hash = fingerprint({'arguments': arguments, 'objective': task.objective,
                                  'inputArtifactIds': task.input_artifact_ids})
        with self.jobs.projects.transaction() as session:
            project, _ = self.repository._check(session, scope)
            receipt = session.scalar(select(Receipt).where(Receipt.tenant_id == scope.actor.tenant_id,
                Receipt.user_id == scope.actor.user_id, Receipt.key == key))
            if receipt is not None:
                job_id = receipt.result.get('job', {}).get('id')
                if not job_id:
                    raise DomainError('Action identity was already used', 'idempotency_conflict')
                job, _ = self._job(session, scope, task, job_id)
                if (job.presentation.get('_toolInputHash') != input_hash
                        or job.presentation.get('_runId') != scope.run_id
                        or job.presentation.get('_taskId') != task.id):
                    raise DomainError('Action identity was reused for different work', 'idempotency_conflict')
                return job_id
            revision = project.revision
        references = await self.repository.resolve_artifacts(scope, task.input_artifact_ids)
        request = ExternalRequest(project_id=scope.project_id, revision=revision, kind=self.capability.value,
            provider=self.provider, objective=task.objective,
            artifact_bindings=[ArtifactBinding(artifact_id=ref.id, sha256=ref.sha256, revision=ref.revision) for ref in references],
            bom=arguments.get('bom', []), work_order=arguments.get('workOrder'), ceiling_minor_units=0, currency='USD')
        receipt = self.jobs.create(scope.actor, request, key, presentation={
            '_toolInputHash': input_hash, '_runId': scope.run_id, '_taskId': task.id})
        return receipt['job']['id']

    async def invoke(self, scope, task, operation, arguments):
        await self.authorize(scope, task, operation, arguments)
        blockers, status = [], 'PASS'
        if operation == 'create':
            job_id = await self._create(scope, task, arguments)
            details = {'jobId': job_id, 'status': self.jobs.get(scope.actor, job_id)['status'], 'scopePersisted': True}
            summary = 'Saved a scoped local external-work draft. No order, hire or payment was made.'
        elif operation in {'discover', 'quote', 'draft'}:
            job_id = arguments['jobId']
            result = await self.jobs.prepare(scope.actor, job_id, operation)
            details = {'jobId': job_id, 'operation': operation, 'result': result['result']}
            summary = {'discover': 'Recorded a read-only provider lookup.',
                       'quote': 'Recorded nonbinding catalog pricing; availability and price can change.',
                       'draft': 'Saved the local scoped draft; it has not been submitted to the provider.'}[operation]
        elif operation == 'observe':
            job_id = arguments['jobId']
            job = self.jobs.get(scope.actor, job_id)
            if job['effectState'] != 'none':
                job = await self.jobs.poll(scope.actor, job_id)
            details = {'jobId': job_id, 'status': job['status'], 'externalId': job['externalId'],
                       'observedAt': job['observedAt'], 'nextPollAt': job['nextPollAt'],
                       'observation': job['observation'], 'providerClaimOnly': True}
            if job['status'] == 'failed':
                status, blockers = 'FAIL', ['External work failed; observing the failure is not successful work']
            elif job['status'] in {'cancelled', 'expired'}:
                status, blockers = 'UNKNOWN', ['External work closed without successful completion']
            elif job['status'] != 'completed':
                status = 'UNKNOWN'
                blockers = ['External work is waiting or has no verified provider observation']
            summary = 'Read the durable external-work status. Provider claims do not establish physical acceptance.'
        else:
            raise DomainError('External action is not registered', 'physical_policy', 403)
        await self.repository.assert_active(scope)
        details.update(committed=False, physicalAcceptance=False, credentialScope='current_actor', kind=self.capability.value)
        if len(json.dumps(details, allow_nan=False)) > 32000:
            details = {'jobId': job_id, 'resultStored': True, 'resultOmitted': 'Saved response exceeds task context bound',
                       'committed': False, 'physicalAcceptance': False}
            status, blockers = 'UNKNOWN', ['Inspect the stored job response; it exceeds this tool context bound']
        return ToolResult(summary=summary, observations=[Observation(name=f'external.{self.capability.value}.{operation}',
                          status=status, details=details, independent=False)], blockers=blockers)


def register_external_jobs(registry: CapabilityRegistry, jobs, repository, *, capability: Capability,
                           provider: str, provider_version: str, installation_hash: str,
                           qualified: bool, operations=None):
    """Register server-installed scoped provider accounts; credentials are never tool inputs.

    jobs.providers keys are (tenant_id,user_id,provider_id). This function does not
    create providers, infer credentials, approve work, link arbitrary provider jobs,
    or register purchase/hire/submit/cancel operations.
    """
    if capability not in {Capability.SOURCING, Capability.HUMAN_WORK}:
        raise ValueError('External job tools support sourcing and human work only')
    allowed = {'create', 'discover', 'draft', 'observe'} | ({'quote'} if capability == Capability.SOURCING else set())
    selected = tuple(operations) if operations is not None else tuple(sorted(allowed))
    if not selected or len(set(selected)) != len(selected) or any(operation not in allowed for operation in selected):
        raise ValueError('Only read-only external job operations can be registered')
    matching = []
    for principal, adapter in jobs.providers.items():
        if (not isinstance(principal, tuple) or len(principal) != 3
                or any(not isinstance(value, str) or not value for value in principal)):
            raise ValueError('External providers require explicit tenant/user/provider bindings')
        if principal[2] == provider:
            if (adapter.id != provider or adapter.kind != capability.value or adapter.version != provider_version
                    or adapter.effect_capable is not False):
                raise DomainError('Provider is not the pinned read-only installation', 'provider_changed')
            matching.append(principal)
    if not matching:
        raise DomainError('No scoped external provider account is installed', 'provider_unavailable', 503)
    gateway = ExternalJobTools(jobs, repository, capability, provider, provider_version)
    keys = []
    for operation in selected:
        schema = (CreateSourcing if capability == Capability.SOURCING else CreateHumanWork) if operation == 'create' else JobAction
        manifest = ToolManifest(id=f'{capability.value}.{operation}', version='1.0.0', capability=capability,
            operation=operation, provider=provider, provider_version=provider_version, installation_hash=installation_hash,
            transport='internal' if operation in {'create', 'draft'} else 'http',
            effect='artifact_write' if operation in {'create', 'draft'} else 'read', qualified=qualified,
            description=f'{operation.capitalize()} a scoped {capability.value} job using the installed read-only {provider} adapter.',
            instructions='Create saves a local draft and returns jobId. Use that jobId for lookups or local draft preparation. '
                'The host supplies actionId. BOM references must use exact supplier SKUs. The server binds task inputs and account. '
                'Drafts and catalog prices are not orders, reservations, hires, payments or independent evidence. '
                'Observe reports actual saved/provider state and explicit waits. No physical actions are available.')
        async def authorize(scope, task, arguments, op=operation):
            await gateway.authorize(scope, task, op, arguments)
        async def invoke(scope, task, arguments, op=operation):
            return await gateway.invoke(scope, task, op, arguments)
        registry.register(manifest, schema, invoke, authorize)
        keys.append(manifest.key)
    return keys
