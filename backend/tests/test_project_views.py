import json
from pathlib import Path

import pytest
from test_release_api import accepted_package
from test_validation import setup as setup

from kleos.contracts.models import Actor, DomainError
from kleos.projects.store import Objective, Project
from kleos.projects.views import ProjectViews
from kleos.validation.service import Candidate, ValidationCheck


async def test_checks_and_agents_project_real_records_and_keep_accepted_checks_current(setup, tmp_path):
    _, _, _, _ = await accepted_package(setup, tmp_path)
    store, scope, _, _ = setup
    views = ProjectViews(store)
    workspace = views.workspace(scope.actor, scope.project_id)
    check = workspace['checks'][0]
    assert check['status'] == 'PASS' and check['details']['independent'] is True
    assert check['details']['candidateStatus'] == 'accepted'
    assert check['details']['historical'] is False
    assert check['details']['physicalAcceptance'] is False
    agents = views.agents(scope.actor, scope.project_id)['agents']
    assert sorted(agents, key=lambda row: row['id']) == sorted(workspace['agents'], key=lambda row: row['id'])
    assert {row['agentType'] for row in agents} == {'orchestrator', 'cad'}
    assert next(row for row in agents if row['agentType'] == 'orchestrator')['lifecycle'] == 'succeeded'
    assert all(row['lifetime'] == 'bounded' for row in agents)
    schema = json.loads((Path(__file__).parents[1] / 'contracts/openapi.json').read_text())['components']['schemas']
    for value, name in [(check, 'ValidationView'), *[(row, 'AgentSummary') for row in agents]]:
        assert set(schema[name]['required']).issubset(value)
        assert set(value).issubset(schema[name]['properties'])
    page = views.agents(scope.actor, scope.project_id, limit=1)
    assert set(page).issubset(schema['AgentsPage']['properties'])
    second = views.agents(scope.actor, scope.project_id, after=page['nextCursor'], limit=1)
    assert page['agents'] + second['agents'] == agents and second['nextCursor'] is None
    with store.transaction() as session:
        project = session.get(Project, scope.project_id)
        project.advance_revision(context=False)
    assert views.workspace(scope.actor, scope.project_id)['checks'][0]['details']['historical'] is False
    with store.transaction() as session:
        session.get(Project, scope.project_id).advance_revision()
    assert views.workspace(scope.actor, scope.project_id)['checks'][0]['details']['historical'] is True
    with pytest.raises(DomainError, match='not found'):
        views.agents(Actor(tenant_id='foreign', user_id='owner'), scope.project_id)


async def test_running_check_remains_unknown_and_cancelled_tasks_are_visible(setup):
    store, scope, _, _ = setup
    with store.transaction() as session:
        session.add(Candidate(id='checking', project_id=scope.project_id, run_id=scope.run_id,
            revision=scope.revision, request_key='checking', request_hash='a' * 64,
            subject_hash='b' * 64, status='checking', data={}))
        session.add(ValidationCheck(candidate_id='checking', checker_key='cad.geometry',
            status='running', data={'independent': True}))
        session.get(Objective, scope.run_id).status = 'cancelled'
    workspace = ProjectViews(store).workspace(scope.actor, scope.project_id)
    assert workspace['checks'][0]['status'] == 'UNKNOWN'
    assert workspace['checks'][0]['details']['executionState'] == 'running'
    assert all(row['lifecycle'] == 'cancelled' for row in workspace['agents'])
