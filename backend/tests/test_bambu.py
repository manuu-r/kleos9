"""Real protocol bytes with injected transports; no printer/LAN operations."""
import asyncio
import hashlib
import json
import ssl
import threading
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from kleos.capabilities.fabrication import (
    BambuConnections,
    LocalAccess,
    MemoryCredentialStore,
    PairRequest,
    ProbeRequest,
)
from kleos.capabilities.fabrication.discovery import parse_advertisement
from kleos.capabilities.fabrication.models import CertificateView
from kleos.capabilities.fabrication.transport import (
    ConnectionFailure,
    MQTTStatusTransport,
    mqtt_packet,
    mqtt_string,
    normalize_observation,
    read_packet,
    tls_context,
    verify_certificate,
)
from kleos.contracts.models import Actor, DomainError

CERT = b'fixture-printer-certificate'
PIN = hashlib.sha256(CERT).hexdigest()
ACCESS = LocalAccess(Actor(tenant_id='tenant', user_id='owner'), True)


def pair(**changes):
    return PairRequest(address='192.168.1.33', serial='SERIAL123', accessCode='ABCD1234',
                       modelId='N1', **changes)


@pytest.mark.parametrize('address', ['localhost', '127.0.0.1', '169.254.1.1', '8.8.8.8', '::1', '192.168.1.1.evil'])
def test_only_literal_private_ipv4_can_reach_transports(address):
    with pytest.raises(ValidationError):
        ProbeRequest(address=address)


def test_secret_excluded_and_manual_pin_requires_explicit_verification():
    assert 'ABCD1234' not in repr(pair())
    assert 'accessCode' not in pair().model_dump(by_alias=True)
    with pytest.raises(ValidationError):
        pair(certificateSha256=PIN)
    assert pair(certificateSha256=PIN, certificateVerified=True).model_id == 'N1'


def test_certificate_bundle_is_pinned_and_verification_enabled():
    from importlib.resources import files
    assert hashlib.sha256(files('kleos.capabilities.fabrication').joinpath('bambu_ca.pem').read_bytes()).hexdigest() == (
        '36f2bcee347ec7adce719b5fd350099591a4d3d0ec4e039c7019890d78e152a0')
    context = tls_context()
    assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
    assert context.minimum_version == ssl.TLSVersion.TLSv1_2
    assert len(context.get_ca_certs()) == 5
    with pytest.raises(ValueError):
        verify_certificate('8.8.8.8', 'SERIAL123', connector=lambda *_: pytest.fail('Network touched'))
    with pytest.raises(ValueError):
        verify_certificate('192.168.1.1', 'bad', connector=lambda *_: pytest.fail('Network touched'))


def advertisement(extra=''):
    return ('NOTIFY * HTTP/1.1\r\nNT: urn:bambulab-com:device:3dprinter:1\r\n'
            'LOCATION: http://192.168.1.33\r\nUSN: SERIAL123\r\n'
            'DevModel.bambu.com: N1\r\n' + extra).encode()


def test_discovery_identity_is_hint_and_sender_must_match():
    item = parse_advertisement(advertisement(), '192.168.1.33')
    assert item.model_id == 'N1'
    assert parse_advertisement(advertisement(), '192.168.1.34') is None
    assert parse_advertisement(advertisement('USN: OTHER123\r\n'), '192.168.1.33') is None
    assert parse_advertisement(b'x' * 4097, '192.168.1.33') is None


class Stream:
    def __init__(self, incoming=b''):
        self.incoming, self.sent = bytearray(incoming), []
    def __enter__(self):
        return self
    def __exit__(self, *_):
        pass
    def settimeout(self, _):
        pass
    def getpeercert(self, **_):
        return CERT
    def sendall(self, value):
        self.sent.append(bytes(value))
    def recv(self, size):
        chunk = bytes(self.incoming[:min(size, 3)])
        del self.incoming[:len(chunk)]
        return chunk


def transport(stream):
    return MQTTStatusTransport(connector=lambda *_, **__: stream,
        context_factory=lambda **_: SimpleNamespace(wrap_socket=lambda *_, **__: stream))


def report(retained=False):
    return mqtt_packet(0x31 if retained else 0x30,
        mqtt_string('device/SERIAL123/report') + json.dumps({'print': {'gcode_state': 'IDLE',
            'task_id': '1', 'subtask_id': '2', 'mc_percent': 20, 'password': 'UNTRUSTED'}}).encode())


@pytest.mark.parametrize('retained', [False, True])
def test_actual_mqtt_bytes_allow_report_before_ack_never_publish(retained):
    stream = Stream(mqtt_packet(0x20, b'\0\0') + report(retained) + mqtt_packet(0x90, b'\0\1\0'))
    observed = transport(stream).observe(pair(), PIN)
    assert observed['connected']
    assert observed['state'] == ('unknown' if retained else 'idle')
    assert ('providerJobId' in observed) is (not retained)
    assert [packet[0] for packet in stream.sent] == [0x10, 0x82, 0xe0]
    assert b'ABCD1234' in stream.sent[0]
    assert 'UNTRUSTED' not in json.dumps(observed)
    assert observed['profile']['physicalAcceptance'] is False


def test_pin_failure_precedes_any_credentials():
    stream = Stream()
    with pytest.raises(ConnectionFailure) as error:
        transport(stream).observe(pair(), 'a' * 64)
    assert error.value.code == 'bambu_certificate_changed'
    assert not stream.sent


def test_read_packet_rejects_oversized_and_deadline():
    with pytest.raises(ValueError):
        read_packet(Stream(b'\x30\xff\xff\xff\x7f'), 100, clock=lambda: 1)
    with pytest.raises(TimeoutError):
        read_packet(Stream(b'\x30\0'), 1, clock=lambda: 2)


def test_sanitizer_ignores_untrusted_nonfinite_fields_and_unknown_job():
    result = normalize_observation({'print': {'gcode_state': 'FINISH', 'mc_percent': float('nan'),
        'nozzle_temper': True, 'task_id': '1', 'subtask_id': '0', 'file': 'secret-file'}})
    assert result['state'] == 'finish' and 'providerJobId' not in result
    assert 'progressPercent' not in result['profile'] and 'nozzleTemperatureC' not in result['profile']
    assert 'secret-file' not in json.dumps(result)


@pytest.fixture
def service():
    clock = SimpleNamespace(now=100.0)
    store = MemoryCredentialStore(clock=lambda: clock.now)
    worker = SimpleNamespace(observe=lambda *_: normalize_observation({'print': {'gcode_state': 'IDLE'}}))
    def verifier(address, serial, mode):
        return CertificateView(address=address, certificate_sha256=PIN, verified=True, service=mode)
    value = BambuConnections(enabled=True, transport=worker, secrets=store,
                            verifier=verifier, clock=lambda: clock.now)
    yield value, clock, worker
    store.close()


async def test_pair_refresh_ttl_is_nonsliding_and_tenant_scoped(service):
    value, clock, _ = service
    result = await value.pair(ACCESS, pair())
    assert result.paired and result.credentials_stored and not result.direct_printing
    other = LocalAccess(Actor(tenant_id='different', user_id='owner'), True)
    assert not value.state(other).paired
    clock.now += 121
    assert value.state(ACCESS).status == 'stale'
    refreshed = await value.refresh(ACCESS)
    assert refreshed.expires_after_seconds == 1679
    clock.now += 1680
    assert not value.state(ACCESS).paired
    with pytest.raises(DomainError) as error:
        await value.refresh(ACCESS)
    assert error.value.code == 'bambu_pairing_required'


async def test_default_disabled_and_nonlocal_cannot_access_network(service):
    value, _, _ = service
    with pytest.raises(DomainError):
        await value.pair(LocalAccess(ACCESS.actor), pair())
    with pytest.raises(DomainError):
        await BambuConnections().pair(ACCESS, pair())


async def test_temporary_failure_preserves_pairing_auth_failure_forgets_it(service):
    value, _, worker = service
    await value.pair(ACCESS, pair())
    def unavailable(*_):
        raise OSError('SECRET must not leak')
    worker.observe = unavailable
    view = await value.refresh(ACCESS)
    assert view.paired and not view.connected
    assert 'SECRET' not in view.model_dump_json()
    def denied(*_):
        raise ConnectionFailure('bambu_access_denied')
    worker.observe = denied
    view = await value.refresh(ACCESS)
    assert not view.paired and not value.state(ACCESS).credentials_stored


async def test_forget_fences_inflight_pairing(service):
    value, _, worker = service
    started, release = threading.Event(), threading.Event()
    def observe(*_):
        started.set()
        assert release.wait(5)
        return normalize_observation({'print': {'gcode_state': 'IDLE'}})
    worker.observe = observe
    task = asyncio.create_task(value.pair(ACCESS, pair()))
    await asyncio.to_thread(started.wait, 3)
    value.forget(ACCESS)
    release.set()
    with pytest.raises(DomainError) as error:
        await task
    assert error.value.code == 'bambu_pairing_changed'
    assert not value.state(ACCESS).credentials_stored


@pytest.mark.parametrize(('reply', 'code'), [
    (b'\0\4', 'bambu_access_denied'), (b'\0\5', 'bambu_access_denied'),
    (b'\0\3', 'bambu_mqtt_unavailable'), (b'\1\0', 'bambu_protocol_error'),
])
def test_authentication_denial_never_subscribes(reply, code):
    stream = Stream(mqtt_packet(0x20, reply))
    with pytest.raises(ConnectionFailure) as error:
        transport(stream).observe(pair(), PIN)
    assert error.value.code == code
    assert len(stream.sent) == 1


def test_valid_status_without_subscription_ack_does_not_succeed():
    stream = Stream(mqtt_packet(0x20, b'\0\0') + report())
    with pytest.raises(OSError):
        transport(stream).observe(pair(), PIN)
    assert [packet[0] for packet in stream.sent] == [0x10, 0x82]


async def test_automatic_certificate_verification_happens_before_secret_transport(service):
    value, _, worker = service
    calls = []
    def verify(address, serial, mode):
        calls.append('verified')
        assert address == '192.168.1.33' and serial == 'SERIAL123' and mode == 'mqtt'
        return CertificateView(address=address, certificate_sha256=PIN, verified=True, service=mode)
    def observe(request, fingerprint):
        assert calls == ['verified']
        calls.append('observe')
        assert fingerprint == PIN
        return normalize_observation({'print': {'gcode_state': 'IDLE'}})
    value.verifier, worker.observe = verify, observe
    await value.pair(ACCESS, pair())
    assert calls == ['verified', 'observe']


async def test_failed_ca_verification_never_sends_code_and_does_not_store_it(service):
    value, _, worker = service
    def verify(*_):
        raise ssl.SSLError('untrusted certificate')
    worker.observe = lambda *_: pytest.fail('Credentials reached transport')
    value.verifier = verify
    with pytest.raises(DomainError) as error:
        await value.pair(ACCESS, pair())
    assert error.value.code == 'bambu_identity_unverified'
    assert not value.state(ACCESS).paired


@pytest.fixture
def encrypted_store(tmp_path):
    from kleos.capabilities.fabrication.credentials import EncryptedCredentialStore
    from kleos.projects.store import ProjectStore
    database = ProjectStore('sqlite:///' + str(tmp_path / 'printer.db'))
    database.initialize_for_tests()
    clock = SimpleNamespace(now=100., wall=2000.)
    key = bytes(range(32))
    def make(encryption_key=key, capacity=16):
        return EncryptedCredentialStore(database.sessions, encryption_key,
            clock=lambda: clock.now, wall_clock=lambda: clock.wall, capacity=capacity)
    yield database, clock, make
    database.engine.dispose()


def encrypted_service(store, clock, worker=None):
    def verifier(address, serial, service):
        return CertificateView(address=address, certificate_sha256=PIN, verified=True, service=service)
    return BambuConnections(enabled=True, secrets=store, clock=lambda: clock.now, verifier=verifier,
        transport=worker or SimpleNamespace(observe=lambda *_: normalize_observation({'print': {'gcode_state': 'IDLE'}})))


async def test_encrypted_pairing_survives_service_restart_without_sliding_expiry(encrypted_store):
    from kleos.capabilities.fabrication.credentials import PrinterCredential
    database, clock, make = encrypted_store
    first = encrypted_service(make(), clock)
    await first.pair(ACCESS, pair())
    first.close()
    with database.sessions() as session:
        row = session.get(PrinterCredential, ('tenant', 'owner'))
        assert b'ABCD1234' not in row.ciphertext
        assert b'192.168.1.33' not in row.ciphertext
        assert b'SERIAL123' not in row.ciphertext
        assert row.expires_at == 3800
    clock.wall += 60
    clock.now = 5.  # New machine/process monotonic epoch is independent of persisted UTC expiry.
    restarted = encrypted_service(make(), clock)
    assert restarted.state(ACCESS).paired
    assert restarted.state(ACCESS).expires_after_seconds == 1740
    await restarted.refresh(ACCESS)
    with database.sessions() as session:
        assert session.get(PrinterCredential, ('tenant', 'owner')).expires_at == pytest.approx(3800)
    clock.wall = 3801
    clock.now += 1741
    assert not restarted.state(ACCESS).paired
    with database.sessions() as session:
        assert session.get(PrinterCredential, ('tenant', 'owner')) is None


async def test_encrypted_credentials_bound_to_tenant_user_and_correct_key(encrypted_store):
    from kleos.capabilities.fabrication.credentials import PrinterCredential
    database, clock, make = encrypted_store
    service = encrypted_service(make(), clock)
    await service.pair(ACCESS, pair())
    other = LocalAccess(Actor(tenant_id='different', user_id='owner'), True)
    assert not encrypted_service(make(), clock).state(other).paired
    with pytest.raises(DomainError) as error:
        encrypted_service(make(bytes(reversed(range(32)))), clock).state(ACCESS)
    assert error.value.code == 'bambu_credential_unavailable'
    assert service.state(ACCESS).paired  # Wrong key must not destroy valid encrypted data.
    with database.sessions.begin() as session:
        source = session.get(PrinterCredential, ('tenant', 'owner'))
        session.add(PrinterCredential(tenant_id='different', user_id='owner',
            generation=source.generation, expires_at=source.expires_at, ciphertext=source.ciphertext))
    with pytest.raises(DomainError) as error:
        service.state(other)  # A database ciphertext swap cannot authenticate for another principal.
    assert error.value.code == 'bambu_credential_unavailable'


async def test_encrypted_store_rejects_modified_ciphertext(encrypted_store):
    from kleos.capabilities.fabrication.credentials import PrinterCredential
    database, clock, make = encrypted_store
    service = encrypted_service(make(), clock)
    await service.pair(ACCESS, pair())
    with database.sessions.begin() as session:
        row = session.get(PrinterCredential, ('tenant', 'owner'))
        row.ciphertext = row.ciphertext[:-1] + bytes([row.ciphertext[-1] ^ 1])
    with pytest.raises(DomainError):
        service.state(ACCESS)


async def test_different_process_forget_fences_pending_pair(encrypted_store):
    _, clock, make = encrypted_store
    started, release = threading.Event(), threading.Event()
    def observe(*_):
        started.set()
        assert release.wait(5)
        return normalize_observation({'print': {'gcode_state': 'IDLE'}})
    first = encrypted_service(make(), clock, SimpleNamespace(observe=observe))
    second = encrypted_service(make(), clock)
    pending = asyncio.create_task(first.pair(ACCESS, pair()))
    assert await asyncio.to_thread(started.wait, 3)
    second.forget(ACCESS)
    release.set()
    with pytest.raises(DomainError) as error:
        await pending
    assert error.value.code == 'bambu_pairing_changed'
    assert not first.state(ACCESS).paired and not second.state(ACCESS).paired


async def test_stale_store_mutations_cannot_replace_new_pairing(encrypted_store):
    _, clock, make = encrypted_store
    first, second = make(), make()
    service = encrypted_service(first, clock)
    await service.pair(ACCESS, pair())
    key = ('tenant', 'owner')
    old = first.get(key)
    fresh_token = second.claim(key)
    second.commit(key, fresh_token, old)
    with pytest.raises(DomainError):
        first.claim(key, preserve=True, expected_token=old.token)
    with pytest.raises(DomainError):
        first.delete(key, expected_token=old.token)
    with pytest.raises(DomainError):
        first.commit(key, old.token, old)
    assert second.get(key).token == fresh_token


async def test_durable_capacity_and_key_requirement(encrypted_store):
    _, clock, make = encrypted_store
    with pytest.raises(ValueError):
        make(b'weak')
    limited = make(capacity=1)
    await encrypted_service(limited, clock).pair(ACCESS, pair())
    with pytest.raises(DomainError) as error:
        limited.claim(('another', 'owner'))
    assert error.value.code == 'bambu_capacity'
