"""Historical reads and exact source successors; native IO is an inert fixture."""
import copy

import pytest
import test_composition as fixtures
from langgraph.checkpoint.memory import InMemorySaver
from test_composition_results import compile_targets
from test_validation_policies import registry

from kleos.artifacts.service import Artifact
from kleos.contracts.models import DomainError, TaskResult, TaskSpec
from kleos.execution.gateway import NativeJob
from kleos.execution.protocol import fingerprint
from kleos.orchestration.engine import ObjectiveEngine
from kleos.projects.composition_results import (
    CompositionResults,
    effective_artifacts,
    historical_input_refs,
    input_artifacts,
    input_projection,
)
from kleos.projects.store import Objective, Project
from kleos.validation.policies import published_native_rows
from kleos.validation.service import Candidate, ValidationService

composed = fixtures.composed


async def append_repair(service, scope, tasks, source, *, declared=True, task_id='repair'):
    task = TaskSpec(id=task_id, capability='cad', objective='Repair the same source after checking',
        input_artifact_ids=[source['id']], allowed_paths=['model.py'], depends_on=['base'] if declared else [])
    await service.repository.publish_plan(scope, tasks=[*tasks, task], superseded={}, turn=1,
        summary='Explicit source-only follow-on repair', question=None)
    return task


@pytest.mark.parametrize('composed', [['base', 'app']], indirect=True)
async def test_appended_plan_reads_old_compilation_without_current_release_authority(composed):
    service, scope, tasks, _, refs, _ = composed
    grant = service.freeze(scope)
    results = await compile_targets(service, scope, grant)
    CompositionResults(service).record(scope, grant['id'], results)
    source = next(ref for ref in refs['base'] if ref['path'] == 'model.py')
    await append_repair(service, scope, tasks, source)
    with service.repository.store.transaction() as session:
        run = session.get(Objective, scope.run_id)
        inputs = input_projection(session, run)
        assert inputs['historical'] and inputs['compositionId'] == grant['id']
        assert inputs['historicalTaskIds'] == ['app', 'base']
        expected = {ref['id'] for ref in results['base']['artifacts']}
        assert {ref.id for ref in input_artifacts(session, run, 'base') if ref.path.startswith('outputs/')} == expected
        assert expected <= historical_input_refs(session, run).keys()
        with pytest.raises(DomainError):
            effective_artifacts(session, run, 'base')
        with pytest.raises(DomainError):
            published_native_rows(session, run)
    # Actual repository APIs still enforce current actor/run and exact read IDs.
    dependencies = await service.repository.dependency_artifacts(scope, ['base'])
    assert {ref.id for ref in dependencies['base'] if ref.path.startswith('outputs/')} == expected
    resolved = await service.repository.resolve_artifacts(scope, list(expected))
    assert {ref.id for ref in resolved} == expected


@pytest.mark.parametrize('composed', [['base', 'app']], indirect=True)
@pytest.mark.parametrize('change', ['receipt', 'source', 'task', 'authority', 'cancel', 'stale', 'missing-plan'])
async def test_historical_inputs_never_hide_invalid_current_receipt_or_authority(composed, change):
    service, scope, tasks, _, refs, _ = composed
    grant = service.freeze(scope)
    results = await compile_targets(service, scope, grant)
    CompositionResults(service).record(scope, grant['id'], results)
    await append_repair(service, scope, tasks, next(ref for ref in refs['base'] if ref['path'] == 'model.py'))
    with service.repository.store.transaction() as session:
        run = session.get(Objective, scope.run_id)
        data = copy.deepcopy(run.data)
        if change == 'receipt':
            data['compositionResults'][grant['id']]['targets']['base']['result']['summary'] = 'Corrupt'
        elif change == 'source':
            session.get(Artifact, grant['sources'][0]['id']).sha256 = 'f'*64
        elif change == 'task':
            data['tasks'][0]['objective'] += ' altered'
        elif change == 'authority':
            project = session.get(Project, scope.project_id)
            project.data = {**project.data, 'requirements': {'different': True}}
        elif change == 'cancel':
            run.status = 'cancelled'
        elif change == 'stale':
            session.get(Project, scope.project_id).advance_revision()
        else:
            data['sourceComposition'].pop('authoringPlan')
        run.data = data
    with service.repository.store.transaction() as session, pytest.raises(DomainError):
        input_projection(session, session.get(Objective, scope.run_id))


@pytest.mark.parametrize('composed', [['base', 'app']], indirect=True)
async def test_exact_source_successor_gets_new_grant_and_recompiles_final_set(composed):
    service, scope, tasks, baseline, refs, authoring_docker = composed
    first = service.freeze(scope)
    initial = await compile_targets(service, scope, first)
    first_receipt = CompositionResults(service).record(scope, first['id'], initial)
    source = next(ref for ref in refs['base'] if ref['path'] == 'model.py')
    task = await append_repair(service, scope, tasks, source)
    with service.repository.store.transaction() as session:
        saved = copy.deepcopy(session.get(Objective, scope.run_id).data['results'])
    pinned = await ObjectiveEngine(service.repository, None, None, checkpointer=InMemorySaver())._prepare_inputs(
        scope, task.model_dump(mode='json'), await service.repository.context(scope), saved)
    task = TaskSpec.model_validate(pinned['task'])
    assert task.input_artifact_ids == [source['id']]
    assert any(ref['path'].startswith('outputs/') for ref in pinned['context']['artifacts'])
    service.gateway.docker = authoring_docker
    await service.gateway.invoke(scope, task, 'patch', {'actionId': 'patch', 'path': 'model.py',
        'content': 'Explicit repaired source fixture', 'expectedSha256': source['sha256']})
    await service.gateway.invoke(scope, task, 'build', {'actionId': 'build'})
    finished = await service.gateway.invoke(scope, task, 'finish', {'actionId': 'finish'})
    await service.repository.publish(scope, TaskResult(task_id=task.id, status='succeeded', summary='Repair candidate',
        artifacts=finished['artifacts']))
    replacement = next(ref for ref in finished['artifacts'] if ref['path'] == 'model.py')
    second = service.freeze(scope)
    assert first['id'] != second['id']
    assert {author['taskId'] for author in second['authors']} == {'base', 'app', 'repair'}
    assert {target['taskId'] for target in second['targets']} == {'repair', 'app'}
    assert second['replacedTargets'] == {'base': 'repair'}
    assert second['sourceLineage']['model.py'] == [source['id'], replacement['id']]
    assert second['closure']['model.py'] == replacement['id']
    # Before second compilation, prior outputs remain explicitly historical reads.
    with service.repository.store.transaction() as session:
        run = session.get(Objective, scope.run_id)
        inputs = input_projection(session, run)
        assert inputs['historical'] and inputs['compositionId'] == first['id']
        assert replacement['id'] in {ref.id for ref in inputs['artifacts']['repair']}
        assert run.data['compositionResults'][first['id']] == first_receipt
    final = await compile_targets(service, scope, second)
    CompositionResults(service).record(scope, second['id'], final)
    with service.repository.store.transaction() as session:
        run = session.get(Objective, scope.run_id)
        rows = published_native_rows(session, run)
        assert source['id'] not in {row.id for row in rows}
        assert replacement['id'] in {row.id for row in rows}
        assert {row.id for row in rows if row.details['role'] == 'output'} == {
            ref['id'] for value in final.values() for ref in value['artifacts']}
        assert all(not ref.path.startswith('outputs/') for ref in effective_artifacts(session, run, 'base'))
        ids = [row.id for row in rows]
        assert session.get(Project, scope.project_id).data['sourceManifest'] == baseline
    checker = ValidationService(service.repository.store, registry('cad', 'application'), repository=service.repository)
    candidate_id = checker.prepare(scope, ids, 'digital.cad-application', 'final-closure')
    with service.repository.store.transaction() as session:
        assert session.get(Candidate, candidate_id).data['closure'] == second['closure']
        assert session.get(Objective, scope.run_id).data['sourceCompositions'][first['id']] == first


async def produce(service, scope, plan, task, turn, patches):
    await service.repository.publish_plan(scope, tasks=[*plan, task], superseded={}, turn=turn,
        summary='Explicit candidate source edits', question=None)
    for index, (path, checksum) in enumerate(patches.items()):
        await service.gateway.invoke(scope, task, 'patch', {'actionId': 'patch-'+str(index), 'path': path,
            'content': task.id+' fixture '+path, 'expectedSha256': checksum})
    await service.gateway.invoke(scope, task, 'build', {'actionId': 'build'})
    result = await service.gateway.invoke(scope, task, 'finish', {'actionId': 'finish'})
    await service.repository.publish(scope, TaskResult(task_id=task.id, status='succeeded', summary='Candidate',
        artifacts=result['artifacts']))
    return result


@pytest.mark.parametrize('composed', [['base', 'app']], indirect=True)
@pytest.mark.parametrize('change', ['undeclared', 'wrong-hash', 'wrong-id', 'different-lane', 'fork'])
async def test_successor_ownership_rejects_ambiguous_or_forged_read_ancestry(composed, change):
    service, scope, tasks, _, refs, _ = composed
    source = next(ref for ref in refs['base'] if ref['path'] == 'model.py')
    task = TaskSpec(id='repair', capability='application' if change == 'different-lane' else 'cad',
        objective='Repair exact source', input_artifact_ids=[source['id']], allowed_paths=['model.py'],
        depends_on=[] if change == 'undeclared' else ['base'])
    result = await produce(service, scope, tasks, task, 1, {'model.py': source['sha256']})
    if change in {'wrong-hash', 'wrong-id'}:
        with service.repository.store.transaction() as session:
            row = session.get(Artifact, result['artifacts'][0]['id'])
            job = session.get(NativeJob, row.details['nativeJobId'])
            request = copy.deepcopy(job.request)
            request['sources'][0]['sha256' if change == 'wrong-hash' else 'artifactId'] = 'f'*64
            job.request, job.request_hash = request, fingerprint(request)
    elif change == 'fork':
        branch = task.model_copy(update={'id': 'fork'})
        await produce(service, scope, [*tasks, task], branch, 2, {'model.py': source['sha256']})
    with pytest.raises(DomainError):
        service.freeze(scope)


@pytest.mark.parametrize('composed', [['base', 'app']], indirect=True)
async def test_partial_source_ownership_cannot_retire_predecessor_export_target(composed):
    service, scope, tasks, _, refs, _ = composed
    source = next(ref for ref in refs['base'] if ref['path'] == 'model.py')
    whole = TaskSpec(id='whole', capability='cad', objective='Two-source candidate',
        input_artifact_ids=[source['id']], allowed_paths=['model.py', 'zhelper.py'], depends_on=['base'])
    result = await produce(service, scope, tasks, whole, 1, {'model.py': source['sha256'], 'zhelper.py': None})
    sources = [ref for ref in result['artifacts'] if not ref['path'].startswith('outputs/')]
    partial = TaskSpec(id='partial', capability='cad', objective='Partial source successor',
        input_artifact_ids=[ref['id'] for ref in sources], allowed_paths=['model.py'], depends_on=['whole'])
    await produce(service, scope, [*tasks, whole], partial, 2,
        {'model.py': next(ref['sha256'] for ref in sources if ref['path'] == 'model.py')})
    with pytest.raises(DomainError, match='ownership'):
        service.freeze(scope)


@pytest.mark.parametrize('composed', [['base', 'app']], indirect=True)
async def test_carried_old_outputs_never_restore_stale_read_ids(composed):
    service, scope, tasks, _, refs, _ = composed
    grant = service.freeze(scope)
    results = await compile_targets(service, scope, grant)
    CompositionResults(service).record(scope, grant['id'], results)
    old = next(ref for ref in refs['base'] if ref['path'].startswith('outputs/'))
    check = TaskSpec(id='old-check', capability='validation', objective='Carry old historical ID', depends_on=['base'])
    await service.repository.publish_plan(scope, tasks=[*tasks, check], superseded={}, turn=1, summary='Inspect', question=None)
    await service.repository.publish(scope, TaskResult(task_id=check.id, status='blocked', summary='Historical echo', artifacts=[old]))
    with service.repository.store.transaction() as session:
        run = session.get(Objective, scope.run_id)
        assert input_projection(session, run)['artifacts'][check.id] == []
        assert old['id'] not in historical_input_refs(session, run)
    with pytest.raises(DomainError):
        await service.repository.resolve_artifacts(scope, [old['id']])


@pytest.mark.parametrize('composed', [['base', 'app']], indirect=True)
async def test_rehashed_plan_cannot_rewrite_actual_historical_producer_task(composed):
    service, scope, tasks, _, refs, _ = composed
    grant = service.freeze(scope)
    results = await compile_targets(service, scope, grant)
    CompositionResults(service).record(scope, grant['id'], results)
    await append_repair(service, scope, tasks, next(ref for ref in refs['base'] if ref['path'] == 'model.py'))
    with service.repository.store.transaction() as session:
        run = session.get(Objective, scope.run_id)
        data = copy.deepcopy(run.data)
        original = grant['id']
        forged = copy.deepcopy(grant)
        for task in data['tasks']:
            if task['id'] == 'base':
                task['objective'] = 'Forged old task'
        for field in ('tasks', 'authoringTasks'):
            for task in forged['authoringPlan'][field]:
                if task['id'] == 'base':
                    task['objective'] = 'Forged old task'
        forged['planHash'] = fingerprint(forged['authoringPlan'])
        forged['id'] = forged['snapshotHash'] = fingerprint({key: value for key, value in forged.items()
                                                            if key not in {'id', 'snapshotHash'}})
        receipt = data['compositionResults'].pop(original)
        receipt['grantId'] = forged['id']
        receipt['receiptHash'] = fingerprint({key: value for key, value in receipt.items() if key != 'receiptHash'})
        data['sourceComposition'], data['sourceCompositions'] = forged, {forged['id']: forged}
        data['compositionResults'][forged['id']] = receipt
        run.data = data
    with service.repository.store.transaction() as session, pytest.raises(DomainError, match='actual producer job'):
        input_projection(session, session.get(Objective, scope.run_id))
