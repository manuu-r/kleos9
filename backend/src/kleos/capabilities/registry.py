"""Operator-installed, immutable capability bindings; no user-supplied executables."""
import hashlib
import json
from dataclasses import dataclass
from typing import Awaitable, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field

from kleos.contracts.models import Capability, DomainError, WireModel
from kleos.orchestration.specialist import RegisteredTool, ToolResult


class ToolManifest(WireModel):
    model_config = ConfigDict(alias_generator=WireModel.model_config['alias_generator'],
                              populate_by_name=True, extra='forbid', frozen=True)
    id: str = Field(pattern=r'^[a-z][a-z0-9_.-]{0,79}$')
    version: str = Field(pattern=r'^[0-9]+\.[0-9]+\.[0-9]+$')
    capability: Capability
    operation: str = Field(pattern=r'^[a-z][a-z0-9_.-]{0,79}$')
    provider: str = Field(pattern=r'^[a-z][a-z0-9_.-]{0,79}$')
    provider_version: str = Field(min_length=1, max_length=100)
    installation_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    transport: Literal['native', 'mcp', 'http', 'internal']
    effect: Literal['read', 'artifact_write', 'external_action'] = 'read'
    description: str = Field(min_length=1, max_length=2000)
    instructions: str = Field(default='', max_length=6000)
    qualified: bool = False

    @property
    def key(self):
        return f'{self.id}@{self.version}'


@dataclass(frozen=True)
class Binding:
    manifest: ToolManifest
    schema: type[BaseModel]
    invoke: Callable[..., Awaitable[ToolResult]]
    authorize: Callable[..., Awaitable[None]]
    digest: str


class CapabilityRegistry:
    def __init__(self):
        self._installed: dict[str, Binding] = {}
        self._enabled: set[str] = set()

    def register(self, manifest: ToolManifest, schema: type[BaseModel], invoke, authorize, *, enabled=True):
        """Called only by operator-owned startup wiring, never through agent tools."""
        if not manifest.qualified:
            raise DomainError('Unqualified tools cannot be enabled', 'tool_unqualified')
        if schema.model_config.get('extra') != 'forbid':
            raise ValueError('Tool input schemas must reject unknown fields')
        encoded = {'manifest': manifest.model_dump(mode='json'), 'inputSchema': schema.model_json_schema()}
        digest = hashlib.sha256(json.dumps(encoded, sort_keys=True).encode()).hexdigest()
        if manifest.key in self._installed:
            raise DomainError('Installed tool versions are immutable', 'installation_conflict')
        self._installed[manifest.key] = Binding(manifest, schema, invoke, authorize, digest)
        if enabled:
            self._enabled.add(manifest.key)
        return digest

    def revoke(self, key):
        """Operator revocation also fences existing snapshots at next dispatch."""
        self._enabled.discard(key)

    def snapshot(self, selected: list[str]):
        if len(selected) > 64 or len(selected) != len(set(selected)):
            raise DomainError('Invalid capability selection', 'invalid_tool_selection')
        return [{'key': key, 'digest': self._binding(key).digest,
                 'manifest': self._binding(key).manifest.model_dump(mode='json')}
                for key in selected]

    def _binding(self, key):
        if key not in self._enabled or key not in self._installed:
            raise DomainError('Selected tool is unavailable', 'tool_unavailable')
        return self._installed[key]

    def available(self):
        return sorted({self._installed[key].manifest.capability.value for key in self._enabled})

    def catalog(self):
        return [{'key': key, 'digest': self._installed[key].digest,
                 'manifest': self._installed[key].manifest.model_dump(mode='json')}
                for key in sorted(self._enabled)]

    def tools(self, snapshot: list[dict]):
        result = []
        names = set()
        for pin in snapshot:
            binding = self._binding(pin['key'])
            if binding.digest != pin['digest']:
                raise DomainError('Tool installation differs from pinned version', 'installation_conflict')
            name = binding.manifest.id
            if name in names:
                raise DomainError('Select only one version of a tool per attempt', 'invalid_tool_selection')
            names.add(name)
            result.append(self._tool(pin['key'], binding))
        return result

    def _tool(self, key, binding):
        async def authorize(scope, task, arguments):
            current = self._binding(key)
            if current.digest != binding.digest or task.capability != binding.manifest.capability:
                raise DomainError('Tool does not match the pinned task capability', 'tool_unavailable')
            parsed = binding.schema.model_validate(arguments)
            await binding.authorize(scope, task, parsed.model_dump(mode='json', by_alias=True))

        async def invoke(scope, task, arguments):
            # Recheck even when called without ModelSpecialist, including operator revocation.
            await authorize(scope, task, arguments)
            parsed = binding.schema.model_validate(arguments)
            output = ToolResult.model_validate(await binding.invoke(
                scope, task, parsed.model_dump(mode='json', by_alias=True)))
            # A generator cannot label its own checks as independent.
            if binding.manifest.capability != Capability.VALIDATION:
                output = output.model_copy(update={'observations': [check.model_copy(update={'independent': False})
                                                                     for check in output.observations]})
            return output
        manifest = binding.manifest
        description = manifest.description + ('\nSkill instructions: ' + manifest.instructions if manifest.instructions else '')
        return RegisteredTool(manifest.id, description, frozenset({manifest.capability.value}), invoke, authorize,
                              binding.schema.model_json_schema(by_alias=True))
