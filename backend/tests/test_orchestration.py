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


@pytest.mark.parametrize('placeholder', ['null', ' None ', 'N/A', ''])
def test_plan_question_placeholder_text_is_not_a_question(placeholder):
    assert PlanDecision(question=placeholder).question is None
    assert PlanDecision(question='Which material?').question == 'Which material?'


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


async def test_replan_may_name_the_replacement_in_dependents_but_not_rewrite_them():
    class RepairRunner(Runner):
        async def execute(self, scope, task, context):
            self.calls.append(task.id)
            return TaskResult(task_id=task.id, status='failed' if task.id == 'original' else 'succeeded',
                              summary='Observed fixture result')
    def plans(verify):
        return [PlanDecision(tasks=[task('original'), task('verify', 'validation', ['original'])]),
                PlanDecision(tasks=[task('repair'), verify], supersedes={'original': 'repair'}),
                PlanDecision(), PlanDecision(done=True)]
    runner = RepairRunner()
    result = await ObjectiveEngine(Repo(), Planner(plans(task('verify', 'validation', ['repair']))), runner,
        checkpointer=InMemorySaver()).start(scope(), 'Repair bracket')
    assert runner.calls == ['original', 'repair', 'verify']
    assert result['tasks']['verify']['depends_on'] == ['original']
    rewritten = task('verify', 'validation', ['repair']).model_copy(update={'objective': 'A different job'})
    with pytest.raises(DomainError, match='cannot be rewritten'):
        await ObjectiveEngine(Repo(), Planner(plans(rewritten)), RepairRunner(),
            checkpointer=InMemorySaver()).start(scope(), 'Repair bracket')


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


async def test_specialist_reports_schema_invalid_tool_arguments_and_lets_the_model_correct_them():
    from pydantic import BaseModel

    from kleos.contracts.models import Observation
    from kleos.orchestration.specialist import ModelSpecialist, RegisteredTool, ToolResult

    class Read(BaseModel):
        path: str
    repo, calls = Repo(), []
    async def permit(scope, task, args):
        Read.model_validate(args)
    async def invoke(scope, task, args):
        calls.append(args)
        return ToolResult(summary='Read fixture', observations=[Observation(name='geometry', status='PASS')])
    model = scripted_model([{'action': 'tool', 'tool': 'read', 'arguments': {'secret': 'do-not-echo'}},
                            {'action': 'tool', 'tool': 'read', 'arguments': {'path': 'bracket.py'}},
                            {'action': 'finish', 'summary': 'Observed geometry; candidate only'}])
    harness = ModelSpecialist(model, [RegisteredTool('read', 'Read', frozenset({'cad'}), invoke, permit)], repo)
    s = scope()
    result = await harness.execute(s, task().model_copy(update={'max_actions': 3}), await repo.context(s))
    assert result.status == 'succeeded' and calls == [{'path': 'bracket.py'}]
    assert list(repo.receipts) == ['bracket:model:0', 'bracket:model:1', 'bracket:tool:1', 'bracket:model:2']


async def test_specialist_retries_one_schema_invalid_reply_then_fails_with_a_stable_code():
    from kleos.contracts.models import Observation
    from kleos.orchestration.specialist import ModelSpecialist, RegisteredTool, ToolResult

    async def permit(*args):
        pass
    async def invoke(*args):
        return ToolResult(summary='Read fixture', observations=[Observation(name='geometry', status='PASS')])
    tool = RegisteredTool('read', 'Read', frozenset({'cad'}), invoke, permit)
    finish = {'action': 'finish', 'summary': 'Observed geometry; candidate only'}
    use = {'action': 'tool', 'tool': 'read', 'arguments': {}}
    repo, s = Repo(), scope()
    context = await repo.context(s)
    recovered = scripted_model([{'action': 'not-an-action'}, use, finish])
    result = await ModelSpecialist(recovered, [tool], repo).execute(s, task().model_copy(update={'max_actions': 2}), context)
    assert result.status == 'succeeded' and recovered.calls == 3
    failing = scripted_model([{'action': 'not-an-action'}, {'action': 'still-wrong'}])
    with pytest.raises(DomainError) as raised:
        await ModelSpecialist(failing, [tool], Repo()).execute(s, task(), context)
    assert raised.value.code == 'model_response_invalid' and failing.calls == 2


def test_action_infers_a_tool_call_only_when_a_tool_is_named():
    from pydantic import ValidationError

    from kleos.orchestration.specialist import Action
    assert Action.model_validate({'tool': 'cad.read', 'arguments': {'path': 'model.py'}}).action == 'tool'
    decoded = Action.model_validate({'action': 'tool', 'tool': 'cad.read', 'arguments': '{"path": "model.py"}'})
    assert decoded.arguments == {'path': 'model.py'}
    assert Action.model_validate({'action': 'tool', 'tool': 'cad.build', 'arguments': None}).arguments == {}
    for broken in ('[1, 2]', 'not json', 7):
        with pytest.raises(ValidationError):
            Action.model_validate({'action': 'tool', 'tool': 'cad.read', 'arguments': broken})
    for incomplete in ({'summary': 'Done'}, {'tool': '', 'summary': 'Done'}, {'tool': None}):
        with pytest.raises(ValidationError):
            Action.model_validate(incomplete)


async def test_specialist_rejects_a_missing_tool_name_without_failing_the_objective():
    from kleos.contracts.models import Observation
    from kleos.orchestration.specialist import ModelSpecialist, RegisteredTool, ToolResult
    async def permit(*args):
        pass
    async def invoke(*args):
        return ToolResult(summary='Read fixture', observations=[Observation(name='geometry', status='PASS')])
    tool = RegisteredTool('read', 'Read', frozenset({'cad'}), invoke, permit)
    repo, s = Repo(), scope()
    model = scripted_model([{'action': 'tool', 'tool': None, 'arguments': {'path': 'a.py'}},
                            {'action': 'tool', 'tool': 'read', 'arguments': {}},
                            {'action': 'finish', 'summary': 'Observed geometry; candidate only'}])
    result = await ModelSpecialist(model, [tool], repo).execute(
        s, task().model_copy(update={'max_actions': 3}), await repo.context(s))
    assert result.status == 'succeeded' and model.calls == 3


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


def test_lane_guides_state_each_native_source_contract_from_the_qualified_fixtures():
    from kleos.capabilities.lane_guides import lane_guide
    assert 'model.py' in lane_guide('cad') and 'parts' in lane_guide('cad')
    pcb = lane_guide('pcb')
    assert 'circuit.kicad_sch' in pcb and 'board.kicad_pcb' in pcb and 'starter project' in pcb and 'global' in pcb
    app = lane_guide('application')
    assert all(name in app for name in ('pubspec.lock', 'web/index.html', 'lib/main.dart')) and 'packages:' in app
    assert lane_guide('research') == ''


async def test_specialist_prompt_carries_the_lane_guide_for_its_task_capability():
    from kleos.orchestration.specialist import ModelSpecialist
    seen = []

    class Recorder:
        def with_structured_output(self, schema):
            return self

        def bind(self, **settings):
            return self

        async def ainvoke(self, messages):
            seen.append(messages[0]['content'])
            return {'action': 'block', 'summary': 'Nothing to do'}
    async def permit(*args):
        pass
    async def invoke(*args):
        return None
    from kleos.orchestration.specialist import RegisteredTool
    tool = RegisteredTool('read', 'Read', frozenset({'cad'}), invoke, permit)
    repo, s = Repo(), scope()
    harness = ModelSpecialist(Recorder(), [tool], repo, lane_guides=lambda capability: f'GUIDE for {capability}')
    await harness.execute(s, task(), await repo.context(s))
    assert seen[0].endswith('GUIDE for cad')


async def test_planner_rejects_directory_style_native_paths_with_a_stable_code():
    from kleos.orchestration.planner import ModelPlanner

    class Model:
        def __init__(self, paths):
            self.paths = paths

        def with_structured_output(self, schema):
            return self

        def bind(self, **settings):
            return self

        async def ainvoke(self, messages):
            return PlanDecision(tasks=[TaskSpec(id='board', objective='Route', capability='pcb', allowed_paths=self.paths)])
    repo, s = Repo(), scope()
    context = await repo.context(s)
    good = await ModelPlanner(Model(['circuit.kicad_sch', 'board.kicad_pcb']), ['pcb']).plan(
        s, 'Route board', context, [], [], None)
    assert good.tasks[0].allowed_paths == ['circuit.kicad_sch', 'board.kicad_pcb']
    for bad in (['pcb/'], ['pcb/**'], ['/abs.kicad_pcb']):
        with pytest.raises(DomainError) as raised:
            await ModelPlanner(Model(bad), ['pcb']).plan(s, 'Route board', context, [], [], None)
        assert raised.value.code == 'invalid_plan'


def test_tool_functions_expose_exact_parameters_without_the_host_owned_action_id():
    from kleos.orchestration.specialist import RegisteredTool, action_from_message, tool_functions
    schema = {'type': 'object', 'required': ['actionId', 'path'],
              'properties': {'actionId': {'type': 'string'}, 'path': {'type': 'string'}}}
    async def noop(*args):
        return None
    tool = RegisteredTool('cad.read', 'Read a file', frozenset({'cad'}), noop, noop, schema)
    functions = {f['function']['name']: f['function'] for f in tool_functions({'cad.read': tool})}
    assert set(functions) == {'cad__read', 'complete_task', 'block_task'}
    assert functions['cad__read']['parameters']['required'] == ['path']
    assert list(functions['cad__read']['parameters']['properties']) == ['path']
    assert schema['required'] == ['actionId', 'path']  # the registered schema is not mutated

    from langchain_core.messages import AIMessage
    call = AIMessage(content='Reading', tool_calls=[{'id': '1', 'name': 'cad__read', 'args': {'path': 'model.py'}, 'type': 'tool_call'}])
    assert action_from_message(call).model_dump() == {
        'action': 'tool', 'tool': 'cad.read', 'arguments': {'path': 'model.py'}, 'summary': 'Reading'}
    done = AIMessage(content='', tool_calls=[{'id': '2', 'name': 'complete_task', 'args': {'summary': 'Candidate only'}, 'type': 'tool_call'}])
    assert action_from_message(done).action == 'finish' and action_from_message(done).summary == 'Candidate only'
    assert action_from_message(AIMessage(content='I will build it')) is None


async def test_specialist_in_function_mode_calls_tools_and_retries_a_reply_without_a_call():
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, ChatResult

    from kleos.contracts.models import Observation
    from kleos.orchestration.specialist import ModelSpecialist, RegisteredTool, ToolResult

    class Functions(BaseChatModel):
        replies: list
        offered: list = []

        @property
        def _llm_type(self):
            return 'fixture-no-network'

        def bind_tools(self, tools, **kwargs):
            self.offered = [tool['function']['name'] for tool in tools]
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            return ChatResult(generations=[ChatGeneration(message=self.replies.pop(0))])

    def call(name, **args):
        return AIMessage(content='', tool_calls=[{'id': name, 'name': name, 'args': args, 'type': 'tool_call'}])
    repo, seen = Repo(), []
    async def permit(*args):
        pass
    async def invoke(scope, task, args):
        seen.append(args)
        return ToolResult(summary='Read fixture', observations=[Observation(name='geometry', status='PASS')])
    tool = RegisteredTool('cad.read', 'Read', frozenset({'cad'}), invoke, permit, {'type': 'object', 'properties': {'path': {'type': 'string'}}})
    model = Functions(replies=[AIMessage(content='Let me think'), call('cad__read', path='model.py'), call('complete_task', summary='Observed geometry; candidate only')])
    s = scope()
    result = await ModelSpecialist(model, [tool], repo, function_calls=True).execute(
        s, task().model_copy(update={'max_actions': 2}), await repo.context(s))
    assert result.status == 'succeeded' and seen == [{'path': 'model.py'}]
    assert model.offered == ['cad__read', 'complete_task', 'block_task']


async def test_a_tool_call_on_a_closed_native_job_is_reported_to_the_model_not_fatal_to_the_run():
    from kleos.orchestration.specialist import ModelSpecialist, RegisteredTool
    async def permit(*args):
        pass
    async def invoke(*args):
        raise DomainError('Native job is closed', 'native_closed')
    tool = RegisteredTool('read', 'Read', frozenset({'cad'}), invoke, permit)
    repo, s = Repo(), scope()
    model = scripted_model([{'action': 'tool', 'tool': 'read', 'arguments': {}},
                            {'action': 'finish', 'summary': 'Candidate files were already published'}])
    result = await ModelSpecialist(model, [tool], repo).execute(
        s, task().model_copy(update={'max_actions': 2}), await repo.context(s))
    assert result.summary == 'Candidate files were already published' and model.calls == 2


def test_a_run_that_cannot_start_is_blocked_instead_of_crashing_the_worker(tmp_path, caplog):
    import asyncio

    from kleos.orchestration.worker import ObjectiveWorker
    worker = ObjectiveWorker.__new__(ObjectiveWorker)
    blocked = []

    class Locks:
        class _Hold:
            async def __aenter__(self):
                return object()

            async def __aexit__(self, *exc):
                return False

        def hold(self, run_id):
            return self._Hold()
    worker.locks = Locks()
    worker._begin = lambda run_id: (_ for _ in ()).throw(KeyError('internal detail'))
    worker._block = lambda run_id, code, **kw: blocked.append((run_id, code))
    with caplog.at_level('ERROR'):
        assert asyncio.run(worker.process('run-1')) is True
    assert blocked == [('run-1', 'execution_error')]
    assert 'KeyError' in caplog.text and 'internal detail' not in caplog.text


async def test_repeating_an_answered_call_without_changing_files_is_rejected_until_a_patch_intervenes():
    from kleos.contracts.models import Observation
    from kleos.orchestration.specialist import ModelSpecialist, RegisteredTool, ToolResult
    calls = []
    async def permit(*args):
        pass
    def tool(name):
        async def invoke(scope, task, args):
            calls.append(name)
            return ToolResult(summary=name, observations=[Observation(name='geometry', status='PASS')])
        return RegisteredTool(name, name, frozenset({'cad'}), invoke, permit)
    repo, s = Repo(), scope()
    model = scripted_model([{'action': 'tool', 'tool': 'cad.build', 'arguments': {}},
                            {'action': 'tool', 'tool': 'cad.build', 'arguments': {}},
                            {'action': 'tool', 'tool': 'cad.patch', 'arguments': {}},
                            {'action': 'tool', 'tool': 'cad.build', 'arguments': {}},
                            {'action': 'finish', 'summary': 'Observed geometry; candidate only'}])
    result = await ModelSpecialist(model, [tool('cad.build'), tool('cad.patch')], repo).execute(
        s, task().model_copy(update={'max_actions': 5}), await repo.context(s))
    assert calls == ['cad.build', 'cad.patch', 'cad.build'] and result.status == 'succeeded'


def test_older_bulky_results_are_compacted_to_fit_while_the_latest_stay_whole():
    from kleos.orchestration.specialist import compact_history
    big = lambda tag: {'tool': 'cad.read', 'result': {'summary': 'ok', 'observations': [{'details': {'content': tag * 20000}}]}}  # noqa: E731
    history = [big('a'), big('b'), big('c'), big('d')]
    fitted = compact_history(history, 50000)
    assert len(str(fitted)) < 51000 and fitted[-1] == history[-1] and fitted[-2] == history[-2]
    assert 'omitted for space' in str(fitted[0]) and history[0]['result']['observations'][0]['details']['content'] == 'a' * 20000
    assert compact_history(history[:1], 100000) == history[:1]
    # When even the newest results do not fit, they are shortened too rather than failing the task.
    assert len(str(compact_history(history, 20000))) <= 22000


async def test_failures_superseded_by_a_successful_publish_do_not_block_the_task():
    from kleos.contracts.models import ArtifactRef, Observation
    from kleos.orchestration.specialist import ModelSpecialist, RegisteredTool, ToolResult
    s, repo = scope(), Repo()
    published = ArtifactRef(id='a1', path='outputs/part.step', sha256='a' * 64, revision=s.revision, media_type='model/step', size=1)

    async def permit(*args):
        pass

    async def invoke_patch(scope, task, args):
        return ToolResult(summary='Patch source hash conflict', observations=[Observation(name='native.patch', status='ERROR')])

    async def invoke_finish(scope, task, args):
        return ToolResult(summary='Native candidate files collected', artifacts=[published],
                          observations=[Observation(name='native.finish', status='UNKNOWN', details={
                              'executionSucceeded': True, 'candidateOnly': True})])
    tools = [RegisteredTool('cad.patch', 'Patch', frozenset({'cad'}), invoke_patch, permit),
             RegisteredTool('cad.finish', 'Finish', frozenset({'cad'}), invoke_finish, permit)]
    model = scripted_model([{'action': 'tool', 'tool': 'cad.patch', 'arguments': {}},
                            {'action': 'tool', 'tool': 'cad.finish', 'arguments': {}},
                            {'action': 'finish', 'summary': 'Candidate published; not independently validated'}])
    result = await ModelSpecialist(model, tools, repo).execute(
        s, task().model_copy(update={'max_actions': 3}), await repo.context(s))
    assert result.status == 'succeeded' and result.artifacts and all(check.status != 'ERROR' for check in result.observations)


async def test_planner_reads_compact_task_results_when_full_ones_would_overflow_its_context():
    from kleos.contracts.models import ArtifactRef, Observation
    from kleos.orchestration.planner import ModelPlanner
    seen = []

    class Model:
        def with_structured_output(self, schema):
            return self

        def bind(self, **settings):
            return self

        async def ainvoke(self, messages):
            seen.append(messages[1]['content'])
            return PlanDecision(done=True)
    repo, s = Repo(), scope()
    context = await repo.context(s)
    refs = [ArtifactRef(id=f'a{i}', path=f'outputs/p{i}.step', sha256='b' * 64, revision=1, media_type='x/y', size=9)
            for i in range(40)]
    bulky = TaskResult(task_id='cad', status=TaskStatus.SUCCEEDED, summary='Built', artifacts=refs, observations=[
        Observation(name='native.build', status='UNKNOWN', details={'content': 'x' * 5000})])
    planner = ModelPlanner(Model(), ['cad'], max_context_chars=6000)
    await planner.plan(s, 'Review', context, [], [bulky], None)
    assert len(seen[0]) <= 6000 and '"a39"' in seen[0] and 'x' * 100 not in seen[0] and 'sha256' not in seen[0]


async def test_planner_gets_one_corrective_re_ask_for_replacements_it_did_not_define():
    from kleos.orchestration.planner import ModelPlanner

    class Model:
        def __init__(self, decisions):
            self.decisions, self.messages = iter(decisions), []

        def with_structured_output(self, schema):
            return self

        def bind(self, **settings):
            return self

        async def ainvoke(self, messages):
            self.messages.append(messages)
            return next(self.decisions)
    repo, s = Repo(), scope()
    context = await repo.context(s)
    existing = [task('original')]
    broken = PlanDecision(supersedes={'original': 'repair'})
    fixed = PlanDecision(tasks=[task('repair')], supersedes={'original': 'repair'})
    model = Model([broken, fixed])
    decision = await ModelPlanner(model, ['cad']).plan(s, 'Repair', context, existing, [], None)
    assert decision.supersedes == {'original': 'repair'} and len(model.messages) == 2
    assert 'not defined in this plan' in model.messages[1][-1]['content']
    with pytest.raises(DomainError) as raised:
        await ModelPlanner(Model([broken, broken]), ['cad']).plan(s, 'Repair', context, existing, [], None)
    assert raised.value.code == 'invalid_plan'


async def test_planner_is_told_not_to_repeat_a_lane_that_already_succeeded():
    from kleos.orchestration.planner import ModelPlanner

    class Model:
        def __init__(self, decisions):
            self.decisions, self.messages = iter(decisions), []

        def with_structured_output(self, schema):
            return self

        def bind(self, **settings):
            return self

        async def ainvoke(self, messages):
            self.messages.append(messages)
            return next(self.decisions)
    repo, s = Repo(), scope()
    context = await repo.context(s)
    finished = TaskResult(task_id='cad_first', status=TaskStatus.SUCCEEDED, summary='Built')
    repeat = PlanDecision(tasks=[task('cad_second')])
    model = Model([repeat, PlanDecision(done=True)])
    decision = await ModelPlanner(model, ['cad']).plan(s, 'Next', context, [task('cad_first')], [finished], None)
    assert decision.done and not decision.tasks
    assert "'cad_first'" in model.messages[1][-1]['content'] and 'done=true' in model.messages[1][-1]['content']
    # A repair of a failed task in the same lane stays allowed.
    failed = TaskResult(task_id='cad_first', status=TaskStatus.FAILED, summary='Broken')
    repair = PlanDecision(tasks=[task('cad_repair')], supersedes={'cad_first': 'cad_repair'})
    assert (await ModelPlanner(Model([repair]), ['cad']).plan(
        s, 'Fix', context, [task('cad_first')], [failed], None)).supersedes == {'cad_first': 'cad_repair'}


async def test_engine_releases_what_a_task_holds_when_it_ends_blocked_but_not_when_it_succeeds():
    released = []

    class Mixed(Runner):
        async def execute(self, scope, task, context):
            self.calls.append(task.id)
            return TaskResult(task_id=task.id, status=TaskStatus.BLOCKED if task.id == 'stuck' else TaskStatus.SUCCEEDED,
                              summary='Observed fixture result')

    async def closed(scope, task_id):
        released.append(task_id)
    planner = Planner([PlanDecision(tasks=[task('stuck'), task('fine')]), PlanDecision(question='What next?')])
    await ObjectiveEngine(Repo(), planner, Mixed(), checkpointer=InMemorySaver(), task_closed=closed).start(scope(), 'Make both')
    assert released == ['stuck']


async def test_a_failed_call_may_be_repeated_because_the_failure_can_be_transient():
    from kleos.contracts.models import Observation
    from kleos.orchestration.specialist import ModelSpecialist, RegisteredTool, ToolResult
    outcomes = iter(['ERROR', 'UNKNOWN'])
    async def permit(*args):
        pass
    async def invoke(scope, task, args):
        return ToolResult(summary='build', observations=[Observation(name='native.build', status=next(outcomes))])
    tool = RegisteredTool('pcb.build', 'Build', frozenset({'cad'}), invoke, permit)
    repo, s = Repo(), scope()
    model = scripted_model([{'action': 'tool', 'tool': 'pcb.build', 'arguments': {}},
                            {'action': 'tool', 'tool': 'pcb.build', 'arguments': {}},
                            {'action': 'finish', 'summary': 'Candidate built; not independently validated'}])
    result = await ModelSpecialist(model, [tool], repo).execute(
        s, task().model_copy(update={'max_actions': 3}), await repo.context(s))
    assert next(outcomes, None) is None and result.status in {'succeeded', 'blocked'}


async def test_a_file_already_read_several_times_without_an_edit_is_not_read_again():
    from kleos.contracts.models import Observation
    from kleos.orchestration.specialist import MAX_READS_PER_FILE, ModelSpecialist, RegisteredTool, ToolResult
    reads = []
    async def permit(*args):
        pass
    async def invoke(scope, task, args):
        reads.append(args['path'])
        return ToolResult(summary='read', observations=[Observation(name='native.read', status='UNKNOWN')])
    tool = RegisteredTool('cad.read', 'Read', frozenset({'cad'}), invoke, permit)
    repo, s = Repo(), scope()
    steps = [{'action': 'tool', 'tool': 'cad.read', 'arguments': {'path': 'model.py', 'offset': n * 1000}}
             for n in range(MAX_READS_PER_FILE + 2)]
    model = scripted_model([*steps, {'action': 'finish', 'summary': 'Observed geometry; candidate only'}])
    await ModelSpecialist(model, [tool], repo).execute(
        s, task().model_copy(update={'max_actions': len(steps) + 1}), await repo.context(s))
    assert len(reads) == MAX_READS_PER_FILE
