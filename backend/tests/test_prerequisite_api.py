"""Existing HTTP chat -> durable graph interrupt -> exact interface approval.

Models and installation metadata are fixtures; no native/provider work runs.
"""
from types import SimpleNamespace

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from test_interfaces import Model
from test_orchestration import Planner
from test_worker import FixtureLocks, Specialist

from kleos.access.service import AccessService, AccessSettings
from kleos.api.app import create_app
from kleos.api.interfaces import InterfaceFacade
from kleos.api.prerequisites import PrerequisiteFacade
from kleos.api.services import ApplicationServices
from kleos.contracts.models import Actor, DomainError, TaskSpec
from kleos.orchestration.engine import ObjectiveEngine, PlanDecision
from kleos.orchestration.prerequisites import InterfacePrerequisites
from kleos.orchestration.worker import ObjectiveWorker
from kleos.projects.graph_repository import GraphRepository, execution_owner
from kleos.projects.interfaces import InterfaceService
from kleos.projects.prerequisites import PrerequisiteService
from kleos.projects.store import Membership, Objective, Project, ProjectStore


@pytest.fixture
async def api(tmp_path):
    projects = ProjectStore(f'sqlite:///{tmp_path / "prerequisite-api.db"}', require_account_authority=True)
    projects.initialize_for_tests()
    access = AccessService(projects, AccessSettings(secret_key='f'*40, environment='test', development_no_login=True))
    actor = access.authenticate({}, {}, 'GET')
    project = projects.create(actor, 'Implement only existing board firmware', 'create')['projectId']
    admitted = projects.submit(actor, project, 'Add the missing firmware', 1, 'start')
    repository = GraphRepository(projects)
    interfaces = InterfaceService(projects)
    service = PrerequisiteService(projects, interfaces, repository)
    model = Model(output={'summary': 'Review this explicit board before firmware work.',
        'interfaces': {'firmware': {'board': 'nrf52840dk/nrf52840'}}, 'sourceIds': ['brief'],
        'limitations': ['Fixture proposal; device behavior remains unverified.']})
    planner = Planner([PlanDecision(tasks=[TaskSpec(id='firmware', capability='firmware',
        objective='Only the missing firmware', allowed_paths=['firmware/src/main.c'])]), PlanDecision(done=True)])
    specialist, saver = Specialist(), InMemorySaver()
    bridge = InterfacePrerequisites(repository, service, lambda scope, context: model)
    def factory(scope):
        # Trusted fixture composition binds installation metadata before graph IO.
        # This does not install or execute a firmware image.
        with projects.transaction() as session:
            run = session.get(Objective, scope.run_id)
            if 'runtimePins' not in run.data:
                run.data = {**run.data, 'runtimePins': {'native': [{'capability': 'firmware',
                    'firmwareBoard': 'nrf52840dk/nrf52840', 'installationHash': 'a'*64}]}}
        return ObjectiveEngine(repository, planner, specialist, checkpointer=saver, prerequisites=bridge)
    worker = ObjectiveWorker(projects, factory, locks=FixtureLocks(), heartbeat_seconds=.01)
    facade = PrerequisiteFacade(service)
    services = ApplicationServices(projects, access, worker=worker, interfaces=InterfaceFacade(interfaces), prerequisites=facade)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(services)), base_url='http://local') as client:
        yield SimpleNamespace(projects=projects, actor=actor, project=project, run=admitted['runId'],
            worker=worker, service=service, facade=facade, services=services, model=model, planner=planner,
            specialist=specialist, client=client)
    projects.engine.dispose()


async def workspace(api):
    response = await api.client.get(f'/api/v1/projects/{api.project}/workspace')
    assert response.status_code == 200, response.text
    return response.json()


async def message(api, text, key, *, revision=None, **extra):
    revision = (await workspace(api))['revision'] if revision is None else revision
    return await api.client.post('/api/v1/commands', headers={'Idempotency-Key': key}, json={
        'type': 'project.message', 'expectedRevision': revision,
        'payload': {'projectId': api.project, 'text': text, 'autoResume': True, **extra}})


async def wait(api):
    assert await api.worker.process(api.run)
    with api.projects.transaction() as session:
        run = session.get(Objective, api.run)
        assert run.status == 'waiting', run.data.get('blocker')
        assert run.data['question']['kind'] == 'interface_prerequisite'
        assert run.data.get('interruptId')
    assert api.specialist.calls == 0


async def test_existing_message_approves_unique_durable_proposal_and_replays_after_resume(api):
    await wait(api)
    current = await workspace(api)
    assert current['project']['interfaces'] == {}
    assert 'approve interfaces' in current['project']['state']['conversation'][-1]['text']
    before_calls = api.model.calls
    approved = await message(api, 'approve interfaces', 'approve', revision=current['revision'])
    assert approved.status_code == 202, approved.text
    assert approved.json()['runId'] == api.run
    assert approved.json()['revision'] > current['revision']
    assert api.model.calls == before_calls
    assert await api.worker.process(api.run)
    api.services.prerequisites = PrerequisiteFacade(PrerequisiteService(api.projects, InterfaceService(api.projects)))
    replay = await message(api, 'approve interfaces', 'approve', revision=current['revision'])
    assert replay.status_code == 202 and replay.json() == approved.json()
    assert api.specialist.calls == 1 and api.model.calls == before_calls
    with api.projects.transaction() as session:
        project, run = session.get(Project, api.project), session.get(Objective, api.run)
        assert project.data['interfaces']['firmware'] == {'board': 'nrf52840dk/nrf52840'}
        assert not project.data.get('acceptedReleaseId') and run.status == 'succeeded'


async def test_generic_yes_refines_without_approval_and_keeps_original_run(api):
    await wait(api)
    first = api.service.pending(api.actor, api.project)
    reply = await message(api, 'yes', 'generic-yes')
    assert reply.status_code == 202, reply.text
    assert reply.json()['runId'] == api.run
    with api.projects.transaction() as session:
        assert session.get(Project, api.project).data['interfaces'] == {}
    assert await api.worker.process(api.run)
    second = api.service.pending(api.actor, api.project)
    assert second['requestId'] != first['requestId']
    assert api.model.calls == 2 and api.specialist.calls == 0


async def test_refinement_rounds_are_bounded_and_exhaustion_does_not_approve(api):
    await wait(api)
    for index in range(2):
        response = await message(api, 'Please clarify board selection '+str(index), 'refine-'+str(index))
        assert response.status_code == 202, response.text
        assert await api.worker.process(api.run)
    rejected = await message(api, 'Another clarification', 'exhausted')
    assert rejected.status_code == 409 and rejected.json()['code'] == 'prerequisite_budget_exhausted'
    assert api.model.calls == 3 and api.specialist.calls == 0
    with api.projects.transaction() as session:
        assert session.get(Project, api.project).data['interfaces'] == {}
        assert session.get(Objective, api.run).status == 'waiting'


@pytest.mark.parametrize('extra', [{'referenceImageIds': ['new-image']}, {'selectedPartId': 'part'}, {'conceptVersion': 2}])
async def test_mixed_new_context_cannot_approve_or_fall_through_to_another_objective(api, extra):
    await wait(api)
    answer = await message(api, 'approve interfaces', 'mixed', **extra)
    assert answer.status_code == 409, answer.text
    with api.projects.transaction() as session:
        assert session.get(Project, api.project).data['interfaces'] == {}
        assert session.get(Objective, api.run).status == 'waiting'
    assert api.specialist.calls == 0 and api.model.calls == 1


async def test_approval_before_worker_persists_exact_interrupt_is_rejected(api):
    original, errors = api.service.publish, []
    def publication(scope, request_id, result):
        payload = original(scope, request_id, result)
        with api.projects.transaction() as session:
            revision = api.projects.authorized(session, api.actor, api.project).revision
        # Represent a separate HTTP caller, not a command issued by the worker.
        token = execution_owner.set(None)
        try:
            with pytest.raises(DomainError) as error:
                api.facade.dispatch(api.actor, {'type': 'project.message', 'expectedRevision': revision,
                    'payload': {'projectId': api.project, 'text': 'approve interfaces'}}, 'too-early')
        finally:
            execution_owner.reset(token)
        errors.append(error.value.code)
        return payload
    api.service.publish = publication
    await wait(api)
    assert errors == ['run_conflict'] and api.model.calls == 1
    with api.projects.transaction() as session:
        assert session.get(Project, api.project).data['interfaces'] == {}


async def test_generic_objective_answer_cannot_bypass_bound_proposal_approval(api):
    await wait(api)
    current = await workspace(api)
    with api.projects.transaction() as session:
        ident = session.get(Objective, api.run).data['interruptId']
    answer = await api.client.post('/api/v1/commands', headers={'Idempotency-Key': 'generic-answer'}, json={
        'type': 'project.objective.answer', 'expectedRevision': current['revision'], 'payload': {
            'projectId': api.project, 'runId': api.run, 'interruptId': ident, 'answer': 'approve interfaces'}})
    assert answer.status_code == 409
    with api.projects.transaction() as session:
        assert session.get(Project, api.project).data['interfaces'] == {}


async def test_stale_revision_changed_replay_and_revoked_writer_are_fenced(api):
    await wait(api)
    current = await workspace(api)
    stale = await message(api, 'approve interfaces', 'stale', revision=current['revision']-1)
    assert stale.status_code == 409
    accepted = await message(api, 'approve interfaces', 'approve', revision=current['revision'])
    assert accepted.status_code == 202, accepted.text
    changed = await message(api, 'Do something else', 'approve', revision=current['revision'])
    assert changed.status_code == 409
    with api.projects.transaction() as session:
        session.get(Membership, (api.project, api.actor.user_id)).role = 'viewer'
    revoked = await message(api, 'approve interfaces', 'approve', revision=current['revision'])
    assert revoked.status_code in {403, 404}
    with pytest.raises(DomainError):
        api.facade.dispatch(Actor(tenant_id='foreign', user_id=api.actor.user_id), {
            'type': 'project.message', 'expectedRevision': current['revision'],
            'payload': {'projectId': api.project, 'text': 'approve interfaces'}}, 'foreign')


@pytest.mark.parametrize('changed_context', [False, True])
async def test_restart_after_scope_checkpoint_before_approval_resolution_rechecks_receipt(api, changed_context):
    await wait(api)
    approved = await message(api, 'approve interfaces', 'approve')
    assert approved.status_code == 202, approved.text
    scope, _, answer, token = api.worker._begin(api.run)
    engine = api.worker.engine_factory(scope)
    original = api.service.resolve
    def lost_worker(*args):
        raise RuntimeError('Trusted fixture process loss before consuming approval')
    api.service.resolve = lost_worker
    owner = execution_owner.set((api.run, token))
    try:
        with pytest.raises(RuntimeError, match='process loss'):
            await engine.resume(scope, answer={answer['interruptId']: answer['value']})
    finally:
        execution_owner.reset(owner)
        api.service.resolve = original
    state = await engine.graph.aget_state(engine._config(scope))
    assert state.values['scope'] == scope.model_dump(mode='json')
    assert api.specialist.calls == 0
    if changed_context:
        with api.projects.transaction() as session:
            project = session.get(Project, api.project)
            project.data = {**project.data, 'interfaces': {'firmware': {'board': 'different'}}}
    assert await api.worker.process(api.run)
    with api.projects.transaction() as session:
        run = session.get(Objective, api.run)
        assert run.status == ('blocked' if changed_context else 'succeeded')
    assert api.specialist.calls == (0 if changed_context else 1)
    assert api.model.calls == 1


def test_unrelated_commands_and_generic_explicit_answers_keep_their_original_route():
    class Service:
        def dispatch_message(self, *args):
            raise AssertionError('Unrelated route must not call prerequisite service')
    facade = PrerequisiteFacade(Service())
    actor = Actor(tenant_id='t', user_id='u')
    assert facade.dispatch(actor, {'type': 'project.solveInterfaces', 'payload': {}}, 'k') is None
    assert facade.dispatch(actor, {'type': 'project.message', 'payload': {'runId': 'r'}}, 'k') is None


@pytest.mark.parametrize('project_id', [[], {}, '', 'x'*37])
async def test_malformed_project_identity_is_rejected_before_database_lookup(api, project_id):
    answer = await api.client.post('/api/v1/commands', headers={'Idempotency-Key': 'invalid-project'}, json={
        'type': 'project.message', 'payload': {'projectId': project_id, 'text': 'approve interfaces'}, 'expectedRevision': 1})
    assert answer.status_code == 422 and answer.json()['code'] == 'invalid_request'
