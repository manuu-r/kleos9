"""Bounded LAN MQTT 3.1.1 subscription only. No publish, upload or printer control.

Bambu Developer Mode protocols are not vendor-supported. A certificate fingerprint
from the owner is mandatory; access codes are sent only after the pin matches.
"""

import asyncio
import hashlib
import hmac
import ipaddress
import json
import math
import re
import secrets
import socket
import ssl
import time
from datetime import UTC, datetime

from .bambu_contracts import model_catalog

MAX_PACKET = 256000


def identify_model(value):
    if not isinstance(value, str):
        return None
    for model in model_catalog()["models"]:
        if value.casefold() in {model["id"].casefold(), model["name"].casefold(),
                               model["name"].removeprefix("Bambu Lab ").casefold()}:
            return model["id"]
    return None


def observation(payload):
    if not isinstance(payload, dict):
        raise ValueError("Printer report must be an object")
    report = payload.get("print", {})
    info = payload.get("info", {})
    if not isinstance(report, dict) or not isinstance(info, dict):
        raise ValueError("Invalid printer status")
    model = None
    modules = info.get("module", [])
    sources = [payload, info, report, *([m for m in modules[:16] if isinstance(m, dict)] if isinstance(modules, list) else [])]
    for source in sources:
        for key in ("model_id", "dev_model_name", "product_name", "project_name", "printer_type"):
            model = model or identify_model(source.get(key))
    state = report.get("gcode_state", "unknown")
    if state not in {"IDLE", "RUNNING", "PAUSE", "FINISH", "FAILED", "PREPARE", "SLICING"}:
        state = "unknown"
    profile = {"modelId": model, "modelDetection": "reported" if model else "select_manually",
               "transport": "lan_mqtt_tls_pinned", "physicalAcceptance": False}
    for key, label, low, high in (("mc_percent", "progressPercent", 0, 100),
                                  ("bed_temper", "bedTemperatureC", -10, 500),
                                  ("nozzle_temper", "nozzleTemperatureC", -10, 500),
                                  ("mc_remaining_time", "remainingMinutes", 0, 1000000)):
        value = report.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and low <= value <= high:
            profile[label] = value
    return {"kind": "bambu", "connected": True, "state": state.lower(), "profile": profile,
            "observedAt": datetime.now(UTC).isoformat()}


def _string(value):
    raw = value.encode("utf-8")
    if len(raw) > 65535:
        raise ValueError("MQTT string exceeds its bound")
    return len(raw).to_bytes(2, "big") + raw


def _packet(header, body):
    length, encoded = len(body), bytearray()
    while True:
        digit, length = length % 128, length // 128
        encoded.append(digit | (128 if length else 0))
        if not length:
            break
    return bytes([header]) + encoded + body


def _read(stream, deadline):
    def exact(count):
        data = bytearray()
        while len(data) < count:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Printer observation deadline")
            stream.settimeout(remaining)
            chunk = stream.recv(count - len(data))
            if not chunk:
                raise OSError("Printer connection closed")
            data.extend(chunk)
        return bytes(data)
    header, length = exact(1)[0], 0
    for i in range(4):
        digit = exact(1)[0]
        length += (digit & 127) * 128 ** i
        if length > MAX_PACKET:
            raise ValueError("Printer packet exceeds its bound")
        if digit < 128:
            return header, exact(length)
    raise ValueError("Invalid MQTT length")


class BambuPrinter:
    def __init__(self, address, serial, access_code, certificate_sha256, connector=socket.create_connection):
        ip = ipaddress.ip_address(address)
        local = any(ip in ipaddress.ip_network(net) for net in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))
        if not local or not re.fullmatch(r"[A-Za-z0-9_-]{6,64}", serial):
            raise ValueError("Configure a literal private-LAN printer IP and serial")
        if not re.fullmatch(r"[a-f0-9]{64}", certificate_sha256) or not re.fullmatch(r"[A-Za-z0-9]{8,64}", access_code):
            raise ValueError("Configure the owner-verified TLS fingerprint and access code")
        self.address, self.serial, self.access_code = str(ip), serial, access_code
        self.fingerprint, self.connector = certificate_sha256, connector

    async def status(self):
        try:
            return await asyncio.to_thread(self._status)
        except (OSError, ValueError, TimeoutError, RecursionError):
            return {"kind": "bambu", "connected": False, "state": "unknown", "profile": {},
                    "observedAt": datetime.now(UTC).isoformat()}

    def _status(self):
        deadline = time.monotonic() + 10
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        # Self-signed printer certs are authenticated by an explicit pin before credentials are sent.
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        with self.connector((self.address, 8883), timeout=5) as connection:
            with context.wrap_socket(connection, server_hostname=None) as stream:
                if not hmac.compare_digest(hashlib.sha256(stream.getpeercert(binary_form=True)).hexdigest(), self.fingerprint):
                    raise ValueError("Printer certificate identity changed")
                body = b"\x00\x04MQTT\x04\xc2\x00\x0f" + _string("kleos-" + secrets.token_hex(8)) + _string("bblp") + _string(self.access_code)
                stream.sendall(_packet(0x10, body))
                if _read(stream, deadline) != (0x20, b"\x00\x00"):
                    raise ValueError("Printer MQTT authentication failed")
                topic = "device/" + self.serial + "/report"
                stream.sendall(_packet(0x82, b"\x00\x01" + _string(topic) + b"\x00"))
                if _read(stream, deadline) != (0x90, b"\x00\x01\x00"):
                    raise ValueError("Printer subscription was not acknowledged")
                for _ in range(32):
                    header, body = _read(stream, deadline)
                    if header != 0x30 or len(body) < 2:
                        continue
                    size = int.from_bytes(body[:2], "big")
                    if size > len(body) - 2 or body[2:2 + size].decode() != topic:
                        raise ValueError("Unexpected printer topic")
                    result = observation(json.loads(body[2 + size:]))
                    stream.sendall(b"\xe0\x00")
                    return result
                raise ValueError("Printer did not send an observation")
