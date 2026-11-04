"""Real account revocation must fence previously admitted worker scopes."""
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import SecretStr
from sqlalchemy import select
from test_access import joined, owner
from test_runtime import migrate, settings, test_persistence

from kleos.access.service import Account
from kleos.contracts.models import DomainError
from kleos.infrastructure.runtime import Runtime
from kleos.orchestration.engine import RunScope
from kleos.projects.graph_repository import Effect
from kleos.projects.store import Membership


@pytest.mark.parametrize('revocation', ['remove', 'viewer', 'account'])
async def test_runtime_rechecks_account_authority_after_tool_observation(tmp_path, revocation):
    runtime = Runtime(settings(tmp_path, setup_token=SecretStr('setup-secret')),
                      persistence_factory=test_persistence)
    migrate(runtime)
    administrator, _, _ = owner(runtime.access)
    actor, _, _ = joined(runtime.access, administrator)
    project = runtime.projects.create(actor, 'Existing project', 'create')['projectId']
    submitted = runtime.projects.submit(actor, project, 'Inspect existing sources', 1, 'run')
    scope = RunScope(actor=actor, project_id=project, run_id=submitted['runId'],
                     revision=submitted['revision'], deadline_at=datetime.now(UTC)+timedelta(minutes=5))
    await runtime.repository.assert_active(scope)

    async def observed():
        if revocation == 'account':
            with runtime.projects.transaction() as session:
                session.delete(session.get(Account, actor.user_id))
        else:
            runtime.access.action(administrator, 'remove_member' if revocation == 'remove' else 'change_role',
                                  {'subject': actor.user_id}, {} if revocation == 'remove' else {'role': 'viewer'})
        return {'observed': True}

    with pytest.raises(DomainError, match='organization access'):
        await runtime.repository.run_once(scope, 'fixture-observation', observed)
    with pytest.raises(DomainError, match='organization access'):
        await runtime.repository.assert_active(scope)
    with runtime.projects.transaction() as session:
        # The project membership still exists: organization authority is the fence.
        assert session.get(Membership, (project, actor.user_id)).role == 'owner'
        effect = session.scalar(select(Effect).where(Effect.run_id == scope.run_id))
        assert effect.status == 'completed' and effect.value == {'observed': True}
    with pytest.raises(DomainError):
        runtime.projects.submit(actor, project, 'Cannot start more work', submitted['revision'], 'another')
    if revocation != 'viewer':
        with pytest.raises(DomainError):
            runtime.services.views.list(actor)
    else:
        assert runtime.services.views.list(actor)['projects']
    runtime.projects.engine.dispose()
