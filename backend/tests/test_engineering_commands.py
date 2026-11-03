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
        assert 'do not force a complete device pipeline' in runs[0].data['objective']
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


@pytest.mark.parametrize('kind', ['effect', 'model', 'validation', 'native'])
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
