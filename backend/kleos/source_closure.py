"""Exact, reusable source manifests across incremental engineering attempts."""
from sqlalchemy import select

from .contracts import DomainError
from .db import Artifact


def is_source(artifact):
    return not artifact.name.startswith('native/') and (
        not artifact.provenance.get('nativeJobId') or artifact.provenance.get('artifactRole') == 'source')


def reference(artifact):
    return {'id': artifact.id, 'name': artifact.name, 'lane': artifact.lane, 'sha256': artifact.sha256,
            'size': artifact.size, 'mediaType': artifact.media_type, 'provenance': artifact.provenance}


def current_sources(session, project, lane):
    files = [a for a in session.scalars(select(Artifact).where(
        Artifact.tenant_id == project.tenant_id, Artifact.project_id == project.id,
        Artifact.revision == project.engineering_revision, Artifact.lane == lane,
        Artifact.status.in_(['candidate', 'accepted'])).order_by(Artifact.name)) if is_source(a)]
    if len(files) > 256 or len({a.name for a in files}) != len(files):
        raise DomainError('The source manifest is too large or contains ambiguous paths', 409, 'stale_artifacts')
    return files


def resolve_sources(session, project, lane, refs):
    if not isinstance(refs, list) or len(refs) > 256 or any(not isinstance(ref, dict) for ref in refs):
        raise DomainError('Invalid source manifest', 409, 'stale_artifacts')
    files = []
    for ref in refs:
        artifact = session.get(Artifact, ref.get('id'))
        if (not artifact or artifact.tenant_id != project.tenant_id or artifact.project_id != project.id
                or artifact.revision != project.engineering_revision or artifact.lane != lane
                or artifact.status not in {'candidate', 'accepted'} or not is_source(artifact)
                or reference(artifact) != ref):
            raise DomainError('Source manifest changed or was superseded', 409, 'stale_artifacts')
        files.append(artifact)
    if len({a.id for a in files}) != len(files) or len({a.name for a in files}) != len(files):
        raise DomainError('Source manifest contains duplicate inputs', 409, 'stale_artifacts')
    return files
