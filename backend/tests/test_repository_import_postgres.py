"""Explicit disposable PostgreSQL only: publication serializes authority revocation."""
import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_postgres_runtime import database  # noqa: F401
from test_repository_import import Downloader, request

from kleos.access.service import AccessService, AccessSettings
from kleos.artifacts.repository_import import RepositoryImportService
from kleos.artifacts.service import ArtifactService
from kleos.artifacts.storage import BlobStore


@pytest.mark.parametrize('table,key', [('kleos2_organization_members', 'user_id'), ('kleos2_accounts', 'id')])
def test_publication_holds_current_account_and_membership_until_commit(database, tmp_path, monkeypatch, table, key):  # noqa: F811
    projects, _ = database
    access = AccessService(projects, AccessSettings(secret_key='x'*40, environment='test', development_no_login=True))
    actor = access.authenticate({}, {}, 'POST')
    ident = projects.create(actor, 'Revocation fixture', 'create')['projectId']
    artifacts = ArtifactService(projects, BlobStore(tmp_path/'blobs'))
    service = RepositoryImportService(artifacts, Downloader(), access=access)
    locked, release = threading.Event(), threading.Event()
    publish = service.archives.publish_files
    def held(*args, **kwargs):
        locked.set()
        assert release.wait(8)
        return publish(*args, **kwargs)
    monkeypatch.setattr(service.archives, 'publish_files', held)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(lambda: asyncio.run(service.import_repository(actor, ident, request(), 'import')))
        try:
            assert locked.wait(8)
            with pytest.raises(DBAPIError) as error:
                with projects.transaction() as session:
                    session.execute(text("SET LOCAL lock_timeout = '300ms'"))
                    # Both identifiers are fixed parameterized test cases, never user input.
                    session.execute(text(f'DELETE FROM {table} WHERE {key} = :actor'), {'actor': actor.user_id})
            assert error.value.orig.sqlstate == '55P03'
        finally:
            release.set()
        assert pending.result(timeout=8)['revision'] == 2
