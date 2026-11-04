"""Immutable advisory repository metadata, deliberately outside native source inputs."""
import re
from uuid import uuid4

from kleos.artifacts.import_archive import _path, _portable_prefixes
from kleos.artifacts.service import Artifact
from kleos.contracts.models import ArtifactRef, DomainError
from kleos.projects.store import fingerprint

MAX_METADATA_FILES = 64
MAX_METADATA_FILE = 256*1024
MAX_METADATA_TOTAL = 2*1024**2
ROOT_FILES = {'.gitignore', '.gitattributes', '.editorconfig'}
PRIVATE_NAMES = {'id_rsa', 'id_dsa', 'id_ecdsa', 'id_ed25519', 'credentials', 'credential',
                 'secrets', 'secret', 'private', 'keys', 'credentials.json', 'secrets.json',
                 'secrets.yml', 'secrets.yaml'}
PRIVATE_SUFFIXES = ('.pem', '.key', '.p12', '.pfx', '.keystore', '.jks')


def metadata_path(path, *, directory=False):
    if not isinstance(path, str):
        raise DomainError('Repository metadata path is unsupported', 'repository_metadata_path', 422)
    if path in ROOT_FILES:
        return path
    if path == '.github' and directory:
        return path
    if not isinstance(path, str) or not path.startswith('.github/'):
        raise DomainError('Repository metadata path is unsupported', 'repository_metadata_path', 422)
    tail = path[len('.github/'):]
    _path(tail)
    if any(part.casefold() in PRIVATE_NAMES or part.casefold().split('.', 1)[0] in PRIVATE_NAMES
           or part.casefold().startswith(('id_rsa', 'id_dsa', 'id_ecdsa', 'id_ed25519'))
           or part.casefold().endswith(PRIVATE_SUFFIXES) for part in tail.split('/')):
        raise DomainError('Private repository paths cannot become context', 'repository_metadata_private', 422)
    return path


def repository_archive_path(path):
    """The provider wrapper is validated, then a finite metadata path exception applies."""
    if not isinstance(path, str) or '/' not in path:
        return _path(path)
    wrapper, relative = path.split('/', 1)
    _path(wrapper)
    if relative in ROOT_FILES or relative == '.github' or relative.startswith('.github/'):
        metadata_path(relative, directory=relative == '.github')
        # Metadata never bypasses total path, Unicode or portable-name bounds.
        _path(wrapper+'/metadata/'+relative.removeprefix('.'))
        return path
    return _path(path)


def split_metadata(files):
    sources, metadata, total = [], [], 0
    for path, content in files:
        if path in ROOT_FILES or path == '.github' or path.startswith('.github/'):
            metadata_path(path)
            total += len(content)
            if len(metadata) >= MAX_METADATA_FILES or len(content) > MAX_METADATA_FILE or total > MAX_METADATA_TOTAL:
                raise DomainError('Repository metadata exceeds its bound', 'repository_metadata_limit', 422)
            try:
                decoded = content.decode('utf-8')
            except UnicodeDecodeError:
                raise DomainError('Repository metadata must be UTF-8 text', 'repository_metadata_content', 422) from None
            if '\x00' in decoded:
                raise DomainError('Repository metadata must be UTF-8 text', 'repository_metadata_content', 422)
            metadata.append((path, content))
        else:
            sources.append((path, content))
    return sources, metadata


def closure_hash(refs):
    return fingerprint({ref.path: {'id': ref.id, 'sha256': ref.sha256, 'size': ref.size} for ref in refs})


def resolve_repository_metadata(session, project):
    from kleos.artifacts.repository_import import RepositoryImport

    manifest = project.data.get('repositoryContextManifest', {})
    if not isinstance(manifest, dict) or len(manifest) > MAX_METADATA_FILES:
        raise DomainError('Repository context manifest is invalid', 'artifact_conflict')
    refs, total = [], 0
    for path, ident in sorted(manifest.items()):
        metadata_path(path)
        row = session.get(Artifact, ident) if isinstance(ident, str) else None
        if (row is None or row.project_id != project.id or row.name != path or row.status != 'input'
                or row.lane != 'research' or row.details.get('origin') != 'repository_import'
                or row.details.get('role') != 'repository_context'
                or row.details.get('nativeInput') is not False or row.details.get('originalPath') != path
                or not isinstance(row.sha256, str) or not re.fullmatch('[a-f0-9]{64}', row.sha256)
                or not 0 <= row.size <= MAX_METADATA_FILE or row.revision > project.context_revision
                or not isinstance(row.details.get('repository'), dict)
                or row.details['repository'].get('archiveSha256') != row.details.get('archiveSha256')):
            raise DomainError('Repository context binding changed', 'artifact_conflict')
        provenance = row.details['repository']
        import_id = provenance.get('repositoryImportId')
        imported = session.get(RepositoryImport, import_id) if isinstance(import_id, str) else None
        if (imported is None or imported.project_id != project.id or imported.tenant_id != project.tenant_id
                or imported.status != 'completed' or imported.user_id != row.details.get('actorId')
                or imported.request_hash != fingerprint(imported.data.get('request'))
                or not isinstance(provenance.get('archiveSha256'), str)
                or not re.fullmatch('[a-f0-9]{64}', provenance['archiveSha256'])
                or imported.data.get('archiveSha256') != provenance.get('archiveSha256')
                or imported.data.get('request', {}).get('commit') != provenance.get('commit')
                or imported.data.get('request', {}).get('repositoryUrl') != provenance.get('repositoryUrl')
                or imported.data.get('publishedRevision') != row.revision
                or imported.data.get('wrapperDirectory') != provenance.get('wrapperDirectory')
                or row.details.get('archivePath') != str(provenance.get('wrapperDirectory'))+'/'+path):
            raise DomainError('Repository metadata provenance changed', 'artifact_conflict')
        refs.append(ArtifactRef(id=row.id, path=path, sha256=row.sha256, size=row.size,
                                revision=row.revision, media_type=row.media_type))
        total += row.size
    if total > MAX_METADATA_TOTAL or len({ref.id for ref in refs}) != len(refs):
        raise DomainError('Repository context closure exceeds its bound', 'artifact_conflict')
    digest = closure_hash(refs) if refs else None
    if project.data.get('repositoryContextHash') != digest:
        raise DomainError('Repository context closure hash changed', 'artifact_conflict')
    return refs, digest


def publish_metadata(session, projects, artifacts, actor, project, metadata, provenance, original_paths):
    existing, _ = resolve_repository_metadata(session, project)
    if not metadata:
        return
    manifest = dict(project.data.get('repositoryContextManifest', {}))
    if (len(existing)+len(metadata) > MAX_METADATA_FILES
            or len(project.data.get('sourceManifest', {}))+len(existing)+len(metadata) > 256
            or sum(ref.size for ref in existing)+sum(len(content) for _, content in metadata) > MAX_METADATA_TOTAL):
        raise DomainError('Project repository context exceeds its bound', 'repository_metadata_limit', 422)
    paths = set(manifest)
    if any(path in paths or any(path.startswith(old+'/') or old.startswith(path+'/') for old in paths) for path, _ in metadata):
        raise DomainError('Repository metadata import is add-only', 'source_conflict')
    _portable_prefixes([*paths, *(path for path, _ in metadata)])
    refs = list(existing)
    for path, content in metadata:
        digest = artifacts.blobs.put(content)
        row = Artifact(id=str(uuid4()), project_id=project.id, revision=project.context_revision,
            name=path, sha256=digest, size=len(content), media_type='text/plain', status='input', lane='research',
            details={'origin': 'repository_import', 'actorId': actor.user_id, 'role': 'repository_context',
                'nativeInput': False, 'originalPath': path, 'archivePath': original_paths[path],
                'archiveSha256': provenance['archiveSha256'], 'repository': provenance})
        session.add(row)
        manifest[path] = row.id
        refs.append(ArtifactRef(id=row.id, path=path, sha256=digest, revision=row.revision, size=row.size, media_type=row.media_type))
    digest = closure_hash(refs)
    project.data = {**project.data, 'repositoryContextManifest': manifest, 'repositoryContextHash': digest}
    projects._emit(session, project, 'repository_context.imported', {'closureHash': digest,
        'metadata': [ref.model_dump(mode='json', by_alias=True) for ref in refs], 'nativeInput': False,
        'instructionAuthority': False, 'importId': provenance['repositoryImportId']})
