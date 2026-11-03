import pytest

from kleos.contracts.models import Actor, DomainError
from kleos.projects.consent import set_training_consent
from kleos.projects.store import Project, ProjectStore


def test_consent_is_explicit_idempotent_and_does_not_change_engineering_context(tmp_path):
    store = ProjectStore(f'sqlite:///{tmp_path}/consent.db')
    store.initialize_for_tests()
    actor = Actor(tenant_id='tenant', user_id='owner')
    project = store.create(actor, 'Bracket', 'create')['projectId']
    body = {'type': 'project.setTrainingConsent', 'expectedRevision': 1,
            'payload': {'projectId': project, 'optedIn': True}}
    receipt = set_training_consent(store, actor, body, 'consent')
    assert set_training_consent(store, actor, body, 'consent') == receipt
    with store.transaction() as session:
        row = session.get(Project, project)
        assert row.context_revision == 1 and row.revision == 2
        assert row.data['trainingConsent']['optedIn'] is True
    with pytest.raises(DomainError):
        set_training_consent(store, Actor(tenant_id='other', user_id='owner'), body, 'foreign')
    with pytest.raises(DomainError):
        set_training_consent(store, actor, body, 'stale')
    body['expectedRevision'] = 2
    body['payload']['optedIn'] = False
    set_training_consent(store, actor, body, 'revoke')
    with store.transaction() as session:
        assert session.get(Project, project).data['trainingConsent']['optedIn'] is False
    body['payload']['optedIn'] = 'true'
    with pytest.raises(DomainError):
        set_training_consent(store, actor, body, 'string')
    submitted = store.submit(actor, project, 'Continue engineering after preference changes', 3, 'objective')
    from kleos.projects.store import Objective
    with store.transaction() as session:
        row = session.get(Project, project)
        run = session.get(Objective, submitted['runId'])
        assert row.revision == 4 and row.context_revision == 4
        assert run.revision == row.context_revision
    store.engine.dispose()
