"""Server-owned fixtures cannot become ordinary user-editable projects."""
import pytest

from kleos.contracts.models import Actor, DomainError
from kleos.projects.store import Project, ProjectStore
from kleos.projects.views import ProjectViews


@pytest.fixture
def internal(tmp_path):
    store = ProjectStore(f'sqlite:///{tmp_path}/internal.db')
    store.initialize_for_tests()
    actor = Actor(tenant_id='tenant', user_id='owner')
    ordinary = store.create(actor, 'Normal project', 'normal')
    fixture = store.create(actor, 'Fixture project', 'fixture')
    with store.transaction() as session:
        project = session.get(Project, fixture['projectId'])
        project.data = {**project.data, 'internalPurpose': 'bambu_test_print'}
    yield store, actor, ordinary, fixture
    store.engine.dispose()


def test_internal_project_is_not_listed_or_readable_even_by_owner(internal):
    store, actor, ordinary, fixture = internal
    views = ProjectViews(store)
    assert [row['id'] for row in views.list(actor)['projects']] == [ordinary['projectId']]
    with pytest.raises(DomainError, match='Project not found'):
        views.workspace(actor, fixture['projectId'])
    with store.transaction() as session:
        for write in (False, True):
            with pytest.raises(DomainError, match='Project not found'):
                store.authorized(session, actor, fixture['projectId'], write=write)


def test_internal_project_cannot_start_objectives(internal):
    store, actor, _, fixture = internal
    with pytest.raises(DomainError, match='Project not found'):
        store.submit(actor, fixture['projectId'], 'Change the fixed fixture', 1, 'attempt')
