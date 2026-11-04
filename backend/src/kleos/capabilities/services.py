"""Typed integration boundaries for operator-installed research/sourcing/human providers."""
from typing import Protocol

from pydantic import Field

from kleos.capabilities.registry import CapabilityRegistry, ToolManifest
from kleos.contracts.models import Capability, WireModel
from kleos.orchestration.specialist import ToolResult


class SearchInput(WireModel):
    query: str = Field(min_length=1, max_length=2000)
    limit: int = Field(default=5, ge=1, le=20)


class QuoteItem(WireModel):
    supplier_part: str = Field(min_length=1, max_length=120)
    quantity: int = Field(ge=1, le=100000)


class QuoteInput(WireModel):
    items: list[QuoteItem] = Field(min_length=1, max_length=100)
    currency: str = Field(default='USD', pattern=r'^[A-Z]{3}$')


class DraftWorkInput(WireModel):
    title: str = Field(min_length=1, max_length=200)
    procedure: str = Field(min_length=1, max_length=12000)
    evidence_required: list[str] = Field(min_length=1, max_length=30)
    artifact_ids: list[str] = Field(min_length=1, max_length=64)
    ceiling_minor_units: int = Field(ge=0, le=100000000)
    currency: str = Field(default='USD', pattern=r'^[A-Z]{3}$')


class ObserveInput(WireModel):
    external_job_id: str = Field(min_length=1, max_length=160)


class ApprovedActionInput(WireModel):
    prepared_action_id: str = Field(min_length=1, max_length=100)
    approval_id: str = Field(min_length=1, max_length=100)


class ServiceGateway(Protocol):
    async def authorize(self, scope, task, operation: str, arguments: dict) -> None: ...
    async def invoke(self, scope, task, operation: str, arguments: dict) -> dict: ...


_DEFINITIONS = {
    Capability.RESEARCH: {'search': (SearchInput, 'read')},
    Capability.SOURCING: {'search': (SearchInput, 'read'), 'quote': (QuoteInput, 'read'),
                         'observe': (ObserveInput, 'read'), 'purchase': (ApprovedActionInput, 'external_action')},
    Capability.HUMAN_WORK: {'search': (SearchInput, 'read'), 'draft': (DraftWorkInput, 'artifact_write'),
                           'observe': (ObserveInput, 'read'), 'hire': (ApprovedActionInput, 'external_action')},
}


def register_service(registry: CapabilityRegistry, gateway: ServiceGateway, *, capability: Capability,
                     provider: str, provider_version: str, installation_hash: str,
                     qualified: bool, operations: tuple[str, ...], transport='http'):
    """Only explicitly implemented/qualified operations are registered; nothing simulated."""
    definitions = _DEFINITIONS.get(capability, {})
    if not operations or any(name not in definitions for name in operations):
        raise ValueError('Unsupported service operation')
    keys = []
    for operation in operations:
        schema, effect = definitions[operation]
        manifest = ToolManifest(id=f'{capability.value}.{operation}', version='1.0.0',
            capability=capability, operation=operation, provider=provider, provider_version=provider_version,
            installation_hash=installation_hash, transport=transport, effect=effect, qualified=qualified,
            description=f'{operation.capitalize()} through the configured {provider} service.',
            instructions='Provider observations are evidence candidates. Never infer an order, hire, payment, '
                'shipment or physical completion from a draft/search. External actions require an exact '
                'prepared action and independent server-issued approval; credentials are never tool inputs.')
        async def authorize(scope, task, arguments, op=operation):
            await gateway.authorize(scope, task, op, arguments)
        async def invoke(scope, task, arguments, op=operation):
            return ToolResult.model_validate(await gateway.invoke(scope, task, op, arguments))
        registry.register(manifest, schema, invoke, authorize)
        keys.append(manifest.key)
    return keys
