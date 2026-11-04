"""Existing workbench connector projection over installed, principal-scoped services."""
from kleos.contracts.models import DomainError

PREFIX = {'cad': 'mechanics', 'pcb': 'electronics', 'firmware': 'firmware',
          'application': 'application', 'validation': 'engineering.validation',
          'research': 'research', 'sourcing': 'sourcing', 'human_work': 'human',
          'fabrication': 'engineering.fabrication'}
NAMES = {'digikey': 'DigiKey', 'rentahuman': 'RentAHuman'}


def connector(ident, name, version, transport, capabilities, *, mode='read_only',
              enabled=True, disclosure=(), telemetry=None):
    # This is a public allowlist, not a serialization of runtime settings/manifests.
    row = {'id': ident, 'name': name, 'enabled': enabled, 'manifest': {
        'name': name, 'version': version, 'transport': transport,
        'capabilities': sorted(set(capabilities)), 'mode': mode, 'endpoint': None,
        'supportsIdempotency': False, 'supportsCancellation': False,
        'disclosure': list(disclosure)}}
    if telemetry is not None:
        row['telemetry'] = telemetry
    return row


class Connectors:
    def __init__(self, access, registry, *, external=None, bambu=None, fabrication=None):
        self.access, self.registry = access, registry
        self.external, self.bambu, self.fabrication = external, bambu, fabrication

    def query(self, actor, params):
        with self.access.store.transaction() as session:
            self.access._member(session, actor)
        accounts = {}
        if self.external:
            for pin in self.external.pins_for(actor):
                provider = self.external.jobs.providers.get((actor.tenant_id, actor.user_id, pin['provider']))
                if (provider is not None and provider.effect_capable is False
                        and provider.version == pin['providerVersion']
                        and self.external.jobs.installation_pin(actor, pin['provider']) == pin['installationHash']):
                    accounts[pin['provider']] = pin
        groups = {}
        for entry in self.registry.catalog():
            manifest = entry['manifest']
            capability, provider = manifest['capability'], manifest['provider']
            if not manifest['qualified'] or manifest['effect'] == 'external_action':
                continue
            if capability in {'sourcing', 'human_work'} or provider in NAMES:
                if provider not in accounts or accounts[provider]['providerVersion'] != manifest['provider_version']:
                    continue
            if provider == 'orca' and capability == 'fabrication':
                continue  # The actual slicing installation is projected below, not its waiting tool.
            key = (provider, capability, manifest['provider_version'],
                   'http' if provider in accounts else manifest['transport'])
            groups.setdefault(key, []).append(manifest['operation'])
        rows = []
        for (provider, capability, version, transport), operations in sorted(groups.items()):
            external = provider in accounts
            ident = provider if external else f'{provider}-{capability}'
            # Multiple installed versions/transports remain distinct without leaking hashes.
            if any(row['id'] == ident for row in rows):
                ident += '-' + version + '-' + transport
            name = NAMES.get(provider, f'{provider.title()} {capability.replace("_", " ")}')
            disclosure = ['Only the listed installed operations are available. No physical action is enabled.']
            if external:
                disclosure += ['Configured for your account; live provider access has not been verified.',
                               'Read-only discovery and observations; no purchasing, hiring, payment or messages.']
            else:
                disclosure += ['Qualification applies to recorded installation fixtures; it does not certify arbitrary output.']
            rows.append(connector(ident, name, version,
                {'native': 'builtin', 'internal': 'builtin', 'http': 'rest', 'mcp': 'mcp'}[transport],
                [PREFIX.get(capability, 'tools') + '.' + operation for operation in operations],
                mode='read_only' if external else 'live', disclosure=disclosure))
        if self.fabrication and self.fabrication.enabled:
            rows.append(connector('orcaslicer', 'OrcaSlicer', '2.4.2', 'builtin',
                ['engineering.fabrication.prepare'], mode='live', disclosure=[
                    'Offline preparation for the installed qualified profiles only.',
                    'Prepared files remain candidates. Printer upload/start and physical acceptance are blocked.']))
        printer = self._printer(actor, params)
        if printer is not None:
            rows.append(printer)
        return {'connectors': sorted(rows, key=lambda row: row['id'])}

    def _printer(self, actor, params):
        if self.bambu is None:
            return None
        try:
            local = self.bambu._local(actor, params)
        except DomainError as error:
            if error.status == 403:
                return None
            raise
        state = self.bambu.connections.state(local)
        # Expose only display status, never addresses, serials, credentials or raw observations.
        telemetry = {'kind': 'bambu', 'connected': bool(state.connected),
            'stale': state.status == 'stale', 'profile': {'physicalAcceptance': False}}
        if state.model_id is not None:
            telemetry['profile']['modelId'] = state.model_id
        return connector('bambu-local', 'Bambu Lab', '1.0.0', 'bridge',
            ['printer.discover', 'printer.pair', 'printer.status'], enabled=bool(state.paired),
            disclosure=['Local read-only printer connection. Paired status does not establish current connectivity.',
                        'Printer upload/start and physical acceptance are blocked.'], telemetry=telemetry)
