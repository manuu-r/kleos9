"""The write transport against injected MQTT streams and a real loopback implicit-FTPS server; no printer is touched."""
import datetime
import hashlib
import json
import socket
import ssl
import threading
from types import SimpleNamespace

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from kleos.capabilities.fabrication import PairRequest
from kleos.capabilities.fabrication.print_transport import (
    BambuPrintTransport,
    project_command,
    stored_file,
)
from kleos.capabilities.fabrication.transport import ConnectionFailure, mqtt_packet, mqtt_string

CERT = b'fixture-printer-certificate'
PIN = hashlib.sha256(CERT).hexdigest()
REPORT = 'device/SERIAL123/report'


def pair():
    return PairRequest(address='192.168.1.33', serial='SERIAL123', accessCode='ABCD1234', modelId='BL-P001')


class Stream:
    def __init__(self, incoming=b'', cert=CERT):
        self.incoming, self.sent, self.cert = bytearray(incoming), [], cert

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def settimeout(self, _):
        pass

    def getpeercert(self, **_):
        return self.cert

    def sendall(self, value):
        self.sent.append(bytes(value))

    def recv(self, size):
        chunk = bytes(self.incoming[:min(size, 3)])
        del self.incoming[:len(chunk)]
        return chunk


def mqtt(stream):
    return BambuPrintTransport(connector=lambda *_, **__: stream,
        context_factory=lambda **_: SimpleNamespace(wrap_socket=lambda *_, **__: stream))


def report(state, **extra):
    return mqtt_packet(0x30, mqtt_string(REPORT) + json.dumps({'print': {'gcode_state': state, **extra}}).encode())


def test_start_publishes_one_project_command_and_confirms_only_a_visible_start():
    command = project_command('part.3mf')
    stream = Stream(mqtt_packet(0x20, b'\0\0') + mqtt_packet(0x90, b'\0\1\0') + report('RUNNING', mc_percent=0))
    outcome = mqtt(stream).start(pair(), PIN, command)
    assert outcome['confirmed'] is True and outcome['state'] == 'running'
    assert [packet[0] for packet in stream.sent] == [0x10, 0x82, 0x30, 0xe0]  # connect, subscribe, ONE publish, disconnect
    published = stream.sent[2]
    assert b'device/SERIAL123/request' in published and b'"project_file"' in published
    assert b'file:///sdcard/part.3mf' in published and b'Metadata/plate_1.gcode' in published
    assert b'ABCD1234' in stream.sent[0] and b'ABCD1234' not in published


def test_start_without_a_report_is_unconfirmed_not_failed():
    stream = Stream(mqtt_packet(0x20, b'\0\0') + mqtt_packet(0x90, b'\0\1\0'))
    outcome = mqtt(stream).start(pair(), PIN, project_command('part.3mf'), wait_seconds=.2)
    assert outcome == {'confirmed': False, 'state': 'unknown'}
    assert sum(packet[0] == 0x30 for packet in stream.sent) == 1


def test_a_failed_project_result_is_reported_as_rejected():
    stream = Stream(mqtt_packet(0x20, b'\0\0') + report('IDLE', command='project_file', result='fail', reason='x'))
    with pytest.raises(ConnectionFailure) as error:
        mqtt(stream).start(pair(), PIN, project_command('part.3mf'))
    assert error.value.code == 'bambu_start_rejected'


def test_certificate_pin_failure_precedes_any_credential_or_command():
    stream = Stream(cert=b'another printer')
    with pytest.raises(ConnectionFailure) as error:
        mqtt(stream).start(pair(), PIN, project_command('part.3mf'))
    assert error.value.code == 'bambu_certificate_changed' and not stream.sent


@pytest.mark.parametrize(('reply', 'code'), [(b'\0\4', 'bambu_access_denied'), (b'\0\5', 'bambu_access_denied'),
                                              (b'\0\3', 'bambu_mqtt_unavailable')])
def test_refused_login_never_publishes(reply, code):
    stream = Stream(mqtt_packet(0x20, reply))
    with pytest.raises(ConnectionFailure) as error:
        mqtt(stream).start(pair(), PIN, project_command('part.3mf'))
    assert error.value.code == code and all(packet[0] != 0x30 for packet in stream.sent)


@pytest.mark.parametrize('name', ['../x.3mf', 'a b.3mf', 'part.gcode', '.3mf', 'x' * 70 + '.3mf', 'a/b.3mf'])
def test_only_plain_3mf_names_are_accepted(name):
    with pytest.raises(ValueError):
        stored_file(name, b'data')


def test_empty_or_non_bytes_files_are_rejected():
    for data in (b'', 'text'):
        with pytest.raises(ValueError):
            stored_file('part.3mf', data)


class Ftps:
    """A minimal implicit-FTPS server on loopback: TLS from the first byte, PASV data over TLS, STOR to memory."""

    def __init__(self, password='ABCD1234'):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'SERIAL123')])
        now = datetime.datetime.now(datetime.UTC)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
                .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(days=1))
                .not_valid_after(now + datetime.timedelta(days=1)).sign(key, hashes.SHA256()))
        self.der = cert.public_bytes(serialization.Encoding.DER)
        self.fingerprint = hashlib.sha256(self.der).hexdigest()
        self.directory = None
        import tempfile
        self.directory = tempfile.TemporaryDirectory()
        cert_file, key_file = f'{self.directory.name}/c.pem', f'{self.directory.name}/k.pem'
        open(cert_file, 'wb').write(cert.public_bytes(serialization.Encoding.PEM))
        open(key_file, 'wb').write(key.private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.context.load_cert_chain(cert_file, key_file)
        self.password, self.stored, self.commands = password, {}, []
        self.listener = socket.socket()
        self.listener.bind(('127.0.0.1', 0))
        self.listener.listen(1)
        self.port = self.listener.getsockname()[1]
        threading.Thread(target=self.serve, daemon=True).start()

    def serve(self):
        try:
            raw, _ = self.listener.accept()
            control = self.context.wrap_socket(raw, server_side=True)
            reader, data_listener = control.makefile('rb'), None

            def say(text):
                control.sendall((text + '\r\n').encode())
            say('220 ready')
            for line in reader:
                text = line.decode().strip()
                verb, _, argument = text.partition(' ')
                self.commands.append(verb)
                if verb == 'USER':
                    say('331 password')
                elif verb == 'PASS':
                    say('230 ok' if argument == self.password else '530 denied')
                elif verb in {'PBSZ', 'PROT', 'TYPE'}:
                    say('200 ok')
                elif verb == 'PASV':
                    data_listener = socket.socket()
                    data_listener.bind(('127.0.0.1', 0))
                    data_listener.listen(1)
                    port = data_listener.getsockname()[1]
                    say(f'227 Entering Passive Mode (127,0,0,1,{port >> 8},{port & 255})')
                elif verb == 'STOR':
                    say('150 go')
                    data_raw, _ = data_listener.accept()
                    data = self.context.wrap_socket(data_raw, server_side=True)
                    received = bytearray()
                    try:
                        while chunk := data.recv(65536):
                            received.extend(chunk)
                    except (ssl.SSLError, OSError):
                        pass
                    data.close()
                    self.stored[argument] = bytes(received)
                    say('226 stored')
                elif verb == 'QUIT':
                    say('221 bye')
                    break
            control.close()
        except (ssl.SSLError, OSError):
            pass  # a client that rejects the certificate hangs up before login

    def close(self):
        self.listener.close()
        self.directory.cleanup()


@pytest.fixture
def ftps():
    server = Ftps()
    yield server
    server.close()


def uploader(server):
    return BambuPrintTransport(connector=lambda address, timeout=None: socket.create_connection(('127.0.0.1', server.port), 5))


def test_upload_stores_the_exact_bytes_over_pinned_implicit_tls(ftps):
    package = b'PK\x03\x04' + bytes(range(256)) * 600
    uploader(ftps).upload(pair(), ftps.fingerprint, 'part.3mf', package)
    assert ftps.stored == {'part.3mf': package}
    assert ftps.commands[:2] == ['USER', 'PASS'] and 'STOR' in ftps.commands


def test_upload_to_a_different_certificate_sends_no_credentials(ftps):
    with pytest.raises(ConnectionFailure) as error:
        uploader(ftps).upload(pair(), '0' * 64, 'part.3mf', b'data')
    assert error.value.code == 'bambu_certificate_changed'
    assert 'USER' not in ftps.commands and not ftps.stored


def test_a_wrong_access_code_is_reported_as_denied_and_stores_nothing():
    server = Ftps(password='DIFFERENT1')
    try:
        with pytest.raises(ConnectionFailure) as error:
            uploader(server).upload(pair(), server.fingerprint, 'part.3mf', b'data')
        assert error.value.code == 'bambu_access_denied' and not server.stored
    finally:
        server.close()
