"""Automatic source staging differs from explicit derived inputs and check inputs."""
import copy
import json

import pytest
from test_composition import composed as composed
from test_composition_planning import CapturedModel
from test_composition_planning import prepare as prepare_compiled
from test_composition_successors import append_repair
from test_dependency_sources import prepare, produce, setup

from kleos.contracts.models import TaskSpec
from kleos.execution.docker import NativeSettings
from kleos.execution.gateway import NativeGateway
from kleos.orchestration.engine import PlanDecision
from kleos.orchestration.planner import ModelPlanner
from kleos.projects.store import Objective, Project


@pytest.mark.parametrize('lane', ['cad', 'pcb', 'firmware', 'application'])
@pytest.mark.parametrize('explicit_export', [False, True])
async def test_native_dependencies_stage_sources_but_preserve_explicit_export_identity(tmp_path, lane, explicit_export):
    env = await setup(tmp_path)
    try:
        producer = await produce(env, 'producer')
        source, exported = producer.artifacts
        task = TaskSpec(id='consumer', capability=lane, objective='Use the authorized dependency',
            depends_on=['producer'], allowed_paths=['derived.py'],
            input_artifact_ids=[env.base.id, *([exported.id] if explicit_export else [])])
        pinned = await prepare(env, task)
        ids = pinned['task']['input_artifact_ids']
        assert set(ids) == {source.id, *([exported.id] if explicit_export else [])}
        assert pinned['task']['allowed_paths'] == ['derived.py']
        context = {ref['id']: ref for ref in pinned['context']['artifacts']}
        assert context[exported.id] == exported.model_dump(mode='json')
        assert context[source.id] == source.model_dump(mode='json')
        assert env.base.id not in context
    finally:
        env.store.engine.dispose()


@pytest.mark.parametrize('lane', ['validation', 'research'])
async def test_non_native_dependencies_keep_source_and_export_inputs(tmp_path, lane):
    env = await setup(tmp_path)
    try:
        producer = await produce(env, 'producer')
        task = TaskSpec(id='observer', capability=lane, objective='Inspect exact candidate artifacts',
            depends_on=['producer'])
        pinned = await prepare(env, task)
        assert pinned['task']['input_artifact_ids'] == [ref.id for ref in producer.artifacts]
        assert pinned['task']['allowed_paths'] == []
    finally:
        env.store.engine.dispose()


async def test_explicit_export_reaches_native_request_under_its_exact_read_only_input_path(tmp_path):
    env = await setup(tmp_path)
    try:
        producer = await produce(env, 'producer')
        source, exported = producer.artifacts
        task = TaskSpec(id='consumer', capability='cad', objective='Read explicitly requested STEP',
            depends_on=['producer'], input_artifact_ids=[exported.id], allowed_paths=['derived.py'])
        pinned = await prepare(env, task)
        selected = TaskSpec.model_validate(pinned['task'])
        gateway = NativeGateway(env.store, env.artifacts,
            NativeSettings(tmp_path/'control', {'cad': 'sha256:'+'a'*64}), object(), env.repo)
        # No Docker or source execution: this is the real authorized request builder.
        resolved = await gateway.authorize(env.scope, selected, {})
        job = gateway._job(env.scope, selected, resolved)
        inputs = {item['artifactId']: item for item in job.request['sources']}
        assert set(inputs) == {source.id, exported.id}
        assert inputs[exported.id]['path'] == f'inputs/{exported.id}/part.step'
        assert inputs[exported.id]['sha256'] == exported.sha256
        assert job.request['allowedPaths'] == ['derived.py']
    finally:
        env.store.engine.dispose()


@pytest.mark.parametrize('composed', [['base', 'app']], indirect=True)
async def test_actual_planner_packet_labels_historical_compilation_after_repair_append(composed):
    service, scope, engine, _, state, original, docker = await prepare_compiled(composed)
    tasks = composed[2]
    source = next(ref for ref in original['base']['artifacts'] if ref['path'] == 'model.py')
    repair = await append_repair(service, scope, tasks, source)
    state['tasks'][repair.id] = repair.model_dump(mode='json')
    state['turns'] = 2

    class QuestionModel(CapturedModel):
        async def ainvoke(self, messages):
            await super().ainvoke(messages)
            return PlanDecision(question='Which requested repair should the next task address?')

    model = QuestionModel()
    engine.planner = ModelPlanner(model, ['cad', 'application'])
    with service.repository.store.transaction() as session:
        before = copy.deepcopy(session.get(Objective, scope.run_id).data)
        manifest = copy.deepcopy(session.get(Project, scope.project_id).data['sourceManifest'])
    dispatches = docker.dispatches
    result = await engine._plan(state)
    assert result['question'] and not result['done']
    packet = json.loads(model.messages[0][1]['content'])
    assert packet['artifactContext']['historical'] is True
    assert packet['artifactContext']['historicalTaskIds'] == ['app', 'base']
    assert packet['artifactContext']['compositionId'] == before['sourceComposition']['id']
    assert 'cannot establish current validation or release coverage' in packet['artifactContext']['authority']
    dependency = await service.repository.dependency_artifacts(scope, ['base', 'app'])
    for observed in packet['results']:
        assert observed['artifacts'] == [ref.model_dump(mode='json') for ref in dependency[observed['task_id']]]
    with service.repository.store.transaction() as session:
        after = session.get(Objective, scope.run_id).data
        for key in ['results', 'sourceComposition', 'sourceCompositions', 'compositionResults']:
            assert after[key] == before[key]
        assert session.get(Project, scope.project_id).data['sourceManifest'] == manifest
    assert state['results'] == original and docker.dispatches == dispatches
