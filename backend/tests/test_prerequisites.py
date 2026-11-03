"""Same-run canonical approval, bounded preparation and exact revision amendments."""
import copy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
import test_imported_interfaces as imported_fixtures
import test_interfaces as interface_fixtures
from test_interfaces import proposal

from kleos.contracts.models import Actor, DomainError, Observation, TaskResult, TaskSpec
from kleos.orchestration.engine import RunScope
from kleos.projects import compute
from kleos.projects.graph_repository import Effect, GraphRepository, execution_owner
from kleos.projects.interfaces import accepted_circuit_identity
from kleos.projects.prerequisites import PrerequisiteService
from kleos.projects.store import Membership, Objective, Project

interface_setup = interface_fixtures.setup
imported = imported_fixtures.imported


@pytest.fixture
def env(interface_setup):
    interfaces, actor, project_id = interface_setup
    store = interfaces.projects
    revision = store.context(actor, project_id).revision
    submitted = store.submit(actor, project_id, 'Build the requested PCB and firmware', revision, 'generic')
    scope = RunScope(actor=actor, project_id=project_id, run_id=submitted['runId'],
        revision=submitted['revision'], deadline_at=datetime.now(UTC)+timedelta(minutes=10))
    pins = {'native': [{'capability': 'pcb', 'image': 'fixture-pin'},
        {'capability': 'firmware', 'firmwareBoard': 'nrf52840dk/nrf52840'}],
        'models': [{'key': 'original-provider-fixture'}], 'maxActions': 8}
    with store.transaction() as session:
        run = session.get(Objective, scope.run_id)
        run.data = {**run.data, 'scope': scope.model_dump(mode='json'), 'runtimePins': pins,
                    'budgetSentinel': {'committedMicroUsd': 12345}}
    repo = GraphRepository(store)
    tasks = [TaskSpec(id='board', capability='pcb', objective='Route the imported board'),
             TaskSpec(id='firmware', capability='firmware', objective='Compile firmware')]
    service = PrerequisiteService(store, interfaces, repo)
    return SimpleNamespace(service=service, interfaces=interfaces, repo=repo, store=store,
        scope=scope, tasks=tasks, actor=actor, project_id=project_id, pins=pins)


async def begin(env):
    await env.repo.publish_plan(env.scope, tasks=env.tasks, superseded={}, turn=0,
        summary='Only requested engineering tasks', question=None)
    return env.service.inspect(env.scope, env.tasks)


def publish(env, pending, candidate=None):
    return env.service.publish(env.scope, pending['requestId'], TaskResult(task_id='candidate',
        status='succeeded', summary='Review configuration', observations=[Observation(
            name='interfaces.proposal', status='UNKNOWN', independent=False,
            details={'candidate': candidate or proposal()})]))


def wait(env, payload):
    with env.store.transaction() as session:
        run = session.get(Objective, env.scope.run_id)
        run.status = 'waiting'
        run.data = {**run.data, 'question': payload, 'interruptId': 'interrupt-1'}
        return session.get(Project, env.project_id).revision


def message(env, revision, text='approve interfaces'):
    return {'type': 'project.message', 'expectedRevision': revision,
        'payload': {'projectId': env.project_id, 'text': text, 'autoResume': True, 'clientMessageId': 'ui-message'}}


async def test_generic_request_approval_preserves_run_spend_pins_and_canonical_next_context(env):
    pending = await begin(env)
    assert pending['missing'] == ['circuit', 'firmware']
    assert env.service.inspect(env.scope, env.tasks) == pending
    packet = env.service.request(env.scope, pending['requestId'])
    assert packet['qualifiedFirmwareBoards'] == ['nrf52840dk/nrf52840']
    payload = publish(env, pending)
    assert env.service.wait_payload(env.scope, pending['requestId']) == payload
    revision = wait(env, payload)
    body = message(env, revision)
    receipt = env.service.dispatch_message(env.actor, body, 'approve')
    assert receipt['runId'] == env.scope.run_id and receipt['revision'] > revision
    assert env.service.dispatch_message(env.actor, body, 'approve') == receipt
    with env.store.transaction() as session:
        run = session.get(Objective, env.scope.run_id)
        current = RunScope.model_validate(run.data['scope'])
        assert current == env.scope.model_copy(update={'revision': run.revision})
        assert run.data['initialScope'] == env.scope.model_dump(mode='json')
        assert run.data['runtimePins'] == env.pins
        assert run.data['budgetSentinel'] == {'committedMicroUsd': 12345}
        assert len(run.data['prerequisites']) == len(run.data['scopeAmendments']) == 1
        answer = copy.deepcopy(run.data['pendingAnswer'])
        project = session.get(Project, env.project_id)
        assert project.data['interfaces']['firmware'] == {'board': 'nrf52840dk/nrf52840'}
        assert project.data['interfaces']['mechanical'] == {'units': 'mm', 'height': 20}
        assert accepted_circuit_identity(session, project)['peripheralConceptIds'] == ['power-input']
    interrupts = [SimpleNamespace(id='interrupt-1', value=payload)]
    assert env.service.validate_resume(env.scope, current, {'interrupt-1': answer['value']}, interrupts)
    assert env.service.resolve(current, payload, answer['value']) == {'status': 'approved'}
    assert env.service.inspect(current, env.tasks) is None
    assert env.store.context(env.actor, env.project_id).interfaces['firmware']['board'] == 'nrf52840dk/nrf52840'
    with pytest.raises(DomainError):
        await env.repo.assert_active(env.scope)


async def test_refinement_is_bounded_and_never_interprets_generic_yes_as_approval(env):
    pending = await begin(env)
    for index in range(3):
        payload = publish(env, pending)
        revision = wait(env, payload)
        if index == 2:
            with pytest.raises(DomainError) as failure:
                env.service.dispatch_message(env.actor, message(env, revision, 'yes'), f'refine-{index}')
            assert failure.value.code == 'prerequisite_budget_exhausted'
            break
        env.service.dispatch_message(env.actor, message(env, revision, 'yes'), f'refine-{index}')
        with env.store.transaction() as session:
            run = session.get(Objective, env.scope.run_id)
            answer = run.data['pendingAnswer']['value']
            assert session.get(Project, env.project_id).context_revision == env.scope.revision
            assert not session.get(Project, env.project_id).data.get('acceptedInterfaces')
        outcome = env.service.resolve(env.scope, payload, answer)
        assert outcome['status'] == 'revise'
        pending = outcome['pending']
        assert pending['round'] == index+2


@pytest.mark.parametrize('change', ['revision', 'interrupt', 'source', 'pins', 'member', 'active_grant', 'claimed_effect', 'initial_scope'])
async def test_approval_rechecks_current_authority_and_quiescence(env, change):
    payload = publish(env, await begin(env))
    revision = wait(env, payload)
    with env.store.transaction() as session:
        run = session.get(Objective, env.scope.run_id)
        project = session.get(Project, env.project_id)
        if change == 'revision':
            project.advance_revision()
        elif change == 'interrupt':
            run.data = {**run.data, 'question': {**payload, 'proposalHash': '0'*64}}
        elif change == 'source':
            project.data = {**project.data, 'requirements': {'changed': True}}
        elif change == 'pins':
            run.data = {**run.data, 'runtimePins': {'changed': True}}
        elif change == 'member':
            session.delete(session.get(Membership, (env.project_id, env.actor.user_id)))
        elif change == 'active_grant':
            run.data = {**run.data, 'activeBudget': {'lease': {'owner': 'still-active'}, 'limitMs': 10000, 'committedMs': 1}}
        elif change == 'claimed_effect':
            session.add(Effect(id=str(uuid4()), run_id=run.id, key='uncertain', status='claimed', value={}))
        else:
            run.data = {**run.data, 'initialScope': {**run.data['initialScope'], 'max_tasks': 128}}
    with pytest.raises(DomainError):
        env.service.dispatch_message(env.actor, message(env, revision), 'approval')
    with env.store.transaction() as session:
        assert not session.get(Project, env.project_id).data.get('acceptedInterfaces')


@pytest.mark.parametrize('change', ['deadline', 'budget', 'actor', 'reply', 'interrupt', 'initial_scope'])
async def test_revision_resume_cannot_expand_or_replace_original_scope(env, change):
    payload = publish(env, await begin(env))
    revision = wait(env, payload)
    env.service.dispatch_message(env.actor, message(env, revision), 'approval')
    with env.store.transaction() as session:
        run = session.get(Objective, env.scope.run_id)
        scope = RunScope.model_validate(run.data['scope'])
        answer = copy.deepcopy(run.data['pendingAnswer']['value'])
    interrupts = [SimpleNamespace(id='interrupt-1', value=payload)]
    if change == 'deadline':
        scope = scope.model_copy(update={'deadline_at': scope.deadline_at+timedelta(seconds=1)})
    elif change == 'budget':
        scope = scope.model_copy(update={'max_plan_calls': scope.max_plan_calls+1})
    elif change == 'actor':
        scope = scope.model_copy(update={'actor': scope.actor.model_copy(update={'user_id': 'other'})})
    elif change == 'reply':
        answer['proposalHash'] = '0'*64
    elif change == 'interrupt':
        interrupts[0].id = 'another-interrupt'
    else:
        with env.store.transaction() as session:
            run = session.get(Objective, env.scope.run_id)
            run.data = {**run.data, 'initialScope': {**run.data['initialScope'], 'max_tasks': 128}}
    with pytest.raises(DomainError):
        env.service.validate_resume(env.scope, scope, {'interrupt-1': answer}, interrupts)


async def test_candidate_cannot_select_unqualified_board_or_extra_interface(env):
    pending = await begin(env)
    candidate = proposal()
    candidate['interfaces']['firmware']['board'] = 'unqualified-board'
    with pytest.raises(DomainError) as failure:
        publish(env, pending, candidate)
    assert failure.value.code == 'firmware_target_required'
    token = execution_owner.set(('untrusted-model', 'token'))
    try:
        with pytest.raises(DomainError) as failure:
            env.service.reply(env.actor, env.project_id, 1, 'model-approve', text='approve interfaces')
        assert failure.value.code == 'approval_required'
    finally:
        execution_owner.reset(token)


async def test_unrelated_commands_keep_original_routing_and_owned_replay_checks_exact_body(env):
    body = message(env, env.scope.revision, 'Ordinary objective')
    assert env.service.dispatch_message(env.actor, body, 'ordinary') is None
    pending = await begin(env)
    payload = publish(env, pending)
    revision = wait(env, payload)
    approved = message(env, revision)
    env.service.dispatch_message(env.actor, approved, 'approve')
    with pytest.raises(DomainError) as failure:
        env.service.dispatch_message(env.actor, message(env, revision, 'different'), 'approve')
    assert failure.value.code == 'idempotency_conflict'


async def test_completed_read_only_research_packet_does_not_block_first_native_prerequisite(env):
    research = TaskSpec(id='research', capability='research', objective='Read imported source context')
    env.tasks = [research, *env.tasks]
    await env.repo.publish_plan(env.scope, tasks=env.tasks, superseded={}, turn=0,
        summary='Read context then build requested lanes', question=None)
    observed = TaskResult(task_id=research.id, status='succeeded', summary='Read-only context observed')
    await env.repo.publish(env.scope, observed)
    inputs = {'research': {'task': research.model_dump(mode='json'),
        'context': (await env.repo.context(env.scope)).model_dump(mode='json')}}
    pending = env.service.inspect(env.scope, env.tasks, {research.id: observed.model_dump(mode='json')}, inputs)
    assert pending['missing'] == ['circuit', 'firmware']
    inputs['board'] = {'task': env.tasks[1].model_dump(mode='json')}
    with pytest.raises(DomainError) as failure:
        env.service.inspect(env.scope, env.tasks, task_inputs=inputs)
    assert failure.value.code == 'prerequisite_not_quiescent'


async def test_approval_keeps_spent_active_compute_and_original_lifetime_between_worker_grants(env):
    env.scope = env.scope.model_copy(update={'compute_budget_seconds': 120})
    started = compute.now_ms()
    with env.store.transaction() as session:
        run = session.get(Objective, env.scope.run_id)
        run.data = {**run.data, 'scope': env.scope.model_dump(mode='json'), 'workerToken': 'before-approval'}
        compute.begin(run, env.scope, 'before-approval', now=started)
    token = execution_owner.set((env.scope.run_id, 'before-approval'))
    try:
        payload = publish(env, await begin(env))
    finally:
        execution_owner.reset(token)
    revision = wait(env, payload)
    with pytest.raises(DomainError, match='closed worker grant'):
        env.service.dispatch_message(env.actor, message(env, revision), 'approval')
    with env.store.transaction() as session:
        run = session.get(Objective, env.scope.run_id)
        compute.finish(run, env.scope, 'before-approval', now=started+1000)
        original_budget = copy.deepcopy(run.data['activeBudget'])
    env.service.dispatch_message(env.actor, message(env, revision), 'approval')
    with env.store.transaction() as session:
        run = session.get(Objective, env.scope.run_id)
        current = RunScope.model_validate(run.data['scope'])
        assert current.deadline_at == env.scope.deadline_at
        assert run.data['activeBudget'] == original_budget
        assert original_budget['committedMs'] == 1000 and original_budget['lease'] is None
        run.data = {**run.data, 'workerToken': 'after-approval'}
        compute.begin(run, current, 'after-approval')
        assert run.data['activeBudget']['committedMs'] == 31000
        assert run.data['activeBudget']['limitMs'] == 120000
        assert run.data['activeBudget']['deadlineMs'] == original_budget['deadlineMs']
        answer = run.data['pendingAnswer']['value']
    token = execution_owner.set((env.scope.run_id, 'after-approval'))
    try:
        assert env.service.validate_resume(env.scope, current, {'interrupt-1': answer},
            [SimpleNamespace(id='interrupt-1', value=payload)])
    finally:
        execution_owner.reset(token)


async def test_generic_imported_pcb_approval_retains_full_source_binding_without_concept(imported):
    interfaces, actor, project_id = imported
    store = interfaces.projects
    before = imported_fixtures.row(imported)
    original = copy.deepcopy(before.data['sourceManifest'])
    submitted = store.submit(actor, project_id, 'Route this existing board only', before.revision, 'generic')
    scope = RunScope(actor=actor, project_id=project_id, run_id=submitted['runId'], revision=submitted['revision'],
        deadline_at=datetime.now(UTC)+timedelta(minutes=5))
    with store.transaction() as session:
        run = session.get(Objective, scope.run_id)
        run.data = {**run.data, 'scope': scope.model_dump(mode='json'),
            'runtimePins': {'native': [{'capability': 'pcb', 'image': 'fixture-qualified-pin'}]}}
    repo = GraphRepository(store)
    tasks = [TaskSpec(id='route', capability='pcb', objective='Preserve and route original board',
        input_artifact_ids=list(original.values()))]
    local = SimpleNamespace(service=PrerequisiteService(store, interfaces, repo), interfaces=interfaces,
        actor=actor, project_id=project_id, store=store, scope=scope, tasks=tasks, repo=repo)
    payload = publish(local, await begin(local), imported_fixtures.proposed(imported))
    revision = wait(local, payload)
    local.service.dispatch_message(actor, message(local, revision), 'approval')
    with store.transaction() as session:
        project = session.get(Project, project_id)
        accepted = accepted_circuit_identity(session, project)
        assert accepted['mode'] == 'imported_no_concept'
        assert {ref['id'] for ref in accepted['sourceArtifacts']} == set(original.values())
        assert project.data['sourceManifest'] == original and project.data.get('approvedDesign') is None
        assert len(project.data['interfaceApprovalHistory']) == 1


@pytest.mark.parametrize('revoke_original', [False, True])
async def test_authorized_second_writer_approves_without_transferring_execution_actor(env, revoke_original):
    payload = publish(env, await begin(env))
    revision = wait(env, payload)
    writer = Actor(tenant_id=env.actor.tenant_id, user_id='second-writer')
    with env.store.transaction() as session:
        session.add(Membership(project_id=env.project_id, user_id=writer.user_id, role='editor'))
        if revoke_original:
            session.delete(session.get(Membership, (env.project_id, env.actor.user_id)))
    if revoke_original:
        with pytest.raises(DomainError):
            env.service.dispatch_message(writer, message(env, revision), 'approval')
        with env.store.transaction() as session:
            assert not session.get(Project, env.project_id).data.get('acceptedInterfaces')
    else:
        env.service.dispatch_message(writer, message(env, revision), 'approval')
        with env.store.transaction() as session:
            run = session.get(Objective, env.scope.run_id)
            assert RunScope.model_validate(run.data['scope']).actor == env.actor
            assert run.data['submittedBy'] == env.actor.user_id
            assert run.data['runtimePins'] == env.pins
            assert session.get(Project, env.project_id).data['acceptedInterfaces']['approvedBy'] == writer.user_id


@pytest.mark.parametrize('kind', ['source', 'image'])
@pytest.mark.parametrize('mutation', ['media_type', 'revision', 'size', 'sha256', 'name', 'bytes'])
@pytest.mark.parametrize('boundary', ['request', 'reply', 'resume'])
async def test_reviewed_artifact_metadata_and_bytes_remain_bound(env, kind, mutation, boundary):
    from kleos.artifacts.service import Artifact

    blobs = env.interfaces.artifacts.blobs
    content = b'bounded source bytes' if kind == 'source' else b'bounded image fixture bytes'
    digest = blobs.put(content)
    ident = str(uuid4())
    path = 'context.txt' if kind == 'source' else 'reference.png'
    with env.store.transaction() as session:
        session.add(Artifact(id=ident, project_id=env.project_id, revision=env.scope.revision,
            name=path, sha256=digest, size=len(content), media_type='text/plain' if kind == 'source' else 'image/png',
            status='input', lane='research', details={'origin': 'user_upload' if kind == 'source' else 'reference_image'}))
        project = session.get(Project, env.project_id)
        project.data = {**project.data, **({'sourceManifest': {path: ident}} if kind == 'source'
            else {'referenceImageIds': [ident]})}
    pending = await begin(env)
    # Genuine immutable inputs remain usable before and after approval.
    env.service.request(env.scope, pending['requestId'])
    if boundary != 'request':
        payload = publish(env, pending)
        revision = wait(env, payload)
    if boundary == 'resume':
        env.service.dispatch_message(env.actor, message(env, revision), 'approval')
        with env.store.transaction() as session:
            run = session.get(Objective, env.scope.run_id)
            current = RunScope.model_validate(run.data['scope'])
            answer = copy.deepcopy(run.data['pendingAnswer'])
        interrupts = [SimpleNamespace(id='interrupt-1', value=payload)]
        assert env.service.validate_resume(env.scope, current, {'interrupt-1': answer['value']}, interrupts)
    if mutation == 'bytes':
        (blobs.root / digest).write_bytes(b'corrupted bytes')
    else:
        with env.store.transaction() as session:
            row = session.get(Artifact, ident)
            replacement = {'media_type': 'application/octet-stream', 'revision': row.revision+1,
                'size': row.size+1, 'sha256': blobs.put(b'other same size data'), 'name': 'renamed.txt'}[mutation]
            setattr(row, mutation, replacement)
    with pytest.raises(DomainError):
        if boundary == 'request':
            env.service.request(env.scope, pending['requestId'])
        elif boundary == 'reply':
            env.service.dispatch_message(env.actor, message(env, revision), 'approval')
        else:
            env.service.validate_resume(env.scope, current, {'interrupt-1': answer['value']}, interrupts)
    if boundary != 'resume':
        with env.store.transaction() as session:
            assert not session.get(Project, env.project_id).data.get('acceptedInterfaces')
