"""Print-preparation observations only; no upload, print or physical acceptance."""
from datetime import UTC, datetime, timedelta

import test_slicing

from kleos.contracts.models import TaskResult, TaskSpec
from kleos.execution.capacity import CapacityService
from kleos.infrastructure.schema import metadata
from kleos.orchestration.engine import RunScope
from kleos.orchestration.external_waits import ExternalWaits
from kleos.projects.graph_repository import GraphRepository
from kleos.projects.store import Objective

prepared = test_slicing.prepared


async def test_queued_capacity_wait_does_not_replan_or_hold_compute(prepared):
    preparations, project_id, body, driver = prepared
    projects = preparations.projects
    metadata().create_all(projects.engine)
    clock = [datetime.now(UTC).timestamp()]
    preparations.clock = lambda: clock[0]
    capacity = CapacityService(projects)
    preparations.capacity = capacity
    capacity.reserve('native:fixture', project_id, 'a'*64, {'cpus': 3, 'memoryBytes': 1024**3, 'scratchBytes': 1024**3})
    job_id = preparations.create(test_slicing.ACTOR, project_id, body, 'prepare')['preparation']['id']
    scope = RunScope(actor=test_slicing.ACTOR, project_id=project_id, run_id='run', revision=1,
        deadline_at=datetime.fromtimestamp(clock[0], UTC)+timedelta(days=1))
    task = TaskSpec(id='prepare', capability='fabrication', objective='Wait for the existing print preparation', input_artifact_ids=['source'])
    repo = GraphRepository(projects)
    with projects.transaction() as session:
        session.add(Objective(id=scope.run_id, project_id=project_id, revision=1, status='running', data={
            'scope': scope.model_dump(mode='json'), 'tasks': [task.model_dump(mode='json')], 'runtimePins': {}}))
    bridge = ExternalWaits(projects, repo, preparations=preparations, clock=lambda: clock[0])
    ident = await bridge.bind(scope, task, 'print_preparation', job_id)
    await repo.publish(scope, TaskResult(task_id=task.id, status='waiting', summary='Awaiting offline preparation', wait_ids=[ident]))
    with projects.transaction() as session:
        run = session.get(Objective, scope.run_id)
        run.status, run.data = 'waiting', {**run.data, 'interruptId': 'print-interrupt',
            'question': {'kind': 'external_wait', 'waitIds': [ident]}}
    assert not await preparations.process_next() and driver.started == []
    clock[0] += 31
    assert (await bridge.poll_due())['woken'] == 0
    assert capacity.usage()['jobs'] == 1  # Only the unrelated fixture compute reservation.
    capacity.release('native:fixture', 'a'*64, {'kind': 'container-removed', 'identity': 'fixture'})
    assert await preparations.process_next()
    await preparations.reconcile()
    clock[0] += 31
    assert (await bridge.poll_due())['woken'] == 1
    with projects.transaction() as session:
        answer = session.get(Objective, scope.run_id).data['pendingAnswer']['value']
    result = (await bridge.resolve(scope, [ident], answer))[0]
    assert result.status.value == 'blocked' and result.observations[0].details['status'] == 'ready'
    assert result.observations[0].details['physicalAcceptance'] is False
    assert not result.artifacts  # A preparation claim cannot silently introduce accepted files.
