"""Fixture-only durable post-run checking; no model, native process or retrieval calls."""
import asyncio
from datetime import UTC, datetime, timedelta

import pytest
import test_validation
from test_validation import registry

from kleos.contracts.models import Actor, DomainError
from kleos.projects.graph_repository import GraphRepository
from kleos.projects.store import Membership, Objective, Project
from kleos.projects.verification import VerificationJob, VerificationService
from kleos.validation.service import Candidate, CheckReport, ValidationCheck, ValidationService

setup = test_validation.setup


def complete(fixture):
    store, scope, artifacts, baseline = fixture
    with store.transaction() as session:
        run = session.get(Objective, scope.run_id)
        run.status = 'succeeded'
        run.data = {**run.data, 'results': {'part': {'artifacts': [r.model_dump(mode='json') for r in artifacts]}}}
    return store, scope, artifacts, baseline


def submit(service, scope, key='verify'):
    return service.submit(scope.actor, scope.project_id, scope.revision, key, policy_key='digital', run_id=scope.run_id)


async def test_completed_verification_preserves_context_sources_and_exact_run(setup):
    store, scope, artifacts, baseline = complete(setup)
    calls = []
    service = None
    async def check(check_scope, subject):
        calls.append(subject)
        assert check_scope.run_id == scope.run_id
        assert await service.repository.resolve_artifacts(check_scope, [r.id for r in subject.outputs]) == subject.outputs
        return CheckReport(subject_hash=subject.subject_hash, status='PASS', execution_id='independent', summary='Fixture measured')
    service = VerificationService(store, registry(check))
    queued = submit(service, scope)
    assert submit(service, scope) == queued
    assert submit(service, scope, 'different-key')['verificationId'] == queued['verificationId']
    # A fresh service simulates a restart before work starts.
    service = VerificationService(store, service.registry)
    assert await service.process_next()
    assert not await service.process_next()
    response = service.get(scope.actor, scope.project_id, queued['verificationId'])
    assert response['status'] == 'completed' and len(calls) == 1
    with store.transaction() as session:
        project = session.get(Project, scope.project_id)
        assert project.revision == scope.revision == project.context_revision
        assert project.data['sourceManifest'] == baseline
        assert session.get(Objective, scope.run_id).status == 'succeeded'
        assert session.get(Candidate, response['candidateId']).status == 'qualified'
    assert service.validation.accept(scope.actor, scope.project_id, response['candidateId'], scope.revision, 'accept')['releaseId']


@pytest.mark.parametrize('mutation,blocker', [('context', 'stale_context'), ('permission', 'forbidden'), ('cancel', None), ('run', 'run_inactive')])
async def test_current_authority_cancellation_and_context_fence_before_io(setup, mutation, blocker):
    store, scope, _, _ = complete(setup)
    async def check(*args):
        pytest.fail('Checker must not run after invalidation')
    service = VerificationService(store, registry(check))
    job = submit(service, scope)
    if mutation == 'cancel':
        service.cancel(scope.actor, scope.project_id, job['verificationId'])
    else:
        with store.transaction() as session:
            if mutation == 'context':
                session.get(Project, scope.project_id).advance_revision()
            elif mutation == 'permission':
                session.get(Membership, (scope.project_id, scope.actor.user_id)).role = 'viewer'
            else:
                session.get(Objective, scope.run_id).status = 'cancelled'
    await service.process_next()
    result = service.get(scope.actor, scope.project_id, job['verificationId'])
    assert result['status'] == ('cancelled' if mutation == 'cancel' else 'blocked')
    assert result['blocker'] == blocker


async def test_cancel_during_check_retains_observation_but_cannot_accept(setup):
    store, scope, _, _ = complete(setup)
    job = None
    async def check(_, subject):
        service.cancel(scope.actor, scope.project_id, job['verificationId'])
        return CheckReport(subject_hash=subject.subject_hash, status='PASS', execution_id='independent', summary='Measured before cancel')
    service = VerificationService(store, registry(check))
    job = submit(service, scope)
    await service.process_next()
    response = service.get(scope.actor, scope.project_id, job['verificationId'])
    assert response['status'] == 'cancelled'
    assert service.validation.evidence(scope.actor, scope.project_id, response['candidateId'])[0]['status'] == 'PASS'
    with pytest.raises(DomainError, match='cancelled'):
        service.validation.accept(scope.actor, scope.project_id, response['candidateId'], scope.revision, 'accept')


async def test_interrupted_check_is_uncertain_and_never_repeated(setup):
    store, scope, _, _ = complete(setup)
    calls = []
    async def check(_, subject):
        calls.append(subject)
        raise asyncio.CancelledError()
    service = VerificationService(store, registry(check))
    job = submit(service, scope)
    with pytest.raises(asyncio.CancelledError):
        await service.process_next()
    assert not await service.process_next()  # live claim retained
    with store.transaction() as session:
        session.get(VerificationJob, job['verificationId']).lease_until = 0
    restarted = VerificationService(store, service.registry)
    assert await restarted.process_next()
    response = restarted.get(scope.actor, scope.project_id, job['verificationId'])
    assert response['status'] == 'blocked' and response['blocker'] == 'effect_uncertain'
    assert len(calls) == 1
    assert submit(restarted, scope, 'another-key')['verificationId'] == job['verificationId']


async def test_existing_same_run_uncertain_check_cannot_be_bypassed(setup):
    store, scope, artifacts, _ = setup
    async def check(*args):
        pytest.fail('An uncertain check must never repeat')
    reg = registry(check)
    existing = ValidationService(store, reg).prepare(scope, [r.id for r in artifacts], 'digital', 'original')
    with store.transaction() as session:
        session.add(ValidationCheck(candidate_id=existing, checker_key='geometry', status='running', data={}))
    complete(setup)
    service = VerificationService(store, reg)
    job = submit(service, scope)
    await service.process_next()
    result = service.get(scope.actor, scope.project_id, job['verificationId'])
    assert result['candidateId'] == existing and result['blocker'] == 'effect_uncertain'


async def test_old_objective_deadline_does_not_require_a_new_generation_run(setup):
    store, scope, _, _ = complete(setup)
    with store.transaction() as session:
        run = session.get(Objective, scope.run_id)
        run.data = {**run.data, 'scope': scope.model_copy(update={'deadline_at': datetime.now(UTC)-timedelta(days=1)}).model_dump(mode='json')}
    async def check(_, subject):
        return CheckReport(subject_hash=subject.subject_hash, status='UNKNOWN', execution_id='independent', summary='Unsupported property')
    service = VerificationService(store, registry(check))
    job = submit(service, scope)
    await service.process_next()
    result = service.get(scope.actor, scope.project_id, job['verificationId'])
    assert result['status'] == 'completed'
    with pytest.raises(DomainError, match='have not passed'):
        service.validation.accept(scope.actor, scope.project_id, result['candidateId'], scope.revision, 'accept')
    with pytest.raises(DomainError, match='not found'):
        service.get(Actor(tenant_id='other', user_id='owner'), scope.project_id, job['verificationId'])
    with pytest.raises(DomainError, match='no longer active'):
        await GraphRepository(store).assert_active(scope)


async def test_workbench_command_filters_ancillary_outputs_and_accepts_only_checked_manifest(setup):
    from sqlalchemy import select

    from kleos.api.engineering import EngineeringFacade
    from kleos.api.releases import ReleaseFacade
    from kleos.artifacts.service import Artifact
    from kleos.capabilities.validation import CAD_POLICY, check_cad_artifacts
    from kleos.projects.engineering import EngineeringService
    from kleos.projects.store import Event
    from kleos.validation.service import CheckRequirement, ReleasePolicy

    store, scope, artifacts, _ = complete(setup)
    with store.transaction() as session:
        template = session.get(Artifact, artifacts[1].id)
        ancillary = Artifact(id='preview', project_id=scope.project_id, revision=scope.revision,
            name='outputs/preview.svg', sha256='a'*64, size=1, media_type='image/svg+xml', status='candidate',
            lane='cad', details=dict(template.details))
        session.add(ancillary)
        from kleos.validation.service import _ref
        run = session.get(Objective, scope.run_id)
        run.data = {**run.data, 'results': {'part': {'artifacts': [r.model_dump(mode='json') for r in artifacts]+[_ref(ancillary).model_dump(mode='json')]}}}
    service = None
    async def check(check_scope, subject):
        check_cad_artifacts(service.repository, check_scope, [r.id for r in subject.outputs])
        assert [r.path for r in subject.outputs] == ['outputs/bracket.step']
        return CheckReport(subject_hash=subject.subject_hash, status='PASS', execution_id='independent', summary='Export fixture only')
    reg = registry(check)
    reg.register_policy(ReleasePolicy(key=CAD_POLICY, version='1', required_checks=(CheckRequirement(checker_key='geometry'),)))
    service = VerificationService(store, reg)
    facade = EngineeringFacade(EngineeringService(store), verification=service)
    response = facade.command(scope.actor, {'type': 'project.verify', 'expectedRevision': scope.revision,
        'payload': {'projectId': scope.project_id, 'runId': scope.run_id}}, 'verify-command')
    await service.process_next()
    result = service.get(scope.actor, scope.project_id, response['verificationId'])
    assert result['checkStatuses'] == {'geometry': 'PASS'}
    with store.transaction() as session:
        events = list(session.scalars(select(Event).where(Event.project_id == scope.project_id)))
        assert any(e.data['type'] == 'verification.completed' and e.data['payload']['candidateId'] == result['candidateId'] for e in events)
    accepted = ReleaseFacade(store, None, service.validation).accept(scope.actor, {
        'expectedRevision': scope.revision, 'payload': {'projectId': scope.project_id, 'candidateId': result['candidateId']}}, 'accept-command')
    assert accepted['revision'] == scope.revision+1


async def test_restart_after_preparation_reuses_exact_candidate_before_first_dispatch(setup):
    store, scope, _, _ = complete(setup)
    calls = []
    async def check(_, subject):
        calls.append(subject.candidate_id)
        return CheckReport(subject_hash=subject.subject_hash, status='PASS', execution_id='independent', summary='Fixture')
    service = VerificationService(store, registry(check))
    job = submit(service, scope)
    async def interrupted_before_dispatch(*args):
        raise asyncio.CancelledError()
    service.validation.run = interrupted_before_dispatch
    with pytest.raises(asyncio.CancelledError):
        await service.process_next()
    prepared = service.get(scope.actor, scope.project_id, job['verificationId'])['candidateId']
    with store.transaction() as session:
        session.get(VerificationJob, job['verificationId']).lease_until = 0
    restarted = VerificationService(store, service.registry)
    assert await restarted.process_next()
    assert calls == [prepared]
    assert restarted.get(scope.actor, scope.project_id, job['verificationId'])['status'] == 'completed'


async def test_cancel_command_visible_pending_and_idempotent_without_cancelling_completed_run(setup):
    from kleos.api.engineering import EngineeringFacade
    from kleos.projects.engineering import EngineeringService
    store, scope, _, _ = complete(setup)
    async def check(*args):
        pytest.fail('Cancelled verification cannot dispatch')
    service = VerificationService(store, registry(check))
    job = submit(service, scope)
    with store.transaction() as session:
        project = session.get(Project, scope.project_id)
        assert project.data['status'] == 'queued'
        assert project.data['state']['planning'] == {'phase': 'verification', 'pending': True,
            'runId': scope.run_id, 'verificationId': job['verificationId']}
    assert service.has_active(scope.actor, scope.project_id)
    facade = EngineeringFacade(EngineeringService(store), verification=service)
    command = {'type': 'project.cancel', 'expectedRevision': scope.revision, 'payload': {'projectId': scope.project_id}}
    result = facade.command(scope.actor, command, 'cancel-command')
    assert not service.has_active(scope.actor, scope.project_id)
    assert facade.command(scope.actor, command, 'cancel-command') == result
    # Facade may route a replay to ordinary cancellation after has_active becomes false.
    assert EngineeringService(store).cancel(scope.actor, scope.project_id, None, scope.revision, 'cancel-command') == result
    assert not await service.process_next()
    with store.transaction() as session:
        project = session.get(Project, scope.project_id)
        assert project.context_revision == scope.revision
        assert project.data['state']['planning']['pending'] is False
        assert project.data['status'] == 'cancelled'
        assert session.get(Objective, scope.run_id).status == 'succeeded'


async def test_capacity_deferral_requeues_exact_job_without_error_evidence_or_new_scope(setup):
    from kleos.validation.service import CheckerAdmissionDeferred
    store, scope, _, _ = complete(setup)
    calls = []
    busy = True
    async def check(_, subject):
        calls.append(subject.candidate_id)
        if busy:
            raise CheckerAdmissionDeferred('Fixture pool is full; no dispatch')
        return CheckReport(subject_hash=subject.subject_hash, status='PASS', execution_id='independent', summary='Measured')
    service = VerificationService(store, registry(check))
    job = submit(service, scope)
    await service.process_next()
    result = service.get(scope.actor, scope.project_id, job['verificationId'])
    assert result['status'] == 'queued' and result['blocker'] == 'native_capacity_busy'
    with store.transaction() as session:
        saved_scope = dict(session.get(VerificationJob, job['verificationId']).data['scope'])
        assert session.get(Project, scope.project_id).data['state']['planning']['pending'] is True
        evidence = session.get(ValidationCheck, (result['candidateId'], 'geometry'))
        assert evidence.status == 'queued' and 'report' not in evidence.data
    busy = False
    await service.process_next()
    result = service.get(scope.actor, scope.project_id, job['verificationId'])
    assert result['status'] == 'completed' and result['checkStatuses'] == {'geometry': 'PASS'}
    assert calls == [result['candidateId'], result['candidateId']]
    with store.transaction() as session:
        assert session.get(VerificationJob, job['verificationId']).data['scope'] == saved_scope
        assert session.get(ValidationCheck, (result['candidateId'], 'geometry')).data['admissionAttempts'] == 2


async def test_final_job_publication_rechecks_authority_after_validation_returns(setup):
    store, scope, _, _ = complete(setup)
    async def check(_, subject):
        return CheckReport(subject_hash=subject.subject_hash, status='PASS', execution_id='independent', summary='Measured')
    service = VerificationService(store, registry(check))
    original = service.validation.run
    async def revoke_after_validation(*args):
        result = await original(*args)
        with store.transaction() as session:
            session.get(Membership, (scope.project_id, scope.actor.user_id)).role = 'viewer'
        return result
    service.validation.run = revoke_after_validation
    job = submit(service, scope)
    await service.process_next()
    result = service.get(scope.actor, scope.project_id, job['verificationId'])
    assert result['status'] == 'blocked' and result['blocker'] == 'forbidden'
    assert result['checkStatuses'] == {'geometry': 'PASS'}  # Measured observation is retained.
