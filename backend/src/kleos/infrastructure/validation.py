"""Explicit deployment wiring for independently qualified digital export policies."""
from dataclasses import dataclass
from pathlib import Path

from pydantic import Field

from kleos.artifacts.service import Artifact
from kleos.capabilities.validation import register_validation_tool
from kleos.contracts.models import DomainError, WireModel
from kleos.execution.docker import DockerClient
from kleos.projects.store import fingerprint
from kleos.projects.verification import VerificationRepository
from kleos.validation.cad import CadChecker, CadRequirements, IsolatedCadProcess, install_cad_checker
from kleos.validation.export_process import IsolatedExportProcess
from kleos.validation.exports import ApplicationChecker, FirmwareChecker, install_export_checker
from kleos.validation.pcb import PcbChecker, install_pcb_checker
from kleos.validation.pcb_process import IsolatedPcbProcess
from kleos.validation.policies import register_digital_policies
from kleos.validation.service import CheckerRegistry, ValidationService


class CadCheckerInstallation(WireModel):
    image_digest: str = Field(pattern=r'^sha256:[a-f0-9]{64}$')
    qualification_reference: str = Field(min_length=1, max_length=1000)
    daemon_control_root: str | None = None


@dataclass(frozen=True)
class ValidationWiring:
    service: ValidationService
    registry: CheckerRegistry
    tool_keys: tuple[str, ...]
    docker: DockerClient | None


def configure_validation(settings, projects, artifacts, capabilities, *, docker=None, capacity=None):
    """No install means no claimed capability; returned Docker lifetime belongs to runtime."""
    registry = CheckerRegistry()
    repository = VerificationRepository(projects)
    installations = {lane: getattr(settings, lane+'_checker_installation', None)
                     for lane in ('cad', 'pcb', 'firmware', 'application')}
    if not any(installations.values()):
        return ValidationWiring(ValidationService(projects, registry, repository=repository), registry, (), docker)
    if settings.environment not in {'development', 'test'}:
        raise DomainError('Independent checkers are qualified only for local deployment', 'checker_unqualified')
    processes, configs = [], []
    if installations['cad']:
        configured = CadCheckerInstallation.model_validate(installations['cad'])

        async def requirements_loader(scope, subject):
            with projects.transaction() as session:
                project, _ = repository._check(session, scope)
                if subject.project_id != project.id or subject.revision != project.context_revision:
                    raise DomainError('Independent check subject changed', 'stale_context')
                raw = project.data.get('interfaces', {}).get('validation', {}).get('cad')
                return CadRequirements() if raw is None else CadRequirements.model_validate(raw)

        process = IsolatedCadProcess(docker, configured.image_digest, deployment='local', capacity=capacity)
        cad_adapter = CadChecker(repository, artifacts.blobs, process, Path(settings.checker_control_root),
                             requirements_loader, daemon_root=configured.daemon_control_root)
        # Keep the qualified CAD adapter/image unchanged while admitting mixed subjects.
        async def checked_cad(scope, subject):
            with projects.transaction() as session:
                repository._check(session, scope)
                outputs = []
                for ref in subject.outputs:
                    row = session.get(Artifact, ref.id)
                    if row is None or row.project_id != scope.project_id:
                        raise DomainError('Checker artifact is unavailable', 'checker_scope')
                    if row.lane == 'cad':
                        if Path(ref.path).suffix.lower() not in {'.step', '.stp', '.stl'}:
                            raise DomainError('CAD policy checks STEP/STL only', 'checker_scope')
                        outputs.append(ref)
                if not outputs:
                    raise DomainError('CAD policy requires CAD outputs', 'checker_scope')
            return await cad_adapter.invoke(scope, subject.model_copy(update={'outputs': outputs}))

        temporary = CheckerRegistry()
        config = install_cad_checker(temporary, cad_adapter, configured.qualification_reference)
        registry.register(config, checked_cad)
        configs.append(config)
        processes.append(process)
    if installations['pcb']:
        configured = CadCheckerInstallation.model_validate(installations['pcb'])
        process = IsolatedPcbProcess(docker, configured.image_digest, deployment='local', capacity=capacity)
        pcb_adapter = PcbChecker(repository, artifacts.blobs, process, Path(settings.checker_control_root),
                                 daemon_root=configured.daemon_control_root)
        configs.append(install_pcb_checker(registry, pcb_adapter, configured.qualification_reference))
        processes.append(process)
    for lane, adapter_type in (('firmware', FirmwareChecker), ('application', ApplicationChecker)):
        if installations[lane]:
            configured = CadCheckerInstallation.model_validate(installations[lane])
            process = IsolatedExportProcess(docker, configured.image_digest, lane=lane,
                                            deployment='local', capacity=capacity)
            adapter = adapter_type(repository, artifacts.blobs, process, Path(settings.checker_control_root),
                                   daemon_root=configured.daemon_control_root)
            configs.append(install_export_checker(registry, adapter, configured.qualification_reference))
            processes.append(process)
    register_digital_policies(registry)
    service = ValidationService(projects, registry, repository=repository)
    installation_hash = configs[0].installation_hash if len(configs) == 1 else fingerprint(
        [config.model_dump(mode='json') for config in configs])
    key = register_validation_tool(capabilities, service, repository, installation_hash)
    # Open the shared client only after every installation report has passed.
    docker = docker or DockerClient(socket=settings.docker_socket)
    for process in processes:
        process.docker = docker
    return ValidationWiring(service, registry, (key,), docker)
