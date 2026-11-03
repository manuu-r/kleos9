import copy
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from kleos.contracts.models import Actor, DomainError
from kleos.projects.graph_repository import execution_owner
from kleos.projects.store import Event, Membership, Objective, Project, ProjectStore, Receipt
from kleos.projects.views import project_view
from kleos.projects.workbench_progress import MAX_ASSEMBLY_STEP, WorkbenchProgress


@pytest.fixture
def fixture(tmp_path):
    store = ProjectStore(f'sqlite:///{tmp_path}/workbench.db')
    store.initialize_for_tests()
    actor = Actor(tenant_id='tenant', user_id='owner')
    project = store.create(actor, 'Workbench fixture', 'create')['projectId']
    with store.transaction() as session:
        row = session.get(Project, project)
        row.data = {**row.data, 'displayStage': 8, 'status': 'ready', 'sourceManifest': {'model.py': 'source-id'},
                    'acceptedReleaseId': 'existing-release', 'requirements': {'items': ['Preserve']},
                    'interfaces': {'circuit': {'preserve': True}}, 'approvedDesign': {'preserve': True},
                    'state': {'planning': {'pending': True, 'runId': 'ongoing'},
                              'shipment': {'status': 'blocked'}, 'kit': {'status': 'unavailable'}}}
    service = WorkbenchProgress(store, clock=lambda: datetime(2026, 11, 3, tzinfo=UTC))
    yield store, actor, project, service
    store.engine.dispose()


def body(project, kind, value, revision=1):
    field = 'mode' if kind == 'selectPrint' else 'step'
    return {'type': 'project.'+kind, 'payload': {'projectId': project, field: value}, 'expectedRevision': revision}


def test_home_preference_does_not_require_or_dispatch_a_printer(fixture):
    store, actor, project, service = fixture
    with store.transaction() as session:
        before = copy.deepcopy(session.get(Project, project).data)
    receipt = service.command(actor, body(project, 'selectPrint', 'home'), 'home')
    assert set(receipt) == {'commandId', 'projectId', 'revision'}
    assert receipt['revision'] == 2
    with store.transaction() as session:
        row = session.get(Project, project)
        view = project_view(row)
        assert view['state']['printMode'] == 'home'
        record = view['state']['printPreference']
        assert record['evidenceKind'] == 'user_preference' and record['mode'] == 'home'
        assert not record['printingStarted'] and not record['ordersPlaced'] and not record['physicalAcceptance']
        assert row.context_revision == view['engineeringRevision'] == 1
        for key in ('sourceManifest', 'acceptedReleaseId', 'requirements', 'interfaces', 'approvedDesign', 'displayStage', 'status'):
            assert row.data[key] == before[key]
        for key in ('planning', 'shipment', 'kit'):
            assert row.data['state'][key] == before['state'][key]
        assert list(session.scalars(select(Objective))) == []
        assert [e.data['type'] for e in session.scalars(select(Event).order_by(Event.sequence))] == [
            'project.created', 'project.printPreferenceRecorded']


def test_replay_after_other_command_preserves_current_choice_and_receipt(fixture):
    store, actor, project, service = fixture
    home = body(project, 'selectPrint', 'home')
    first = service.command(actor, home, 'home')
    service.command(actor, body(project, 'selectPrint', 'order', 2), 'order')
    assert service.command(actor, home, 'home') == first
    with store.transaction() as session:
        row = session.get(Project, project)
        assert row.revision == 3 and row.data['state']['printMode'] == 'order'
        assert row.data['state']['printPreference']['ordersPlaced'] is False
        assert len(list(session.scalars(select(Event)))) == 3
        assert len(list(session.scalars(select(Receipt)))) == 3
    with pytest.raises(DomainError, match='different input'):
        service.command(actor, body(project, 'selectPrint', 'order'), 'home')


def test_assembly_records_zero_forward_back_and_same_step_without_completion(fixture):
    store, actor, project, service = fixture
    revision = 1
    for step in [0, 1, 2, 1, 1, 2, 3, 4, 5, 6]:
        receipt = service.command(actor, body(project, 'assemblyStep', step, revision), 'step-'+str(revision))
        revision = receipt['revision']
    with store.transaction() as session:
        row = session.get(Project, project)
        state = project_view(row)['state']
        assert state['assemblyStep'] == 6
        assert state['assemblyProgress']['evidenceKind'] == 'synthetic'
        assert state['assemblyProgress']['status'] == 'simulated'
        assert state['assemblyProgress']['assemblyCompletionStatus'] == 'UNKNOWN'
        assert state['assemblyProgress']['physicalAcceptance'] is False
        assert state['assemblyProgress']['activationStarted'] is False
        assert not {'assemblyDone', 'guideStep', 'activation', 'physical_acceptance'} & state.keys()
        assert row.context_revision == row.data['engineeringRevision'] == 1
        assert row.data['displayStage'] == 8 and row.data['acceptedReleaseId'] == 'existing-release'
        assert list(session.scalars(select(Objective))) == []


@pytest.mark.parametrize('kind,value', [('selectPrint', None), ('selectPrint', 'print'), ('selectPrint', True),
    ('assemblyStep', True), ('assemblyStep', 0.0), ('assemblyStep', '0'), ('assemblyStep', -1),
    ('assemblyStep', MAX_ASSEMBLY_STEP+1)])
def test_invalid_payload_types_and_bounds_have_no_effect(fixture, kind, value):
    store, actor, project, service = fixture
    with pytest.raises(DomainError) as error:
        service.command(actor, body(project, kind, value), 'invalid')
    assert error.value.status == 422
    with store.transaction() as session:
        assert session.get(Project, project).revision == 1
        assert len(list(session.scalars(select(Event)))) == 1


@pytest.mark.parametrize('revision', [None, True, 1.0, 0, -1, '1'])
def test_invalid_expected_revision_is_rejected(fixture, revision):
    _, actor, project, service = fixture
    with pytest.raises(DomainError) as error:
        service.command(actor, body(project, 'selectPrint', 'home', revision), 'bad-revision')
    assert error.value.status == 422


def test_initial_skips_and_stale_revisions_cannot_record_progress(fixture):
    store, actor, project, service = fixture
    with pytest.raises(DomainError, match='adjacent'):
        service.command(actor, body(project, 'assemblyStep', 1), 'skip')
    first = service.command(actor, body(project, 'assemblyStep', 0), 'zero')
    with pytest.raises(DomainError, match='refresh'):
        service.command(actor, body(project, 'assemblyStep', 1), 'stale')
    with pytest.raises(DomainError, match='adjacent'):
        service.command(actor, body(project, 'assemblyStep', 2, first['revision']), 'skip-two')
    with store.transaction() as session:
        assert session.get(Project, project).data['state']['assemblyStep'] == 0


def test_write_authority_is_checked_on_new_commands_and_receipt_replay(fixture):
    store, actor, project, service = fixture
    request = body(project, 'selectPrint', 'home')
    service.command(actor, request, 'choice')
    with pytest.raises(DomainError):
        service.command(Actor(tenant_id='other', user_id=actor.user_id), request, 'choice')
    with pytest.raises(DomainError):
        service.command(Actor(tenant_id=actor.tenant_id, user_id='stranger'), request, 'other')
    with store.transaction() as session:
        session.get(Membership, (project, actor.user_id)).role = 'viewer'
    with pytest.raises(DomainError, match='read-only'):
        service.command(actor, request, 'choice')
    with pytest.raises(DomainError, match='read-only'):
        service.command(actor, body(project, 'assemblyStep', 0, 2), 'new')


def test_worker_and_payload_authority_injection_are_rejected(fixture):
    _, actor, project, service = fixture
    owner = execution_owner.set(('run', 'worker'))
    try:
        with pytest.raises(DomainError, match='user command'):
            service.command(actor, body(project, 'selectPrint', 'home'), 'worker')
    finally:
        execution_owner.reset(owner)
    request = body(project, 'assemblyStep', 0)
    request['payload']['physicalAcceptance'] = True
    with pytest.raises(DomainError):
        service.command(actor, request, 'injection')


async def test_preferences_leave_actual_active_objective_scope_valid(fixture):
    from datetime import timedelta

    from kleos.orchestration.engine import RunScope
    from kleos.projects.graph_repository import GraphRepository

    store, actor, project, service = fixture
    submitted = store.submit(actor, project, 'Continue existing engineering', 1, 'engineering')
    scope = RunScope(actor=actor, project_id=project, run_id=submitted['runId'], revision=submitted['revision'],
                     deadline_at=datetime.now(UTC)+timedelta(minutes=5))
    repository = GraphRepository(store)
    await repository.assert_active(scope)
    service.command(actor, body(project, 'selectPrint', 'home', submitted['revision']), 'choice')
    service.command(actor, body(project, 'assemblyStep', 0, submitted['revision']+1), 'step')
    await repository.assert_active(scope)
    with store.transaction() as session:
        row, run = session.get(Project, project), session.get(Objective, scope.run_id)
        assert row.context_revision == run.revision == scope.revision
        assert row.data['sourceManifest'] == {'model.py': 'source-id'}
        assert len(list(session.scalars(select(Objective)))) == 1
        assert run.status == 'pending'
