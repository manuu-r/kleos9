"""Model-facing compiled references preserve immutable authoring history."""
import copy
import json

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from test_composition import composed as composed
from test_composition_execution import CompilationDocker

from kleos.contracts.models import DomainError, TaskResult, TaskSpec
from kleos.orchestration.composition import FinalCompilation
from kleos.orchestration.engine import ObjectiveEngine, PlanDecision
from kleos.orchestration.planner import ModelPlanner
from kleos.projects.store import Objective


class CapturedModel:
    def __init__(self):
        self.messages = []

    def with_structured_output(self, schema):
        assert schema is PlanDecision
        return self

    def bind(self, **settings):
        assert settings['max_tokens'] == 4096
        return self

    async def ainvoke(self, messages):
        self.messages.append(copy.deepcopy(messages))
        return PlanDecision(done=True, summary='Candidate files compiled; no release acceptance')


async def prepare(composed):
    service, scope, tasks, _, _, _ = composed
    grant = service.freeze(scope)
    docker = CompilationDocker(grant)
    service.gateway.docker = docker
    final = FinalCompilation(service.repository, service.artifacts, service.gateway)
    await final.run(scope)
    with service.repository.store.transaction() as session:
        original = copy.deepcopy(session.get(Objective, scope.run_id).data['results'])
    model = CapturedModel()
    engine = ObjectiveEngine(service.repository, ModelPlanner(model, ['cad', 'application']), None,
        checkpointer=InMemorySaver(), composition=final)
    state = {'scope': scope.model_dump(mode='json'), 'objective': 'Compile final candidates',
        'tasks': {task.id: task.model_dump(mode='json') for task in tasks},
        'results': copy.deepcopy(original), 'turns': 1, 'superseded': {}}
    return service, scope, engine, model, state, original, docker


@pytest.mark.parametrize('composed', [['base', 'app']], indirect=True)
async def test_engine_planner_packet_uses_compiled_outputs_without_rewriting_or_replanning(composed):
    service, scope, engine, model, state, original, docker = await prepare(composed)
    dispatches = docker.dispatches
    expected = await service.repository.dependency_artifacts(scope, list(state['tasks']))
    result = await engine._plan(state)
    assert result['done'] and not result['compilation_requested']
    assert len(model.messages) == 1
    packet = json.loads(model.messages[0][1]['content'])
    for observed in packet['results']:
        task_id = observed['task_id']
        assert observed['artifacts'] == [ref.model_dump(mode='json') for ref in expected[task_id]]
        prior = TaskResult.model_validate(original[task_id]).model_dump(mode='json')
        assert {key: value for key, value in observed.items() if key != 'artifacts'} == {
            key: value for key, value in prior.items() if key != 'artifacts'}
        old_outputs = {ref['id'] for ref in prior['artifacts'] if ref['path'].startswith('outputs/')}
        assert old_outputs.isdisjoint(ref['id'] for ref in observed['artifacts'])
    assert state['results'] == original and docker.dispatches == dispatches
    # A replay from before the planning checkpoint observes its saved decision.
    assert await engine._plan(state) == result
    assert len(model.messages) == 1 and docker.dispatches == dispatches
    with service.repository.store.transaction() as session:
        assert session.get(Objective, scope.run_id).data['results'] == original


@pytest.mark.parametrize('composed', [['base', 'app']], indirect=True)
@pytest.mark.parametrize('change', ['checkpoint_result', 'compiled_receipt'])
async def test_engine_rejects_changed_history_before_calling_planner(composed, change):
    service, scope, engine, model, state, _, docker = await prepare(composed)
    count = docker.dispatches
    if change == 'checkpoint_result':
        state['results']['base']['summary'] = 'Unsaved replacement summary'
    else:
        with service.repository.store.transaction() as session:
            run = session.get(Objective, scope.run_id)
            data = copy.deepcopy(run.data)
            grant_id = data['sourceComposition']['id']
            data['compositionResults'][grant_id]['receiptHash'] = '0'*64
            run.data = data
    with pytest.raises(DomainError) as failure:
        await engine._plan(state)
    assert failure.value.code == ('artifact_conflict' if change == 'checkpoint_result'
                                  else 'composition_result_conflict')
    assert not model.messages and docker.dispatches == count


@pytest.mark.parametrize('composed', [['base', 'app']], indirect=True)
async def test_engine_rejects_explicit_old_output_instead_of_mixing_dependency_versions(composed):
    service, scope, engine, model, state, original, docker = await prepare(composed)
    count = docker.dispatches
    old = next(ref['id'] for ref in original['base']['artifacts'] if ref['path'].endswith('.step'))
    task = TaskSpec(id='verify', capability='validation', objective='Verify compiled candidates',
        depends_on=['base', 'app'], input_artifact_ids=[old])
    context = await service.repository.context(scope)
    with pytest.raises(DomainError) as failure:
        await engine._prepare_inputs(scope, task.model_dump(mode='json'), context, state['results'])
    assert failure.value.code == 'artifact_scope_conflict'
    task = task.model_copy(update={'input_artifact_ids': []})
    pinned = await engine._prepare_inputs(scope, task.model_dump(mode='json'), context, state['results'])
    assert old not in pinned['task']['input_artifact_ids']
    output_paths = [ref['path'] for ref in pinned['context']['artifacts'] if ref['path'].startswith('outputs/')]
    assert len(output_paths) == len(set(output_paths))
    assert not model.messages and docker.dispatches == count
