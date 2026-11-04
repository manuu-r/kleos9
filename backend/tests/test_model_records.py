import asyncio
import json

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from test_application_services import services as services
from test_budget_views import reserve
from test_orchestration import Planner, Repo, scope, task

from kleos.access.service import OrganizationMember
from kleos.api import create_app
from kleos.contracts.models import Actor, DomainError, TaskResult
from kleos.orchestration.engine import ObjectiveEngine, PlanDecision
from kleos.orchestration.models import ModelAttempt
from kleos.orchestration.observability import model_task


async def test_model_record_requires_project_and_admin_and_excludes_raw_fields(services):
    actor = services.authenticate({}, {}, 'GET')
    project = services.projects.create(actor, 'Private model record', 'create')['projectId']
    ledger, ident = reserve(services, actor, project, key='run')
    ledger.settle(ident, 'uncertain')
    with services.projects.transaction() as session:
        attempt = session.get(ModelAttempt, ident)
        attempt.data = {**attempt.data, 'requestId': 'PRIVATE_REQUEST_ID', 'reasoning': 'PRIVATE_REASONING',
            'prompt': 'PRIVATE_PROMPT', 'usage': None,
            'model': {**attempt.data['model'], 'baseUrl': 'https://PRIVATE_ENDPOINT.example'}}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(services)),
                                base_url='http://localhost') as client:
        response = await client.get(f'/api/v1/invocations/{ident}/transcript')
    assert response.status_code == 200
    record = response.json()
    assert record['status'] == 'uncertain' and record['usageKnown'] is False
    assert record['usage'] == {} and record['projectId'] == project
    assert 'PRIVATE_' not in json.dumps(record)
    with pytest.raises(DomainError, match='not found'):
        services.query(Actor(tenant_id='foreign', user_id=actor.user_id), 'invocation_transcript', {'invocationId': ident})
    with services.projects.transaction() as session:
        session.get(OrganizationMember, (actor.tenant_id, actor.user_id)).role = 'editor'
    with pytest.raises(DomainError, match='administrator'):
        services.query(actor, 'invocation_transcript', {'invocationId': ident})


def test_stale_async_task_context_cannot_attribute_another_run(services):
    actor = services.authenticate({}, {}, 'GET')
    project = services.projects.create(actor, 'Task attribution', 'create')['projectId']
    token = model_task.set(('wrong-run', 'cad-task'))
    try:
        _, ident = reserve(services, actor, project, key='run')
    finally:
        model_task.reset(token)
    with services.projects.transaction() as session:
        assert session.get(ModelAttempt, ident).data['taskId'] is None


async def test_parallel_specialists_keep_separate_task_attribution():
    ready = asyncio.Event()
    arrivals = []
    observed = {}
    class Specialist:
        async def execute(self, run_scope, task_spec, context):
            arrivals.append(task_spec.id)
            if len(arrivals) == 2:
                ready.set()
            await asyncio.wait_for(ready.wait(), timeout=1)
            observed[task_spec.id] = model_task.get()
            return TaskResult(task_id=task_spec.id, status='succeeded', summary='Fixture observation')
    engine = ObjectiveEngine(Repo(), Planner([PlanDecision(tasks=[task('cad'), task('pcb', 'pcb')]),
        PlanDecision(done=True)]), Specialist(), checkpointer=InMemorySaver())
    await engine.start(scope(), 'Coordinate two independent tasks')
    assert observed == {'cad': ('run', 'cad'), 'pcb': ('run', 'pcb')}
    assert model_task.get() is None
