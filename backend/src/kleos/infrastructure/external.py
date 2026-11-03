"""Explicit account-bound read-only external installations, with reviewed source pins."""
import hashlib
import hmac
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator

from kleos.api import external as external_api_module
from kleos.api.external import ExternalFacade
from kleos.capabilities import external as external_module
from kleos.capabilities import external_tools
from kleos.capabilities.external import ExternalJobs
from kleos.capabilities.external_tools import register_external_jobs
from kleos.capabilities.human_work import rentahuman
from kleos.capabilities.sourcing import digikey
from kleos.contracts.models import Capability, DomainError, WireModel
from kleos.projects.store import fingerprint

PROTOCOL = 'kleos-external-readonly-qualification-v1'
SCOPE = 'read-only mocked HTTP schema fixtures'
COMMON_CHECKS = ('principal_scope', 'project_scope', 'context_fence', 'credential_redaction',
                 'get_only', 'no_physical_actions', 'provider_errors_fail_closed', 'durable_observation')
PROVIDERS = {
    'digikey': (digikey.DigiKeyCatalog, Capability.SOURCING,
                ('exact_sku', 'usd_price_breaks', 'bounded_inventory', 'no_order_api')),
    'rentahuman': (rentahuman.RentAHumanDiscovery, Capability.HUMAN_WORK,
                  ('profile_allowlist', 'owned_job_read', 'poll_waits', 'no_hire_or_messages')),
}


class ExternalInstallation(WireModel):
    provider: Literal['digikey', 'rentahuman']
    version: str = Field(min_length=1, max_length=100)
    tenant_id: str = Field(min_length=1, max_length=100)
    user_id: str = Field(min_length=1, max_length=100)
    qualification_reference: Path
    client_id_secret: str | None = Field(default=None, pattern=r'^[a-zA-Z][a-zA-Z0-9_.-]{0,99}$')
    access_token_secret: str | None = Field(default=None, pattern=r'^[a-zA-Z][a-zA-Z0-9_.-]{0,99}$')
    account_id_secret: str | None = Field(default=None, pattern=r'^[a-zA-Z][a-zA-Z0-9_.-]{0,99}$')
    api_key_secret: str | None = Field(default=None, pattern=r'^[a-zA-Z][a-zA-Z0-9_.-]{0,99}$')

    @model_validator(mode='after')
    def fixed_adapter(self):
        if self.tenant_id.strip() != self.tenant_id or self.user_id.strip() != self.user_id or not self.tenant_id.strip() or not self.user_id.strip():
            raise ValueError('Provider account identities must be explicit and nonblank')
        if self.version != PROVIDERS[self.provider][0].version:
            raise ValueError('External installation version differs from the installed adapter')
        if self.provider == 'digikey':
            if not self.client_id_secret or not self.access_token_secret or self.api_key_secret:
                raise ValueError('DigiKey requires client/access-token references and no human-service key')
        elif self.client_id_secret or self.access_token_secret or self.account_id_secret:
            raise ValueError('RentAHuman accepts only its optional API-key reference')
        return self


def external_qualification_manifest(provider):
    """Describe evidence to review; this does not execute checks or grant qualification."""
    if provider not in PROVIDERS:
        raise ValueError('Unsupported external provider')
    adapter, _, checks = PROVIDERS[provider]
    # RentAHuman imports the bounded price parser from DigiKey, so both are included.
    modules = [external_module, external_tools, external_api_module, digikey]
    if provider == 'rentahuman':
        modules.append(rentahuman)
    sources = {module.__name__: hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest() for module in modules}
    sources[__name__] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    return {'protocol': PROTOCOL, 'provider': provider, 'providerVersion': adapter.version,
            'sourceHashes': sources, 'sourceHash': fingerprint(sources), 'qualificationScope': SCOPE,
            'liveAccessVerified': False, 'requiredChecks': [*COMMON_CHECKS, *checks]}


def external_qualification(installation, *, environment):
    """Only operator-owned reviewed reports can enable exact current adapter sources."""
    path = installation.qualification_reference
    try:
        expected = external_qualification_manifest(installation.provider)
        if path.is_symlink() or not path.is_file():
            raise ValueError('Missing report')
        with path.open('rb') as stream:
            raw = stream.read(128 * 1024 + 1)
        if len(raw) > 128 * 1024:
            raise ValueError('Oversized report')
        report = json.loads(raw)
        for field in ('protocol', 'provider', 'providerVersion', 'sourceHash', 'sourceHashes', 'qualificationScope'):
            if report.get(field) != expected[field]:
                raise ValueError('Evidence differs from adapter')
        if report.get('liveAccessVerified') is not False or report.get('sourceOverride') is not False:
            raise ValueError('Evidence has unsupported scope')
        if report.get('testOnly') is not False and not (environment == 'test' and report.get('testOnly') is True):
            raise ValueError('Synthetic runtime fixtures cannot enable a real installation')
        checks = report.get('checks', {})
        if any(checks.get(check) != 'PASS' for check in expected['requiredChecks']):
            raise ValueError('Required fixture checks did not pass')
        review = report.get('review', {})
        reviewer, reference = review.get('reviewer'), review.get('reference')
        if (review.get('independent') is not True or review.get('sourceHash') != expected['sourceHash']
                or not isinstance(reviewer, str) or not 1 <= len(reviewer.strip()) <= 200
                or not isinstance(reference, str) or not 1 <= len(reference.strip()) <= 1000
                or not isinstance(review.get('reviewedAt'), str)
                or datetime.fromisoformat(review['reviewedAt'].replace('Z', '+00:00')).tzinfo is None):
            raise ValueError('Independent review is absent or does not bind these sources')
    except (OSError, ValueError, TypeError, AttributeError, KeyError):
        raise DomainError('External installation lacks independently reviewed qualification for its exact sources',
                          'tool_unqualified', 503) from None
    return {'provider': installation.provider, 'providerVersion': installation.version,
            'sourceHash': expected['sourceHash'], 'qualificationHash': hashlib.sha256(raw).hexdigest(),
            'qualificationScope': SCOPE, 'liveAccessVerified': False, 'physicalActionsEnabled': False}


@dataclass
class ExternalRuntime:
    jobs: ExternalJobs
    facade: ExternalFacade
    pins: list[dict]
    tool_keys: list[str]
    _principal_pins: dict = field(default_factory=dict, repr=False)
    _closed: bool = False

    def pins_for(self, actor):
        return [dict(pin) for (tenant_id, user_id, _), pin in sorted(self._principal_pins.items())
                if (tenant_id, user_id) == (actor.tenant_id, actor.user_id)]

    def tool_keys_for(self, actor):
        capabilities = {PROVIDERS[pin['provider']][1].value for pin in self.pins_for(actor)}
        return [key for key in self.tool_keys if key.split('.', 1)[0] in capabilities]

    async def close(self):
        if self._closed:
            return
        self._closed = True
        failed = False
        for adapter in self.jobs.providers.values():
            try:
                await adapter.http.close()
            except Exception:
                failed = True
        if failed:
            raise DomainError('An external provider transport did not close cleanly', 'provider_error', 503)


def build_external(settings, projects, access, registry, repository):
    """Build configured accounts only; no lookup is made at startup.

    Settings: external_installations:list[dict], external_credentials:dict[str,SecretStr].
    Installations reference secret names; neither references nor values enter returned
    pins or tool manifests. Reports attest fixture coverage, never live access.
    """
    raw_installations = getattr(settings, 'external_installations', [])
    credentials = getattr(settings, 'external_credentials', {})
    try:
        if not isinstance(raw_installations, list) or len(raw_installations) > 64 or not isinstance(credentials, dict):
            raise ValueError('External installation bounds exceeded')
        installations = [ExternalInstallation.model_validate(value) for value in raw_installations]
        principals = [(item.tenant_id, item.user_id, item.provider) for item in installations]
        if len(set(principals)) != len(principals):
            raise ValueError('Duplicate external account installation')
        pin_key = None
        if installations:
            secret = getattr(settings, 'session_secret', None)
            if not isinstance(secret, SecretStr) or len(secret.get_secret_value()) < 32:
                raise ValueError('External account pins require a persistent strong server key')
            pin_key = secret.get_secret_value().encode()
        selected = []
        for item in installations:
            secrets = {}
            for field in ('client_id_secret', 'access_token_secret', 'account_id_secret', 'api_key_secret'):
                reference = getattr(item, field)
                if reference is not None:
                    value = credentials.get(reference)
                    if not isinstance(value, SecretStr) or not value.get_secret_value():
                        raise ValueError('Missing referenced provider secret')
                    secrets[field] = value
            selected.append((item, secrets))
    except (ValueError, TypeError):
        raise DomainError('External provider account configuration is invalid or a referenced secret is missing',
                          'configuration_required', 503) from None
    # Every installation is qualified before constructing any network-capable client.
    qualified = [external_qualification(item, environment=settings.environment) for item in installations]
    providers, principal_pins = {}, {}
    for (item, secrets), qualification in zip(selected, qualified, strict=True):
        if item.provider == 'digikey':
            adapter = digikey.DigiKeyCatalog(client_id=secrets['client_id_secret'], access_token=secrets['access_token_secret'],
                                            account_id=secrets.get('account_id_secret'))
        else:
            adapter = rentahuman.RentAHumanDiscovery(api_key=secrets.get('api_key_secret'))
        principal = (item.tenant_id, item.user_id, item.provider)
        providers[principal] = adapter
        # The keyed opaque ID fences even a value changed behind the same secret name.
        # Values and reference names never appear in model/run/job records or public pins.
        bindings = {name: {'reference': getattr(item, name), 'value': secret.get_secret_value()}
                    for name, secret in secrets.items()}
        encoded = json.dumps({'principal': principal, 'qualification': qualification, 'bindings': bindings},
                             sort_keys=True, separators=(',', ':')).encode()
        ident = hmac.new(pin_key, encoded, hashlib.sha256).hexdigest()
        principal_pins[principal] = {**qualification, 'installationHash': ident}
    jobs = ExternalJobs(projects, providers, installation_pins={key: pin['installationHash']
                                                               for key, pin in principal_pins.items()})
    pins, tool_keys = [], []
    for provider in sorted({item.provider for item in installations}):
        items = [(item, pin) for item, pin in zip(installations, qualified, strict=True) if item.provider == provider]
        source_pin = {key: value for key, value in items[0][1].items() if key != 'qualificationHash'}
        pin = {**source_pin, 'installationHash': fingerprint(source_pin)}
        pins.append(pin)
        adapter, capability, _ = PROVIDERS[provider]
        tool_keys.extend(register_external_jobs(registry, jobs, repository, capability=capability,
            provider=provider, provider_version=adapter.version, installation_hash=pin['installationHash'], qualified=True))
    return ExternalRuntime(jobs, ExternalFacade(access, jobs), pins, tool_keys, principal_pins)
