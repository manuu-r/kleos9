"""Real project/artifact publication proofs for declared dependency source precedence."""
import hashlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import select

from kleos.artifacts.service import Artifact, ArtifactService, NativePublication
from kleos.artifacts.storage import BlobStore
from kleos.contracts.models import Actor, DomainError, TaskResult, TaskSpec
from kleos.execution.gateway import NativeAction, NativeJob
from kleos.orchestration.engine import ObjectiveEngine, PlanDecision, RunScope
from kleos.projects.graph_repository import GraphRepository
from kleos.projects.store import Objective, Project, ProjectStore, fingerprint


async def setup(tmp_path):
    store = ProjectStore(f'sqlite:///{tmp_path}/dependencies.db')
    store.initialize_for_tests()
    actor = Actor(tenant_id='tenant', user_id='owner')
    project = store.create(actor, 'Repair a part using its dependent successor', 'create')
    artifacts = ArtifactService(store, BlobStore(tmp_path/'blobs'))
    revision = 1
    for path, content in [('part.py', 'original'), ('unchanged.txt', 'keep')]:
        upload = artifacts.upload(actor, project['projectId'], {'file': {'path': path, 'content': content},
            'sha256': hashlib.sha256(content.encode()).hexdigest(), 'expectedEngineeringRevision': revision}, path)
        revision = upload['revision']
    submitted = store.submit(actor, project['projectId'], 'Replace missing part', revision, 'run')
    scope = RunScope(actor=actor, project_id=project['projectId'], run_id=submitted['runId'],
        revision=submitted['revision'], deadline_at=datetime.now(UTC)+timedelta(minutes=5))
    repo = GraphRepository(store)
    base = next(ref for ref in (await repo.context(scope)).artifacts if ref.path == 'part.py')
    return SimpleNamespace(store=store, artifacts=artifacts, scope=scope, repo=repo, base=base, tasks=[], results={}, turn=0)


async def produce(env, ident, *, parent=None, declared=True, input_ref=None):
    input_ref = input_ref or (parent.artifacts[0] if parent else env.base)
    task = TaskSpec(id=ident, capability='cad', objective='Candidate '+ident, allowed_paths=['part.py'],
        input_artifact_ids=[input_ref.id], depends_on=[parent.task_id] if parent and declared else [])
    env.tasks.append(task)
    await env.repo.publish_plan(env.scope, tasks=env.tasks, superseded={}, turn=env.turn, summary='Fixture plan', question=None)
    env.turn += 1
    content = ('candidate '+ident).encode()
    request = {'lane': 'cad', 'imageDigest': 'sha256:'+'a'*64, 'baselines': {'part.py': env.base.sha256},
        'sources': [{'path': input_ref.path, 'artifactId': input_ref.id, 'sha256': input_ref.sha256}]}
    with env.store.transaction() as session:
        session.add(NativeJob(id='job-'+ident, project_id=env.scope.project_id, run_id=env.scope.run_id, task_id=ident,
            task_hash=fingerprint(task.model_dump(mode='json')), request=request, request_hash=fingerprint(request),
            status='finished', active_action=None, source_manifest={'part.py': hashlib.sha256(content).hexdigest()}))
    refs = await env.artifacts.publish_native(env.scope, task, [
        {'path': 'part.py', 'role': 'source', 'content': content, 'expectedSha256': env.base.sha256},
        {'path': 'outputs/part.step', 'role': 'output', 'content': b'candidate STEP data'}],
        {'nativeJobId': 'job-'+ident, 'imageDigest': request['imageDigest'], 'requestHash': fingerprint(request)})
    result = TaskResult(task_id=ident, status='succeeded', summary='Published candidate fixture', artifacts=refs)
    await env.repo.publish(env.scope, result)
    env.results[ident] = result.model_dump(mode='json')
    return result


def downstream(env, dependencies):
    return TaskSpec(id='consumer', capability='cad', objective='Use exact changed source', depends_on=dependencies,
                    input_artifact_ids=[env.base.id], allowed_paths=['derived.py'])


async def prepare(env, task):
    engine = ObjectiveEngine(env.repo, None, None, checkpointer=InMemorySaver())
    return await engine._prepare_inputs(env.scope, task.model_dump(mode='json'), await env.repo.context(env.scope), env.results)


async def test_declared_successor_replaces_baseline_in_inputs_and_context_only(tmp_path):
    env = await setup(tmp_path)
    try:
        first = await produce(env, 'first')
        task = downstream(env, ['first'])
        pinned = await prepare(env, task)
        assert pinned['task']['input_artifact_ids'] == [first.artifacts[0].id]
        assert first.artifacts[1].id in {ref['id'] for ref in pinned['context']['artifacts']}
        assert pinned['task']['allowed_paths'] == ['derived.py']
        context = pinned['context']['artifacts']
        assert env.base.id not in {ref['id'] for ref in context}
        assert [ref['id'] for ref in context if ref['path'] == 'part.py'] == [first.artifacts[0].id]
        assert any(ref['path'] == 'unchanged.txt' for ref in context)
        with env.store.transaction() as session:
            assert session.get(Project, env.scope.project_id).data['sourceManifest']['part.py'] == env.base.id
    finally:
        env.store.engine.dispose()


@pytest.mark.parametrize('chain', [False, True])
async def test_multiple_dependencies_require_exact_declared_lineage_and_keep_outputs(tmp_path, chain):
    env = await setup(tmp_path)
    try:
        first = await produce(env, 'first')
        second = await produce(env, 'second', parent=first if chain else None)
        if not chain:
            with pytest.raises(DomainError, match='Competing'):
                await prepare(env, downstream(env, ['first', 'second']))
        else:
            pinned = await prepare(env, downstream(env, ['first', 'second']))
            assert pinned['task']['input_artifact_ids'] == [second.artifacts[0].id]
            assert [ref['id'] for ref in pinned['context']['artifacts'] if ref['path'] == 'part.py'] == [second.artifacts[0].id]
            assert len([ref for ref in pinned['context']['artifacts'] if ref['path'] == 'outputs/part.step']) == 2
    finally:
        env.store.engine.dispose()


@pytest.mark.parametrize('mutation', ['foreign', 'stale', 'request', 'image', 'baseline', 'manifest', 'publication',
    'published_ref', 'source_closure', 'origin', 'status', 'active_action', 'allowed_path', 'missing_producer'])
async def test_dependency_override_requires_current_exact_publication_provenance(tmp_path, mutation):
    env = await setup(tmp_path)
    try:
        first = await produce(env, 'first')
        with env.store.transaction() as session:
            row = session.get(Artifact, first.artifacts[0].id)
            job = session.get(NativeJob, 'job-first')
            run = session.get(Objective, env.scope.run_id)
            if mutation == 'foreign':
                row.project_id = 'foreign'
            elif mutation == 'stale':
                row.revision -= 1
            elif mutation == 'request':
                job.request = {**job.request, 'tampered': True}
            elif mutation == 'image':
                row.details = {**row.details, 'imageDigest': 'sha256:'+'f'*64}
            elif mutation == 'baseline':
                job.request = {**job.request, 'baselines': {'part.py': 'f'*64}}
                job.request_hash = fingerprint(job.request)
                row.details = {**row.details, 'requestHash': job.request_hash}
            elif mutation == 'manifest':
                job.source_manifest = {'part.py': 'f'*64}
            elif mutation == 'publication':
                session.delete(session.scalar(select(NativePublication)))
            elif mutation == 'published_ref':
                session.scalar(select(NativePublication)).references = []
            elif mutation == 'source_closure':
                row.details = {**row.details, 'sourceClosure': {'part.py': env.base.id}}
            elif mutation == 'origin':
                row.details = {**row.details, 'origin': 'repository_import'}
            elif mutation == 'status':
                job.status = 'running'
            elif mutation == 'active_action':
                job.active_action = 'unresolved-action'
            elif mutation == 'allowed_path':
                plans = [dict(plan, allowedPaths=[], allowed_paths=[]) for plan in run.data['tasks']]
                run.data = {**run.data, 'tasks': plans}
            else:
                row.details = {**row.details, 'taskId': 'unknown'}
        with pytest.raises(DomainError):
            await prepare(env, downstream(env, ['first']))
    finally:
        env.store.engine.dispose()


async def test_input_artifact_link_without_declared_dependency_does_not_establish_lineage(tmp_path):
    env = await setup(tmp_path)
    try:
        first = await produce(env, 'first')
        await produce(env, 'second', parent=first, declared=False)
        with pytest.raises(DomainError, match='ancestry'):
            await prepare(env, downstream(env, ['first', 'second']))
    finally:
        env.store.engine.dispose()


async def test_restart_uses_checkpointed_selection_without_reresolving_new_candidates(tmp_path):
    env = await setup(tmp_path)
    try:
        first = await produce(env, 'first')
        consumer = downstream(env, ['first'])
        saver = InMemorySaver()
        class Planner:
            async def plan(self, *args, **kwargs):
                return PlanDecision(tasks=[consumer])
        class Runner:
            async def execute(self, scope, task, context):
                assert task.input_artifact_ids == [first.artifacts[0].id]
                assert first.artifacts[1].id in {ref.id for ref in context.artifacts}
                assert env.base.id not in {ref.id for ref in context.artifacts}
                return TaskResult(task_id=task.id, status='succeeded', summary='Read exact pinned successor')
        engine = ObjectiveEngine(env.repo, Planner(), Runner(), checkpointer=saver)
        state = {'scope': env.scope.model_dump(mode='json'), 'objective': 'Use exact successor',
            'tasks': {task.id: task.model_dump(mode='json') for task in env.tasks},
            'results': env.results, 'turns': env.turn, 'done': False}
        config = engine._config(env.scope)
        await engine.graph.ainvoke(state, config, interrupt_before=['execute'], durability='sync')
        before = await engine.graph.aget_state(config)
        pinned = before.values['task_inputs']['consumer']
        # A new engine/repository receives exactly the persisted context, not a fresh chooser.
        class Finished:
            async def plan(self, *args, **kwargs):
                return PlanDecision(done=True)
        restarted_repo = GraphRepository(env.store)
        async def forbidden(*args):
            raise AssertionError('Checkpointed source selection was repeated')
        restarted_repo.select_dependency_sources = forbidden
        restarted = ObjectiveEngine(restarted_repo, Finished(), Runner(), checkpointer=saver)
        result = await restarted.resume(env.scope)
        assert result['done'] and result['task_inputs']['consumer'] == pinned
    finally:
        env.store.engine.dispose()


async def repair_run(env):
    prior = await produce(env, 'prior')
    prior_scope = env.scope
    with env.store.transaction() as session:
        session.get(Objective, prior_scope.run_id).status = 'blocked'
    admitted = env.store.submit(prior_scope.actor, prior_scope.project_id, 'Repair pinned candidate',
                               prior_scope.revision, 'repair')
    env.scope = prior_scope.model_copy(update={'run_id': admitted['runId'], 'revision': admitted['revision']})
    with env.store.transaction() as session:
        run = session.get(Objective, env.scope.run_id)
        run.data = {**run.data, 'repairSourceRunId': prior_scope.run_id,
                    'repairInputs': [prior.artifacts[0].model_dump(mode='json')]}
    env.tasks, env.results, env.turn = [], {}, 0
    return prior.artifacts[0]


@pytest.mark.parametrize('chain', [False, True])
async def test_dependency_repair_successor_requires_exact_server_pinned_source(tmp_path, chain):
    env = await setup(tmp_path)
    try:
        repair = await repair_run(env)
        first = await produce(env, 'first', input_ref=repair)
        latest = await produce(env, 'second', parent=first) if chain else first
        names = ['first', 'second'] if chain else ['first']
        pinned = await prepare(env, downstream(env, names))
        source_refs = [ref for ref in pinned['context']['artifacts'] if ref['path'] == 'part.py']
        assert [ref['id'] for ref in source_refs] == [latest.artifacts[0].id]
        assert env.base.id not in pinned['task']['input_artifact_ids']
        assert repair.id not in pinned['task']['input_artifact_ids']
        with env.store.transaction() as session:
            assert session.get(Project, env.scope.project_id).data['sourceManifest']['part.py'] == env.base.id
            assert session.get(Objective, env.scope.run_id).data['repairInputs'][0]['id'] == repair.id
    finally:
        env.store.engine.dispose()


@pytest.mark.parametrize('mutation', ['baseline_instead', 'hash', 'repair_source_run'])
async def test_repair_handoff_rejects_ignored_or_changed_snapshot(tmp_path, mutation):
    env = await setup(tmp_path)
    try:
        repair = await repair_run(env)
        first = await produce(env, 'first', input_ref=env.base if mutation == 'baseline_instead' else repair)
        if mutation != 'baseline_instead':
            with env.store.transaction() as session:
                if mutation == 'repair_source_run':
                    run = session.get(Objective, env.scope.run_id)
                    run.data = {**run.data, 'repairSourceRunId': 'wrong'}
                else:
                    job = session.get(NativeJob, 'job-first')
                    job.request = {**job.request, 'sources': [{'path': repair.path, 'artifactId': repair.id, 'sha256': 'f'*64}]}
                    job.request_hash = fingerprint(job.request)
                    row = session.get(Artifact, first.artifacts[0].id)
                    row.details = {**row.details, 'requestHash': job.request_hash}
        with pytest.raises(DomainError, match='[Rr]epair'):
            await prepare(env, downstream(env, ['first']))
    finally:
        env.store.engine.dispose()


async def test_committed_candidate_handoff_does_not_infer_container_termination(tmp_path):
    env = await setup(tmp_path)
    try:
        first = await produce(env, 'first')
        with env.store.transaction() as session:
            job = session.get(NativeJob, 'job-first')
            session.add(NativeAction(job_id=job.id, action_id='_dispatch', status='completed',
                request_hash=job.request_hash, result={'containerId': 'a'*64}))
        pinned = await prepare(env, downstream(env, ['first']))
        assert first.artifacts[0].id in pinned['task']['input_artifact_ids']
        with env.store.transaction() as session:
            assert session.get(NativeAction, ('job-first', '_termination')) is None
            assert session.get(NativeAction, ('job-first', '_cleanup')) is None
            assert session.get(NativeAction, ('job-first', '_dispatch')).result == {'containerId': 'a'*64}
            assert session.get(NativeJob, 'job-first').status == 'finished'
    finally:
        env.store.engine.dispose()


@pytest.mark.parametrize('invalid', [None, 'missing', 'cycle'])
async def test_dependency_ancestry_uses_effective_replacement_and_preserves_original_plan(tmp_path, invalid):
    env = await setup(tmp_path)
    try:
        failed = TaskSpec(id='failed', capability='cad', objective='Original prerequisite', allowed_paths=['part.py'])
        env.tasks.append(failed)
        result = TaskResult(task_id='failed', status='failed', summary='Needs a replacement')
        await env.repo.publish(env.scope, result)
        env.results['failed'] = result.model_dump(mode='json')
        first = await produce(env, 'replacement')
        second = await produce(env, 'second', parent=first)
        # The engine persists the original consumer plan, but dispatches its
        # effective dependency after the failed prerequisite is replaced.
        original_second = env.tasks[-1].model_copy(update={'depends_on': ['failed']})
        saved_tasks = [*env.tasks[:-1], original_second]
        superseded = {'failed': 'replacement'}
        if invalid:
            superseded['failed'] = 'unknown' if invalid == 'missing' else 'failed'
        await env.repo.publish_plan(env.scope, tasks=saved_tasks, superseded=superseded,
            turn=env.turn, summary='Original task identity retained', question=None)
        if invalid:
            with pytest.raises(DomainError, match='missing task|cycle'):
                await prepare(env, downstream(env, ['replacement', 'second']))
        else:
            pinned = await prepare(env, downstream(env, ['replacement', 'second']))
            assert [ref['id'] for ref in pinned['context']['artifacts'] if ref['path'] == 'part.py'] == [second.artifacts[0].id]
            with env.store.transaction() as session:
                plans = session.get(Objective, env.scope.run_id).data['tasks']
                assert next(plan for plan in plans if plan['id'] == 'second')['dependsOn'] == ['failed']
    finally:
        env.store.engine.dispose()
