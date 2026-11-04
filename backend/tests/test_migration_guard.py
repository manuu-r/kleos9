"""A database carrying another schema's migration history is never modified by `migrate`."""
import sqlite3

import pytest

from kleos.cli import migrate
from kleos.infrastructure.settings import Settings


def test_migrate_refuses_a_database_with_foreign_migration_history_and_changes_nothing(tmp_path):
    path = tmp_path / 'old-backend.db'
    with sqlite3.connect(path) as connection:
        connection.execute('CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)')
        connection.execute("INSERT INTO alembic_version VALUES ('legacy_backend_0042')")
        connection.execute('CREATE TABLE projects (id TEXT PRIMARY KEY)')
    settings = Settings(_env_file=None, database_url=f'sqlite:///{path}')
    with pytest.raises(Exception, match='fresh rewrite database'):
        migrate(settings)
    with sqlite3.connect(path) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        version = connection.execute('SELECT version_num FROM alembic_version').fetchall()
    assert tables == {'alembic_version', 'projects'} and version == [('legacy_backend_0042',)]
