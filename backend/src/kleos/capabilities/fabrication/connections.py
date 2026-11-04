"""Local pairing lifecycle, with credentials isolated from graphs and durable state."""
import asyncio
import ssl
import threading
import time
from dataclasses import dataclass, field
from typing import Protocol
from uuid import uuid4

from kleos.contracts.models import DomainError

from .discovery import discover_printers
from .models import ConnectionState, LocalAccess, PairRequest, ProbeRequest
from .transport import (
    ERRORS,
    ConnectionFailure,
    MQTTStatusTransport,
    inspect_certificate,
    verify_certificate,
)


@dataclass(repr=False)
class StoredPairing:
    request: PairRequest
    fingerprint: str
    expires: float
    observed: float
    view: ConnectionState
    token: str = field(default_factory=lambda: uuid4().hex)


class CredentialStore(Protocol):
    """Trusted credential store, isolated from graph/model state."""
    def get(self, key: tuple[str, str]) -> StoredPairing | None: ...
    def claim(self, key: tuple[str, str], *, preserve=False, expected_token=None) -> str: ...
    def current(self, key: tuple[str, str], token: str) -> bool: ...
    def commit(self, key: tuple[str, str], token: str, value: StoredPairing) -> None: ...
    def delete(self, key: tuple[str, str], *, expected_token=None) -> None: ...


class MemoryCredentialStore:
    def __init__(self, *, clock=time.monotonic, capacity=16):
        self.clock, self.capacity = clock, capacity
        self._entries, self._timers, self._generations, self._deadlines = {}, {}, {}, {}
        self._lock = threading.RLock()

    def _expire(self, key):
        if self._deadlines.get(key, float('inf')) <= self.clock():
            self.delete(key)

    def get(self, key):
        with self._lock:
            self._expire(key)
            return self._entries.get(key)

    def _schedule(self, key, token, expires):
        previous = self._timers.pop(key, None)
        if previous:
            previous.cancel()
        self._deadlines[key] = expires
        def expire():
            with self._lock:
                if self._generations.get(key) == token:
                    self.delete(key)
        timer = threading.Timer(max(0, expires - self.clock()), expire)
        timer.daemon = True
        self._timers[key] = timer
        timer.start()

    def claim(self, key, *, preserve=False, expected_token=None):
        with self._lock:
            for existing in list(self._generations):
                self._expire(existing)
            entry = self._entries.get(key)
            if expected_token is not None and self._generations.get(key) != expected_token:
                raise DomainError('Printer pairing changed', 'bambu_pairing_changed', 409)
            if preserve and entry is None:
                raise DomainError('Pair the printer first', 'bambu_pairing_required', 409)
            if key not in self._generations and len(self._generations) >= self.capacity:
                raise DomainError('Local pairing capacity reached', 'bambu_capacity', 429)
            token = uuid4().hex
            self._generations[key] = token
            if not preserve:
                self._entries.pop(key, None)
            self._schedule(key, token, entry.expires if preserve else self.clock() + 60)
            return token

    def current(self, key, token):
        with self._lock:
            self._expire(key)
            return self._generations.get(key) == token

    def commit(self, key, token, value):
        with self._lock:
            if not self.current(key, token):
                raise DomainError('Printer pairing changed or expired', 'bambu_pairing_changed', 409)
            value.token = token
            self._entries[key] = value
            self._schedule(key, token, value.expires)

    def delete(self, key, *, expected_token=None):
        with self._lock:
            if expected_token is not None and self._generations.get(key) != expected_token:
                raise DomainError('Printer pairing changed', 'bambu_pairing_changed', 409)
            self._entries.pop(key, None)
            self._generations.pop(key, None)
            self._deadlines.pop(key, None)
            timer = self._timers.pop(key, None)
            if timer:
                timer.cancel()

    def close(self):
        with self._lock:
            for key in list(self._generations):
                self.delete(key)


class BambuConnections:
    """The API supplies LocalAccess after checking environment/origin/admin policy.

    All IO is injected; fixed default transports discover, verify and subscribe
    only. This service cannot start a print, heat a printer or upload a package;
    those live in `printing.PrintDispatcher`, which reads the pairing from here.
    """
    def __init__(self, *, enabled=False, transport=None, discovery=discover_printers,
                 verifier=verify_certificate, inspector=inspect_certificate, secrets=None, clock=time.monotonic,
                 printing_models=frozenset()):
        self.enabled, self.clock = enabled, clock
        self.printing_models = frozenset(printing_models)  # models the operator enabled for direct printing
        self.transport = transport or MQTTStatusTransport()
        self.discovery, self.verifier, self.inspector = discovery, verifier, inspector
        self.secrets = secrets if secrets is not None else MemoryCredentialStore(clock=clock)
        self._capacity = asyncio.Semaphore(2)
        self._tokens = {}

    def close(self):
        self.enabled = False
        self._tokens.clear()
        close = getattr(self.secrets, 'close', None)
        if close is not None:
            close()

    def _guard(self, access: LocalAccess):
        if not self.enabled or not access.local_admin:
            raise DomainError('Printer credentials require an enabled local administrator session',
                              'bambu_local_only', 403)
        return access.actor.tenant_id, access.actor.user_id

    def _available(self):
        if self._capacity.locked():
            raise DomainError('Printer connection service is busy; retry shortly', 'bambu_busy', 429)

    def state(self, access):
        if not self.enabled or not access.local_admin:
            return ConnectionState(enabled=False)
        key = self._guard(access)
        entry = self.secrets.get(key)
        if entry is None:
            return ConnectionState(enabled=True)
        result = entry.view.model_copy(deep=True)
        result.expires_after_seconds = max(0, int(entry.expires - self.clock()))
        if self.clock() - entry.observed > 120:
            result.connected, result.status, result.observation = False, 'stale', {}
            result.message = 'Printer is paired. Refresh for current status.'
        return result

    def pairing(self, access):
        """The unexpired stored pairing for the print service; the secret never leaves the trusted process."""
        key = self._guard(access)
        entry = self.secrets.get(key)
        if entry is None or entry.expires <= self.clock():
            raise DomainError('Pair the printer first', 'bambu_pairing_required', 409)
        return entry

    def forget(self, access):
        key = self._guard(access)
        self._tokens.pop(key, None)
        self.secrets.delete(key)
        return ConnectionState(enabled=True, message='Connection forgotten; no command was sent to the printer')

    async def discover(self, access):
        self._guard(access)
        self._available()
        async with self._capacity:
            try:
                return await asyncio.to_thread(self.discovery)
            except (OSError, ValueError, TimeoutError) as exc:
                raise DomainError('Local discovery is unavailable; enter the printer IP manually',
                                  'bambu_discovery_unavailable', 422) from exc

    async def probe(self, access, request: ProbeRequest):
        self._guard(access)
        self._available()
        async with self._capacity:
            try:
                return await asyncio.to_thread(self.inspector, request.address, request.service)
            except (OSError, ValueError, TimeoutError) as exc:
                raise DomainError('Printer TLS endpoint is unreachable', 'bambu_unreachable', 422) from exc

    async def pair(self, access, request: PairRequest):
        key = self._guard(access)
        self._available()
        token = self._tokens[key] = self.secrets.claim(key)
        async with self._capacity:
            try:
                fingerprint = request.certificate_sha256
                if fingerprint is None:
                    identity = await asyncio.to_thread(self.verifier, request.address, request.serial, 'mqtt')
                    if not identity.verified or identity.address != request.address or identity.service != 'mqtt':
                        raise ValueError('Printer identity not verified')
                    fingerprint = identity.certificate_sha256
                # Developer Mode selection is metadata only, not print authority.
                self._current(key, token)
                view = await self._observe(request, fingerprint)
                self._current(key, token)
                if view.connected:
                    now = self.clock()
                    self.secrets.commit(key, token, StoredPairing(request, fingerprint, now + 1800, now, view, token))
                return view
            except (ssl.SSLError, ValueError) as exc:
                raise DomainError('Could not verify the selected printer identity', 'bambu_identity_unverified', 422) from exc
            except (OSError, TimeoutError) as exc:
                raise DomainError('Cannot reach the printer on the local network', 'bambu_unreachable', 422) from exc
            finally:
                if self._tokens.get(key) == token:
                    self._tokens.pop(key, None)

    async def refresh(self, access):
        key = self._guard(access)
        self._available()
        entry = self.secrets.get(key)
        if entry is None:
            raise DomainError('Pair the printer first', 'bambu_pairing_required', 409)
        token = self._tokens[key] = self.secrets.claim(key, preserve=True, expected_token=entry.token)
        try:
            async with self._capacity:
                view = await self._observe(entry.request, entry.fingerprint)
            self._current(key, token)
            if entry.expires <= self.clock():
                raise DomainError('Printer pairing expired or changed', 'bambu_pairing_changed', 409)
            if not view.connected and view.error_code not in {
                'bambu_unreachable', 'bambu_connect_timeout', 'bambu_status_timeout', 'bambu_mqtt_unavailable'}:
                self.secrets.delete(key, expected_token=token)
                return view
            view.paired = view.credentials_stored = True
            view.expires_after_seconds = max(0, int(entry.expires - self.clock()))
            updated = StoredPairing(entry.request, entry.fingerprint, entry.expires,
                                    self.clock() if view.connected else entry.observed, view, token)
            self.secrets.commit(key, token, updated)
            return view
        finally:
            if self._tokens.get(key) == token:
                self._tokens.pop(key, None)

    def _current(self, key, token):
        if self._tokens.get(key) != token or not self.secrets.current(key, token):
            raise DomainError('The printer pairing changed during the request', 'bambu_pairing_changed', 409)

    async def _observe(self, request, fingerprint):
        code, observation = None, {}
        try:
            observation = await asyncio.to_thread(self.transport.observe, request, fingerprint)
        except ConnectionFailure as exc:
            code = exc.code
        except TimeoutError:
            code = 'bambu_connect_timeout'
        except ssl.SSLError:
            code = 'bambu_tls_failed'
        except OSError:
            code = 'bambu_unreachable'
        except (ValueError, RecursionError, UnicodeError):
            code = 'bambu_protocol_error'
        connected = code is None
        return ConnectionState(enabled=True, paired=connected, connected=connected,
            status='connected' if connected else 'not_connected', error_code=code,
            message=ERRORS[code] if code else 'Printer paired; status is observational only.',
            address=request.address, serial=request.serial, model_id=request.model_id,
            firmware_version=request.firmware_version, direct_printing=request.model_id in self.printing_models,
            credentials_stored=connected, expires_after_seconds=1800 if connected else 0,
            observation=observation if connected else {})
