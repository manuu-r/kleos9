"""Read-only Bambu TLS/MQTT status transport; commands and uploads live in print_transport.py."""
import hashlib
import hmac
import json
import math
import re
import secrets
import socket
import ssl
import time
from datetime import UTC, datetime
from importlib.resources import files

from .models import MODELS, CertificateView, PairRequest, private_address

ERRORS = {
    'bambu_certificate_changed': 'The printer certificate changed. Verify the printer and reconnect.',
    'bambu_access_denied': 'The printer rejected the LAN access code.',
    'bambu_mqtt_unavailable': 'Printer LAN access is unavailable.',
    'bambu_subscription_denied': 'The printer denied status access. Check its serial number.',
    'bambu_status_timeout': 'Connected, but no printer status arrived.',
    'bambu_connect_timeout': 'The printer did not respond. Check its IP address and power.',
    'bambu_tls_failed': 'The printer secure connection failed.',
    'bambu_unreachable': 'Cannot reach the printer on the local network.',
    'bambu_protocol_error': 'The printer returned an unreadable status response.',
    'bambu_start_rejected': 'The printer rejected the print start request.',
}


class ConnectionFailure(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(ERRORS[code])


def tls_context(*, verify=True):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    if verify:
        context.load_verify_locations(cadata=files(__package__).joinpath('bambu_ca.pem').read_text())
    else:
        # Pin check immediately follows handshake, before any credential bytes.
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    return context


def verify_certificate(address, serial, service='mqtt', *, connector=socket.create_connection):
    address = private_address(address)
    if not isinstance(serial, str) or not re.fullmatch(r'[A-Za-z0-9_-]{6,64}', serial):
        raise ValueError('Invalid printer serial')
    if service not in {'mqtt', 'ftps'}:
        raise ValueError('Unsupported service')
    context = tls_context()
    with connector((address, 990 if service == 'ftps' else 8883), timeout=5) as raw:
        with context.wrap_socket(raw, server_hostname=serial) as stream:
            return CertificateView(address=address, service=service, verified=True,
                certificate_sha256=hashlib.sha256(stream.getpeercert(binary_form=True)).hexdigest())


def inspect_certificate(address, service='mqtt', *, connector=socket.create_connection):
    address = private_address(address)
    if service not in {'mqtt', 'ftps'}:
        raise ValueError('Unsupported service')
    with connector((address, 990 if service == 'ftps' else 8883), timeout=5) as raw:
        with tls_context(verify=False).wrap_socket(raw, server_hostname=None) as stream:
            return CertificateView(address=address, service=service, verified=False,
                certificate_sha256=hashlib.sha256(stream.getpeercert(binary_form=True)).hexdigest())


def mqtt_string(text):
    encoded = text.encode('utf-8')
    if len(encoded) > 65535:
        raise ValueError('MQTT string exceeds bounds')
    return len(encoded).to_bytes(2, 'big') + encoded


def mqtt_packet(header, body):
    size, length = len(body), bytearray()
    while True:
        digit, size = size % 128, size // 128
        length.append(digit | (128 if size else 0))
        if not size:
            return bytes([header]) + length + body


def read_packet(stream, deadline, *, clock=time.monotonic):
    def read_exact(size):
        result = bytearray()
        while len(result) < size:
            remaining = deadline - clock()
            if remaining <= 0:
                raise TimeoutError('Observation deadline')
            stream.settimeout(remaining)
            chunk = stream.recv(size - len(result))
            if not chunk:
                raise OSError('Connection closed')
            result.extend(chunk)
        return bytes(result)
    header, size = read_exact(1)[0], 0
    for index in range(4):
        value = read_exact(1)[0]
        size += (value & 127) * 128 ** index
        if size > 256000:
            raise ValueError('MQTT packet exceeds bounds')
        if value < 128:
            return header, read_exact(size)
    raise ValueError('Invalid MQTT packet length')


def normalize_observation(payload):
    if not isinstance(payload, dict):
        raise ValueError('Status must be an object')
    report, info = payload.get('print', {}), payload.get('info', {})
    if not isinstance(report, dict) or not isinstance(info, dict) or not (report or info):
        raise ValueError('Missing printer status')
    state = report.get('gcode_state')
    if state not in ('IDLE', 'RUNNING', 'PAUSE', 'FINISH', 'FAILED', 'PREPARE', 'SLICING'):
        state = 'UNKNOWN'
    profile = {'physicalAcceptance': False, 'transport': 'lan_mqtt_tls_pinned'}
    modules = info.get('module', [])
    sources = [payload, info, report, *([m for m in modules[:16] if isinstance(m, dict)]
                                      if isinstance(modules, list) else [])]
    model_id = None
    for source in sources:
        for name in ('model_id', 'dev_model_name', 'product_name', 'printer_type'):
            value = source.get(name)
            if isinstance(value, str):
                model_id = model_id or next((key for key, label in MODELS.items()
                    if value.casefold() in {key.casefold(), label.casefold(), label[10:].casefold()}), None)
    profile.update(modelId=model_id, modelDetection='reported' if model_id else 'select_manually')
    for source, target, low, high in (
        ('mc_percent', 'progressPercent', 0, 100), ('bed_temper', 'bedTemperatureC', -10, 500),
        ('nozzle_temper', 'nozzleTemperatureC', -10, 500), ('mc_remaining_time', 'remainingMinutes', 0, 1000000)):
        value = report.get(source)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and low <= value <= high:
            profile[target] = value
    task, subtask = report.get('task_id'), report.get('subtask_id')
    identity = {}
    if (isinstance(task, str) and isinstance(subtask, str) and subtask != '0'
            and re.fullmatch(r'[A-Za-z0-9_.-]{1,64}', task)
            and re.fullmatch(r'[A-Za-z0-9_.-]{1,64}', subtask)):
        identity['providerJobId'] = f'bambu:{task}:{subtask}'
    return {'kind': 'bambu', 'connected': True, 'state': state.lower(), 'profile': profile,
            'observedAt': datetime.now(UTC).isoformat(), **identity}


class MQTTStatusTransport:
    def __init__(self, *, connector=socket.create_connection, context_factory=tls_context, clock=time.monotonic):
        self.connector, self.context_factory, self.clock = connector, context_factory, clock

    def observe(self, request: PairRequest, fingerprint: str):
        deadline = self.clock() + 20
        with self.connector((request.address, 8883), timeout=5) as raw:
            with self.context_factory(verify=False).wrap_socket(raw, server_hostname=None) as stream:
                actual = hashlib.sha256(stream.getpeercert(binary_form=True)).hexdigest()
                if not hmac.compare_digest(actual, fingerprint):
                    raise ConnectionFailure('bambu_certificate_changed')
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
                topic = 'device/' + request.serial + '/report'
                stream.sendall(mqtt_packet(0x82, b'\x00\x01' + mqtt_string(topic) + b'\x00'))
                subscribed, observation = False, None
                for _ in range(32):
                    try:
                        header, body = read_packet(stream, deadline, clock=self.clock)
                    except TimeoutError as exc:
                        raise ConnectionFailure('bambu_status_timeout') from exc
                    if header == 0x90:
                        if body == b'\x00\x01\x80':
                            raise ConnectionFailure('bambu_subscription_denied')
                        if subscribed or body != b'\x00\x01\x00':
                            raise ConnectionFailure('bambu_protocol_error')
                        subscribed = True
                    elif header in (0x30, 0x31):
                        if len(body) < 2:
                            raise ConnectionFailure('bambu_protocol_error')
                        size = int.from_bytes(body[:2], 'big')
                        if size > len(body) - 2 or body[2:2+size].decode('utf-8') != topic:
                            raise ConnectionFailure('bambu_protocol_error')
                        observation = normalize_observation(json.loads(body[2+size:]))
                        if header == 0x31:
                            observation = {'kind': 'bambu', 'connected': True, 'state': 'unknown',
                                'observedAt': observation['observedAt'],
                                'profile': {'retained': True, 'physicalAcceptance': False}}
                    else:
                        raise ConnectionFailure('bambu_protocol_error')
                    if subscribed and observation is not None:
                        stream.sendall(b'\xe0\x00')
                        return observation
                raise ConnectionFailure('bambu_status_timeout')
