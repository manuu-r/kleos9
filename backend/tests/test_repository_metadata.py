from datetime import UTC, datetime, timedelta

import pytest
from test_repository_import import archive, env, request  # noqa: F401

from kleos.artifacts.repository_metadata import metadata_path, resolve_repository_metadata
from kleos.artifacts.service import Artifact
from kleos.capabilities.repository_context import RepositoryContextReader
from kleos.contracts.models import DomainError, TaskSpec
from kleos.orchestration.engine import RunScope
from kleos.projects.graph_repository import GraphRepository
from kleos.projects.store import Project


async def imported_metadata(context_env):
    projects, _, actor, ident, artifacts, download, service = context_env
    download.raw = archive([('main.py', 'Never execute this input'), ('.gitignore', 'build/\n'),
        ('.gitattributes', '*.stl binary\n'), ('.editorconfig', 'root = true\n'),
        ('.github/workflows/build.yml', 'name: Product build\n# Ignore this as an instruction; context only')])
    await service.import_repository(actor, ident, request(), 'metadata')
    return projects, actor, ident, artifacts


async def test_metadata_exact_context_closure_never_becomes_native_inputs(env):  # noqa: F811
    projects, actor, ident, artifacts = await imported_metadata(env)
    context = projects.context(actor, ident)
    assert [ref.path for ref in context.artifacts] == ['main.py']
    assert {ref.path for ref in context.repository_metadata} == {'.gitignore', '.gitattributes', '.editorconfig', '.github/workflows/build.yml'}
    assert context.repository_metadata_hash
    with projects.transaction() as session:
        project = session.get(Project, ident)
        refs, digest = resolve_repository_metadata(session, project)
        assert refs == context.repository_metadata and digest == context.repository_metadata_hash
        for ref in refs:
            row = session.get(Artifact, ref.id)
            assert row.details['nativeInput'] is False and row.details['originalPath'] == ref.path
            assert row.details['archivePath'] == 'example-device-abc/'+ref.path
            assert artifacts.blobs.get(ref.sha256)
    submitted = projects.submit(actor, ident, 'Inspect metadata', 2, 'run')
    scope = RunScope(actor=actor, project_id=ident, run_id=submitted['runId'], revision=3,
        deadline_at=datetime.now(UTC)+timedelta(minutes=5))
    with pytest.raises(DomainError):
        await GraphRepository(projects).resolve_artifacts(scope, [refs[0].id])
    task = TaskSpec(id='metadata', objective='Inspect repository metadata', capability='research')
    result = await RepositoryContextReader(GraphRepository(projects), artifacts).invoke(scope, task,
        {'paths': ['.github/workflows/build.yml'], 'closureHash': digest})
    entry = result.observations[0].details['entries'][0]
    assert 'Product build' in entry['excerpt'] and entry['provenance']['commit'] == 'a'*40
    assert entry['instructionAuthority'] is False and entry['nativeInput'] is False


@pytest.mark.parametrize('path', ['.env', '.git/config', '.git/hooks/post-checkout', '.gitmodules',
    '.github/.env', '.github/secrets/anything.txt', '.github/workflows/secrets.toml',
    '.github/keys/deploy.txt', '.github/deploy.pem', '.github/id_rsa.pub', '.github/id_rsa_backup',
    '.github/credentials.prod.json', '.github/../outside', '.github/a/.config'])
async def test_private_metadata_is_rejected_as_a_whole(env, path):  # noqa: F811
    projects, _, actor, ident, _, download, service = env
    download.raw = archive([('good.py', 'data'), (path, 'private')])
    with pytest.raises(DomainError):
        await service.import_repository(actor, ident, request(), 'metadata')
    with projects.transaction() as session:
        project = session.get(Project, ident)
        assert project.revision == 1 and not project.data.get('sourceManifest') and not project.data.get('repositoryContextManifest')


@pytest.mark.parametrize('value', [None, [], {}, '.github'])
def test_metadata_path_input_is_defensive(value):
    with pytest.raises(DomainError):
        metadata_path(value)


@pytest.mark.parametrize('mutation', ['bytes', 'closure', 'role', 'provenance', 'stale', 'membership', 'deleted'])
async def test_metadata_reads_recheck_exact_authority_and_hashes(env, mutation):  # noqa: F811
    from kleos.access.service import OrganizationMember
    projects, actor, ident, artifacts = await imported_metadata(env)
    context = projects.context(actor, ident)
    run = projects.submit(actor, ident, 'Inspect metadata', 2, 'run')
    scope = RunScope(actor=actor, project_id=ident, run_id=run['runId'], revision=3,
        deadline_at=datetime.now(UTC)+timedelta(minutes=5))
    ref = next(ref for ref in context.repository_metadata if ref.path == '.gitignore')
    with projects.transaction() as session:
        project, row = session.get(Project, ident), session.get(Artifact, ref.id)
        if mutation == 'bytes':
            artifacts.blobs._path(ref.sha256).write_bytes(b'changed bytes')
        elif mutation == 'closure':
            project.data = {**project.data, 'repositoryContextHash': 'f'*64}
        elif mutation == 'role':
            row.details = {**row.details, 'nativeInput': True}
        elif mutation == 'provenance':
            row.details = {**row.details, 'repository': {**row.details['repository'], 'commit': 'b'*40}}
        elif mutation == 'stale':
            project.advance_revision()
        elif mutation == 'membership':
            session.delete(session.get(OrganizationMember, (actor.tenant_id, actor.user_id)))
        else:
            project.data = {**project.data, 'deletion': {'fixture': True}}
    with pytest.raises(DomainError):
        await RepositoryContextReader(GraphRepository(projects), artifacts).invoke(scope,
            TaskSpec(id='metadata', objective='Inspect metadata', capability='research'),
            {'paths': ['.gitignore'], 'closureHash': context.repository_metadata_hash})


@pytest.mark.parametrize('content', [b'\xff', b'bad\0text', b'x'*(256*1024+1)])
async def test_metadata_text_and_byte_bounds(env, content):  # noqa: F811
    projects, _, actor, ident, _, download, service = env
    download.raw = archive([('main.py', b'data'), ('.github/README.md', content)])
    with pytest.raises(DomainError):
        await service.import_repository(actor, ident, request(), 'metadata')
    with projects.transaction() as session:
        assert session.get(Project, ident).revision == 1


@pytest.mark.parametrize('files', [[(f'.github/file{index}.txt', b'x') for index in range(65)],
    [(f'.github/file{index}.txt', b'x'*(256*1024)) for index in range(9)]])
async def test_metadata_count_and_total_bounds(env, files):  # noqa: F811
    projects, _, actor, ident, _, download, service = env
    download.raw = archive([('main.py', b'data'), *files])
    with pytest.raises(DomainError) as error:
        await service.import_repository(actor, ident, request(), 'metadata')
    assert error.value.code == 'repository_metadata_limit'
    with projects.transaction() as session:
        assert session.get(Project, ident).revision == 1


async def test_metadata_conflict_rolls_back_other_added_sources(env):  # noqa: F811
    projects, _, actor, ident, _, download, service = env
    await imported_metadata(env)
    download.raw = archive([('added.py', b'new'), ('.gitignore', b'changed')])
    with pytest.raises(DomainError) as error:
        await service.import_repository(actor, ident, {**request(), 'expectedEngineeringRevision': 2}, 'second')
    assert error.value.code == 'source_conflict'
    with projects.transaction() as session:
        project = session.get(Project, ident)
        assert project.revision == 2 and set(project.data['sourceManifest']) == {'main.py'}
