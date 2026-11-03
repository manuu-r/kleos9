"""Durable read-only download receipts followed by ordinary atomic source publication."""
import asyncio
import hashlib
import time
from uuid import uuid4

from sqlalchemy import JSON, String, UniqueConstraint, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column

from kleos.access.service import Account, OrganizationMember
from kleos.artifacts.import_archive import MAX_ARCHIVE_BYTES, ArchiveImportService, _path, read_archive
from kleos.artifacts.repository_download import source_descriptor
from kleos.artifacts.repository_metadata import (
    ROOT_FILES,
    metadata_path,
    publish_metadata,
    repository_archive_path,
    split_metadata,
)
from kleos.contracts.models import DomainError
from kleos.projects.graph_repository import execution_owner
from kleos.projects.store import Base, Receipt, fingerprint


class RepositoryImport(Base):
    __tablename__ = 'kleos2_repository_imports'
    __table_args__ = (UniqueConstraint('tenant_id', 'user_id', 'idempotency_key'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(100))
    user_id: Mapped[str] = mapped_column(String(100))
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20))
    data: Mapped[dict] = mapped_column(JSON)


def repository_files(raw):
    """Remove one provider wrapper, never guess paths or omit unsupported content."""
    original = read_archive(raw, _path_validator=repository_archive_path)
    roots = {path.split('/')[0] for path, _ in original}
    if len(roots) != 1 or any('/' not in path for path, _ in original):
        raise DomainError('Repository archive needs one enclosing directory', 'repository_layout_unsupported', 422)
    files, paths = [], {}
    for name, content in original:
        path = name.split('/', 1)[1]
        if path in ROOT_FILES or path == '.github' or path.startswith('.github/'):
            metadata_path(path)
        else:
            _path(path)
        if content.startswith(b'version https://git-lfs.github.com/spec/v1'):
            raise DomainError('Git LFS objects require an explicit source upload', 'repository_lfs_unsupported', 422)
        files.append((path, content))
        paths[path] = name
    return files, paths, next(iter(roots))


class RepositoryImportService:
    def __init__(self, artifacts, downloader, *, access, clock=time.time):
        self.artifacts, self.projects, self.downloader = artifacts, artifacts.projects, downloader
        self.access, self.clock = access, clock
        self.archives = ArchiveImportService(artifacts)

    def _authorize(self, session, actor, project_id):
        # Preview serving already locks Project before organization membership.
        # Keep that order, and serialize publication with account/member revocation.
        project = self.projects.authorized(session, actor, project_id, write=True)
        account = session.get(Account, actor.user_id, with_for_update=True, populate_existing=True)
        member = session.get(OrganizationMember, (actor.tenant_id, actor.user_id),
                             with_for_update=True, populate_existing=True)
        if member is None or member.role == 'viewer' or account is None:
            raise DomainError('Organization write access is required', 'forbidden', 403)
        return project

    def _actor_lock(self, session, actor):
        if session.get_bind().dialect.name == 'postgresql':
            lock = int.from_bytes(hashlib.sha256((actor.tenant_id+':'+actor.user_id).encode()).digest()[:8], 'big', signed=True)
            session.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': lock})

    def _admit(self, actor, project_id, body, key, canonical):
        with self.projects.transaction() as session:
            self._actor_lock(session, actor)
            project = self._authorize(session, actor, project_id)
            digest = fingerprint(canonical)
            receipt = session.scalar(select(Receipt).where(Receipt.tenant_id == actor.tenant_id,
                Receipt.user_id == actor.user_id, Receipt.key == key))
            if receipt is not None:
                if receipt.fingerprint != digest:
                    raise DomainError('Idempotency key was used for different input', 'idempotency_conflict')
                return None, receipt.result
            row = session.scalar(select(RepositoryImport).where(RepositoryImport.tenant_id == actor.tenant_id,
                RepositoryImport.user_id == actor.user_id, RepositoryImport.idempotency_key == key).with_for_update())
            if row is not None:
                if row.request_hash != digest or row.project_id != project_id:
                    raise DomainError('Idempotency key was used for different input', 'idempotency_conflict')
                if row.status == 'downloaded':
                    return row.id, None
                if row.status == 'fetching':
                    code = 'repository_download_pending' if self.clock() < row.data['deadlineAt'] else 'repository_download_uncertain'
                    raise DomainError('The existing download has no confirmed result; it will not be repeated', code)
                raise DomainError('This import ended without publication; use a new explicit request to retry',
                                  row.data.get('errorCode', 'repository_import_failed'))
            if (body['expectedEngineeringRevision'] != project.context_revision
                    or canonical['expectedRevision'] is not None and canonical['expectedRevision'] != project.revision):
                raise DomainError('Project changed before repository import', 'revision_conflict')
            count = session.scalar(select(func.count()).select_from(RepositoryImport).where(
                RepositoryImport.project_id == project_id))
            active = session.scalar(select(func.count()).select_from(RepositoryImport).where(
                RepositoryImport.tenant_id == actor.tenant_id, RepositoryImport.user_id == actor.user_id,
                RepositoryImport.status == 'fetching', RepositoryImport.data['deadlineAt'].as_float() > self.clock()))
            if count >= 64 or active >= 2:
                raise DomainError('Repository import allowance is exhausted', 'repository_import_limit', 429)
            ident = str(uuid4())
            session.add(RepositoryImport(id=ident, tenant_id=actor.tenant_id, user_id=actor.user_id,
                project_id=project_id, idempotency_key=key, request_hash=digest, status='fetching',
                data={'request': canonical, 'deadlineAt': self.clock()+30, 'startedAt': self.clock(), 'attempts': 1}))
            return ident, None

    def _failed(self, ident, code, *, uncertain=False):
        with self.projects.transaction() as session:
            row = session.get(RepositoryImport, ident, with_for_update=True)
            if row and row.status in {'fetching', 'downloaded'}:
                row.status = 'uncertain' if uncertain else 'failed'
                row.data = {**row.data, 'errorCode': code}

    def cancel(self, actor, project_id, import_key, expected_revision, key):
        if execution_owner.get() is not None:
            raise DomainError('Repository import cancellation requires a user command', 'approval_required')
        if (not isinstance(project_id, str) or not 1 <= len(project_id) <= 36
                or not isinstance(import_key, str) or not 1 <= len(import_key) <= 128
                or type(expected_revision) is not int or expected_revision < 1):
            raise DomainError('Provide the import key and current revision', 'invalid_request', 422)

        def operation(session):
            project = self._authorize(session, actor, project_id)
            if project.revision != expected_revision:
                raise DomainError('Project changed before import cancellation', 'revision_conflict')
            row = session.scalar(select(RepositoryImport).where(RepositoryImport.project_id == project_id,
                RepositoryImport.tenant_id == actor.tenant_id, RepositoryImport.user_id == actor.user_id,
                RepositoryImport.idempotency_key == import_key).with_for_update())
            if row is None:
                raise DomainError('Repository import not found', 'not_found', 404)
            if row.status not in {'fetching', 'downloaded', 'uncertain', 'cancelled'}:
                raise DomainError('Repository import is already terminal; existing sources are unchanged', 'repository_import_terminal')
            if row.status != 'cancelled':
                row.status = 'cancelled'
                row.data = {**row.data, 'errorCode': 'repository_import_cancelled', 'cancelledAt': self.clock()}
                project.advance_revision(context=False)
                self.projects._emit(session, project, 'repository_import.cancelled',
                    {'importId': row.id, 'transportTerminationConfirmed': False})
            return {'projectId': project.id, 'revision': project.revision}

        return self.projects._command(actor, key, {'type': 'project.cancelRepositoryImport',
            'projectId': project_id, 'importKey': import_key, 'expectedRevision': expected_revision}, operation,
            replay_authorizer=lambda session, result: self._authorize(session, actor, project_id))

    async def import_repository(self, actor, project_id, body, key, *, expected_revision=None):
        if execution_owner.get() is not None:
            raise DomainError('Repository import requires a user command', 'approval_required')
        if (not isinstance(key, str) or not 1 <= len(key) <= 128 or not isinstance(project_id, str)
                or not 1 <= len(project_id) <= 36 or not isinstance(body, dict)
                or set(body) != {'repositoryUrl', 'commit', 'expectedEngineeringRevision'}
                or type(body['expectedEngineeringRevision']) is not int or body['expectedEngineeringRevision'] < 1
                or expected_revision is not None and (type(expected_revision) is not int or expected_revision < 1)):
            raise DomainError('Provide a bounded repository import request and idempotency key', 'invalid_request', 422)
        source = source_descriptor(body['repositoryUrl'], body['commit'])
        canonical = {'type': 'project.importRepository', 'projectId': project_id,
            'repositoryUrl': source.url, 'commit': source.commit,
            'expectedEngineeringRevision': body['expectedEngineeringRevision'], 'expectedRevision': expected_revision}
        try:
            ident, replay = self._admit(actor, project_id, body, key, canonical)
        except IntegrityError:
            raise DomainError('Repository admission changed concurrently; retry the same request', 'repository_admission_conflict') from None
        if replay is not None:
            return replay
        with self.projects.transaction() as session:
            row = session.get(RepositoryImport, ident)
            cached = row.data.get('archiveSha256') if row.status == 'downloaded' else None
        try:
            if cached is None:
                raw = await self.downloader.download(source)
                if not isinstance(raw, bytes) or not raw or len(raw) > MAX_ARCHIVE_BYTES:
                    raise DomainError('Repository archive exceeds its download bound', 'archive_limit', 422)
                digest = self.artifacts.blobs.put(raw)
                with self.projects.transaction() as session:
                    project = self._authorize(session, actor, project_id)
                    row = session.get(RepositoryImport, ident, with_for_update=True)
                    if (row.status != 'fetching' or self.clock() >= row.data['deadlineAt']
                            or project.context_revision != body['expectedEngineeringRevision']):
                        raise DomainError('Repository import context or download deadline changed', 'revision_conflict')
                    row.status = 'downloaded'
                    row.data = {**row.data, 'archiveSha256': digest, 'archiveBytes': len(raw)}
            else:
                raw, digest = self.artifacts.blobs.get(cached), cached
            files, original_paths, root = repository_files(raw)
            files, metadata = split_metadata(files)
            provenance = {'provider': 'github', 'repositoryUrl': source.url, 'commit': source.commit,
                'repositoryImportId': ident, 'archiveSha256': digest, 'wrapperDirectory': root,
                'scope': 'commit_archive_sources', 'historyIncluded': False, 'submodulesFetched': False,
                'lfsFetched': False}

            def publish(session):
                project = self._authorize(session, actor, project_id)
                row = session.get(RepositoryImport, ident, with_for_update=True)
                if row.status != 'downloaded' or row.data.get('archiveSha256') != digest:
                    raise DomainError('Repository download receipt changed', 'repository_import_conflict')
                result = self.archives.publish_files(session, actor, project_id, files,
                    source.repository+'-'+source.commit+'.zip', digest, body['expectedEngineeringRevision'],
                    expected_revision=expected_revision, provenance=provenance, original_paths=original_paths)
                publish_metadata(session, self.projects, self.artifacts, actor, project, metadata, provenance, original_paths)
                row.status = 'completed'
                row.data = {**row.data, 'completedAt': self.clock(), 'sourceHashes': fingerprint(
                    {path: hashlib.sha256(content).hexdigest() for path, content in files}),
                    'repositoryContextHash': project.data.get('repositoryContextHash'),
                    'publishedRevision': project.context_revision, 'wrapperDirectory': root}
                return result

            return self.projects._command(actor, key, canonical, publish,
                replay_authorizer=lambda session, result: self._authorize(session, actor, project_id))
        except asyncio.CancelledError:
            self._failed(ident, 'repository_download_uncertain', uncertain=True)
            raise
        except DomainError as error:
            self._failed(ident, error.code, uncertain=error.code == 'repository_download_uncertain')
            raise
        except Exception:
            self._failed(ident, 'repository_download_uncertain', uncertain=True)
            raise DomainError('Repository import outcome is unknown; it will not be repeated automatically',
                              'repository_download_uncertain', 502) from None
