"""Composed final compilation uses actual services with fixed inert native responses."""
import pytest
from sqlalchemy import select
from test_composition import composed as composed
from test_composition_execution import CompilationDocker

from kleos.artifacts.service import Artifact
from kleos.contracts.models import DomainError, TaskResult
from kleos.execution.gateway import NativeJob
from kleos.orchestration.composition import FinalCompilation
from kleos.projects.store import Objective, Project
from kleos.validation.policies import authored_native_rows, published_native_rows


@pytest.mark.parametrize('composed', [['base', 'app']], indirect=True)
async def test_final_compilation_joins_lanes_without_new_sources_models_or_repeated_execution(composed):
    service, scope, tasks, baseline, refs, _ = composed
    grant = service.freeze(scope)
    docker = CompilationDocker(grant)
    service.gateway.docker = docker
    final = FinalCompilation(service.repository, service.artifacts, service.gateway)
    with service.repository.store.transaction() as session:
        raw_results = session.get(Objective, scope.run_id).data['results']
    assert await final.needed(scope, {task.id: task.model_dump(mode='json') for task in tasks}, raw_results)
    await final.run(scope)
    count = docker.dispatches
    assert not await final.needed(scope, {task.id: task.model_dump(mode='json') for task in tasks}, raw_results)
    projected = await service.repository.planning_results(scope, [TaskResult.model_validate(r) for r in raw_results.values()])
    dependencies = await service.repository.dependency_artifacts(scope, [task.id for task in tasks])
    for result in projected:
        assert result.artifacts == dependencies[result.task_id]
        assert result.artifacts != TaskResult.model_validate(raw_results[result.task_id]).artifacts
    await final.run(scope)
    assert docker.dispatches == count and docker.calls == ['build', 'finish', 'build', 'render', 'finish']
    with service.repository.store.transaction() as session:
        run = session.get(Objective, scope.run_id)
        assert run.data['results'] == raw_results
        authored = authored_native_rows(session, run)
        canonical = published_native_rows(session, run)
        assert {row.id for row in authored if row.details['role'] == 'source'} == {
            row.id for row in canonical if row.details['role'] == 'source'}
        assert {row.id for row in authored if row.details['role'] == 'output'}.isdisjoint({
            row.id for row in canonical if row.details['role'] == 'output'})
        assert all(row.details['sourceClosure'] == grant['closure'] for row in canonical if row.details['role'] == 'output')
        assert session.get(Project, scope.project_id).data['sourceManifest'] == baseline
        assert len(list(session.scalars(select(NativeJob)))) == 4
        assert all(session.get(Artifact, ident) for ident in grant['closure'].values())


async def test_multiple_same_lane_targets_block_before_any_compilation_dispatch(composed):
    service, scope, _, _, _, docker = composed
    count = docker.dispatches
    final = FinalCompilation(service.repository, service.artifacts, service.gateway)
    with pytest.raises(DomainError) as caught:
        await final.run(scope)
    assert caught.value.code == 'composition_target_unqualified'
    assert docker.dispatches == count


@pytest.mark.parametrize('composed', [['base', 'app']], indirect=True)
async def test_failed_final_build_cannot_replace_the_authoring_outputs(composed):
    service, scope, _, _, _, _ = composed
    grant = service.freeze(scope)
    docker = CompilationDocker(grant)
    docker.fail_action = 'build'
    service.gateway.docker = docker
    final = FinalCompilation(service.repository, service.artifacts, service.gateway)
    with pytest.raises(DomainError, match='successful fixed action'):
        await final.run(scope)
    with service.repository.store.transaction() as session:
        run = session.get(Objective, scope.run_id)
        assert not run.data.get('compositionResults')
        assert {row.id for row in published_native_rows(session, run)} == {
            row.id for row in authored_native_rows(session, run)}


@pytest.mark.parametrize('composed', [['base', 'app']], indirect=True)
async def test_completed_compilation_rejects_explicit_superseded_output_selection(composed):
    service, scope, _, _, _, _ = composed
    grant = service.freeze(scope)
    service.gateway.docker = CompilationDocker(grant)
    with service.repository.store.transaction() as session:
        run = session.get(Objective, scope.run_id)
        old_output = next(row.id for row in authored_native_rows(session, run) if row.details['role'] == 'output')
    final = FinalCompilation(service.repository, service.artifacts, service.gateway)
    await final.run(scope)
    with pytest.raises(DomainError) as caught:
        await service.repository.resolve_artifacts(scope, [old_output])
    assert caught.value.code == 'artifact_scope_conflict'
    sources = await service.repository.resolve_artifacts(scope, list(grant['closure'].values()))
    assert {ref.id for ref in sources} == set(grant['closure'].values())


@pytest.mark.parametrize('composed', [['base', 'app']], indirect=True)
async def test_needed_rejects_corrupt_completion_instead_of_rebuilding(composed):
    service, scope, tasks, _, _, _ = composed
    grant = service.freeze(scope)
    service.gateway.docker = CompilationDocker(grant)
    final = FinalCompilation(service.repository, service.artifacts, service.gateway)
    await final.run(scope)
    with service.repository.store.transaction() as session:
        run = session.get(Objective, scope.run_id)
        results = run.data['results']
        receipt = {**run.data['compositionResults'][grant['id']], 'receiptHash': 'corrupt'}
        run.data = {**run.data, 'compositionResults': {grant['id']: receipt}}
    count = service.gateway.docker.dispatches
    with pytest.raises(DomainError) as caught:
        await final.needed(scope, {task.id: task.model_dump(mode='json') for task in tasks}, results)
    assert caught.value.code == 'composition_result_conflict'
    assert service.gateway.docker.dispatches == count
