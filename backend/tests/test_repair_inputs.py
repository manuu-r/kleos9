from datetime import UTC, datetime, timedelta

import pytest
from test_validation import setup as setup

from kleos.artifacts.service import Artifact
from kleos.contracts.models import DomainError, TaskResult, TaskSpec
from kleos.orchestration.engine import RunScope
from kleos.projects.graph_repository import GraphRepository
from kleos.projects.store import Objective, Project


async def test_repair_snapshot_supplies_exact_previous_candidates_without_acceptance(setup):
    store, prior, artifacts, baseline = setup
    with store.transaction() as session:
        session.get(Objective, prior.run_id).status = 'blocked'
    admitted = store.submit(prior.actor, prior.project_id, 'Repair only failed bracket', prior.revision, 'repair')
    scope = RunScope(actor=prior.actor, project_id=prior.project_id, run_id=admitted['runId'],
        revision=admitted['revision'], deadline_at=datetime.now(UTC) + timedelta(minutes=5))
    repo = GraphRepository(store)
    with pytest.raises(DomainError, match='authorized task input'):
        await repo.resolve_artifacts(scope, [artifacts[0].id])
    with store.transaction() as session:
        run = session.get(Objective, scope.run_id)
        run.data = {**run.data, 'repairSourceRunId': prior.run_id,
            'repairInputs': [ref.model_dump(mode='json') for ref in artifacts]}
    context = await repo.context(scope)
    assert {ref.path: ref.id for ref in context.artifacts} == {
        'part.py': artifacts[0].id, 'other.py': baseline['other.py'], 'outputs/bracket.step': artifacts[1].id}
    assert await repo.resolve_artifacts(scope, [artifacts[0].id]) == [artifacts[0]]
    await repo.publish(scope, TaskResult(task_id='inspect', status='succeeded', summary='Read candidate', artifacts=artifacts))
    with store.transaction() as session:
        project = session.get(Project, scope.project_id)
        assert project.data['sourceManifest'] == baseline
        assert project.data.get('acceptedReleaseId') is None
        session.get(Artifact, artifacts[0].id).sha256 = '0' * 64
    for operation in [lambda: repo.context(scope), lambda: repo.resolve_artifacts(scope, [artifacts[0].id])]:
        with pytest.raises(DomainError, match='snapshot changed'):
            await operation()


async def test_repair_snapshot_requires_matching_source_run_and_unique_paths(setup):
    store, scope, artifacts, _ = setup
    repo = GraphRepository(store)
    with store.transaction() as session:
        run = session.get(Objective, scope.run_id)
        run.data = {**run.data, 'repairSourceRunId': 'wrong',
            'repairInputs': [artifacts[0].model_dump(mode='json')]}
    with pytest.raises(DomainError, match='provenance changed'):
        await repo.context(scope)
    with store.transaction() as session:
        run = session.get(Objective, scope.run_id)
        run.data = {**run.data, 'repairSourceRunId': scope.run_id,
            'repairInputs': [artifacts[0].model_dump(mode='json')] * 2}
    with pytest.raises(DomainError, match='provenance changed'):
        await repo.resolve_artifacts(scope, [artifacts[0].id])


@pytest.mark.parametrize('key', ['repairAllowedPaths', 'editAllowedPaths'])
async def test_plan_cannot_expand_user_source_edit_scope(setup, key):
    store, scope, _, _ = setup
    with store.transaction() as session:
        run = session.get(Objective, scope.run_id)
        run.data = {**run.data, key: ['part.py']}
    task = TaskSpec(id='bad', capability='cad', objective='Unexpected extra changes', allowed_paths=['other.py'])
    with pytest.raises(DomainError, match='authorized source edit scope'):
        await GraphRepository(store).publish_plan(scope, tasks=[task], superseded={}, turn=1, summary='', question=None)
