from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from kleos.api.engineering import EngineeringFacade
from kleos.artifacts.service import ArtifactService
from kleos.artifacts.storage import BlobStore
from kleos.contracts.models import Actor, DomainError, TaskResult, TaskSpec
from kleos.execution.gateway import NativeAction, NativeJob
from kleos.orchestration.engine import RunScope
from kleos.orchestration.models import ModelAttempt, ModelBudget
from kleos.projects.engineering import EngineeringService
from kleos.projects.graph_repository import Effect, GraphRepository
from kleos.projects.store import Objective, Project, ProjectStore
from kleos.validation.service import Candidate, ValidationCheck


@pytest.fixture
def env(tmp_path):
    projects = ProjectStore(f'sqlite:///{tmp_path}/engineering.db')
    projects.initialize_for_tests()
    actor = Actor(tenant_id='tenant', user_id='owner')
    created = projects.create(actor, 'Make only a replacement bracket for an existing instrument', 'create')
    service = EngineeringService(projects)
    yield projects, actor, created['projectId'], service, EngineeringFacade(service), tmp_path
    projects.engine.dispose()


def command(facade, actor, project, kind, revision, key, **payload):
    return facade.command(actor, {'type': kind, 'payload': {'projectId': project, **payload},
                                  'expectedRevision': revision}, key)


def blocked(env):
    projects, actor, project, _, facade, _ = env
    receipt = command(facade, actor, project, 'project.generate', 1, 'generate')
    scope = RunScope(actor=actor, project_id=project, run_id=receipt['runId'], revision=2,
                     deadline_at=datetime.now(UTC)+timedelta(minutes=20))
    with projects.transaction() as session:
        run = session.get(Objective, scope.run_id)
        run.status = 'blocked'
        run.data = {**run.data, 'scope': scope.model_dump(mode='json'), 'runtimePins': {'models': ['original'], 'tools': ['original']},
                    'blocker': 'tool_unavailable', 'workerToken': 'old-worker', 'results': {'done': {'summary': 'preserved'}}}
        session.add(ModelBudget(run_id=scope.run_id, policy_hash='a'*64, max_calls=8, max_cost=2000, calls=3, committed_cost=700))
    return scope


def test_generate_dynamic_objective_and_idempotent_active_reuse(env):
    projects, actor, project, _, facade, _ = env
    receipt = command(facade, actor, project, 'project.generate', 1, 'generate')
    assert command(facade, actor, project, 'project.generate', 1, 'generate') == receipt
    same = command(facade, actor, project, 'project.generate', 2, 'active-click')
    assert same['runId'] == receipt['runId'] and same['revision'] == 2
    with projects.transaction() as session:
        runs = list(session.scalars(select(Objective)))
        assert len(runs) == 1 and runs[0].status == 'pending'
        assert runs[0].data['tasks'] == []
        assert 'whole device' in runs[0].data['objective'] and 'registered capabilities' in runs[0].data['objective']
        assert session.get(Project, project).data['state']['planning']['runId'] == receipt['runId']
    with pytest.raises(DomainError, match='active'):
        command(facade, actor, project, 'project.generate', 2, 'another', objective='Now also make a PCB')
    with pytest.raises(DomainError, match='active'):
        command(facade, actor, project, 'project.generate', 2, 'different-scope', allowedPaths=['clip.py'])


def test_cancel_without_run_id_fences_current_run_and_replays(env):
    projects, actor, project, _, facade, _ = env
    created = command(facade, actor, project, 'project.generate', 1, 'generate')
    receipt = command(facade, actor, project, 'project.cancel', 2, 'cancel')
    assert receipt['runId'] == created['runId'] and receipt['revision'] == 2
    assert command(facade, actor, project, 'project.cancel', 2, 'cancel') == receipt
    with projects.transaction() as session:
        assert session.get(Objective, created['runId']).status == 'cancelled'
        assert session.get(Project, project).data['state']['planning']['pending'] is False
    with pytest.raises(DomainError):
        command(facade, actor, project, 'project.retry', 2, 'retry-cancelled', runId=created['runId'])


def test_retry_preserves_scope_pins_history_cost_and_run_identity(env):
    projects, actor, project, _, facade, _ = env
    scope = blocked(env)
    with projects.transaction() as session:
        original = deepcopy(session.get(Objective, scope.run_id).data)
    receipt = command(facade, actor, project, 'project.retry', 2, 'retry', runId=scope.run_id)
    assert receipt['runId'] == scope.run_id and receipt['revision'] == 2
    assert command(facade, actor, project, 'project.retry', 2, 'retry', runId=scope.run_id) == receipt
    with projects.transaction() as session:
        run = session.get(Objective, scope.run_id)
        assert run.status == 'pending'
        for field in ('scope', 'runtimePins', 'results', 'createdAt', 'blocker', 'modelPolicySnapshot'):
            assert run.data[field] == original[field]
        assert run.data['workerToken'] != 'old-worker' and len(run.data['retryHistory']) == 1
        budget = session.get(ModelBudget, scope.run_id)
        assert (budget.calls, budget.committed_cost, budget.max_calls, budget.max_cost) == (3, 700, 8, 2000)


@pytest.mark.parametrize('kind', ['effect', 'validation', 'native'])
def test_retry_blocks_unknown_effects_without_mutating_run(env, kind):
    projects, actor, project, _, facade, _ = env
    scope = blocked(env)
    with projects.transaction() as session:
        if kind == 'effect':
            session.add(Effect(id='uncertain', run_id=scope.run_id, key='tool', status='claimed', value={}))
        elif kind == 'model':
            session.add(ModelAttempt(id='uncertain', run_id=scope.run_id, status='reserved', data={}))
        elif kind == 'validation':
            session.add(Candidate(id='candidate', project_id=project, run_id=scope.run_id, revision=2,
                                  request_key='check', request_hash='b'*64, subject_hash='c'*64, status='checking', data={}))
            session.add(ValidationCheck(candidate_id='candidate', checker_key='cad', status='running', data={}))
        else:
            session.add(NativeJob(id='job', project_id=project, run_id=scope.run_id, task_id='task', task_hash='a'*64,
                                  request_hash='b'*64, request={}, status='running', source_manifest={}, active_action='build'))
            session.add(NativeAction(job_id='job', action_id='build', request_hash='c'*64, status='pending', result={}))
    with pytest.raises(DomainError) as exc:
        command(facade, actor, project, 'project.retry', 2, 'retry-uncertain', runId=scope.run_id)
    assert exc.value.code == 'effect_uncertain'
    with projects.transaction() as session:
        run = session.get(Objective, scope.run_id)
        assert run.status == 'blocked' and 'retryHistory' not in run.data


@pytest.mark.parametrize('cause', ['expired', 'stale'])
def test_retry_never_extends_deadline_or_changes_context(env, cause):
    projects, actor, project, _, facade, _ = env
    scope = blocked(env)
    with projects.transaction() as session:
        run = session.get(Objective, scope.run_id)
        if cause == 'expired':
            expired = scope.model_copy(update={'deadline_at': datetime.now(UTC)-timedelta(seconds=1)})
            run.data = {**run.data, 'scope': expired.model_dump(mode='json')}
        else:
            session.get(Project, project).advance_revision()
    with pytest.raises(DomainError) as exc:
        command(facade, actor, project, 'project.retry', 2 if cause == 'expired' else 3, 'retry', runId=scope.run_id)
    assert exc.value.code == ('deadline_exceeded' if cause == 'expired' else 'stale_context')


async def test_repair_snapshots_all_prior_sources_without_accepting_them(env):
    projects, actor, project, _, facade, tmp_path = env
    submitted = command(facade, actor, project, 'project.generate', 1, 'generate')
    scope = RunScope(actor=actor, project_id=project, run_id=submitted['runId'], revision=2,
                     deadline_at=datetime.now(UTC)+timedelta(minutes=10))
    repo = GraphRepository(projects)
    task = TaskSpec(id='cad', objective='Make two parts', capability='cad', allowed_paths=['body.py', 'lid.py'])
    await repo.publish_plan(scope, tasks=[task], superseded={}, turn=0, summary='CAD', question=None)
    refs = await ArtifactService(projects, BlobStore(tmp_path/'blobs')).publish_native(scope, task, [
        {'path': 'body.py', 'role': 'source', 'content': b'body candidate', 'expectedSha256': None},
        {'path': 'lid.py', 'role': 'source', 'content': b'lid candidate', 'expectedSha256': None},
        {'path': 'outputs/lid.step', 'role': 'output', 'content': b'fixture STEP'}],
        {'nativeJobId': 'generator', 'imageDigest': 'a'*64, 'requestHash': 'b'*64})
    await repo.publish(scope, TaskResult(task_id='cad', status='blocked', summary='Lid needs repair', artifacts=refs))
    with projects.transaction() as session:
        session.get(Objective, scope.run_id).status = 'blocked'
    receipt = command(facade, actor, project, 'project.repair', 2, 'repair', allowedPaths=['lid.py'])
    with projects.transaction() as session:
        run = session.get(Objective, receipt['runId'])
        assert run.revision == 3 and run.data['repairSourceRunId'] == scope.run_id
        assert run.data['repairAllowedPaths'] == ['lid.py']
        assert {ref['path'] for ref in run.data['repairInputs']} == {'body.py', 'lid.py'}
        assert run.data['repairBaseline'] == {}
        assert session.get(Project, project).data.get('sourceManifest', {}) == {}
        assert session.get(Objective, scope.run_id).status == 'blocked'


def test_unsupported_verification_and_native_retry_do_not_create_runs(env):
    projects, actor, project, _, facade, _ = env
    for kind in ('project.verify', 'project.retryNative'):
        with pytest.raises(DomainError) as exc:
            command(facade, actor, project, kind, 1, kind)
        assert exc.value.status == 503
    with projects.transaction() as session:
        assert list(session.scalars(select(Objective))) == []


def test_commands_enforce_project_membership_and_revision(env):
    _, actor, project, _, facade, _ = env
    with pytest.raises(DomainError) as exc:
        command(facade, Actor(tenant_id='other', user_id=actor.user_id), project, 'project.generate', 1, 'foreign')
    assert exc.value.status == 404
    with pytest.raises(DomainError) as exc:
        command(facade, actor, project, 'project.generate', 0, 'stale')
    assert exc.value.code == 'revision_conflict'


def test_generate_pins_explicit_edit_scope(env):
    projects, actor, project, _, facade, _ = env
    receipt = command(facade, actor, project, 'project.generate', 1, 'specific-part',
                      objective='Generate only the missing clip', allowedPaths=['clip.py'])
    with projects.transaction() as session:
        run = session.get(Objective, receipt['runId'])
        assert run.data['editAllowedPaths'] == ['clip.py']
        assert 'clip.py' in run.data['objective']


def stopped_job(projects, scope, project, *, tool, proof=True, status='failed'):
    """A closed native job whose in-flight action never completed, with the matching journaled effects."""
    import hashlib
    import json
    request = {'sources': [], 'resumeAttempt': 1}
    action_id = hashlib.sha256(json.dumps([scope.run_id, 'task', 4, tool]).encode()).hexdigest()
    with projects.transaction() as session:
        session.add(NativeJob(id='job', project_id=project, run_id=scope.run_id, task_id='task', task_hash='a'*64,
                              request_hash='b'*64, request=request, status=status, source_manifest={'model.py': 'c'*64},
                              active_action=action_id))
        session.add(NativeAction(job_id='job', action_id=action_id, request_hash='d'*64, status='pending', result={}))
        session.add(Effect(id='model', run_id=scope.run_id, key='task:model:4', status='completed',
                           value={'action': 'tool', 'tool': tool}))
        session.add(Effect(id='tool', run_id=scope.run_id, key='task:tool:4', status='claimed', value={}))
        if proof:
            session.add(NativeAction(job_id='job', action_id='_termination', request_hash='b'*64, status='completed',
                                     result={'kind': 'termination-proof', 'jobId': 'job', 'containerStopped': True}))


def test_retry_releases_an_in_flight_native_action_only_when_its_container_is_proven_stopped(env):
    projects, actor, project, _, facade, _ = env
    scope = blocked(env)
    stopped_job(projects, scope, project, tool='cad.build')
    receipt = command(facade, actor, project, 'project.retry', 2, 'retry-stopped', runId=scope.run_id)
    assert receipt['runId'] == scope.run_id
    with projects.transaction() as session:
        assert session.get(Objective, scope.run_id).status == 'pending'
        assert session.scalar(select(Effect).where(Effect.key == 'task:tool:4')) is None
        assert session.scalar(select(Effect).where(Effect.key == 'task:model:4')) is not None  # the paid turn is kept
        assert session.get(NativeJob, 'job').active_action is None
        assert session.scalar(select(NativeAction).where(NativeAction.status == 'pending')) is None


@pytest.mark.parametrize('case', ['finish', 'no-proof'])
def test_retry_keeps_publishing_or_unproven_native_claims_uncertain(env, case):
    projects, actor, project, _, facade, _ = env
    scope = blocked(env)
    stopped_job(projects, scope, project, tool='cad.finish' if case == 'finish' else 'cad.build', proof=case == 'finish')
    with pytest.raises(DomainError) as exc:
        command(facade, actor, project, 'project.retry', 2, 'retry-uncertain', runId=scope.run_id)
    assert exc.value.code == 'effect_uncertain'
    with projects.transaction() as session:
        assert session.scalar(select(Effect).where(Effect.key == 'task:tool:4')) is not None


def test_retry_records_a_claimed_tool_whose_native_action_durably_completed(env):
    import hashlib
    import json
    projects, actor, project, _, facade, _ = env
    scope = blocked(env)
    action_id = hashlib.sha256(json.dumps([scope.run_id, 'task', 6, 'cad.finish']).encode()).hexdigest()
    result = {'summary': 'Native finish completed', 'artifacts': [], 'blockers': [],
              'observations': [{'name': 'native.finish', 'status': 'UNKNOWN', 'details': {}, 'independent': False}]}
    with projects.transaction() as session:
        session.add(NativeJob(id='job', project_id=project, run_id=scope.run_id, task_id='task', task_hash='a'*64,
                              request_hash='b'*64, request={'sources': []}, status='finished', source_manifest={}, active_action=None))
        session.add(NativeAction(job_id='job', action_id=action_id, request_hash='d'*64, status='completed', result=result))
        session.add(Effect(id='model', run_id=scope.run_id, key='task:model:6', status='completed',
                           value={'action': 'tool', 'tool': 'cad.finish'}))
        session.add(Effect(id='tool', run_id=scope.run_id, key='task:tool:6', status='claimed', value={}))
    command(facade, actor, project, 'project.retry', 2, 'retry-recorded', runId=scope.run_id)
    with projects.transaction() as session:
        effect = session.scalar(select(Effect).where(Effect.key == 'task:tool:6'))
        assert effect.status == 'completed' and effect.value['summary'] == 'Native finish completed'
        assert effect.value['observations'][0]['name'] == 'native.finish'


def test_recorded_native_progress_is_kept_even_when_the_retry_is_still_refused(env):
    import hashlib
    import json
    projects, actor, project, _, facade, _ = env
    scope = blocked(env)
    action_id = hashlib.sha256(json.dumps([scope.run_id, 'task', 2, 'cad.build']).encode()).hexdigest()
    with projects.transaction() as session:
        session.add(NativeJob(id='job', project_id=project, run_id=scope.run_id, task_id='task', task_hash='a'*64,
                              request_hash='b'*64, request={'sources': []}, status='running', source_manifest={}, active_action=None))
        session.add(NativeAction(job_id='job', action_id=action_id, request_hash='d'*64, status='completed',
                                 result={'summary': 'Native build completed', 'artifacts': [], 'observations': [], 'blockers': []}))
        session.add(Effect(id='model', run_id=scope.run_id, key='task:model:2', status='completed',
                           value={'action': 'tool', 'tool': 'cad.build'}))
        session.add(Effect(id='tool', run_id=scope.run_id, key='task:tool:2', status='claimed', value={}))
        session.add(Effect(id='other', run_id=scope.run_id, key='memory:final', status='claimed', value={}))
    with pytest.raises(DomainError) as exc:
        command(facade, actor, project, 'project.retry', 2, 'retry-refused', runId=scope.run_id)
    assert exc.value.code == 'effect_uncertain'
    with projects.transaction() as session:
        assert session.scalar(select(Effect).where(Effect.key == 'task:tool:2')).status == 'completed'


def test_retry_settles_a_cut_off_model_call_of_a_blocked_run_and_keeps_its_reserved_cost(env):
    projects, actor, project, _, facade, _ = env
    scope = blocked(env)
    with projects.transaction() as session:
        session.add(ModelAttempt(id='cut-off', run_id=scope.run_id, status='reserved', data={'committedMicroUsd': 120}))
        session.add(Effect(id='asked', run_id=scope.run_id, key='task:model:3', status='claimed', value={}))
        session.add(Effect(id='replan', run_id=scope.run_id, key='plan:1', status='claimed', value={}))
        session.add(Effect(id='planned', run_id=scope.run_id, key='plan:0', status='completed', value={'tasks': []}))
    command(facade, actor, project, 'project.retry', 2, 'retry-cut-off', runId=scope.run_id)
    with projects.transaction() as session:
        attempt = session.get(ModelAttempt, 'cut-off')
        assert attempt.status == 'uncertain' and attempt.data['committedMicroUsd'] == 120
        budget = session.get(ModelBudget, scope.run_id)
        assert budget.committed_cost == 700  # spend history is never reset
        assert session.scalar(select(Effect).where(Effect.key == 'task:model:3')) is None
        assert session.scalar(select(Effect).where(Effect.key == 'plan:1')) is None
        assert session.scalar(select(Effect).where(Effect.key == 'plan:0')).status == 'completed'


def test_an_explicit_partial_request_keeps_its_narrow_scope(env):
    projects, actor, project, _, facade, _ = env
    command(facade, actor, project, 'project.generate', 1, 'partial', objective='Replace only the mounting bracket')
    with projects.transaction() as session:
        text = session.scalar(select(Objective)).data['objective']
        assert text.startswith('Replace only the mounting bracket') and 'do not force a complete device pipeline' in text
        assert 'whole device' not in text


def test_retry_discards_a_plan_the_engine_rejected_so_it_is_asked_again_not_replayed(env):
    projects, actor, project, _, facade, _ = env
    scope = blocked(env)
    with projects.transaction() as session:
        run = session.get(Objective, scope.run_id)
        run.data = {**run.data, 'blocker': 'invalid_plan'}
        for turn in (0, 1):
            session.add(Effect(id=f'plan{turn}', run_id=scope.run_id, key=f'plan:{turn}', status='completed', value={'tasks': []}))
    command(facade, actor, project, 'project.retry', 2, 'retry-invalid-plan', runId=scope.run_id)
    with projects.transaction() as session:
        keys = sorted(effect.key for effect in session.scalars(select(Effect).where(Effect.run_id == scope.run_id)))
        assert keys == ['plan:0']  # the accepted first plan stays; only the rejected latest one is dropped
