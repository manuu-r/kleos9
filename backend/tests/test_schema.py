from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from kleos.infrastructure.schema import metadata


def configuration(connection):
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    config.attributes["connection"] = connection
    return config


def test_fresh_baseline_upgrades_and_matches_all_registered_models(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/fresh.db")
    with engine.begin() as connection:
        config = configuration(connection)
        command.upgrade(config, "head")
        assert set(metadata().tables).issubset(inspect(connection).get_table_names())
        command.check(config)
        command.upgrade(config, "head")
    engine.dispose()


def test_migration_refuses_existing_application_database(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/historical.db")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE project (id TEXT)"))
        connection.execute(text("INSERT INTO project VALUES ('preserve-me')"))
        with pytest.raises(RuntimeError, match="existing application tables are protected"):
            command.upgrade(configuration(connection), "head")
        assert connection.scalar(text("SELECT id FROM project")) == "preserve-me"
        assert inspect(connection).get_table_names() == ["project"]
    engine.dispose()


def test_repository_receipt_migration_preserves_existing_rewrite_projects(tmp_path):
    from kleos.contracts.models import Actor
    from kleos.projects.store import Project, ProjectStore, Receipt

    store = ProjectStore(f'sqlite:///{tmp_path}/existing-rewrite.db')
    with store.engine.begin() as connection:
        command.upgrade(configuration(connection), '0005_external_waits')
    created = store.create(Actor(tenant_id='tenant', user_id='owner'), 'Preserve existing project', 'create')
    with store.engine.begin() as connection:
        command.upgrade(configuration(connection), 'head')
        command.check(configuration(connection))
    with store.transaction() as session:
        project = session.get(Project, created['projectId'])
        assert project.data['brief'] == 'Preserve existing project'
        assert project.revision == created['revision']
        assert session.get(Receipt, created['commandId']).result == created
        assert session.execute(text('SELECT count(*) FROM kleos2_repository_imports')).scalar_one() == 0
    store.engine.dispose()
