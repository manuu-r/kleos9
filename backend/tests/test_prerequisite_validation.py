"""Canonical interface approval provenance through independent fixture checks."""
import copy
from datetime import UTC, datetime, timedelta

import pytest
import test_interfaces as interface_fixtures
from test_validation import finish, registry

from kleos.contracts.models import DomainError, TaskSpec
from kleos.orchestration.engine import RunScope
from kleos.projects.graph_repository import GraphRepository
from kleos.projects.store import Project, fingerprint
from kleos.validation.service import Candidate, CheckReport, ValidationService

interface_setup = interface_fixtures.setup


@pytest.fixture
async def approved(interface_setup):
    interfaces, actor, project_id = interface_setup
    interface_fixtures.publish(interface_setup)
    project = interface_fixtures.current(*interface_setup)
    interfaces.approve(actor, project_id, project.revision, 'approve-configuration', message=True)
    project = interface_fixtures.current(*interface_setup)
    authority = {'schemaVersion': 1, **{key: copy.deepcopy(project.data.get(key)) for key in
        ('interfaces', 'acceptedInterfaces', 'acceptedCircuitIdentity')}}
    receipt = interfaces.projects.submit(actor, project_id, 'Generate only the requested housing',
                                         project.revision, 'generate')
    scope = RunScope(actor=actor, project_id=project_id, run_id=receipt['runId'], revision=receipt['revision'],
                     deadline_at=datetime.now(UTC)+timedelta(minutes=5))
    task = TaskSpec(id='housing', capability='cad', objective='Housing only', allowed_paths=['model.py'])
    await GraphRepository(interfaces.projects).publish_plan(scope, tasks=[task], superseded={}, turn=0,
                                                          summary='Candidate fixture', question=None)
    refs = await interfaces.artifacts.publish_native(scope, task, [
        {'path': 'model.py', 'role': 'source', 'content': b'inert source fixture', 'expectedSha256': None},
        {'path': 'outputs/housing.step', 'role': 'output', 'content': b'inert STEP fixture'}],
        {'nativeJobId': 'generator-fixture', 'imageDigest': 'a'*64, 'requestHash': 'b'*64})
    return interfaces.projects, scope, [ref.id for ref in refs], authority


def subject_data(row):
    keys = ('projectId', 'revision', 'sources', 'outputs', 'policy', 'interfaceAuthority')
    return {key: copy.deepcopy(row.data[key]) for key in keys if key in row.data}


def checker(calls):
    async def run(scope, subject):
        calls.append(subject)
        return CheckReport(subject_hash=subject.subject_hash, status='PASS', execution_id='independent-fixture',
                           summary='Fixture export integrity only; no physical claims.')
    return run


async def test_canonical_approval_is_hash_bound_in_checker_subject_and_immutable_release(approved):
    store, scope, ids, authority = approved
    calls = []
    service = ValidationService(store, registry(checker(calls)))
    candidate_id = service.prepare(scope, ids, 'digital', 'prepare')
    with store.transaction() as session:
        candidate = session.get(Candidate, candidate_id)
        assert candidate.data['interfaceAuthority'] == authority
        assert fingerprint(subject_data(candidate)) == candidate.subject_hash
        unbound = subject_data(candidate)
        unbound.pop('interfaceAuthority')
        assert fingerprint(unbound) != candidate.subject_hash
        subject_hash = candidate.subject_hash
    evidence = await service.run(scope, candidate_id)
    assert len(calls) == 1 and calls[0].subject_hash == subject_hash
    assert evidence[0]['report']['subject_hash'] == subject_hash and evidence[0]['independent']
    finish(store, scope)
    release_id = service.accept(scope.actor, scope.project_id, candidate_id, scope.revision, 'accept')['releaseId']
    manifest = service.release(scope.actor, scope.project_id, release_id)
    assert manifest['interfaceAuthority'] == authority
    assert manifest['interfaceAuthority']['acceptedInterfaces']['proposalId']
    assert manifest['interfaceAuthority']['acceptedInterfaces']['engineeringValidated'] is False
    assert manifest['interfaceAuthority']['acceptedCircuitIdentity']['proposalHash'] == authority['acceptedInterfaces']['proposalHash']
    assert fingerprint({key: value for key, value in manifest.items() if key != 'manifestHash'}) == manifest['manifestHash']
    frozen = copy.deepcopy(manifest)
    with store.transaction() as session:
        project = session.get(Project, scope.project_id)
        project.advance_revision()
        project.data = {**project.data, 'interfaces': {}, 'acceptedInterfaces': None, 'acceptedCircuitIdentity': None}
    assert service.release(scope.actor, scope.project_id, release_id) == frozen


@pytest.mark.parametrize('field', ['interfaces', 'acceptedInterfaces', 'acceptedCircuitIdentity'])
@pytest.mark.parametrize('stage', ['run', 'accept', 'replay'])
async def test_same_revision_authority_change_cannot_reuse_check_or_acceptance(approved, field, stage):
    store, scope, ids, _ = approved
    calls = []
    service = ValidationService(store, registry(checker(calls)))
    candidate = service.prepare(scope, ids, 'digital', 'prepare')
    if stage == 'accept':
        await service.run(scope, candidate)
        finish(store, scope)
    with store.transaction() as session:
        project = session.get(Project, scope.project_id)
        project.data = {**project.data, field: {'changedWithoutRevision': True}}
        assert project.context_revision == scope.revision
    with pytest.raises(DomainError, match='interface authority changed'):
        if stage == 'run':
            await service.run(scope, candidate)
        elif stage == 'accept':
            service.accept(scope.actor, scope.project_id, candidate, scope.revision, 'accept')
        else:
            service.prepare(scope, ids, 'digital', 'prepare')
    assert len(calls) == (1 if stage == 'accept' else 0)


@pytest.mark.parametrize('change', ['remove', 'replace'])
async def test_new_candidate_cannot_be_downgraded_to_unbound_legacy_subject(approved, change):
    store, scope, ids, _ = approved
    service = ValidationService(store, registry(checker([])))
    candidate = service.prepare(scope, ids, 'digital', 'prepare')
    with store.transaction() as session:
        row = session.get(Candidate, candidate)
        data = copy.deepcopy(row.data)
        if change == 'remove':
            data.pop('interfaceAuthority')
        else:
            data['interfaceAuthority']['acceptedInterfaces']['proposalHash'] = 'f'*64
        row.data = data
    with pytest.raises(DomainError, match='subject binding changed'):
        await service.run(scope, candidate)


async def test_legacy_candidate_keeps_original_subject_schema_and_release_shape(approved):
    store, scope, ids, _ = approved
    calls = []
    service = ValidationService(store, registry(checker(calls)))
    candidate = service.prepare(scope, ids, 'digital', 'prepare')
    # Represent a persisted pre-binding candidate with its original valid hash,
    # rather than changing a new candidate while keeping a new-format hash.
    with store.transaction() as session:
        row = session.get(Candidate, candidate)
        data = copy.deepcopy(row.data)
        data.pop('interfaceAuthority')
        row.data = data
        row.subject_hash = fingerprint(subject_data(row))
        legacy_hash = row.subject_hash
    assert service.prepare(scope, ids, 'digital', 'prepare') == candidate
    await service.run(scope, candidate)
    assert calls[0].subject_hash == legacy_hash
    finish(store, scope)
    release = service.accept(scope.actor, scope.project_id, candidate, scope.revision, 'accept')
    assert 'interfaceAuthority' not in service.release(scope.actor, scope.project_id, release['releaseId'])


async def test_authority_change_during_check_retains_observation_without_qualification(approved):
    store, scope, ids, _ = approved
    async def changed(scope, subject):
        with store.transaction() as session:
            project = session.get(Project, scope.project_id)
            project.data = {**project.data, 'acceptedInterfaces': None}
        return CheckReport(subject_hash=subject.subject_hash, status='PASS', execution_id='independent-fixture',
                           summary='Observed before authority drift was detected')
    service = ValidationService(store, registry(changed))
    candidate = service.prepare(scope, ids, 'digital', 'prepare')
    with pytest.raises(DomainError, match='interface authority changed'):
        await service.run(scope, candidate)
    assert service.evidence(scope.actor, scope.project_id, candidate)[0]['status'] == 'PASS'
    with store.transaction() as session:
        assert session.get(Candidate, candidate).status != 'qualified'
    finish(store, scope)
    with pytest.raises(DomainError, match='interface authority changed'):
        service.accept(scope.actor, scope.project_id, candidate, scope.revision, 'accept')
