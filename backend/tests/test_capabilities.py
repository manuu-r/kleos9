import pytest
from pydantic import ValidationError

from kleos.capabilities.native import register_native
from kleos.capabilities.registry import CapabilityRegistry, ToolManifest
from kleos.capabilities.services import SearchInput, register_service
from kleos.contracts.models import Capability, DomainError, TaskSpec


class Gateway:
    def __init__(self):
        self.calls, self.authorizations = [], []
        self.denied = False

    async def authorize(self, scope, task, *arguments):
        self.authorizations.append(arguments)
        if self.denied:
            raise DomainError('Server policy prevents this action', 'approval_required')

    async def invoke(self, scope, task, operation, arguments):
        self.calls.append((operation, arguments))
        return {'summary': 'Real gateway observation', 'observations': [
            {'name': 'geometry', 'status': 'UNKNOWN', 'independent': True}]}


def manifest(**changes):
    values = dict(id='research.search', version='1.0.0', capability='research', operation='search',
                  provider='catalog', provider_version='2026.1', installation_hash='a' * 64,
                  transport='http', description='Search catalog', qualified=True)
    return ToolManifest(**(values | changes))


def task(capability='cad'):
    return TaskSpec(id='task', objective='Create missing part', capability=capability, allowed_paths=['part.py'])


def native(registry, gateway, **changes):
    return register_native(registry, gateway, **(dict(capability=Capability.CAD, provider='native',
        provider_version='1.0.0', installation_hash='a' * 64, qualified=True) | changes))


def test_empty_registry_and_unqualified_installations_do_not_advertise_capabilities():
    registry, gateway = CapabilityRegistry(), Gateway()
    assert registry.available() == []
    with pytest.raises(DomainError, match='Unqualified'):
        registry.register(manifest(qualified=False), SearchInput, gateway.invoke, gateway.authorize)
    assert registry.catalog() == []
    registry.register(manifest(), SearchInput, gateway.invoke, gateway.authorize, enabled=False)
    assert registry.available() == []
    with pytest.raises(DomainError, match='unavailable'):
        registry.snapshot(['research.search@1.0.0'])


def test_pinned_installations_are_immutable_and_schema_is_exposed():
    registry, gateway = CapabilityRegistry(), Gateway()
    keys = native(registry, gateway, actions=('read',))
    pinned = registry.snapshot(keys)
    tool = registry.tools(pinned)[0]
    assert registry.available() == ['cad']
    assert tool.input_schema['additionalProperties'] is False
    assert set(tool.input_schema['properties']) == {'actionId', 'path', 'offset', 'maxBytes'}
    with pytest.raises(DomainError, match='immutable'):
        native(registry, gateway, actions=('read',))
    pinned[0]['digest'] = 'b' * 64
    with pytest.raises(DomainError, match='pinned'):
        registry.tools(pinned)
    assert not gateway.calls


async def test_dispatch_rechecks_scope_schema_and_revocation_and_strips_self_approval():
    registry, gateway = CapabilityRegistry(), Gateway()
    keys = native(registry, gateway, actions=('read',))
    tool = registry.tools(registry.snapshot(keys))[0]
    arguments = {'actionId': 'one', 'path': 'part.py'}
    output = await tool.invoke(None, task(), arguments)
    assert gateway.calls == [('read', arguments | {'offset': 0, 'maxBytes': 16000})]
    assert output.observations[0].status == 'UNKNOWN'
    assert not output.observations[0].independent
    with pytest.raises(ValidationError):
        await tool.invoke(None, task(), arguments | {'command': 'run arbitrary script'})
    with pytest.raises(DomainError, match='capability'):
        await tool.invoke(None, task('pcb'), arguments)
    registry.revoke(keys[0])
    with pytest.raises(DomainError, match='unavailable'):
        await tool.invoke(None, task(), arguments)
    assert len(gateway.calls) == 1


async def test_native_action_selection_and_host_authorization():
    registry, gateway = CapabilityRegistry(), Gateway()
    with pytest.raises(ValueError, match='Unsupported'):
        native(registry, gateway, actions=('read', 'shell'))
    assert registry.available() == []
    keys = native(registry, gateway, capability=Capability.VALIDATION)
    assert keys == ['validation.read@1.0.0', 'validation.analyze@1.0.0', 'validation.finish@1.0.0']
    with pytest.raises(ValueError, match='Unsupported'):
        native(registry, gateway, capability=Capability.VALIDATION, actions=('patch',))
    gateway.denied = True
    with pytest.raises(DomainError, match='policy'):
        await registry.tools(registry.snapshot(keys))[1].invoke(None, task('validation'), {'actionId': 'a'})
    assert gateway.calls == []


async def test_external_action_references_cannot_bypass_server_policy():
    registry, gateway = CapabilityRegistry(), Gateway()
    keys = register_service(registry, gateway, capability=Capability.HUMAN_WORK,
        provider='human-provider', provider_version='1.0.0', installation_hash='c' * 64,
        qualified=True, operations=('search', 'hire'))
    assert registry.available() == ['human_work']
    tools = registry.tools(registry.snapshot(keys))
    await tools[0].invoke(None, task('human_work'), {'query': 'Electronics inspector'})
    assert gateway.calls[0][0] == 'search'
    gateway.denied = True
    with pytest.raises(DomainError, match='policy'):
        await tools[1].invoke(None, task('human_work'),
                              {'preparedActionId': 'prepared', 'approvalId': 'invented'})
    assert len(gateway.calls) == 1
    assert gateway.authorizations[-1][0] == 'hire'
    with pytest.raises(ValidationError):
        await tools[1].invoke(None, task('human_work'),
            {'preparedActionId': 'prepared', 'approvalId': 'invented', 'approved': True})


def test_native_patch_hash_is_optional_for_a_new_file_and_still_validated():
    from pydantic import ValidationError

    from kleos.capabilities.native import PatchInput
    created = PatchInput.model_validate({'actionId': 'a1', 'path': 'model.py', 'content': 'x = 1'})
    assert created.expected_sha256 is None
    assert created.model_dump(mode='json', by_alias=True)['expectedSha256'] is None
    with pytest.raises(ValidationError):
        PatchInput.model_validate({'actionId': 'a1', 'path': 'model.py', 'content': 'x', 'expectedSha256': 'abc'})
