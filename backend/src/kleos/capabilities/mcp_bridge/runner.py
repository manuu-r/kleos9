"""Durable isolated MCP session admission and authority-independent cleanup."""
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import anyio
from sqlalchemy import JSON, Float, String, func, select
from sqlalchemy.orm import Mapped, mapped_column

from kleos.contracts.models import Actor, DomainError
from kleos.projects.store import Base, Objective, fingerprint

from .bridge import RemoteArguments
from .docker import PROTOCOL, RESOURCES, seccomp_hash
from .installation import source_hashes


class McpSession(Base):
    __tablename__ = 'kleos2_mcp_sessions'
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    actor_id: Mapped[str] = mapped_column(String(100))
    tenant_id: Mapped[str] = mapped_column(String(100))
    request_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), index=True)
    request: Mapped[dict] = mapped_column(JSON)
    container_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[float] = mapped_column(Float)
    finished_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    termination: Mapped[dict] = mapped_column(JSON)


def _stage(root, ident, files):
    root = Path(root).absolute()
    if any(path.is_symlink() for path in (root, *root.parents)):
        raise DomainError('MCP control directory must not contain symlinks', 'mcp_storage_invalid')
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = root / ident

    def verify():
        if path.is_symlink() or not path.is_dir() or set(item.name for item in path.iterdir()) != set(files):
            raise DomainError('MCP staged input directory differs from admission', 'mcp_storage_invalid')
        for name, content in files.items():
            fd = os.open(path / name, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd, 'rb') as stream:
                info = os.fstat(stream.fileno())
                if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size != len(content)
                        or stream.read(len(content) + 1) != content):
                    raise DomainError('MCP staged source differs from admission', 'mcp_storage_invalid')

    if path.exists() or path.is_symlink():
        verify()
        return path
    temporary = Path(tempfile.mkdtemp(prefix=ident + '.staging-', dir=root))
    try:
        for name, content in files.items():
            _write(temporary / name, content)
        temporary.chmod(0o555)
        try:
            temporary.rename(path)
        except FileExistsError:
            verify()
        except OSError:
            if path.exists():
                verify()
            else:
                raise
    finally:
        if temporary.exists():
            temporary.chmod(0o700)
            shutil.rmtree(temporary)
    return path


def _write(path, content):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o444)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


class DockerRunner:
    def __init__(self, repository, capacity, docker, *, control_root, daemon_control_root=None):
        self.repository, self.capacity, self.docker = repository, capacity, docker
        self.control_root = Path(control_root).absolute()
        self.daemon_control_root = Path(daemon_control_root or self.control_root)
        if not self.daemon_control_root.is_absolute():
            raise ValueError('MCP daemon control root must be an operator-owned absolute path')

    def _request(self, scope, task, installation, inputs, action_id, daemon_id):
        return {'protocol': PROTOCOL, 'actor': scope.actor.model_dump(mode='json'),
            'projectId': scope.project_id, 'runId': scope.run_id, 'revision': scope.revision,
            'task': task.model_dump(mode='json'), 'actionId': action_id,
            'installation': installation.model_dump(mode='json'), 'bridgeSources': source_hashes(),
            'toolSchemaHash': fingerprint(RemoteArguments.model_json_schema(by_alias=True)),
            'inputs': [{'artifactId': item.reference.id, 'path': item.path,
                'sha256': item.reference.sha256, 'size': item.reference.size,
                'mediaType': item.reference.media_type} for item in inputs],
            'resources': RESOURCES, 'seccompSha256': seccomp_hash(),
            'deadlineAt': scope.deadline_at.isoformat(), 'capacityPoolId': self.capacity.pool_id,
            'maxLifetimeSeconds': installation.timeout_seconds + 15, 'daemonId': daemon_id}

    async def preflight(self, scope, installation):
        await self.repository.assert_active(scope)
        return await self.docker.preflight(installation)

    @asynccontextmanager
    async def open(self, scope, task, installation, inputs, *, action_id):
        await self.repository.assert_active(scope)
        ids = [item.reference.id for item in inputs]
        if (not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', action_id) or task.capability != 'research'
                or not 1 <= len(inputs) <= 16 or len(ids) != len(set(ids))
                or not set(ids).issubset(task.input_artifact_ids)
                or sum(item.reference.size for item in inputs) > installation.max_input_bytes):
            raise DomainError('MCP session input is outside its authorized scope', 'artifact_scope_conflict')
        exact = await self.repository.resolve_artifacts(scope, ids)
        if exact != [item.reference for item in inputs]:
            raise DomainError('MCP session source references changed', 'artifact_scope_conflict')
        if any(ref.media_type not in installation.media_types for ref in exact):
            raise DomainError('MCP image does not support this source type', 'mcp_input_limit')
        # Image absence/invalid labels are known pre-dispatch failures; do not
        # reserve capacity or create uncertain dispatch history for those.
        deployment = await self.preflight(scope, installation)
        request = self._request(scope, task, installation, inputs, action_id, deployment['daemonId'])
        request_hash = fingerprint(request)
        ident = fingerprint([scope.run_id, task.id, action_id, installation.id])[:32]
        directory = self.daemon_control_root / ident
        with self.repository.store.transaction() as session:
            self.repository._check(session, scope)
            old = session.get(McpSession, ident)
            if old is not None:
                code = 'effect_uncertain' if old.request_hash == request_hash else 'idempotency_conflict'
                raise DomainError('MCP session identity has already dispatched; reconcile before retry', code)
        # Stage before compute admission so a local IO/validation failure cannot
        # strand a reservation for a container that was never dispatched.
        files = {}
        for index, item in enumerate(inputs):
            if (item.path != f'inputs/{index:03}' or len(item.content) != item.reference.size
                    or hashlib.sha256(item.content).hexdigest() != item.reference.sha256):
                raise DomainError('MCP staged bytes differ from admission', 'artifact_hash_mismatch')
            files[f'{index:03}'] = item.content
        files['request.json'] = json.dumps({'protocol': PROTOCOL, 'inputs': request['inputs'],
            'timeoutSeconds': installation.timeout_seconds, 'toolName': installation.tool_name,
            'serverName': installation.server_name, 'serverVersion': installation.server_version},
            allow_nan=False, separators=(',', ':')).encode()
        _stage(self.control_root, ident, files)
        admitted = False
        while not admitted:
            with self.repository.store.transaction() as session:
                self.repository._check(session, scope)
                if session.get(McpSession, ident) is not None:
                    raise DomainError('MCP session was concurrently admitted', 'effect_uncertain')
                admitted = self.capacity.reserve('mcp:' + ident, scope.project_id, request_hash, RESOURCES,
                        session=session, details={'containerName': 'kleos-mcp-' + ident,
                            'imageDigest': installation.image, 'protocol': PROTOCOL})
                if admitted:
                    session.add(McpSession(id=ident, project_id=scope.project_id, run_id=scope.run_id,
                        actor_id=scope.actor.user_id, tenant_id=scope.actor.tenant_id, request_hash=request_hash,
                        status='dispatching', request=request, container_id=None, created_at=time.time(),
                        finished_at=None, termination={}))
            if not admitted:
                await anyio.sleep(.1)
        observed = None
        try:
            observed = await self.docker.create(ident, request_hash, installation, directory,
                image_env=deployment['environmentKeys'], daemon_id=deployment['daemonId'])
            # Persist observed identity even after cancellation so cleanup can prove
            # removal. This write grants no publication or further dispatch rights.
            with self.repository.store.transaction() as session:
                row = session.get(McpSession, ident)
                row.container_id = observed
                row.status = 'created'
            await self.repository.assert_active(scope)
            async with self.docker.attach(observed) as channel:
                await self.repository.assert_active(scope)
                await self.docker.start(observed, ident, request_hash, installation, directory)
                with self.repository.store.transaction() as session:
                    self.repository._check(session, scope)
                    session.get(McpSession, ident).status = 'running'
                yield channel
        finally:
            # Cleanup is shielded and bounded independently of revoked authority.
            # Failed/unknown cleanup keeps the capacity reservation; a later
            # reconciler repeats only ownership-checked removal, never tool work.
            with anyio.CancelScope(shield=True):
                with anyio.move_on_after(15):
                    await self.reconcile(ident)

    async def reconcile(self, ident):
        with self.repository.store.transaction() as session:
            row = session.get(McpSession, ident)
            if row is None:
                raise DomainError('MCP session does not exist', 'not_found', 404)
            if row.status == 'removed':
                return row.termination
            request_hash, image, observed = row.request_hash, row.request['installation']['image'], row.container_id
            if row.request.get('capacityPoolId') != self.capacity.pool_id:
                raise DomainError('MCP cleanup belongs to another compute pool', 'capacity_pool_conflict')
            daemon_id = row.request.get('daemonId')
        if not daemon_id or await self.docker.daemon_identity() != daemon_id:
            raise DomainError('MCP cleanup belongs to another Docker daemon', 'mcp_daemon_conflict')
        proof = await self.docker.remove(ident, request_hash, image, observed)
        with self.repository.store.transaction() as session:
            row = session.scalar(select(McpSession).where(McpSession.id == ident).with_for_update())
            if row.request_hash != request_hash or (row.container_id and row.container_id != proof['identity']):
                raise DomainError('MCP cleanup identity changed', 'mcp_container_conflict')
            self.capacity.release('mcp:' + ident, request_hash,
                {'kind': 'container-removed', 'identity': 'kleos-mcp-' + ident}, session=session)
            row.status, row.container_id, row.finished_at = 'removed', proof['identity'], time.time()
            row.termination = proof
        return proof

    async def reconcile_due(self, *, limit=16):
        if type(limit) is not int or not 1 <= limit <= 64:
            raise ValueError('MCP reconciliation batch is invalid')
        # Read a bounded batch, then decide under each project's current authority.
        # This is maintenance of the same durable jobs, not another workflow engine.
        with self.repository.store.transaction() as session:
            ids = list(session.scalars(select(McpSession.id).where(McpSession.status != 'removed')
                .order_by(func.coalesce(McpSession.termination['lastCheckedAt'].as_float(),
                                        McpSession.created_at), McpSession.id).limit(limit)))
        outcomes = []
        for ident in ids:
            with self.repository.store.transaction() as session:
                row = session.get(McpSession, ident)
                if row is None or row.status == 'removed':
                    continue
                # Persist fair scan order, including foreign/malformed/uncertain
                # rows, so a restart cannot starve later eligible cleanup.
                row.termination = {**row.termination, 'lastCheckedAt': time.time()}
                try:
                    request = row.request
                    deadline = datetime.fromisoformat(request['deadlineAt'])
                    lifetime = request['maxLifetimeSeconds']
                    if (deadline.tzinfo is None or type(lifetime) is not int or not 1 <= lifetime <= 135
                            or not isinstance(request['daemonId'], str) or not request['daemonId']):
                        raise ValueError('Invalid persisted MCP lifetime')
                except (ValueError, TypeError, KeyError):
                    outcomes.append({'sessionId': ident, 'status': 'blocked', 'code': 'mcp_history_invalid'})
                    continue
                if request.get('capacityPoolId') != self.capacity.pool_id:
                    outcomes.append({'sessionId': ident, 'status': 'blocked', 'code': 'capacity_pool_conflict'})
                    continue
                now = time.time()
                due = (now >= row.created_at + lifetime
                       or datetime.now(UTC) >= deadline)
                run = session.get(Objective, row.run_id)
                if run is None or run.status not in {'pending', 'running', 'waiting'}:
                    due = True
                if not due:
                    try:
                        actor = Actor(tenant_id=row.tenant_id, user_id=row.actor_id)
                        project = self.repository.store.authorized(session, actor, row.project_id, write=True)
                        due = (project.context_revision != row.request['revision']
                               or run.revision != row.request['revision'])
                    except DomainError:
                        due = True
            if due:
                try:
                    with anyio.fail_after(15):
                        await self.reconcile(ident)
                    outcomes.append({'sessionId': ident, 'status': 'removed'})
                except Exception:
                    outcomes.append({'sessionId': ident, 'status': 'blocked', 'code': 'mcp_termination_uncertain'})
        return outcomes
