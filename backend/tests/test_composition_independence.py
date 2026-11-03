"""Retained unaccepted sources cannot supply their own independent evidence."""
import pytest
from sqlalchemy import select
from test_composition import composed as composed
from test_composition_results import compile_targets
from test_validation_policies import registry

from kleos.artifacts.service import Artifact
from kleos.contracts.models import DomainError, TaskResult, TaskSpec
from kleos.projects.composition_results import CompositionResults
from kleos.projects.engineering import EngineeringService
from kleos.projects.store import Objective, Project
from kleos.projects.verification import current_candidates
from kleos.validation.service import Candidate, CheckReport, Release, ValidationCheck, ValidationService


@pytest.mark.parametrize('composed', [['base', 'lid']], indirect=True)
@pytest.mark.parametrize('include_retained_ids', [True, False])
async def test_retained_source_generator_cannot_approve_compiled_repair(composed, include_retained_ids):
    service, original, _, baseline, refs, _ = composed
    store = service.repository.store
    with store.transaction() as session:
        session.get(Objective, original.run_id).status = 'succeeded'
    admitted = EngineeringService(store).submit(original.actor, original.project_id, 'project.repair',
        original.revision, 'repair', objective='Change lid, retain existing base', allowed_paths=['lid.py'])
    scope = original.model_copy(update={'run_id': admitted['runId'], 'revision': admitted['revision']})
    prior = [ref for items in refs.values() for ref in items if not ref['path'].startswith('outputs/')]
    task = TaskSpec(id='repair', capability='cad', objective='Repair lid only', allowed_paths=['lid.py'],
        input_artifact_ids=[ref['id'] for ref in prior])
    await service.repository.publish_plan(scope, tasks=[task], superseded={}, turn=0, summary='Repair fixture', question=None)
    await service.gateway.invoke(scope, task, 'patch', {'actionId': 'patch', 'path': 'lid.py',
        'content': 'Repaired inert fixture', 'expectedSha256': next(ref['sha256'] for ref in prior if ref['path'] == 'lid.py')})
    await service.gateway.invoke(scope, task, 'build', {'actionId': 'build'})
    result = await service.gateway.invoke(scope, task, 'finish', {'actionId': 'finish'})
    await service.repository.publish(scope, TaskResult(task_id=task.id, status='succeeded',
        summary='Inert response fixture', artifacts=result['artifacts']))
    grant = service.freeze(scope)
    compiled = await compile_targets(service, scope, grant)
    CompositionResults(service).record(scope, grant['id'], compiled)
    retained_id = next(ref['id'] for ref in prior if ref['path'] == 'model.py')
    with store.transaction() as session:
        selected = current_candidates(session, session.get(Objective, scope.run_id))
        generator_id = session.get(Artifact, retained_id).details['nativeJobId']
    checks = registry('cad')
    validation = ValidationService(store, checks, repository=service.repository)
    selected_ids = [ref['id'] for ref in selected if include_retained_ids or ref['id'] != retained_id]
    candidate_id = validation.prepare(scope, selected_ids, 'cad.export-integrity', 'check')
    with store.transaction() as session:
        candidate = session.get(Candidate, candidate_id)
        assert generator_id in candidate.data['producerJobs']
        assert candidate.data['closure']['model.py'] == retained_id
        assert {ref['id'] for ref in candidate.data['sources']} == set(grant['closure'].values())
    async def generator_report(scope, subject):
        return CheckReport(subject_hash=subject.subject_hash, status='PASS', execution_id=generator_id,
            summary='Fixture attempts to reuse retained-source generator identity')
    checks.checker('cad.export-integrity').invoke.side_effect = generator_report
    await validation.run(scope, candidate_id)
    with store.transaction() as session:
        check = session.get(ValidationCheck, (candidate_id, 'cad.export-integrity'))
        assert check.status == 'ERROR' and check.data['report'] is None
        session.get(Objective, scope.run_id).status = 'succeeded'
    with pytest.raises(DomainError, match='independent checks have not passed'):
        validation.accept(scope.actor, scope.project_id, candidate_id, scope.revision, 'accept')
    with store.transaction() as session:
        assert session.get(Project, scope.project_id).data['sourceManifest'] == baseline
        assert session.scalar(select(Release)) is None
