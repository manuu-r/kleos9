"""Bounded multicast discovery. Advertisements are untrusted identity hints."""
import re
import select
import socket
import time
from urllib.parse import urlsplit

from .models import MODELS, DiscoveredPrinter, DiscoveryView, private_address

GROUP = '239.255.255.250'
PORTS = (1990, 2021)
TARGET = 'urn:bambulab-com:device:3dprinter:1'


def parse_advertisement(packet: bytes, sender: str) -> DiscoveredPrinter | None:
    if not packet or len(packet) > 4096:
        return None
    try:
        address = private_address(sender)
        lines = packet.decode('utf-8', errors='strict').splitlines()
        if not lines or lines[0] not in {'NOTIFY * HTTP/1.1', 'HTTP/1.1 200 OK'} or len(lines) > 34:
            return None
        headers = {}
        for line in filter(None, lines[1:]):
            if ':' not in line or line[0].isspace():
                return None
            name, value = (part.strip() for part in line.split(':', 1))
            name = name.lower()
            if name in headers or not re.fullmatch(r'[a-z0-9.-]+', name) or any(ord(c) < 32 for c in value):
                return None
            headers[name] = value
        if headers.get('nt', headers.get('st')) != TARGET:
            return None
        location = headers.get('location', '')
        if '://' in location:
            url = urlsplit(location)
            if url.scheme != 'http' or url.username or url.password:
                return None
            location = url.hostname
        if private_address(location) != address:
            return None
        serial = headers.get('usn', '')
        name = headers.get('devname.bambu.com', 'Bambu printer')
        if not re.fullmatch(r'[A-Za-z0-9_-]{6,64}', serial) or not 1 <= len(name) <= 100:
            return None
        firmware = headers.get('devversion.bambu.com', '')
        model = headers.get('devmodel.bambu.com')
        return DiscoveredPrinter(address=address, serial=serial, name=name,
            model_id=model if model in MODELS else None,
            firmware_version=firmware if re.fullmatch(r'[A-Za-z0-9._-]{1,80}', firmware) else None)
    except (ValueError, TypeError, UnicodeError):
        return None


def discover_printers(*, socket_factory=socket.socket, wait=select.select, clock=time.monotonic):
    sockets, devices = [], {}
    deadline = clock() + 3
    try:
        for port in PORTS:
            stream = socket_factory(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            try:
                stream.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                stream.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
                stream.bind(('', port))
                stream.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                                 socket.inet_aton(GROUP) + socket.inet_aton('0.0.0.0'))
                stream.setblocking(False)
                query = (f'M-SEARCH * HTTP/1.1\r\nHOST: {GROUP}:{port}\r\n'
                         f'MAN: "ssdp:discover"\r\nMX: 1\r\nST: {TARGET}\r\n\r\n')
                stream.sendto(query.encode(), (GROUP, port))
                sockets.append(stream)
            except OSError:
                stream.close()
        if not sockets:
            raise OSError('Discovery unavailable')
        packets = 0
        while packets < 256 and len(devices) < 32 and clock() < deadline:
            ready, _, _ = wait(sockets, [], [], max(0, deadline - clock()))
            if not ready:
                break
            for stream in ready:
                if packets >= 256 or len(devices) >= 32:
                    break
                try:
                    payload, sender = stream.recvfrom(4097)
                except BlockingIOError:
                    continue
                packets += 1
                device = parse_advertisement(payload, sender[0])
                if device:
                    devices[(device.address, device.serial)] = device
        return DiscoveryView(devices=[devices[key] for key in sorted(devices)])
    finally:
        for stream in sockets:
            stream.close()
