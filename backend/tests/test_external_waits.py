"""Durable external correlation fixtures; no model, commercial or hardware calls."""
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from kleos.capabilities.external import ExternalJobs, ExternalRequest, ProviderObservation
from kleos.contracts.models import Actor, DomainError, TaskResult, TaskSpec
from kleos.infrastructure.schema import metadata
from kleos.orchestration.engine import RunScope
from kleos.orchestration.external_waits import ExternalWait, ExternalWaitEvent, ExternalWaits
from kleos.projects.graph_repository import GraphRepository
from kleos.projects.store import Event, Membership, Objective, Project, ProjectStore


class ReadProvider:
    id, version, kind, effect_capable = 'fixture', '1', 'sourcing', False
    def __init__(self):
        self.calls = 0
        self.result = ProviderObservation(state='waiting', external_id='owned', ownership_verified=True)
        self.during = None
    async def status(self, external_id):
        assert external_id == 'owned'
        self.calls += 1
        if self.during:
            self.during()
        return self.result


@pytest.fixture
async def waiting(tmp_path):
    store = ProjectStore('sqlite:///'+str(tmp_path/'wait.db'))
    metadata().create_all(store.engine)
    actor = Actor(tenant_id='tenant', user_id='owner')
    project = store.create(actor, 'Find the missing connector', 'create')['projectId']
    submitted = store.submit(actor, project, 'Observe the existing scoped external work', 1, 'run')
    clock = [datetime.now(UTC).timestamp()]
    scope = RunScope(actor=actor, project_id=project, run_id=submitted['runId'], revision=2,
        deadline_at=datetime.fromtimestamp(clock[0], UTC)+timedelta(days=7))
    task = TaskSpec(id='observe', capability='sourcing', objective='Observe this existing external job')
    repo = GraphRepository(store)
    await repo.publish_plan(scope, tasks=[task], superseded={}, turn=0, summary='Observe', question=None)
    provider = ReadProvider()
    jobs = ExternalJobs(store, {('tenant', 'owner', 'fixture'): provider},
        installation_pins={('tenant', 'owner', 'fixture'): 'a'*64}, clock=lambda: clock[0])
    request = ExternalRequest(project_id=project, revision=2, kind='sourcing', provider='fixture',
        objective='Existing owned supplier inquiry', bom=[{'reference': 'J1', 'supplierPart': 'ABC', 'quantity': 1}])
    job = jobs.create(actor, request, 'job')['job']['id']
    await jobs.observe_existing(actor, job, 'owned', 'link')
    provider.calls = 0
    with store.transaction() as session:
        run = session.get(Objective, scope.run_id)
        run.data = {**run.data, 'scope': scope.model_dump(mode='json'),
            'runtimePins': {'external': [{'provider': 'fixture', 'installationHash': 'a'*64}],
                            'modelPolicy': {'maxCalls': 5, 'maxCostMicroUsd': 100}}}
    bridge = ExternalWaits(store, repo, external_jobs=jobs, clock=lambda: clock[0])
    ident = await bridge.bind(scope, task, 'sourcing', job)
    await repo.publish(scope, TaskResult(task_id=task.id, status='waiting', summary='Waiting for observed external work', wait_ids=[ident]))
    with store.transaction() as session:
        run = session.get(Objective, scope.run_id)
        run.status = 'waiting'
        run.data = {**run.data, 'interruptId': 'interrupt-fixture',
                    'question': {'kind': 'external_wait', 'waitIds': [ident]}}
    yield store, repo, bridge, jobs, provider, scope, task, ident, clock
    store.engine.dispose()


async def test_noop_polls_hold_no_transaction_and_never_wake_graph(waiting):
    store, _, bridge, _, provider, scope, _, ident, clock = waiting
    def during():
        # A second write transaction during the mocked GET proves no DB lock spans IO.
        with store.transaction() as session:
            project = session.get(Project, scope.project_id)
            store._emit(session, project, 'fixture.read', {})
    provider.during = during
    for _ in range(3):
        clock[0] += 61
        result = await bridge.poll_due()
        assert result == {'polled': 1, 'changed': 0, 'errors': 0, 'woken': 0}
    assert provider.calls == 3
    with store.transaction() as session:
        assert session.get(Objective, scope.run_id).status == 'waiting'
        assert 'pendingAnswer' not in session.get(Objective, scope.run_id).data
        assert session.get(ExternalWait, ident).status == 'waiting'
        assert list(session.scalars(select(ExternalWaitEvent))) == []


async def test_restart_terminal_event_dedup_and_controlled_same_task_resume(waiting):
    store, repo, old, jobs, provider, scope, task, ident, clock = waiting
    bridge = ExternalWaits(store, repo, external_jobs=jobs, clock=lambda: clock[0])
    provider.result = ProviderObservation(state='completed', external_id='owned', ownership_verified=True)
    clock[0] += 61
    assert (await bridge.poll_due())['woken'] == 1
    assert bridge.wake_ready() == 0
    with store.transaction() as session:
        run = session.get(Objective, scope.run_id)
        answer = run.data['pendingAnswer']['value']
        original_scope, original_pins = run.data['scope'], run.data['runtimePins']
        sequences = [row.sequence for row in session.scalars(select(Event).where(Event.project_id == scope.project_id))
                     if row.data['type'] == 'external.observed']
    for sequence in reversed(sequences):
        assert bridge.ingest(scope.project_id, sequence) == 0
    results = await bridge.resolve(scope, [ident], answer)
    assert len(results) == 1 and results[0].task_id == task.id
    assert results[0].status.value == 'blocked' and not results[0].wait_ids
    assert all(not item.independent and item.status.value == 'UNKNOWN' for item in results[0].observations)
    assert await old.resolve(scope, [ident], answer) == results
    with store.transaction() as session:
        run = session.get(Objective, scope.run_id)
        assert run.data['scope'] == original_scope and run.data['runtimePins'] == original_pins
        assert session.get(ExternalWait, ident).status == 'consumed'
        assert len(list(session.scalars(select(ExternalWaitEvent)))) == 1
    with pytest.raises(DomainError, match='not bound'):
        await bridge.resolve(scope, [ident], {'kind': 'external_wait', 'eventIds': ['f'*64]})


@pytest.mark.parametrize('change,code', [('context', 'stale_context'), ('account', 'provider_changed'),
    ('cancel', 'run_inactive'), ('role', 'forbidden'), ('pins', 'scope_conflict')])
async def test_context_account_cancellation_and_authority_fence_poll_before_io(waiting, change, code):
    store, _, bridge, jobs, provider, scope, _, ident, clock = waiting
    with store.transaction() as session:
        if change == 'context':
            session.get(Project, scope.project_id).advance_revision()
        elif change == 'account':
            jobs.installation_pins[('tenant', 'owner', 'fixture')] = 'b'*64
        elif change == 'cancel':
            session.get(Objective, scope.run_id).status = 'cancelled'
        elif change == 'role':
            session.get(Membership, (scope.project_id, scope.actor.user_id)).role = 'viewer'
        else:
            run = session.get(Objective, scope.run_id)
            run.data = {**run.data, 'runtimePins': {'modelPolicy': {'maxCalls': 999}}}
    clock[0] += 61
    await bridge.poll_due()
    assert provider.calls == 0
    with store.transaction() as session:
        wait = session.get(ExternalWait, ident)
        assert wait.status == 'blocked' and wait.data['blocker'] == code
        assert session.get(Objective, scope.run_id).status == ('cancelled' if change == 'cancel' else 'blocked')


async def test_timer_expiry_is_deterministic_without_provider_call_or_budget_reset(waiting):
    store, _, bridge, _, provider, scope, _, ident, clock = waiting
    clock[0] += 86401
    result = await bridge.poll_due()
    assert result['woken'] == 1 and provider.calls == 0
    with store.transaction() as session:
        answer = session.get(Objective, scope.run_id).data['pendingAnswer']['value']
    results = await bridge.resolve(scope, [ident], answer)
    assert results[0].observations[-1].details['status'] == 'expired'
    assert (await bridge.poll_due())['woken'] == 0


async def test_account_change_during_lookup_cannot_publish_resume(waiting):
    store, _, bridge, jobs, provider, scope, _, ident, clock = waiting
    provider.during = lambda: jobs.installation_pins.update({('tenant', 'owner', 'fixture'): 'b'*64})
    clock[0] += 61
    await bridge.poll_due()
    with store.transaction() as session:
        assert session.get(ExternalWait, ident).status == 'blocked'
        assert 'pendingAnswer' not in session.get(Objective, scope.run_id).data


async def test_bridge_read_does_not_take_existing_external_job_lock(waiting):
    store, _, bridge, jobs, _, scope, _, ident, _ = waiting
    def forbidden(*args, **kwargs):
        pytest.fail('Bridge must not call job-locking _load while holding project lock')
    jobs._load = forbidden
    assert bridge._refresh(ident) is False
    await bridge.validate(scope, [ident])
    with store.transaction() as session:
        assert session.get(Objective, scope.run_id).status == 'waiting'


async def test_changed_implementation_and_resolved_artifact_provenance_fail_closed(waiting):
    from kleos.artifacts.service import Artifact
    from kleos.contracts.models import ArtifactRef
    store, _, bridge, _, provider, scope, _, ident, clock = waiting
    provider.result = ProviderObservation(state='completed', external_id='owned', ownership_verified=True)
    clock[0] += 61
    await bridge.poll_due()
    with store.transaction() as session:
        run = session.get(Objective, scope.run_id)
        answer = run.data['pendingAnswer']['value']
        ref = ArtifactRef(id='kept', path='kept.txt', sha256='b'*64, size=1, revision=2, media_type='text/plain')
        session.add(Artifact(id=ref.id, project_id=scope.project_id, revision=2, name=ref.path,
            sha256='c'*64, size=1, media_type=ref.media_type, status='candidate', lane='research', details={}))
        saved = dict(run.data['results'])
        saved['observe'] = {**saved['observe'], 'artifacts': [ref.model_dump(mode='json')]}
        run.data = {**run.data, 'results': saved}
    with pytest.raises(DomainError, match='provenance changed'):
        await bridge.resolve(scope, [ident], answer)
    bridge.implementation_hash = 'f'*64
    with pytest.raises(DomainError, match='implementation changed'):
        await bridge.validate(scope, [ident])


async def test_ready_observation_changed_after_deadline_cannot_bypass_resume_binding(waiting):
    from kleos.capabilities.external import ExternalJob
    store, _, bridge, _, provider, scope, _, ident, clock = waiting
    provider.result = ProviderObservation(state='completed', external_id='owned', ownership_verified=True)
    clock[0] += 61
    await bridge.poll_due()
    with store.transaction() as session:
        answer = session.get(Objective, scope.run_id).data['pendingAnswer']['value']
        job_id = session.get(ExternalWait, ident).job_id
        session.get(ExternalJob, job_id).status = 'cancelled'
    clock[0] += 86401
    with pytest.raises(DomainError, match='state changed'):
        await bridge.resolve(scope, [ident], answer)
    with store.transaction() as session:
        assert session.get(ExternalWait, ident).status == 'ready'
        assert session.get(Objective, scope.run_id).data['results']['observe']['status'] == 'waiting'


async def test_wait_deadline_preempts_later_provider_poll_schedule(waiting):
    store, _, bridge, _, provider, scope, _, ident, clock = waiting
    provider.result = ProviderObservation(state='waiting', external_id='owned', ownership_verified=True, retry_after_seconds=3600)
    clock[0] += 86400-60
    assert (await bridge.poll_due())['woken'] == 0
    assert provider.calls == 1
    with store.transaction() as session:
        wait = session.get(ExternalWait, ident)
        assert wait.next_poll_at > wait.deadline_at
    clock[0] += 61
    assert (await bridge.poll_due())['woken'] == 1 and provider.calls == 1
    with store.transaction() as session:
        answer = session.get(Objective, scope.run_id).data['pendingAnswer']['value']
    result = (await bridge.resolve(scope, [ident], answer))[0]
    assert result.observations[-1].details['status'] == 'expired'
