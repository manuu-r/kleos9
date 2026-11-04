"""Installed native tool choices, with exact bindings frozen before the next attempt."""
import copy

from kleos.contracts.models import DomainError
from kleos.projects.store import fingerprint

LANES = {'mechanics': 'cad', 'electronics': 'pcb', 'firmware': 'firmware', 'application': 'application'}
BASE_ACTIONS = {'read', 'patch', 'build', 'finish'}
OUTPUTS = {'cad': ['STEP', 'STL', 'parts.json'], 'pcb': ['Gerbers', 'drills'],
           'firmware': ['firmware.elf', 'firmware.bin'], 'application': ['companion-web.zip']}


def snapshot_project_tool_policy(project):
    return copy.deepcopy({'policy': project.data.get('state', {}).get('toolPolicy', {}),
                          'bindings': project.data.get('toolPolicyBindings', {})})


class ToolInstallations:
    def __init__(self, registry, *, native_pins=()):
        self.registry, self.native_pins = registry, list(native_pins)

    def bundles(self, tools=None):
        groups = {}
        for pin in self.registry.catalog() if tools is None else tools:
            manifest = pin['manifest']
            if manifest['transport'] != 'native' or manifest['capability'] not in LANES.values():
                continue
            key = tuple(manifest[field] for field in ('capability', 'provider', 'provider_version',
                                                     'version', 'installation_hash'))
            groups.setdefault(key, []).append(pin)
        result = []
        for (capability, provider, provider_version, version, installation_hash), pins in sorted(groups.items()):
            pins = sorted(pins, key=lambda pin: pin['key'])
            operations = [pin['manifest']['operation'] for pin in pins]
            required = BASE_ACTIONS | ({'analyze', 'render'} if capability == 'cad' else set())
            complete = (required.issubset(operations) and len(set(operations)) == len(operations)
                        and set(operations).issubset(BASE_ACTIONS | {'analyze', 'render'})
                        and all(pin['manifest']['qualified'] and pin['manifest']['effect'] != 'external_action' for pin in pins))
            canonical = provider == 'native' and all(
                pin['manifest']['id'] == capability + '.' + pin['manifest']['operation'] for pin in pins)
            ident = 'native-' + capability if canonical else f'worker-{provider}-{capability}-{installation_hash[:16]}'
            evidence = next((pin for pin in self.native_pins if fingerprint(pin) == installation_hash), None)
            result.append({'id': ident, 'version': version, 'lane': next(lane for lane, cap in LANES.items() if cap == capability),
                'capability': capability, 'provider': provider, 'providerVersion': provider_version,
                'installationHash': installation_hash, 'pins': pins, 'qualified': complete,
                'evidence': evidence, 'canonical': canonical})
        return result

    @staticmethod
    def defaults(bundles):
        selected = {}
        for lane in LANES:
            available = [row for row in bundles if row['lane'] == lane and row['qualified']]
            if available:
                # Prefer the built-in installation; highest installed version within that ID.
                selected[lane] = sorted(available, key=lambda row: (
                    not row['canonical'], row['id'], tuple(-int(n) for n in row['version'].split('.'))))[0]
        return selected

    def catalog(self):
        bundles = self.bundles()
        defaults = self.defaults(bundles)
        tools = []
        for bundle in bundles:
            evidence = bundle['evidence']
            limitations = [evidence['qualificationScope']] if evidence else ['Qualification is limited to the operator-installed native binding.']
            if not bundle['qualified']:
                limitations += ['A complete, unambiguous fixed native session is required before selection.']
            tools.append({'id': bundle['id'], 'version': bundle['version'],
                'name': f"{bundle['provider'].title()} {bundle['capability']} ({bundle['providerVersion']})",
                'lane': bundle['lane'], 'runner': 'isolated-native', 'sourceContract': 'kleos-native-session-v2',
                'outputFiles': OUTPUTS[bundle['capability']], 'qualified': bundle['qualified'],
                'qualificationReference': evidence.get('qualificationHash') if evidence else None,
                'limitations': limitations, 'configured': True, 'isDefault': defaults.get(bundle['lane']) is bundle})
        return {'tools': tools, 'defaultPolicy': {lane: {'id': row['id'], 'version': row['version']}
                for lane, row in defaults.items()}, 'agents': [], 'defaultAgentPolicy': {}}


class ToolPolicyService:
    def __init__(self, projects, registry, *, native_pins=()):
        self.projects, self.registry = projects, registry
        self.installations = ToolInstallations(registry, native_pins=native_pins)

    def resolve(self, value, *, tools=None):
        if not isinstance(value, dict) or set(value) - set(LANES):
            raise DomainError('Choose tools for supported engineering lanes', 'invalid_tool_policy', 422)
        bundles = self.installations.bundles(tools)
        policy, bindings = {}, {}
        for lane, choice in value.items():
            if not isinstance(choice, dict) or set(choice) != {'id', 'version'}:
                raise DomainError('Each tool choice requires an installed ID and version', 'invalid_tool_policy', 422)
            # Flutter serializes every displayed lane, including an empty selection.
            # Both null means no override; a partially specified choice is invalid.
            if choice['id'] is None and choice['version'] is None:
                continue
            if not all(isinstance(item, str) and 1 <= len(item) <= 200 for item in choice.values()):
                raise DomainError('Each tool choice requires an installed ID and version', 'invalid_tool_policy', 422)
            matches = [row for row in bundles if row['lane'] == lane and row['id'] == choice['id']
                       and row['version'] == choice['version'] and row['qualified']]
            if len(matches) != 1:
                raise DomainError('Selected tool is unavailable, incomplete or ambiguous', 'tool_unavailable')
            row = matches[0]
            policy[lane] = dict(choice)
            bindings[lane] = copy.deepcopy(row['pins'])
        return policy, bindings

    def filter_tools(self, snapshot, tools):
        """Called once before runtime pins are saved; existing runs retain their saved pins."""
        if not isinstance(snapshot, dict) or set(snapshot) - {'policy', 'bindings'}:
            raise DomainError('Objective tool policy snapshot is invalid', 'tool_policy_conflict')
        policy, bindings = self.resolve(snapshot.get('policy', {}), tools=tools)
        if bindings != snapshot.get('bindings', {}):
            raise DomainError('Selected tool installation changed after selection', 'installation_conflict')
        bundles = self.installations.bundles(tools)
        selected = self.installations.defaults(bundles)
        for lane, choice in policy.items():
            selected[lane] = next(row for row in bundles if row['lane'] == lane
                and row['id'] == choice['id'] and row['version'] == choice['version'] and row['qualified'])
        allowed = {pin['key'] for row in selected.values() for pin in row['pins']}
        controlled = {row['capability'] for row in selected.values()}
        return [pin for pin in tools if pin['key'] in allowed or
                (pin['manifest']['capability'] not in controlled and
                 not (pin['manifest']['transport'] == 'native' and pin['manifest']['capability'] in LANES.values()))]

    def command(self, actor, body, key):
        kind, payload = body.get('type'), body.get('payload', {})
        if kind not in {'project.setToolPolicy', 'project.selectTool'} or not isinstance(payload, dict):
            raise DomainError('Unsupported tool policy command', 'invalid_request', 422)
        fields = {'projectId', 'toolPolicy'} if kind == 'project.setToolPolicy' else {'projectId', 'lane', 'toolId'}
        if (set(payload) != fields or not isinstance(payload.get('projectId'), str)
                or not 1 <= len(payload['projectId']) <= 36):
            raise DomainError('Provide only the selected tool policy fields', 'invalid_tool_policy', 422)

        def operation(session):
            project = self.projects.authorized(session, actor, payload['projectId'], write=True)
            if type(body.get('expectedRevision')) is not int or project.revision != body['expectedRevision']:
                raise DomainError('Project changed; refresh before choosing tools', 'revision_conflict')
            value = payload.get('toolPolicy')
            if kind == 'project.selectTool':
                lane, ident = payload['lane'], payload['toolId']
                matches = [row for row in self.installations.bundles() if row['lane'] == lane
                           and row['id'] == ident and row['qualified']]
                if len(matches) != 1:
                    raise DomainError('Choose an available unambiguous tool version', 'tool_unavailable')
                value = {**project.data.get('state', {}).get('toolPolicy', {}),
                         lane: {'id': ident, 'version': matches[0]['version']}}
            policy, bindings = self.resolve(value)
            if kind == 'project.selectTool' and any(
                    pins != project.data.get('toolPolicyBindings', {}).get(lane)
                    for lane, pins in bindings.items() if lane != payload['lane']):
                raise DomainError('An unchanged tool installation differs from its saved choice', 'installation_conflict')
            project.advance_revision(context=False)
            project.data = {**project.data, 'toolPolicyBindings': bindings,
                'state': {**project.data.get('state', {}), 'toolPolicy': policy,
                          'tools': {lane: choice['id'] for lane, choice in policy.items()}}}
            self.projects._emit(session, project, 'project.toolPolicyChanged', {'toolPolicy': policy})
            return {'projectId': project.id, 'revision': project.revision}

        return self.projects._command(actor, key, {'type': kind, 'payload': payload,
            'expectedRevision': body.get('expectedRevision')}, operation)
