"""Explicit reviewed MCP installations; cleanup survives installation removal."""
import json
from pathlib import Path

from sqlalchemy import select

from kleos.capabilities.mcp_bridge.bridge import ArtifactInspector, register_inspector
from kleos.capabilities.mcp_bridge.docker import DockerTransport
from kleos.capabilities.mcp_bridge.installation import Installation, qualified_hash
from kleos.capabilities.mcp_bridge.runner import DockerRunner, McpSession
from kleos.contracts.models import DomainError


def read_installations(settings):
    configured, keys = [], set()
    try:
        for raw in settings.mcp_installations:
            if set(raw) != {'installation', 'qualificationReference'}:
                raise ValueError('Unexpected MCP configuration')
            installation = Installation.model_validate(raw['installation'])
            if installation.id in keys:
                raise ValueError('Duplicate MCP installation')
            keys.add(installation.id)
            path = Path(raw['qualificationReference'])
            if path.is_symlink() or not path.is_file():
                raise ValueError('Qualification must be an operator-owned file')
            with path.open('rb') as stream:
                content = stream.read(262145)
            if len(content) > 262144:
                raise ValueError('Qualification report exceeds its bound')
            report = json.loads(content)
            qualified_hash(installation, report, environment=settings.environment)
            configured.append((installation, report))
    except (OSError, ValueError, TypeError, KeyError):
        raise DomainError('MCP needs matching reviewed installation evidence', 'mcp_unqualified', 503) from None
    return configured


class McpRuntime:
    def __init__(self, settings, registry, repository, artifacts, capacity):
        configured = read_installations(settings)
        self.settings, self.repository, self.capacity = settings, repository, capacity
        self.docker = self.runner = None
        self.keys = []
        # No daemon connection, container creation or host plugin launch at startup.
        for installation, report in configured:
            inspector = ArtifactInspector(installation, report, environment=settings.environment,
                repository=repository, artifacts=artifacts, runner=self)
            self.keys.append(register_inspector(registry, inspector))

    def _runner(self):
        if self.runner is None:
            self.docker = DockerTransport(socket=self.settings.docker_socket)
            self.runner = DockerRunner(self.repository, self.capacity, self.docker,
                control_root=self.settings.mcp_control_root,
                daemon_control_root=self.settings.mcp_daemon_control_root)
        return self.runner

    def open(self, *args, **kwargs):
        return self._runner().open(*args, **kwargs)

    async def preflight(self, scope, installation):
        return await self._runner().preflight(scope, installation)

    async def reconcile(self):
        # Query durable work even when the operator removed all installations.
        # Cleanup grants no tool registration or permission to restart a session.
        with self.repository.store.transaction() as session:
            pending = session.scalar(select(McpSession.id).where(McpSession.status != 'removed').limit(1))
        if pending is not None:
            return await self._runner().reconcile_due()
        return {'reconciled': 0}

    async def close(self):
        if self.docker is not None:
            await self.docker.close()
