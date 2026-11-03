from datetime import UTC, datetime, timedelta

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from kleos.contracts.models import Actor, DomainError, ProjectContext, TaskResult, TaskSpec, TaskStatus
from kleos.orchestration.engine import ObjectiveEngine, PlanDecision, RunScope


def scope():
    return RunScope(actor=Actor(tenant_id='tenant', user_id='owner'), project_id='project',
                    run_id='run', revision=1, deadline_at=datetime.now(UTC) + timedelta(minutes=5))


class Repo:
    def __init__(self):
        self.receipts, self.published = {}, {}
        self.active = True
        self.plans = []
        self.artifacts = {}

    async def assert_active(self, scope):
        if not self.active:
            raise DomainError('Cancelled', 'cancelled')

    async def context(self, scope):
        return ProjectContext(tenant_id=scope.actor.tenant_id, project_id=scope.project_id, revision=scope.revision)

    async def run_once(self, scope, key, operation):
        if key not in self.receipts:
            self.receipts[key] = await operation()
        return self.receipts[key]

    async def publish(self, scope, result):
        await self.assert_active(scope)
        self.published[result.task_id] = result

    async def publish_plan(self, scope, **plan):
        await self.assert_active(scope)
        self.plans.append(plan)

    async def resolve_artifacts(self, scope, artifact_ids):
        await self.assert_active(scope)
        return [self.artifacts[key] for key in artifact_ids]


class Planner:
    def __init__(self, decisions):
        self.decisions, self.calls = iter(decisions), []

    async def plan(self, scope, objective, context, tasks, results, answer, *, superseded=None):
        self.calls.append((objective, [t.id for t in tasks], [r.task_id for r in results], answer))
        return next(self.decisions)


class Runner:
    def __init__(self, repo=None):
        self.calls, self.repo = [], repo

    async def execute(self, scope, task, context):
        self.calls.append(task.id)
        if self.repo:
            self.repo.active = False
        return TaskResult(task_id=task.id, status=TaskStatus.SUCCEEDED, summary='Candidate fixture')


def task(id='bracket', capability='cad', dependencies=None):
    return TaskSpec(id=id, objective='Only replace the missing bracket', capability=capability,
                    depends_on=dependencies or [], allowed_paths=['bracket.py'])


async def test_dynamic_single_part_objective_never_runs_unrequested_lanes():
    repo, runner = Repo(), Runner()
    planner = Planner([PlanDecision(tasks=[task()]), PlanDecision(done=True)])
    engine = ObjectiveEngine(repo, planner, runner, checkpointer=InMemorySaver())
    result = await engine.start(scope(), 'Replace only bracket')
    assert result['done'] and runner.calls == ['bracket']
    assert set(repo.published) == {'bracket'}
    assert planner.calls[1][2] == ['bracket']


async def test_dependencies_and_expansion_follow_actual_task_results():
    repo, runner = Repo(), Runner()
    planner = Planner([PlanDecision(tasks=[task('a'), task('b', 'pcb'), task('verify', 'validation', ['a', 'b'])]),
                       PlanDecision(), PlanDecision(done=True)])
    engine = ObjectiveEngine(repo, planner, runner, checkpointer=InMemorySaver())
    await engine.start(scope(), 'Build two related parts')
    assert set(runner.calls[:2]) == {'a', 'b'}
    assert runner.calls[-1] == 'verify'


async def test_interrupt_resumes_new_engine_without_repeating_completed_work():
    repo, runner, saver = Repo(), Runner(), InMemorySaver()
    planner = Planner([PlanDecision(tasks=[task()]), PlanDecision(question='Which material?'), PlanDecision(done=True)])
    original_scope = scope()
    first = ObjectiveEngine(repo, planner, runner, checkpointer=saver)
    result = await first.start(original_scope, 'Make a bracket')
    assert '__interrupt__' in result and runner.calls == ['bracket']
    second = ObjectiveEngine(repo, planner, runner, checkpointer=saver)
    result = await second.resume(original_scope, answer='PETG')
    assert result['done'] and runner.calls == ['bracket']
    assert planner.calls[-1][-1] == 'PETG'


async def test_cancellation_during_tool_call_fences_publication():
    repo = Repo()
    engine = ObjectiveEngine(repo, Planner([PlanDecision(tasks=[task()])]), Runner(repo), checkpointer=InMemorySaver())
    with pytest.raises(DomainError, match='Cancelled'):
        await engine.start(scope(), 'Bracket')
    assert not repo.published


@pytest.mark.parametrize('tasks', [[task('a', dependencies=['missing'])],
                                  [task('a', dependencies=['b']), task('b', dependencies=['a'])]])
async def test_invalid_dependency_plans_do_not_execute(tasks):
    runner = Runner()
    engine = ObjectiveEngine(Repo(), Planner([PlanDecision(tasks=tasks)]), runner, checkpointer=InMemorySaver())
    with pytest.raises(DomainError):
        await engine.start(scope(), 'Bracket')
    assert not runner.calls


async def test_budget_does_not_reset_between_graph_steps():
    engine = ObjectiveEngine(Repo(), Planner([PlanDecision(tasks=[task()])]), Runner(), checkpointer=InMemorySaver())
    with pytest.raises(DomainError, match='allowance exhausted'):
        await engine.start(scope().model_copy(update={'max_plan_calls': 1}), 'Bracket')


async def test_resume_cannot_change_scope_or_tenant():
    original = scope()
    engine = ObjectiveEngine(Repo(), Planner([PlanDecision(question='Size?')]), Runner(), checkpointer=InMemorySaver())
    await engine.start(original, 'Bracket')
    changed = original.model_copy(update={'revision': 2})
    with pytest.raises(DomainError, match='scope changed'):
        await engine.resume(changed, answer='10mm')
    changed = original.model_copy(update={'actor': Actor(tenant_id='other', user_id='owner')})
    with pytest.raises(DomainError, match='missing'):
        await engine.resume(changed, answer='10mm')


async def test_real_repository_restarts_and_replays_completed_effects(tmp_path):
    from sqlalchemy import select

    from kleos.projects.graph_repository import Effect, GraphRepository
    from kleos.projects.store import Objective, ProjectStore

    url = f'sqlite:///{tmp_path / "authority.db"}'
    store = ProjectStore(url)
    store.initialize_for_tests()
    actor = Actor(tenant_id='tenant', user_id='owner')
    project = store.create(actor, 'Existing assembly missing one bracket', 'create')
    submitted = store.submit(actor, project['projectId'], 'Replace bracket', 1, 'objective')
    saved_scope = RunScope(actor=actor, project_id=project['projectId'], run_id=submitted['runId'],
                          revision=submitted['revision'], deadline_at=datetime.now(UTC) + timedelta(minutes=5))
    saver, runner = InMemorySaver(), Runner()
    planner = Planner([PlanDecision(tasks=[task()]), PlanDecision(question='Confirm material'),
                       PlanDecision(done=True)])
    first = ObjectiveEngine(GraphRepository(store), planner, runner, checkpointer=saver)
    await first.start(saved_scope, 'Replace bracket')
    store.engine.dispose()
    reopened = ProjectStore(url)
    second = ObjectiveEngine(GraphRepository(reopened), planner, runner, checkpointer=saver)
    result = await second.resume(saved_scope, answer='PETG')
    assert result['done'] and runner.calls == ['bracket']
    with reopened.transaction() as session:
        objective = session.get(Objective, submitted['runId'])
        assert objective.data['results']['bracket']['status'] == 'succeeded'
        receipts = session.scalars(select(Effect).where(Effect.run_id == submitted['runId'])).all()
        assert len(receipts) == 4 and all(row.status == 'completed' for row in receipts)
    # A completed tool receipt remains durable across repository reconstruction.
    async def forbidden():
        raise AssertionError('A committed effect must not repeat')
    receipt = await GraphRepository(reopened).run_once(saved_scope, 'task:bracket', forbidden)
    assert receipt['task_id'] == 'bracket'


async def test_failed_task_supersession_preserves_history_and_redirects_dependencies():
    class RepairRunner(Runner):
        async def execute(self, scope, task, context):
            self.calls.append(task.id)
            return TaskResult(task_id=task.id, status='failed' if task.id == 'original' else 'succeeded',
                              summary='Observed fixture result')
    runner = RepairRunner()
    original = task('original')
    planner = Planner([
        PlanDecision(tasks=[original, task('verify', 'validation', ['original'])]),
        PlanDecision(tasks=[task('repair')], supersedes={'original': 'repair'}),
        PlanDecision(), PlanDecision(done=True)])
    result = await ObjectiveEngine(Repo(), planner, runner, checkpointer=InMemorySaver()).start(scope(), 'Repair bracket')
    assert runner.calls == ['original', 'repair', 'verify']
    assert result['results']['original']['status'] == 'failed'
    assert result['tasks']['verify']['depends_on'] == ['original']
    assert result['superseded'] == {'original': 'repair'}


async def test_supersession_cannot_expand_allowed_edit_scope():
    class Failed(Runner):
        async def execute(self, scope, task, context):
            return TaskResult(task_id=task.id, status='failed', summary='Failed build')
    repair = task('repair').model_copy(update={'allowed_paths': ['unrelated.py']})
    planner = Planner([PlanDecision(tasks=[task('original')]),
                       PlanDecision(tasks=[repair], supersedes={'original': 'repair'})])
    with pytest.raises(DomainError, match='broaden edit scope'):
        await ObjectiveEngine(Repo(), planner, Failed(), checkpointer=InMemorySaver()).start(scope(), 'Repair bracket')


def scripted_model(actions):
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, ChatResult

    class Scripted(BaseChatModel):
        actions: list[dict]
        calls: int = 0
        schema_name: str = ''

        @property
        def _llm_type(self):
            return 'fixture-no-network'

        def bind_tools(self, tools, **kwargs):
            self.schema_name = tools[0].__name__
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            assert kwargs['max_tokens'] == 4096
            self.calls += 1
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content='', tool_calls=[{
                'id': f'fixture-{self.calls}', 'name': self.schema_name, 'args': self.actions.pop(0)}]))])

    return Scripted(actions=actions)


async def test_actual_model_specialist_observes_tools_and_replays_receipts_without_new_calls():
    from kleos.contracts.models import Observation
    from kleos.orchestration.specialist import ModelSpecialist, RegisteredTool, ToolResult
    repo, calls = Repo(), []
    async def permit(scope, task, args):
        assert args == {'view': 'top'}
    async def invoke(scope, task, args):
        calls.append('tool')
        return ToolResult(summary='Measured fixture', observations=[Observation(name='geometry', status='PASS')])
    model = scripted_model([{'action': 'tool', 'tool': 'inspect', 'arguments': {'view': 'top'}},
                            {'action': 'finish', 'summary': 'Observed geometry; candidate only'}])
    harness = ModelSpecialist(model, [RegisteredTool('inspect', 'Inspect geometry', frozenset({'cad'}), invoke, permit)], repo)
    s = scope()
    context = await repo.context(s)
    first = await harness.execute(s, task(), context)
    second = await harness.execute(s, task(), context)
    assert first == second and first.status == 'succeeded'
    assert model.calls == 2 and calls == ['tool']
    assert first.observations[0].independent is False


async def test_specialist_cannot_finish_from_prose_or_model_invented_tools():
    from kleos.orchestration.specialist import ModelSpecialist, RegisteredTool, ToolResult
    async def permit(*args):
        pass
    async def invoke(*args):
        return ToolResult(summary='No evidence')
    tool = RegisteredTool('inspect', 'Inspect', frozenset({'cad'}), invoke, permit)
    repo, s = Repo(), scope()
    harness = ModelSpecialist(scripted_model([{'action': 'finish', 'summary': 'I built everything'}]), [tool], repo)
    assert (await harness.execute(s, task(), await repo.context(s))).status == 'blocked'
    harness = ModelSpecialist(scripted_model([{'action': 'tool', 'tool': 'purchase'}]), [tool], Repo())
    with pytest.raises(DomainError, match='unavailable tool'):
        await harness.execute(s, task(), await repo.context(s))


@pytest.mark.parametrize('marker,has_artifact,expected', [(True, True, 'succeeded'),
    (False, True, 'blocked'), (True, False, 'blocked')])
async def test_native_candidate_execution_is_not_confused_with_independent_validation(marker, has_artifact, expected):
    from kleos.contracts.models import ArtifactRef, Observation
    from kleos.orchestration.specialist import ModelSpecialist, RegisteredTool, ToolResult

    async def permit(*args):
        pass

    async def invoke(*args):
        return ToolResult(summary='Candidate file production only',
            artifacts=[ArtifactRef(id='candidate', path='outputs/bracket.step', sha256='a'*64, revision=1, size=20)]
                      if has_artifact else [],
            observations=[Observation(name='native.finish', status='UNKNOWN',
                details={'executionSucceeded': marker, 'candidateOnly': marker})])

    repo, s = Repo(), scope()
    model = scripted_model([{'action': 'tool', 'tool': 'native.finish'}, {'action': 'finish'}])
    harness = ModelSpecialist(model, [RegisteredTool('native.finish', 'Collect candidates', frozenset({'cad'}),
                                                    invoke, permit)], repo)
    result = await harness.execute(s, task(), await repo.context(s))
    assert result.status == expected
    assert result.observations[0].status == 'UNKNOWN' and not result.observations[0].independent


async def test_actual_model_planner_returns_bounded_typed_dynamic_plan():
    from kleos.orchestration.planner import ModelPlanner
    model = scripted_model([{'tasks': [task().model_dump(mode='json')]}])
    repo, s = Repo(), scope()
    decision = await ModelPlanner(model, ['cad']).plan(s, 'Only the missing bracket', await repo.context(s), [], [], None)
    assert len(decision.tasks) == 1 and decision.tasks[0].capability.value == 'cad'
    assert model.calls == 1


async def test_native_action_ids_are_host_owned_stable_on_replay_and_unique_per_action():
    from kleos.capabilities.native import register_native
    from kleos.capabilities.registry import CapabilityRegistry
    from kleos.contracts.models import Capability
    from kleos.orchestration.specialist import ModelSpecialist

    class Gateway:
        calls = []

        async def authorize(self, scope, task, args):
            assert len(args['actionId']) == 64 and 'action_id' not in args

        async def invoke(self, scope, task, operation, args):
            self.calls.append(args['actionId'])
            return {'summary': 'Check observed', 'observations': [{'name': 'check', 'status': 'PASS'}]}

    registry, gateway, repo, s = CapabilityRegistry(), Gateway(), Repo(), scope()
    keys = register_native(registry, gateway, capability=Capability.CAD, provider='native',
        provider_version='1.0.0', installation_hash='a' * 64, qualified=True, actions=('analyze',))
    model = scripted_model([
        {'action': 'tool', 'tool': 'cad.analyze', 'arguments': {'actionId': 'model-chosen', 'action_id': 'alias'}},
        {'action': 'tool', 'tool': 'cad.analyze'}, {'action': 'finish', 'summary': 'Candidate observed'}])
    harness = ModelSpecialist(model, registry.tools(registry.snapshot(keys)), repo)
    context = await repo.context(s)
    first = await harness.execute(s, task(), context)
    assert first.status == 'succeeded'
    assert await harness.execute(s, task(), context) == first
    assert len(gateway.calls) == len(set(gateway.calls)) == 2 and model.calls == 3


@pytest.mark.parametrize('uncertain', [False, True])
async def test_specialist_restart_replays_completed_inner_actions_but_blocks_unknowns(tmp_path, uncertain):
    from sqlalchemy import select

    from kleos.contracts.models import Observation
    from kleos.orchestration.specialist import ModelSpecialist, RegisteredTool, ToolResult
    from kleos.projects.graph_repository import Effect, GraphRepository
    from kleos.projects.store import ProjectStore

    store = ProjectStore(f'sqlite:///{tmp_path / "recovery.db"}')
    store.initialize_for_tests()
    actor = Actor(tenant_id='tenant', user_id='owner')
    created = store.create(actor, 'Bracket', 'create')
    submitted = store.submit(actor, created['projectId'], 'Only bracket', 1, 'submit')
    s = RunScope(actor=actor, project_id=created['projectId'], run_id=submitted['runId'],
                 revision=submitted['revision'], deadline_at=datetime.now(UTC) + timedelta(minutes=5))
    calls = []

    class CrashAfterReceipt(GraphRepository):
        async def run_once(self, scope, key, operation):
            value = await super().run_once(scope, key, operation)
            if key == 'bracket:tool:0':
                raise RuntimeError('Process died after successful receipt')
            return value

    async def authorize(*args):
        pass

    async def invoke(*args):
        calls.append('tool')
        if uncertain:
            raise RuntimeError('Connection lost during external side effect')
        return ToolResult(summary='Observed', observations=[Observation(name='geometry', status='PASS')])

    tool = RegisteredTool('inspect', 'Inspect candidate', frozenset({'cad'}), invoke, authorize)
    repository, saver = CrashAfterReceipt(store), InMemorySaver()
    planner = Planner([PlanDecision(tasks=[task()]), PlanDecision(done=True)])
    first_model = scripted_model([{'action': 'tool', 'tool': 'inspect'}])
    first = ObjectiveEngine(repository, planner, ModelSpecialist(first_model, [tool], repository), checkpointer=saver)
    with pytest.raises(RuntimeError):
        await first.start(s, 'Only bracket')
    stored = await first.graph.aget_state(first._config(s))
    assert stored.next == ('execute',)
    assert stored.values['task_inputs']['bracket']['context']['brief'] == 'Bracket'
    store.engine.dispose()
    reopened = ProjectStore(f'sqlite:///{tmp_path / "recovery.db"}')
    repository = GraphRepository(reopened)
    second_model = scripted_model([{'action': 'finish', 'summary': 'Observed candidate'}])
    second = ObjectiveEngine(repository, planner, ModelSpecialist(second_model, [tool], repository), checkpointer=saver)
    if uncertain:
        with pytest.raises(DomainError, match='reconciliation'):
            await second.resume(s)
        assert second_model.calls == 0
    else:
        assert (await second.resume(s))['done']
        assert second_model.calls == 1
    assert calls == ['tool'] and first_model.calls == 1
    with reopened.transaction() as session:
        effects = list(session.scalars(select(Effect)))
        assert not any(item.key == 'task:bracket' for item in effects)
        saved_tool = next(item for item in effects if item.key == 'bracket:tool:0')
        assert saved_tool.status == ('claimed' if uncertain else 'completed')


async def test_dependency_artifacts_are_pinned_read_inputs_without_broadening_edit_scope():
    from kleos.contracts.models import ArtifactRef

    artifact = ArtifactRef(id='cad-output', path='assembly.step', sha256='a' * 64, revision=1, size=42)
    repo, s = Repo(), scope()
    repo.artifacts[artifact.id] = artifact
    calls = []

    class Produce:
        async def execute(self, scope, spec, context):
            calls.append((spec, context))
            return TaskResult(task_id=spec.id, status='succeeded', summary='Candidate',
                              artifacts=[artifact] if spec.id == 'cad' else [])

    first = task('cad')
    second = task('verify', 'validation', ['cad']).model_copy(update={'allowed_paths': ['report.json']})
    planner = Planner([PlanDecision(tasks=[first, second]), PlanDecision(), PlanDecision(done=True)])
    engine = ObjectiveEngine(repo, planner, Produce(), checkpointer=InMemorySaver())
    assert (await engine.start(s, 'Make and inspect bracket'))['done']
    assert calls[0][0].input_artifact_ids == []
    assert calls[1][0].input_artifact_ids == ['cad-output']
    assert calls[1][0].allowed_paths == ['report.json']
    assert calls[1][1].artifacts == [artifact]
    assert repo.plans[0]['tasks'] == [first, second]
    assert len(repo.plans) == 3


async def test_dependency_artifact_hash_mismatch_blocks_dispatch():
    from kleos.contracts.models import ArtifactRef

    original = ArtifactRef(id='output', path='assembly.step', sha256='a' * 64, revision=1, size=42)
    repo = Repo()
    repo.artifacts[original.id] = original.model_copy(update={'sha256': 'b' * 64})

    class Produce:
        async def execute(self, scope, spec, context):
            assert spec.id == 'cad'
            return TaskResult(task_id=spec.id, status='succeeded', summary='Candidate', artifacts=[original])

    planner = Planner([PlanDecision(tasks=[task('cad'), task('verify', 'validation', ['cad'])]), PlanDecision()])
    with pytest.raises(DomainError, match='version changed'):
        await ObjectiveEngine(repo, planner, Produce(), checkpointer=InMemorySaver()).start(scope(), 'Inspect')
