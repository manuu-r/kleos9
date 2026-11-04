import asyncio
import hashlib
import io
import zipfile

import pytest
from sqlalchemy import select

from kleos.access.service import AccessService, AccessSettings, OrganizationMember
from kleos.artifacts.repository_import import RepositoryImport, RepositoryImportService
from kleos.artifacts.service import Artifact, ArtifactService
from kleos.artifacts.storage import BlobStore
from kleos.contracts.models import DomainError
from kleos.projects.lifecycle import delete_project
from kleos.projects.store import Project, ProjectStore

COMMIT = 'a'*40


def archive(files=None):
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as z:
        for path, content in (files or [('README.md', b'Existing project context'), ('part.py', b'raise RuntimeError("NEVER RUN")')]):
            z.writestr('example-device-abc/'+path, content)
    return out.getvalue()


class Downloader:
    def __init__(self, raw=None, callback=None):
        self.raw, self.calls, self.callback = raw or archive(), 0, callback
    async def download(self, source):
        self.calls += 1
        if self.callback:
            await self.callback()
        return self.raw


@pytest.fixture
def env(tmp_path):
    projects = ProjectStore('sqlite:///'+str(tmp_path/'repo.db'))
    projects.initialize_for_tests()
    access = AccessService(projects, AccessSettings(secret_key='x'*40, environment='test', development_no_login=True))
    actor = access.authenticate({}, {}, 'POST')
    project_id = projects.create(actor, 'Continue this existing device', 'create')['projectId']
    artifacts = ArtifactService(projects, BlobStore(tmp_path/'blobs'))
    download = Downloader()
    service = RepositoryImportService(artifacts, download, access=access)
    yield projects, access, actor, project_id, artifacts, download, service
    projects.engine.dispose()


def request():
    return {'repositoryUrl': 'https://github.com/example/device', 'commit': COMMIT, 'expectedEngineeringRevision': 1}


async def test_atomic_sources_raw_archive_provenance_and_replay(env):
    projects, _, actor, ident, artifacts, download, service = env
    result = await service.import_repository(actor, ident, request(), 'import', expected_revision=1)
    assert result['revision'] == 2 and set(result) == {'commandId', 'projectId', 'revision'}
    assert await service.import_repository(actor, ident, request(), 'import', expected_revision=1) == result
    assert download.calls == 1
    with projects.transaction() as s:
        project = s.get(Project, ident)
        assert set(project.data['sourceManifest']) == {'README.md', 'part.py'}
        job = s.scalar(select(RepositoryImport))
        assert job.status == 'completed' and job.data['attempts'] == 1
        assert artifacts.blobs.get(job.data['archiveSha256']) == download.raw
        for path, aid in project.data['sourceManifest'].items():
            artifact = s.get(Artifact, aid)
            assert artifact.details['archivePath'] == 'example-device-abc/'+path
            assert artifact.details['repository']['commit'] == COMMIT
            assert artifact.sha256 == hashlib.sha256(artifacts.blobs.get(artifact.sha256)).hexdigest()


async def test_existing_global_receipt_collision_precedes_download(env):
    _, _, actor, ident, _, download, service = env
    with pytest.raises(DomainError) as error:
        await service.import_repository(actor, ident, request(), 'create')
    assert error.value.code == 'idempotency_conflict' and download.calls == 0


@pytest.mark.parametrize('change', ['delete', 'revision', 'organization'])
async def test_changes_during_fetch_fence_all_source_publication(env, change):
    projects, _, actor, ident, _, download, service = env
    async def modify():
        if change == 'delete':
            delete_project(projects, actor, {'projectId': ident, 'expectedRevision': 1}, {})
        else:
            with projects.transaction() as s:
                if change == 'revision':
                    s.get(Project, ident).advance_revision()
                else:
                    s.delete(s.get(OrganizationMember, (actor.tenant_id, actor.user_id)))
    download.callback = modify
    with pytest.raises(DomainError):
        await service.import_repository(actor, ident, request(), 'import')
    with projects.transaction() as s:
        assert not s.get(Project, ident).data.get('sourceManifest')
        assert s.scalar(select(RepositoryImport)).status == 'failed'


async def test_replay_rechecks_organization_authority(env):
    projects, _, actor, ident, _, download, service = env
    await service.import_repository(actor, ident, request(), 'import')
    with projects.transaction() as s:
        s.delete(s.get(OrganizationMember, (actor.tenant_id, actor.user_id)))
    with pytest.raises(DomainError):
        await service.import_repository(actor, ident, request(), 'import')
    assert download.calls == 1


async def test_concurrent_same_key_downloads_once(env):
    _, _, actor, ident, _, download, service = env
    entered, finish = asyncio.Event(), asyncio.Event()
    async def pause():
        entered.set()
        await finish.wait()
    download.callback = pause
    pending = asyncio.create_task(service.import_repository(actor, ident, request(), 'import'))
    await entered.wait()
    try:
        with pytest.raises(DomainError) as error:
            await service.import_repository(actor, ident, request(), 'import')
        assert error.value.code == 'repository_download_pending'
    finally:
        finish.set()
    assert (await pending)['revision'] == 2 and download.calls == 1


async def test_downloaded_receipt_survives_restart_without_network(env, monkeypatch):
    import kleos.artifacts.repository_import as module
    projects, access, actor, ident, artifacts, download, service = env
    class Crash(BaseException):
        pass
    original = module.repository_files
    def crash(raw):
        raise Crash()
    monkeypatch.setattr(module, 'repository_files', crash)
    with pytest.raises(Crash):
        await service.import_repository(actor, ident, request(), 'import')
    with projects.transaction() as s:
        assert s.scalar(select(RepositoryImport)).status == 'downloaded'
    monkeypatch.setattr(module, 'repository_files', original)
    restarted = RepositoryImportService(artifacts, download, access=access)
    assert (await restarted.import_repository(actor, ident, request(), 'import'))['revision'] == 2
    assert download.calls == 1


@pytest.mark.parametrize('mode', ['uncertain', 'cancel'])
async def test_uncertain_or_cancelled_fetch_does_not_silently_repeat(env, mode):
    projects, _, actor, ident, _, download, service = env
    async def fail():
        if mode == 'cancel':
            raise asyncio.CancelledError()
        raise DomainError('Unknown result', 'repository_download_uncertain', 502)
    download.callback = fail
    with pytest.raises((DomainError, asyncio.CancelledError)):
        await service.import_repository(actor, ident, request(), 'import')
    with pytest.raises(DomainError):
        await service.import_repository(actor, ident, request(), 'import')
    with projects.transaction() as s:
        assert s.scalar(select(RepositoryImport)).status == 'uncertain'
        assert not s.get(Project, ident).data.get('sourceManifest')
    assert download.calls == 1


@pytest.mark.parametrize('files', [[('.env', 'PRIVATE=value')], [('.gitmodules', 'submodule')],
    [('model.stl', 'version https://git-lfs.github.com/spec/v1\noid sha256:abc')], [('../unsafe', 'no')]])
async def test_unsupported_context_is_rejected_not_silently_omitted(env, files):
    projects, _, actor, ident, _, download, service = env
    download.raw = archive(files)
    with pytest.raises(DomainError):
        await service.import_repository(actor, ident, request(), 'import')
    with projects.transaction() as s:
        assert s.get(Project, ident).revision == 1 and not s.get(Project, ident).data.get('sourceManifest')


async def test_malfunctioning_downloader_cannot_store_oversized_archive(env, monkeypatch):
    import kleos.artifacts.repository_import as module
    projects, _, actor, ident, artifacts, download, service = env
    monkeypatch.setattr(module, 'MAX_ARCHIVE_BYTES', 4)
    def forbidden(raw):
        pytest.fail('Oversized raw archive must be rejected before storage')
    monkeypatch.setattr(artifacts.blobs, 'put', forbidden)
    with pytest.raises(DomainError) as error:
        await service.import_repository(actor, ident, request(), 'import')
    assert error.value.code == 'archive_limit'


async def test_cancel_pending_fences_result_and_preserves_context_revision(env):
    projects, _, actor, ident, _, download, service = env
    entered, finish = asyncio.Event(), asyncio.Event()
    async def pause():
        entered.set()
        await finish.wait()
    download.callback = pause
    pending = asyncio.create_task(service.import_repository(actor, ident, request(), 'import'))
    await entered.wait()
    result = service.cancel(actor, ident, 'import', 1, 'cancel')
    assert result['revision'] == 2
    assert service.cancel(actor, ident, 'import', 1, 'cancel') == result
    finish.set()
    with pytest.raises(DomainError):
        await pending
    with projects.transaction() as s:
        project = s.get(Project, ident)
        assert project.context_revision == 1 and not project.data.get('sourceManifest')
        assert s.scalar(select(RepositoryImport)).status == 'cancelled'
    assert download.calls == 1


@pytest.mark.parametrize('status', ['downloaded', 'uncertain'])
async def test_cancel_confirmed_download_or_uncertain_attempt_never_publishes(env, status):
    projects, _, actor, ident, _, download, service = env
    canonical = {'type': 'project.importRepository', 'projectId': ident, **request(), 'expectedRevision': None}
    job_id, _ = service._admit(actor, ident, request(), 'import', canonical)
    with projects.transaction() as s:
        s.get(RepositoryImport, job_id).status = status
    service.cancel(actor, ident, 'import', 1, 'cancel')
    with pytest.raises(DomainError) as error:
        await service.import_repository(actor, ident, request(), 'import')
    assert error.value.code == 'repository_import_cancelled' and download.calls == 0


async def test_cancel_cannot_undo_completed_import(env):
    projects, _, actor, ident, _, _, service = env
    await service.import_repository(actor, ident, request(), 'import')
    with pytest.raises(DomainError) as error:
        service.cancel(actor, ident, 'import', 2, 'cancel')
    assert error.value.code == 'repository_import_terminal'
    with projects.transaction() as s:
        assert len(s.get(Project, ident).data['sourceManifest']) == 2
