"""Host waiting tools never grant external-effect authority or extra model actions."""
import pytest
import test_external_waits
from pydantic import ValidationError
from test_orchestration import scripted_model

from kleos.capabilities.external_wait_tools import register_external_wait
from kleos.capabilities.registry import CapabilityRegistry
from kleos.contracts.models import DomainError
from kleos.orchestration.external_waits import ExternalWait
from kleos.orchestration.specialist import ModelSpecialist
from kleos.projects.store import Objective

waiting = test_external_waits.waiting


async def test_harness_pauses_immediately_and_receipt_replay_makes_no_more_model_or_provider_calls(waiting):
    store, repo, bridge, _, provider, scope, task, ident, _ = waiting
    with store.transaction() as session:
        job_id = session.get(ExternalWait, ident).job_id
        session.get(Objective, scope.run_id).status = 'running'
    registry = CapabilityRegistry()
    key = register_external_wait(registry, bridge, repo, kind='sourcing', provider='fixture', provider_version='1', qualified=True)
    tools = registry.tools(registry.snapshot([key]))
    model = scripted_model([{'action': 'tool', 'tool': 'sourcing.wait.fixture', 'arguments': {'jobId': job_id}}])
    harness = ModelSpecialist(model, tools, repo)
    context = await repo.context(scope)
    first = await harness.execute(scope, task, context)
    assert first.status.value == 'waiting' and first.wait_ids == [ident]
    assert model.calls == 1 and provider.calls == 0
    assert await harness.execute(scope, task, context) == first
    assert model.calls == 1 and provider.calls == 0
    assert first.observations[0].status.value == 'UNKNOWN' and not first.observations[0].independent
    manifest = registry.catalog()[0]['manifest']
    assert manifest['transport'] == 'internal' and manifest['effect'] == 'artifact_write'


async def test_wait_arguments_cannot_override_deadline_account_or_qualification(waiting):
    store, repo, bridge, _, _, scope, task, ident, _ = waiting
    with store.transaction() as session:
        job_id = session.get(ExternalWait, ident).job_id
    registry = CapabilityRegistry()
    with pytest.raises(DomainError, match='Unqualified'):
        register_external_wait(registry, bridge, repo, kind='sourcing', provider='fixture', provider_version='1', qualified=False)
    key = register_external_wait(registry, bridge, repo, kind='sourcing', provider='fixture', provider_version='1', qualified=True)
    tool = registry.tools(registry.snapshot([key]))[0]
    for extra in ({'deadlineAt': '2099-01-01'}, {'userId': 'other'}, {'qualified': True}, {'submit': True}):
        with pytest.raises(ValidationError):
            await tool.invoke(scope, task, {'actionId': 'wait', 'jobId': job_id, **extra})
    with pytest.raises(DomainError, match='authority differs'):
        await tool.invoke(scope, task.model_copy(update={'max_actions': 64}), {'actionId': 'wait', 'jobId': job_id})
    registry.revoke(key)
    with pytest.raises(DomainError, match='unavailable'):
        await tool.invoke(scope, task, {'actionId': 'wait', 'jobId': job_id})
