"""Write-capable Bambu LAN transport: pinned implicit-FTPS upload and one MQTT start command.

Only the print service may use this, and only for an explicitly authorized dispatch. Every connection checks the exact
leaf certificate fingerprint recorded at pairing before any credential byte is sent. Nothing here retries: a start
command is sent at most once, and a failure after it is reported as uncertain by the caller.
"""
import ftplib
import hashlib
import hmac
import json
import re
import secrets
import socket
import ssl
import time

from .models import PairRequest
from .transport import (
    ConnectionFailure,
    mqtt_packet,
    mqtt_string,
    normalize_observation,
    read_packet,
    tls_context,
)

MAX_UPLOAD_BYTES = 256 * 1024**2
STARTED_STATES = {'PREPARE', 'RUNNING', 'SLICING'}
FILENAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\.3mf$')


def pinned(stream, fingerprint):
    actual = hashlib.sha256(stream.getpeercert(binary_form=True)).hexdigest()
    if not hmac.compare_digest(actual, fingerprint):
        raise ConnectionFailure('bambu_certificate_changed')


class PinnedImplicitFTPS(ftplib.FTP_TLS):
    """Implicit FTPS (TLS from the first byte, port 990) whose control and data channels must present the pinned leaf."""

    def __init__(self, fingerprint, *, context_factory=tls_context, connector=socket.create_connection):
        super().__init__(context=context_factory(verify=False))
        self.fingerprint, self._connector, self._control = fingerprint, connector, None

    def connect(self, host='', port=0, timeout=-999, source_address=None):
        if host:
            self.host = host
        if port:
            self.port = port
        if timeout != -999:
            self.timeout = timeout
        self.sock = self._connector((self.host, self.port), self.timeout)  # wrapped and pinned by the setter
        self.af = self.sock.family
        self.file = self.sock.makefile('r', encoding=self.encoding)
        self.welcome = self.getresp()
        return self.welcome

    @property
    def sock(self):
        return self._control

    @sock.setter
    def sock(self, value):
        if value is not None and not isinstance(value, ssl.SSLSocket):
            value = self.context.wrap_socket(value, server_hostname=None)
            pinned(value, self.fingerprint)  # before USER/PASS is ever sent
        self._control = value

    def storbinary(self, cmd, fp, blocksize=8192, callback=None, rest=None):
        """Upload and judge success by the server's 226 reply; a peer that skips the TLS close_notify is not a failure."""
        self.voidcmd('TYPE I')
        connection = self.transfercmd(cmd, rest)
        try:
            while block := fp.read(blocksize):
                connection.sendall(block)
                if callback:
                    callback(block)
            try:
                connection.unwrap()
            except (ssl.SSLError, OSError, ValueError):
                pass
        finally:
            connection.close()
        return self.voidresp()

    def ntransfercmd(self, cmd, rest=None):
        connection, size = super().ntransfercmd(cmd, rest)
        pinned(connection, self.fingerprint)  # the data channel must be the same printer, before any file byte
        return connection, size


def stored_file(name, data):
    if not isinstance(name, str) or not FILENAME.fullmatch(name):
        raise ValueError('Invalid print file name')
    if not isinstance(data, bytes) or not 0 < len(data) <= MAX_UPLOAD_BYTES:
        raise ValueError('Print file exceeds its bound')
    return name, data


def project_command(name, *, plate=1, use_ams=False):
    """The `project_file` request for an already uploaded, sliced 3MF. Bed leveling and calibration stay on."""
    stem = name.removesuffix('.3mf')
    return {'print': {'sequence_id': '0', 'command': 'project_file', 'param': f'Metadata/plate_{plate}.gcode',
                      'subtask_name': stem, 'url': f'file:///sdcard/{name}', 'project_id': '0', 'profile_id': '0',
                      'task_id': '0', 'subtask_id': '0', 'bed_type': 'auto', 'timelapse': False,
                      'bed_leveling': True, 'flow_cali': True, 'vibration_cali': True, 'layer_inspect': True,
                      'use_ams': use_ams, 'ams_mapping': []}}


class BambuPrintTransport:
    def __init__(self, *, connector=socket.create_connection, context_factory=tls_context,
                 ftp_factory=None, clock=time.monotonic):
        self.connector, self.context_factory, self.clock = connector, context_factory, clock
        self.ftp_factory = ftp_factory or (lambda fingerprint: PinnedImplicitFTPS(
            fingerprint, context_factory=context_factory, connector=connector))

    def upload(self, request: PairRequest, fingerprint: str, name: str, data: bytes):
        """Send the sliced package to the printer's storage. The pin is the printer's FTPS leaf fingerprint."""
        name, data = stored_file(name, data)
        ftp = self.ftp_factory(fingerprint)
        try:
            ftp.connect(request.address, 990, timeout=15)
            ftp.login('bblp', request.access_code.get_secret_value())
            ftp.prot_p()
            from io import BytesIO
            ftp.storbinary('STOR ' + name, BytesIO(data))
        except ConnectionFailure:
            raise
        except ftplib.error_perm as exc:
            raise ConnectionFailure('bambu_access_denied' if str(exc).startswith('530') else 'bambu_protocol_error') from exc
        except TimeoutError as exc:
            raise ConnectionFailure('bambu_connect_timeout') from exc
        except ssl.SSLError as exc:
            raise ConnectionFailure('bambu_tls_failed') from exc
        except (OSError, ftplib.Error) as exc:
            raise ConnectionFailure('bambu_unreachable') from exc
        finally:
            try:
                ftp.close()
            except Exception:
                pass

    def start(self, request: PairRequest, fingerprint: str, command: dict, *, wait_seconds=15):
        """Publish one command and report whether the printer visibly started. Never resends."""
        deadline = self.clock() + wait_seconds + 5
        topic_report, topic_request = f'device/{request.serial}/report', f'device/{request.serial}/request'
        with self.connector((request.address, 8883), timeout=5) as raw:
            with self.context_factory(verify=False).wrap_socket(raw, server_hostname=None) as stream:
                pinned(stream, fingerprint)
                connect = (b'\x00\x04MQTT\x04\xc2\x00\x1e' + mqtt_string('kleos-' + secrets.token_hex(8))
                           + mqtt_string('bblp') + mqtt_string(request.access_code.get_secret_value()))
                stream.sendall(mqtt_packet(0x10, connect))
                header, reply = read_packet(stream, deadline, clock=self.clock)
                if header != 0x20 or len(reply) != 2 or reply[0] != 0 or reply[1] > 5:
                    raise ConnectionFailure('bambu_protocol_error')
                if reply[1] in (4, 5):
                    raise ConnectionFailure('bambu_access_denied')
                if reply[1]:
                    raise ConnectionFailure('bambu_mqtt_unavailable')
                stream.sendall(mqtt_packet(0x82, b'\x00\x01' + mqtt_string(topic_report) + b'\x00'))
                stream.sendall(mqtt_packet(0x30, mqtt_string(topic_request) + json.dumps(command).encode()))
                outcome = {'confirmed': False, 'state': 'unknown'}
                limit = self.clock() + wait_seconds
                for _ in range(64):
                    if self.clock() >= limit:
                        break
                    try:
                        header, body = read_packet(stream, min(limit, deadline), clock=self.clock)
                    except (TimeoutError, OSError):
                        break  # the command was already published; a silent or closed link is unconfirmed, not unsent
                    if header == 0x90:
                        if body == b'\x00\x01\x80':
                            raise ConnectionFailure('bambu_subscription_denied')
                        continue
                    if header not in (0x30, 0x31) or len(body) < 2:
                        continue
                    size = int.from_bytes(body[:2], 'big')
                    if size > len(body) - 2 or body[2:2 + size].decode('utf-8', 'replace') != topic_report:
                        continue
                    try:
                        payload = json.loads(body[2 + size:])
                        view = normalize_observation(payload)
                    except (ValueError, RecursionError, UnicodeError):
                        continue
                    printed = payload.get('print', {}) if isinstance(payload.get('print'), dict) else {}
                    if header == 0x30 and printed.get('command') == 'project_file' and printed.get('result') == 'fail':
                        raise ConnectionFailure('bambu_start_rejected')
                    state = str(view['state']).upper()
                    if header == 0x30 and state in STARTED_STATES:
                        outcome = {'confirmed': True, 'state': view['state'], 'observation': view}
                        break
                    outcome['state'] = view['state'] if header == 0x30 else outcome['state']
                try:
                    stream.sendall(b'\xe0\x00')
                except OSError:
                    pass
                return outcome
