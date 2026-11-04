from datetime import UTC, datetime, timedelta

import pytest
from test_application_services import services as services
from test_model_gateway import configuration

from kleos.contracts.models import Actor, DomainError
from kleos.orchestration.engine import RunScope
from kleos.orchestration.models import ModelLedger, ModelPolicy


def reserve(services, actor, project, *, key):
    revision = services.views.workspace(actor, project)['revision']
    run = services.projects.submit(actor, project, 'Inspect project', revision, key)
    scope = RunScope(actor=actor, project_id=project, run_id=run['runId'], revision=run['revision'],
        deadline_at=datetime.now(UTC) + timedelta(minutes=5))
    ledger = ModelLedger(services.projects)
    attempt = ledger.reserve(scope, ModelPolicy(text_model_key='text'), 'policy',
        configuration(), 'a' * 64, 512)
    return ledger, attempt


def test_views_count_historical_spend_and_retain_uncertain_reservations(services):
    actor = services.authenticate({}, {}, 'GET')
    project = services.projects.create(actor, 'Costed project', 'create')['projectId']
    ledger, first = reserve(services, actor, project, key='first')
    ledger.settle(first, 'completed', usage={'input_tokens': 100, 'output_tokens': 20})
    _, second = reserve(services, actor, project, key='second')
    ledger.settle(second, 'uncertain')
    # A later run must not hide earlier spending or release uncertain usage.
    _, third = reserve(services, actor, project, key='third')
    ledger.settle(third, 'rate_limited')
    budget = services.views.workspace(actor, project)['project']['budget']
    assert budget == {'allowanceUsd': None, 'unlimited': False,
                      'spentEstimateUsd': 0.00014, 'reservedUsd': 0.033024}
    listed = services.views.list(actor)['projects'][0]
    assert listed['budget'] == budget
    # Replayed settlement cannot refund or double count a completed receipt.
    ledger.settle(first, 'completed', usage={'input_tokens': 999, 'output_tokens': 999})
    assert services.views.workspace(actor, project)['project']['budget'] == budget


def test_budget_projection_is_project_and_tenant_scoped(services):
    actor = services.authenticate({}, {}, 'GET')
    project = services.projects.create(actor, 'Own project', 'create')['projectId']
    foreign = Actor(tenant_id='other', user_id='other')
    other = services.projects.create(foreign, 'Other project', 'other')['projectId']
    ledger, attempt = reserve(services, foreign, other, key='foreign-call')
    ledger.settle(attempt, 'usage_unknown')
    own = services.views.list(actor)['projects']
    assert [row['id'] for row in own] == [project]
    assert own[0]['budget']['reservedUsd'] == 0
    assert services.views.workspace(foreign, other)['project']['budget']['reservedUsd'] > 0
    with pytest.raises(DomainError, match='not found'):
        services.views.workspace(actor, other)
