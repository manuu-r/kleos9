"""All-target handoff through real grants/gateway/publication, mocked container bytes."""
import base64
import copy
import json
import time
from datetime import timedelta
from pathlib import Path

import pytest
import test_composition as fixtures
from sqlalchemy import select
from test_execution import FakeDocker
from test_validation_policies import registry

from kleos.artifacts.service import Artifact, NativePublication
from kleos.contracts.models import Actor, DomainError, TaskResult, TaskSpec
from kleos.execution.composition import NativeComposition, compilation_job_id
from kleos.execution.gateway import NativeAction, NativeJob
from kleos.execution.native_runner import atomic_json
from kleos.execution.protocol import PROTOCOL, fingerprint
from kleos.projects.composition_results import (
    CompositionResults,
    effective_artifacts,
    retained_repair_sources,
)
from kleos.projects.store import Objective, Project
from kleos.projects.verification import VerificationService, current_candidates
from kleos.validation.policies import (
    LANE_CHECKS,
    authored_native_rows,
    published_native_rows,
    register_digital_policies,
    select_policy,
)
from kleos.validation.service import (
    Candidate,
    CheckerConfiguration,
    CheckerRegistry,
    CheckReport,
    ValidationService,
)

composed = fixtures.composed


class CompilationDocker(FakeDocker):
    def __init__(self, grant):
        super().__init__()
        self.targets = {target['jobId']: target for target in grant['targets']}

    async def ensure(self, job_id, data, image, control_directory, *, allow_create=True, on_observed=None):
        from kleos.execution.protocol import digest
        assert data['allowedPaths'] == [] and data['baselines'] == {}
        self.dispatches += 1
        self.sessions[job_id] = True
        if on_observed:
            await on_observed(digest(job_id.encode()))
        root = Path(control_directory)
        for path in sorted((root/'actions').glob('*.json')):
            if (root/'results'/path.name).exists():
                continue
            action = json.loads(path.read_bytes())
            assert action['action'] in {'build', 'render', 'finish'}
            result = {'summary': 'Fixture compilation, not engineering evidence'}
            if action['action'] == 'finish':
                files = [{'path': entry['path'], 'role': 'source',
                    'content': base64.b64decode(entry['contentBase64'])} for entry in data['sources']]
                files += [{'path': ref['path'], 'role': 'output', 'content': ('Compiled fixture '+ref['path']).encode()}
                          for ref in self.targets[data['compositionTarget']]['coveredOutputs']]
                result['files'] = [{key: value for key, value in file.items() if key != 'content'} | {
                    'sha256': digest(file['content']), 'size': len(file['content']),
                    'contentBase64': base64.b64encode(file['content']).decode()} for file in files]
            atomic_json(root/'results'/path.name, {'protocol': PROTOCOL, 'jobId': job_id,
                'requestHash': fingerprint(data), 'actionId': action['actionId'],
                'actionHash': fingerprint(action), 'response': {'status': 'succeeded', 'result': result}})
        return job_id


async def compile_targets(service, scope, grant, targets=None):
    service.gateway.docker = CompilationDocker(grant)
    compiler = NativeComposition(service.gateway, service)
    results = {}
    for target in targets if targets is not None else grant['targets']:
        for action in target['actions']:
            result = await compiler.invoke(scope, grant['id'], target['taskId'], action)
        results[target['taskId']] = result
    return results


async def test_canonical_outputs_change_only_after_complete_idempotent_handoff(composed):
    service, scope, _, baseline, _, _ = composed
    grant = service.freeze(scope)
    store = service.repository.store
    with store.transaction() as session:
        run = session.get(Objective, scope.run_id)
        authored = authored_native_rows(session, run)
        old_outputs = {row.id for row in authored if row.details['role'] == 'output'}
        source_ids = {row.id for row in authored if row.details['role'] == 'source'}
        original_results = copy.deepcopy(run.data['results'])
    results = await compile_targets(service, scope, grant)
    with store.transaction() as session:
        run = session.get(Objective, scope.run_id)
        assert {row.id for row in published_native_rows(session, run)} == {row.id for row in authored}
    handoff = CompositionResults(service)
    receipt = handoff.record(scope, grant['id'], results)
    assert handoff.record(scope, grant['id'], results) == receipt
    with store.transaction() as session:
        run = session.get(Objective, scope.run_id)
        canonical = published_native_rows(session, run)
        assert run.data['results'] == original_results
        assert source_ids <= {row.id for row in canonical}
        assert not old_outputs & {row.id for row in canonical}
        assert select_policy(registry('cad', 'application'), canonical) == 'digital.cad-application'
        assert session.get(Project, scope.project_id).data['sourceManifest'] == baseline
        assert all(row.details['sourceClosure'] == grant['closure'] for row in canonical if row.details['role'] == 'output')
        for task_id, result in results.items():
            effective = effective_artifacts(session, run, task_id)
            assert {ref.id for ref in effective if ref.path.startswith('outputs/')} == {ref['id'] for ref in result['artifacts']}
        assert retained_repair_sources(session, run) == {}
        assert current_candidates(session, run)
        assert service.validate_in_session(session, scope, grant['id']) == grant


async def test_partial_targets_never_replace_canonical_outputs(composed):
    service, scope, _, _, _, _ = composed
    grant = service.freeze(scope)
    results = await compile_targets(service, scope, grant, grant['targets'][:1])
    with pytest.raises(DomainError, match='All compilation targets'):
        CompositionResults(service).record(scope, grant['id'], results)
    with service.repository.store.transaction() as session:
        run = session.get(Objective, scope.run_id)
        assert not run.data.get('compositionResults')
        assert published_native_rows(session, run) == authored_native_rows(session, run)


@pytest.mark.parametrize('change', ['foreign-actor', 'cancelled', 'stale', 'forged-result', 'uncertain',
    'missing-publication', 'source-edit', 'source-bytes', 'wrong-closure', 'deadline', 'missing-lane'])
async def test_invalid_completed_sets_cannot_handoff(composed, change):
    service, scope, _, _, _, _ = composed
    grant = service.freeze(scope)
    results = await compile_targets(service, scope, grant)
    target = grant['targets'][0]
    job_id = compilation_job_id(grant, target)
    if change == 'foreign-actor':
        scope = scope.model_copy(update={'actor': Actor(tenant_id='foreign', user_id='owner')})
    elif change == 'forged-result':
        results[target['taskId']]['summary'] = 'Forged success'
    elif change == 'missing-lane':
        results.pop(next(value['taskId'] for value in grant['targets'] if value['lane'] == 'application'))
    else:
        with service.repository.store.transaction() as session:
            run = session.get(Objective, scope.run_id)
            job = session.get(NativeJob, job_id)
            if change == 'cancelled':
                run.status = 'cancelled'
            elif change == 'stale':
                session.get(Project, scope.project_id).advance_revision()
            elif change == 'uncertain':
                session.get(NativeAction, (job_id, 'composition:finish')).status = 'pending'
            elif change == 'missing-publication':
                publication = session.scalar(select(NativePublication).where(NativePublication.job_id == job_id))
                session.delete(publication)
            elif change == 'source-edit':
                job.request = {**job.request, 'allowedPaths': ['model.py']}
                job.request_hash = fingerprint(job.request)
            elif change == 'source-bytes':
                altered = copy.deepcopy(job.request)
                altered['sources'][0]['contentBase64'] = base64.b64encode(b'wrong bytes').decode()
                job.request, job.request_hash = altered, fingerprint(altered)
            elif change == 'wrong-closure':
                row = session.get(Artifact, results[target['taskId']]['artifacts'][0]['id'])
                row.details = {**row.details, 'sourceClosure': {}}
            else:
                altered = copy.deepcopy(run.data['compositionExecutions'])
                altered[grant['id']][job_id]['deadline'] += 10000
                run.data = {**run.data, 'compositionExecutions': altered}
    with pytest.raises(DomainError):
        CompositionResults(service).record(scope, grant['id'], results)
    with service.repository.store.transaction() as session:
        assert not session.get(Objective, scope.run_id).data.get('compositionResults')


async def test_completed_verification_reads_frozen_evidence_without_reactivating_execution(composed):
    service, scope, _, _, _, _ = composed
    grant = service.freeze(scope)
    results = await compile_targets(service, scope, grant)
    CompositionResults(service).record(scope, grant['id'], results)
    with service.repository.store.transaction() as session:
        run = session.get(Objective, scope.run_id)
        run.status = 'succeeded'
        selected = current_candidates(session, run)
    # The completed selector does not call an active graph guard or start/extend native work.
    count = service.gateway.docker.dispatches
    with service.repository.store.transaction() as session:
        run = session.get(Objective, scope.run_id)
        assert current_candidates(session, run) == selected and run.status == 'succeeded'
    assert service.gateway.docker.dispatches == count
    with pytest.raises(DomainError, match='no longer active'):
        service.validate(scope, grant['id'])


@pytest.mark.parametrize('change', ['source', 'authority', 'authoring', 'receipt', 'cancel'])
async def test_completed_projection_rechecks_provenance_after_record(composed, change):
    service, scope, _, _, _, _ = composed
    grant = service.freeze(scope)
    results = await compile_targets(service, scope, grant)
    CompositionResults(service).record(scope, grant['id'], results)
    with service.repository.store.transaction() as session:
        run = session.get(Objective, scope.run_id)
        if change == 'source':
            row = session.get(Artifact, grant['sources'][0]['id'])
            row.sha256 = 'f'*64
        elif change == 'authority':
            project = session.get(Project, scope.project_id)
            project.data = {**project.data, 'requirements': {'changed': True}}
        elif change == 'authoring':
            data = copy.deepcopy(run.data)
            data['tasks'][0]['objective'] += ' changed'
            run.data = data
        elif change == 'cancel':
            run.status = 'cancelled'
        else:
            data = copy.deepcopy(run.data)
            data['compositionResults'][grant['id']]['recordedAt'] = time.time()+10000
            run.data = data
    with service.repository.store.transaction() as session, pytest.raises(DomainError):
        published_native_rows(session, session.get(Objective, scope.run_id))


@pytest.mark.parametrize('composed', [['base', 'lid'], ['base', 'lid', 'app']], indirect=True)
async def test_partial_repair_retains_exact_prior_sources_and_validates_compiled_closure(composed):
    from kleos.projects.engineering import EngineeringService

    service, old_scope, _, baseline, refs, _ = composed
    store = service.repository.store
    with store.transaction() as session:
        session.get(Objective, old_scope.run_id).status = 'succeeded'
    admitted = EngineeringService(store).submit(old_scope.actor, old_scope.project_id, 'project.repair',
        old_scope.revision, 'partial-repair', objective='Repair lid only', allowed_paths=['lid.py'])
    scope = old_scope.model_copy(update={'run_id': admitted['runId'], 'revision': admitted['revision']})
    prior = [ref for values in refs.values() for ref in values if not ref['path'].startswith('outputs/')]
    task = TaskSpec(id='repair-lid', capability='cad', objective='Repair only lid',
        allowed_paths=['lid.py'], input_artifact_ids=[ref['id'] for ref in prior])
    read = TaskSpec(id='read-retained', capability='research', objective='Read retained base',
        input_artifact_ids=[prior[0]['id']])
    await service.repository.publish_plan(scope, tasks=[task, read], superseded={}, turn=0, summary='Repair', question=None)
    await service.repository.publish(scope, TaskResult(task_id=read.id, status='succeeded', summary='Read only',
        artifacts=[prior[0]]))
    old_lid = next(ref for ref in prior if ref['path'] == 'lid.py')
    await service.gateway.invoke(scope, task, 'patch', {'actionId': 'patch', 'path': 'lid.py',
        'content': 'Repaired lid fixture', 'expectedSha256': old_lid['sha256']})
    await service.gateway.invoke(scope, task, 'build', {'actionId': 'build'})
    result = await service.gateway.invoke(scope, task, 'finish', {'actionId': 'finish'})
    await service.repository.publish(scope, TaskResult(task_id=task.id, status='succeeded', summary='Fixture',
        artifacts=result['artifacts']))
    grant = service.freeze(scope)
    with store.transaction() as session:
        run = session.get(Objective, scope.run_id)
        assert effective_artifacts(session, run, read.id)[0].id == prior[0]['id']
        assert all(row.details['runId'] == run.id for row in authored_native_rows(session, run))
    results = await compile_targets(service, scope, grant)
    CompositionResults(service).record(scope, grant['id'], results)
    with store.transaction() as session:
        run = session.get(Objective, scope.run_id)
        retained = retained_repair_sources(session, run)
        assert set(retained) == {ref['path'] for ref in prior if ref['path'] != 'lid.py'}
        assert {ref.id for ref in retained.values()} == {ref['id'] for ref in prior if ref['path'] != 'lid.py'}
        canonical = published_native_rows(session, run)
        assert all(row.id != old_lid['id'] for row in canonical)
        assert effective_artifacts(session, run, read.id)[0].id == prior[0]['id']
        selected = current_candidates(session, run)
    validation = ValidationService(store, registry('cad'), repository=service.repository)
    if 'app' in refs:
        # Retaining an unaccepted application source cannot silently omit its lane.
        with pytest.raises(DomainError):
            validation.prepare(scope, [ref['id'] for ref in selected], 'cad.export-integrity', 'missing-lane')
        with store.transaction() as session, pytest.raises(DomainError, match='covered digital outputs'):
            select_policy(registry('cad', 'application'), published_native_rows(session, session.get(Objective, scope.run_id)))
        return
    candidate_id = validation.prepare(scope, [ref['id'] for ref in selected], 'cad.export-integrity', 'independent')
    with store.transaction() as session:
        candidate = session.get(Candidate, candidate_id)
        assert {ref['path']: ref['id'] for ref in candidate.data['sources']} == grant['closure']
        assert session.get(Project, scope.project_id).data['sourceManifest'] == baseline
        # Revoking a retained source's producer proof also invalidates the projection.
        retained_row = session.get(Artifact, retained['model.py'].id)
        session.get(NativeAction, (retained_row.details['nativeJobId'], '_termination')).status = 'pending'
    with store.transaction() as session, pytest.raises(DomainError):
        published_native_rows(session, session.get(Objective, scope.run_id))


async def test_ordinary_dependencies_do_not_scan_native_results_without_completed_projection(composed, monkeypatch):
    service, scope, _, _, _, _ = composed
    def unexpected(*args):
        raise AssertionError('Ordinary dependency read must not scan native provenance')
    monkeypatch.setattr('kleos.projects.composition_results.authored_native_rows', unexpected)
    with service.repository.store.transaction() as session:
        run = session.get(Objective, scope.run_id)
        assert [ref.id for ref in effective_artifacts(session, run, 'base')] == [
            ref['id'] for ref in run.data['results']['base']['artifacts']]


async def test_validation_carryforward_keeps_authoring_and_grant_immutable(composed):
    service, scope, tasks, _, _, _ = composed
    grant = service.freeze(scope)
    results = await compile_targets(service, scope, grant)
    CompositionResults(service).record(scope, grant['id'], results)
    check = TaskSpec(id='independent', capability='validation', objective='Check exact rebuilt set',
        depends_on=[task.id for task in tasks])
    await service.repository.publish_plan(scope, tasks=[*tasks, check], superseded={}, turn=1,
        summary='Add independent validation', question=None)
    carried = [ref for result in results.values() for ref in result['artifacts']]
    await service.repository.publish(scope, TaskResult(task_id=check.id, status='succeeded',
        summary='Fixture carry-forward, no engineering validation claim', artifacts=carried))
    assert service.validate(scope, grant['id']) == grant
    with service.repository.store.transaction() as session:
        run = session.get(Objective, scope.run_id)
        assert not any(row.details.get('compositionId') for row in authored_native_rows(session, run))
        outputs = [row for row in published_native_rows(session, run) if row.details['role'] == 'output']
        assert {row.id for row in outputs} == {ref['id'] for ref in carried}
        assert {ref.id for ref in effective_artifacts(session, run, check.id)} == {ref['id'] for ref in carried}
        # A carried output without a trusted all-target handoff is not an authoring result.
        run.data = {**run.data, 'compositionResults': {}}
    with service.repository.store.transaction() as session, pytest.raises(DomainError, match='completed handoff'):
        authored_native_rows(session, session.get(Objective, scope.run_id))


async def test_postrun_verification_after_generation_deadline_uses_completed_exact_projection(composed):
    service, scope, _, baseline, _, _ = composed
    grant = service.freeze(scope)
    results = await compile_targets(service, scope, grant)
    CompositionResults(service).record(scope, grant['id'], results)
    store, calls = service.repository.store, []
    with store.transaction() as session:
        session.get(Objective, scope.run_id).status = 'succeeded'
    async def check(check_scope, subject):
        calls.append(subject)
        assert check_scope.run_id == scope.run_id
        assert {ref.path: ref.id for ref in subject.sources} == grant['closure']
        assert {ref.id for ref in subject.outputs} == {ref['id'] for result in results.values() for ref in result['artifacts']}
        return CheckReport(subject_hash=subject.subject_hash, status='UNKNOWN', execution_id='fixture-independent',
            summary='Fixture exercises the handoff only, not real geometry or application validity')
    reg = CheckerRegistry()
    for lane in ('cad', 'application'):
        reg.register(CheckerConfiguration(key=LANE_CHECKS[lane], version='1', installation_hash='a'*64,
            qualified=True), check)
    register_digital_policies(reg)
    now = scope.deadline_at+timedelta(days=1)
    verification = VerificationService(store, reg, clock=lambda: now)
    queued = verification.submit(scope.actor, scope.project_id, scope.revision, 'verify-completed',
        policy_key=None, run_id=scope.run_id)
    dispatches = service.gateway.docker.dispatches
    # New verifier instance resumes durable independent work, not the expired generation scope.
    verification = VerificationService(store, reg, clock=lambda: now)
    assert await verification.process_next()
    state = verification.get(scope.actor, scope.project_id, queued['verificationId'])
    assert state['status'] == 'completed' and set(state['checkStatuses'].values()) == {'UNKNOWN'}
    assert len(calls) == 2 and service.gateway.docker.dispatches == dispatches
    with store.transaction() as session:
        run = session.get(Objective, scope.run_id)
        assert run.status == 'succeeded' and run.data['sourceComposition'] == grant
        assert session.get(Project, scope.project_id).data['sourceManifest'] == baseline
        assert session.get(Project, scope.project_id).context_revision == scope.revision
