"""Independent check tool. Acceptance is deliberately absent from the agent registry."""
import asyncio
from pathlib import PurePosixPath

from pydantic import Field

from kleos.artifacts.service import Artifact
from kleos.capabilities.registry import ToolManifest
from kleos.contracts.models import Capability, DomainError, Observation, WireModel
from kleos.orchestration.specialist import ToolResult
from kleos.validation.policies import check_policy_artifacts, select_policy
from kleos.validation.service import CheckerAdmissionDeferred

CAD_POLICY = 'cad.export-integrity'


class ValidationInput(WireModel):
    artifact_ids: list[str] = Field(min_length=1, max_length=256)
    request_key: str = Field(min_length=1, max_length=100)


def check_cad_artifacts(repository, scope, artifact_ids):
    """The narrow policy cannot qualify other lanes or unchecked ancillary outputs."""
    with repository.store.transaction() as session:
        project, _ = repository._check(session, scope)
        for artifact_id in artifact_ids:
            artifact = session.get(Artifact, artifact_id)
            if artifact is None or artifact.project_id != project.id or artifact.lane != 'cad':
                raise DomainError('This independent policy checks CAD candidates only', 'checker_scope')
            if artifact.details.get('role') == 'output' and PurePosixPath(artifact.name).suffix.lower() not in {'.step', '.stp', '.stl'}:
                raise DomainError('This policy checks paired STEP/STL exports only', 'checker_scope')


def register_validation_tool(capabilities, service, repository, installation_hash):
    async def authorize(scope, task, arguments):
        inputs = ValidationInput.model_validate(arguments)
        ids = inputs.artifact_ids
        if (task.capability != Capability.VALIDATION or len(ids) != len(set(ids))
                or not set(ids).issubset(set(task.input_artifact_ids))):
            raise DomainError('Validation inputs must be exact assigned task artifacts', 'scope_conflict')
        await repository.resolve_artifacts(scope, ids)
        with repository.store.transaction() as session:
            repository._check(session, scope)
            policy = select_policy(service.registry, [session.get(Artifact, ident) for ident in ids])
        if policy == CAD_POLICY:
            check_cad_artifacts(repository, scope, ids)
        check_policy_artifacts(repository, scope, ids, policy)
        return policy

    async def invoke(scope, task, arguments):
        policy = await authorize(scope, task, arguments)
        inputs = ValidationInput.model_validate(arguments)
        # A request identity is local to the immutable task, preventing another task's replay collision.
        import hashlib
        key = hashlib.sha256((task.id+'\0'+inputs.request_key).encode()).hexdigest()
        candidate = service.prepare(scope, inputs.artifact_ids, policy, key)
        while True:
            try:
                evidence = await service.run(scope, candidate)
                break
            except CheckerAdmissionDeferred:
                # Pre-dispatch contention keeps the existing tool effect and scope; no
                # planner/model retry and no independent result has been produced yet.
                await repository.assert_active(scope)
                await asyncio.sleep(.2)
        observations = [Observation(name=row['checkerKey'], status=row['status'], independent=True,
                                    artifact_ids=inputs.artifact_ids,
                                    details={'candidateId': candidate, 'subjectHash': row['subjectHash'],
                                             'policy': policy, 'report': row.get('report'),
                                             'scope': 'Named digital export checks only; no physical acceptance'})
                        for row in evidence]
        unknown = [criterion for criterion in task.acceptance_criteria
                   if criterion not in ({item['checker_key'] for item in service.registry.snapshot(policy)['policy']['required_checks']}
                                        | ({'cad.numeric-constraints'} if policy in {CAD_POLICY, 'digital.cad-pcb'} else set()))]
        if unknown:
            observations.append(Observation(name='unsupported.task-criteria', status='UNKNOWN', independent=True,
                                            details={'criteria': unknown, 'reason': 'These statements are outside the qualified numeric/export policy'}))
        blockers = [item.name+':'+item.status.value for item in observations if item.status.value != 'PASS']
        refs = await repository.resolve_artifacts(scope, inputs.artifact_ids)
        return ToolResult(summary=f'Independent digital check recorded for candidate {candidate}; acceptance requires a separate authorized command.',
                          artifacts=refs, observations=observations, blockers=blockers)

    manifest = ToolManifest(id='validation.check', version='1.0.0', capability=Capability.VALIDATION,
                            operation='check', provider='digital-export-checker', provider_version='1',
                            installation_hash=installation_hash, transport='native', effect='read', qualified=True,
                            description='Independently inspect assigned covered digital candidates and record evidence; cannot accept a release.',
                            instructions='Select changed sources plus covered outputs from task inputArtifactIds. '
                            'CAD requires paired STEP/STL; PCB requires outputs/routed.kicad_pcb with schematic/board sources. '
                            'Do not include SVG, generated rule reports, Gerbers or ZIPs. Use stable requestKey for replay. '
                            'Every selected lane needs its installed independent checker. Unsupported criteria stay UNKNOWN; '
                            'no electrical function, manufacturability or physical approval.')
    capabilities.register(manifest, ValidationInput, invoke, authorize)
    return manifest.key
