"""Fixed native actions bound to an isolated, scoped execution gateway."""
from typing import Protocol

from pydantic import Field

from kleos.capabilities.registry import CapabilityRegistry, ToolManifest
from kleos.contracts.models import Capability, WireModel
from kleos.orchestration.specialist import ToolResult


class ActionInput(WireModel):
    action_id: str = Field(min_length=1, max_length=100)


class ReadInput(ActionInput):
    path: str = Field(min_length=1, max_length=240)
    offset: int = Field(default=0, ge=0, le=32 * 1024 * 1024)
    max_bytes: int = Field(default=16000, ge=1, le=256000)


class PatchInput(ActionInput):
    path: str = Field(min_length=1, max_length=240)
    content: str = Field(max_length=240000)
    expected_sha256: str | None = Field(pattern=r'^[a-f0-9]{64}$')


class RenderInput(ActionInput):
    view: str = Field(default='isometric', pattern=r'^(front|top|isometric)$')


class NativeGateway(Protocol):
    async def authorize(self, scope, task, arguments) -> None: ...
    async def invoke(self, scope, task, action: str, arguments: dict) -> dict: ...


_ACTIONS = {'read': ReadInput, 'patch': PatchInput, 'build': ActionInput, 'analyze': ActionInput,
            'render': RenderInput, 'finish': ActionInput}


def register_native(registry: CapabilityRegistry, gateway: NativeGateway, *, capability: Capability,
                    provider: str, provider_version: str, installation_hash: str,
                    qualified: bool, actions=None, qualification_scope=''):
    """Operator wires an inspected image/tool implementation, never a client executable."""
    if capability not in {Capability.CAD, Capability.PCB, Capability.FIRMWARE,
                          Capability.APPLICATION, Capability.VALIDATION}:
        raise ValueError('This capability is not a native engineering lane')
    if actions is None:
        actions = ('read', 'analyze', 'finish') if capability == Capability.VALIDATION else tuple(_ACTIONS)
    if any(action not in _ACTIONS or (capability == Capability.VALIDATION and action == 'patch') for action in actions):
        raise ValueError('Unsupported native capability action')
    lane_limits = {
        Capability.APPLICATION: 'Current application exports are web archives only. APK, iOS, BLE and browser/device behavior are not qualified.',
        Capability.FIRMWARE: 'Requires an explicitly accepted qualified firmware board. ELF/BIN compilation does not prove execution or peripheral behavior.',
        Capability.PCB: 'Requires the accepted circuit contract with exact references and peripheral identities. Routing/ERC/DRC do not prove physical function.',
        Capability.CAD: 'Produce separate part exports with manufacturing coordinates. Exploded offsets are display-only; generator checks cannot accept a release.'}
    keys = []
    for action in actions:
        manifest = ToolManifest(id=f'{capability.value}.{action}', version='1.0.0', capability=capability,
            operation=action, provider=provider, provider_version=provider_version,
            installation_hash=installation_hash, transport='native',
            effect='artifact_write' if action in {'patch', 'build', 'render', 'finish'} else 'read',
            qualified=qualified, description=f'{action.capitalize()} the current {capability.value} task workspace.',
            instructions='Use stable actionId for one operation; a new operation needs a new ID. '
                'Read hashes before patching. Preserve files outside allowedPaths. All execution is isolated. '
                'Inspect real build errors and finish only after required files and checks are produced. '
                + lane_limits.get(capability, '') + ' Qualified scope: ' + str(qualification_scope)[:1000])
        async def invoke(scope, task, arguments, operation=action):
            return ToolResult.model_validate(await gateway.invoke(scope, task, operation, arguments))
        registry.register(manifest, _ACTIONS[action], invoke, gateway.authorize)
        keys.append(manifest.key)
    return keys
