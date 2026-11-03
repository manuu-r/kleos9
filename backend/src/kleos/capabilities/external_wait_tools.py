"""Optional host-owned waiting tools, separate from qualified provider HTTP adapters."""
from pydantic import Field

from kleos.capabilities.registry import ToolManifest
from kleos.contracts.models import Capability, DomainError, Observation, WireModel
from kleos.orchestration.specialist import ToolResult
from kleos.projects.store import fingerprint


class WaitRequest(WireModel):
    action_id: str = Field(min_length=1, max_length=100)
    job_id: str = Field(min_length=1, max_length=36)


def wait_installation_hash(provider, version):
    from kleos.orchestration.external_waits import implementation_hash
    return fingerprint({'provider': provider, 'version': version, 'implementationHash': implementation_hash()})


def register_external_wait(registry, bridge, repository, *, kind, provider, provider_version, qualified):
    capability = Capability.FABRICATION if kind == 'print_preparation' else Capability(kind)
    if kind not in {'sourcing', 'human_work', 'print_preparation'}:
        raise ValueError('Unsupported external wait capability')
    async def authorize(scope, task, arguments):
        if task.capability != capability:
            raise DomainError('Wait tool differs from assigned capability', 'wait_scope')
        await repository.resolve_artifacts(scope, task.input_artifact_ids)
        with bridge.projects.transaction() as session:
            project, run = repository._check(session, scope)
            bridge._task(run, task)
            binding, _, _, _ = bridge._source(session, scope.actor, project, run, task, kind, arguments['jobId'])
            if kind != 'print_preparation' and (binding['provider'] != provider or binding['version'] != provider_version):
                raise DomainError('Wait tool provider differs from the bound job', 'provider_changed')
    async def invoke(scope, task, arguments):
        await authorize(scope, task, arguments)
        ident = await bridge.bind(scope, task, kind, arguments['jobId'])
        return ToolResult(summary='Waiting for an existing scoped job; no order, hire or print action was started.',
            wait_ids=[ident], observations=[Observation(name='external.wait.bound', status='UNKNOWN', independent=False,
                details={'waitId': ident, 'jobId': arguments['jobId'], 'physicalAcceptance': False})])
    manifest = ToolManifest(id=f'{capability.value}.wait.{provider}', version='1.0.0', capability=capability,
        operation='wait', provider=provider, provider_version=provider_version,
        installation_hash=wait_installation_hash(provider, provider_version), transport='internal', effect='artifact_write',
        qualified=qualified, description='Pause this task until an already-linked external job or queued print preparation has a terminal observation.',
        instructions='Supply an existing jobId within this project and assigned source inputs. The host supplies actionId. '
        'This tool pauses the graph without repeated model calls. It cannot order, hire, send messages or start printing. '
        'Provider completion remains a claim for subsequent independent review, never physical acceptance.')
    registry.register(manifest, WaitRequest, invoke, authorize)
    return manifest.key
