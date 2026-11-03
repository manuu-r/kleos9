import base64
import hashlib
import io
import stat
import struct
import warnings
import zipfile
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select
from test_interface_runtime import configured_runtime

from kleos.artifacts.import_archive import ArchiveImportService, read_archive
from kleos.artifacts.service import Artifact, ArtifactService
from kleos.artifacts.storage import BlobStore
from kleos.contracts.models import Actor, DomainError
from kleos.orchestration.engine import RunScope
from kleos.projects.graph_repository import GraphRepository
from kleos.projects.store import Event, Membership, Objective, Project, ProjectStore


def zipped(entries, compression=zipfile.ZIP_DEFLATED):
    output = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', UserWarning)
        with zipfile.ZipFile(output, 'w', compression=compression) as archive:
            for name, content in entries:
                archive.writestr(name, content)
    return output.getvalue()


def body(raw, revision=1):
    return {'expectedEngineeringRevision': revision, 'sha256': hashlib.sha256(raw).hexdigest(),
        'file': {'path': 'project.zip', 'mediaType': 'application/zip', 'encoding': 'base64',
            'content': base64.b64encode(raw).decode()}}


@pytest.fixture
def imported(tmp_path):
    store = ProjectStore(f'sqlite:///{tmp_path}/archive.db')
    store.initialize_for_tests()
    actor = Actor(tenant_id='tenant', user_id='owner')
    ident = store.create(actor, 'Continue this existing project', 'create')['projectId']
    artifacts = ArtifactService(store, BlobStore(tmp_path / 'blobs'))
    yield store, actor, ident, artifacts, ArchiveImportService(artifacts), tmp_path
    store.engine.dispose()


def project(env):
    with env[0].transaction() as session:
        return session.get(Project, env[2])


def test_atomic_import_retains_exact_bytes_and_private_provenance(imported):
    store, actor, ident, artifacts, service, tmp = imported
    raw = zipped([('docs/', ''), ('docs/spec.txt', b'Load: 15 N'), ('model.py', b'raise RuntimeError("NEVER RUN")'),
        ('board.kicad_pcb', b'(kicad_pcb (version 20240108))')])
    before = deepcopy(project(imported).data)
    received = service.import_archive(actor, ident, body(raw), 'import', expected_revision=1)
    assert set(received) == {'commandId', 'projectId', 'revision'} and received['revision'] == 2
    assert service.import_archive(actor, ident, body(raw), 'import', expected_revision=1) == received
    result = project(imported)
    assert result.context_revision == 2 and len(result.data['sourceManifest']) == 3
    for key in ('requirements', 'interfaces', 'brief'):
        assert result.data[key] == before[key]
    for path, content in read_archive(raw):
        ident_ = result.data['sourceManifest'][path]
        assert artifacts.content(actor, ident_).data == content
        with store.transaction() as session:
            row = session.get(Artifact, ident_)
            assert row.sha256 == hashlib.sha256(content).hexdigest() and row.revision == 2
            assert row.details['origin'] == 'user_upload'
            assert row.details['archiveSha256'] == hashlib.sha256(raw).hexdigest()
    with store.transaction() as session:
        events = [event for event in session.scalars(select(Event)) if event.data['type'] == 'artifact.archive_imported']
        assert len(events) == 1 and len(events[0].data['payload']['artifacts']) == 3
        assert not list(session.scalars(select(Objective)))
    # Source bytes are immutable hash blobs; archive names are never host paths.
    assert not (tmp / 'model.py').exists() and not (tmp / 'docs').exists()


@pytest.mark.parametrize('path', ['../secret', '/etc/passwd', 'a/../../b', 'a\\b', '.env', 'a/.git/config',
    'a//b', 'a/./b', 'C:/x', 'name:stream', 'NUL', 'aux.txt', 'folder./a', 'folder /a', 'e\u0301.txt',
    'hidden\u202epath.txt', 'delete\x7f.txt'])
def test_traversal_private_and_ambiguous_paths_rejected_before_publication(imported, path):
    store, actor, ident, _, service, _ = imported
    with pytest.raises(DomainError):
        service.import_archive(actor, ident, body(zipped([('valid.txt', 'valid'), (path, 'unsafe')])), 'unsafe')
    assert project(imported).revision == 1 and not project(imported).data.get('sourceManifest')
    with store.transaction() as session:
        assert not list(session.scalars(select(Artifact)))


@pytest.mark.parametrize('names', [ ['same', 'same'], ['Same', 'same'], ['folder', 'folder/a'],
    ['folder/a', 'folder'], ['é.txt', 'É.txt'], ['folder/a', 'Folder/b'] ])
def test_duplicate_case_and_file_directory_collisions(names):
    with pytest.raises(DomainError) as error:
        read_archive(zipped([(name, b'x') for name in names]))
    assert error.value.code == 'archive_path_conflict'


@pytest.mark.parametrize('kind', [stat.S_IFLNK, stat.S_IFIFO, stat.S_IFCHR, stat.S_IFSOCK])
def test_links_and_special_files_are_not_imported(kind):
    link = zipfile.ZipInfo('link')
    link.create_system = 3
    link.external_attr = (kind | 0o777) << 16
    with pytest.raises(DomainError) as error:
        read_archive(zipped([(link, '../../secret')]))
    assert error.value.code == 'archive_entry_unsafe'


@pytest.mark.parametrize('variant', ['ratio', 'entry_size', 'entry_count', 'encrypted', 'method', 'nul', 'crc',
    'central_count', 'central_size', 'multidisk', 'trailing', 'zip64', 'unicode_extra'])
def test_malformed_or_expansive_archives_are_bounded(variant):
    if variant == 'ratio':
        raw = zipped([('bomb', b'0' * 200000)])
    elif variant == 'entry_size':
        raw = zipped([('large', b'x' * (4 * 1024 * 1024 + 1))], zipfile.ZIP_STORED)
    elif variant == 'entry_count':
        raw = zipped([(f'{index}.txt', b'x') for index in range(257)])
    elif variant == 'unicode_extra':
        info = zipfile.ZipInfo('safe.txt')
        info.extra = struct.pack('<HH', 0x7075, 1) + b'x'
        raw = zipped([(info, b'x')])
    else:
        raw = bytearray(zipped([('safe.txt', b'hello')], zipfile.ZIP_STORED))
        central, end = raw.index(b'PK\x01\x02'), raw.index(b'PK\x05\x06')
        if variant == 'encrypted':
            struct.pack_into('<H', raw, central + 8, 1)
        elif variant == 'method':
            struct.pack_into('<H', raw, central + 10, zipfile.ZIP_LZMA)
        elif variant == 'nul':
            raw[central + 46] = 0
        elif variant == 'crc':
            raw[30 + len('safe.txt')] ^= 1
        elif variant == 'central_count':
            struct.pack_into('<H', raw, end + 10, 2)
        elif variant == 'central_size':
            struct.pack_into('<I', raw, end + 12, 1)
        elif variant == 'multidisk':
            struct.pack_into('<H', raw, end + 4, 1)
        elif variant == 'trailing':
            raw += b'arbitrary trailing bytes'
        elif variant == 'zip64':
            struct.pack_into('<H', raw, central + 6, 45)
        raw = bytes(raw)
    with pytest.raises(DomainError):
        read_archive(raw)


def test_existing_paths_are_never_overwritten_and_history_preserved(imported):
    _, actor, ident, _, service, _ = imported
    first = body(zipped([('part.py', b'original'), ('other.txt', b'preserve')]))
    service.import_archive(actor, ident, first, 'first')
    before = deepcopy(project(imported).data)
    with pytest.raises(DomainError) as error:
        service.import_archive(actor, ident, body(zipped([('PART.py', 'replacement')]), 2), 'replace')
    assert error.value.code == 'source_conflict'
    assert project(imported).data == before
    service.import_archive(actor, ident, body(zipped([('addition.txt', 'new')]), 2), 'addition')
    after = project(imported)
    assert len(after.data['archiveImports']) == 2
    for path, ref in before['sourceManifest'].items():
        assert after.data['sourceManifest'][path] == ref


def test_permission_hash_revision_and_idempotency_fences(imported):
    store, actor, ident, _, service, _ = imported
    request = body(zipped([('source.txt', 'content')]))
    for mutation, expected in [({'sha256': '0'*64}, 'hash_mismatch'),
        ({'expectedEngineeringRevision': 99}, 'revision_conflict')]:
        with pytest.raises(DomainError) as error:
            service.import_archive(actor, ident, request | mutation, expected)
        assert error.value.code == expected
    with pytest.raises(DomainError):
        service.import_archive(Actor(tenant_id='other', user_id=actor.user_id), ident, {}, 'foreign')
    receipt = service.import_archive(actor, ident, request, 'once')
    with pytest.raises(DomainError) as error:
        service.import_archive(actor, ident, body(zipped([('different', 'data')]), 2), 'once')
    assert error.value.code == 'idempotency_conflict'
    with store.transaction() as session:
        session.get(Membership, (ident, actor.user_id)).role = 'viewer'
    with pytest.raises(DomainError):
        service.import_archive(actor, ident, request, 'once')
    assert project(imported).revision == receipt['revision']


async def test_successful_import_fences_prior_graph_context(imported):
    store, actor, ident, _, service, _ = imported
    run = store.submit(actor, ident, 'Inspect existing sources', 1, 'objective')
    scope = RunScope(actor=actor, project_id=ident, run_id=run['runId'], revision=run['revision'],
        deadline_at=datetime.now(UTC) + timedelta(minutes=5))
    service.import_archive(actor, ident, body(zipped([('source.txt', 'new context')]), 2), 'import')
    with pytest.raises(DomainError) as error:
        await GraphRepository(store).assert_active(scope)
    assert error.value.code == 'stale_context'


def test_storage_failure_rolls_back_all_database_publication(imported, monkeypatch):
    store, actor, ident, artifacts, service, _ = imported
    put = artifacts.blobs.put
    calls = []
    def failing(content):
        calls.append(content)
        if len(calls) == 2:
            raise OSError('fixture disk failure')
        return put(content)
    monkeypatch.setattr(artifacts.blobs, 'put', failing)
    with pytest.raises(OSError):
        service.import_archive(actor, ident, body(zipped([('a.txt', 'a'), ('b.txt', 'b')])), 'import')
    assert project(imported).revision == 1 and not project(imported).data.get('sourceManifest')
    with store.transaction() as session:
        assert not list(session.scalars(select(Artifact)))
    # An unreferenced immutable blob may remain; it is not published or deleted as a repair shortcut.
    monkeypatch.setattr(artifacts.blobs, 'put', put)
    assert service.import_archive(actor, ident, body(zipped([('a.txt', 'a'), ('b.txt', 'b')])), 'import')['revision'] == 2


def test_total_declared_expansion_is_rejected_before_opening_files(monkeypatch):
    raw = bytearray(zipped([(f'{index}.txt', 'x') for index in range(5)]))
    start = 0
    for _ in range(5):
        start = raw.index(b'PK\x01\x02', start)
        struct.pack_into('<II', raw, start + 20, 4 * 1024 * 1024, 4 * 1024 * 1024)
        start += 46
    monkeypatch.setattr(zipfile, 'ZipFile', lambda *_: pytest.fail('Metadata bounds must precede ZIP object parsing'))
    with pytest.raises(DomainError) as error:
        read_archive(bytes(raw))
    assert error.value.code == 'archive_limit'


async def test_actual_http_import_command_and_opaque_zip_upload_are_distinct(tmp_path):
    upstream = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: pytest.fail('Import cannot call a model')))
    runtime = configured_runtime(tmp_path, upstream)
    async with upstream, runtime.start(), httpx.AsyncClient(transport=httpx.ASGITransport(app=runtime.app),
            base_url='http://local') as client:
        created = await client.post('/api/v1/commands', headers={'Idempotency-Key': 'create'},
            json={'type': 'project.create', 'payload': {'brief': 'Use this existing project context'}})
        assert created.status_code == 202
        ident = created.json()['projectId']
        raw = zipped([('board.kicad_pcb', '(kicad_pcb (version 20240108))'),
            ('lib/part.kicad_mod', '(footprint "Imported")')])
        command = {'type': 'project.importArchive', 'expectedRevision': 1,
            'payload': {'projectId': ident, **body(raw)}}
        imported = await client.post('/api/v1/commands', headers={'Idempotency-Key': 'import'}, json=command)
        assert imported.status_code == 202, imported.text
        assert imported.json()['revision'] == 2 and imported.json().get('runId') is None
        again = await client.post('/api/v1/commands', headers={'Idempotency-Key': 'import'}, json=command)
        assert again.json() == imported.json()
        with runtime.projects.transaction() as session:
            project = session.get(Project, ident)
            assert set(project.data['sourceManifest']) == {'board.kicad_pcb', 'lib/part.kicad_mod'}
            assert project.context_revision == 2 and not project.data.get('approvedDesign')
            refs = dict(project.data['sourceManifest'])
        uploaded = await client.post(f'/api/v1/projects/{ident}/artifacts',
            headers={'Idempotency-Key': 'opaque'}, json=body(raw, 2))
        assert uploaded.status_code == 201, uploaded.text
        with runtime.projects.transaction() as session:
            project = session.get(Project, ident)
            assert set(project.data['sourceManifest']) == {*refs, 'project.zip'}
            assert len(project.data['archiveImports']) == 1
            assert all(project.data['sourceManifest'][path] == value for path, value in refs.items())
            assert not list(session.scalars(select(Objective)))
        downloaded = await client.get(f'/api/v1/artifacts/{uploaded.json()["artifact"]["id"]}/content')
        assert downloaded.content == raw
