"""Customer-installed, read-only printer bridge. It never polls for server commands."""

import argparse
import asyncio
import http.client
import ipaddress
import json
import os
import socket
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from .connectors import _PinnedHTTPS
from .contracts import DomainError


def _local_destination(url, resolver):
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or (
            parsed.query or parsed.fragment or parsed.path not in {"", "/"}):
        raise DomainError("Printer address must be an explicit local HTTP(S) origin", 422)
    addresses = {info[4][0] for info in resolver(
        parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)}
    if not addresses:
        raise DomainError("Printer address could not be resolved", 422)
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not (ip.is_private or ip.is_loopback) or ip.is_link_local or ip.is_multicast or ip.is_unspecified:
            raise DomainError("Printer bridge only reads explicitly configured local printers", 422)
    return parsed, sorted(addresses)[0]


def validate_local_printer(url, resolver=socket.getaddrinfo):
    _local_destination(url, resolver)
    return url.rstrip("/")


class _PinnedPrinterClient:
    """Resolve once for a polling cycle and never send local credentials to a rebound host."""
    def __init__(self, parsed, address):
        self.parsed, self.address = parsed, address

    async def get(self, url, headers):
        return await asyncio.to_thread(self._get, url, headers)

    def _get(self, url, headers):
        port = self.parsed.port or (443 if self.parsed.scheme == "https" else 80)
        connection = (_PinnedHTTPS(self.parsed.hostname, self.address, port, 8)
                      if self.parsed.scheme == "https" else http.client.HTTPConnection(self.address, port, timeout=8))
        host = self.parsed.netloc
        try:
            connection.request("GET", urlsplit(url).path, headers={**headers, "Host": host, "Accept-Encoding": "identity"})
            response = connection.getresponse()
            body = response.read(100_001)
            if len(body) > 100_000:
                raise ValueError("Printer response exceeds the status limit")
            return httpx.Response(response.status, content=body, request=httpx.Request("GET", url))
        finally:
            connection.close()


class ReadOnlyPrinter:
    def __init__(self, kind, origin, api_key, client=None, resolver=socket.getaddrinfo):
        if kind not in {"octoprint", "moonraker"}:
            raise DomainError("Unsupported printer bridge", 422)
        self.kind, self.origin, self.api_key, self.client = kind, origin, api_key, client
        self.resolver = resolver

    async def status(self):
        parsed, address = _local_destination(self.origin, self.resolver)
        if self.client:
            return await self._status(self.client)
        return await self._status(_PinnedPrinterClient(parsed, address))

    async def _status(self, client):
        headers = {"X-Api-Key": self.api_key}
        try:
            if self.kind == "octoprint":
                printer = await client.get(self.origin.rstrip("/") + "/api/printer", headers=headers)
                profile = await client.get(self.origin.rstrip("/") + "/api/printerprofiles", headers=headers)
                printer.raise_for_status()
                profile.raise_for_status()
                printer_data, profiles = printer.json(), profile.json().get("profiles", {})
                active = next((p for p in profiles.values() if p.get("current")), {})
                data = {"state": printer_data.get("state", {}).get("text", "unknown"),
                        "profile": {k: active[k] for k in ("name", "volume", "heatedBed") if k in active}}
            else:
                printer = await client.get(self.origin.rstrip("/") + "/printer/info", headers=headers)
                printer.raise_for_status()
                info = printer.json().get("result", {})
                data = {"state": info.get("state", "unknown"), "profile": {
                    "softwareVersion": info.get("software_version"), "message": info.get("state_message")}}
            return {"kind": self.kind, "connected": True, **data, "observedAt": datetime.now(UTC).isoformat()}
        except (httpx.HTTPError, OSError, http.client.HTTPException, ValueError, KeyError):
            return {"kind": self.kind, "connected": False, "state": "unknown", "profile": {},
                    "observedAt": datetime.now(UTC).isoformat()}


class PrinterBridge:
    def __init__(self, printer, platform_url, connector_id, bridge_token, client=None):
        parsed = urlsplit(platform_url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("The platform telemetry URL must use HTTPS")
        self.printer, self.platform_url = printer, platform_url.rstrip("/")
        self.connector_id, self.bridge_token, self.client = connector_id, bridge_token, client

    async def once(self):
        payload = await self.printer.status()
        async def send(client):
            response = await client.post(
                f"{self.platform_url}/api/v1/bridges/{self.connector_id}/telemetry",
                headers={"Authorization": f"Bearer {self.bridge_token}"}, json=payload)
            response.raise_for_status()
            # Response is only an acknowledgement. No commands or action URLs are executed.
            return {"accepted": response.json().get("accepted") is True, "state": payload["state"]}
        if self.client:
            return await send(self.client)
        async with httpx.AsyncClient(timeout=15, follow_redirects=False, trust_env=False) as client:
            return await send(client)

    async def run(self, interval=30):
        if interval < 10:
            raise ValueError("Telemetry interval must be at least 10 seconds")
        while True:
            try:
                await self.once()
            except httpx.HTTPError:
                # Do not print secrets, private printer addresses or provider response bodies.
                print("Telemetry delivery failed; retrying at the next interval", flush=True)
            await asyncio.sleep(interval)


def main():
    parser = argparse.ArgumentParser(description="Read-only Kleos9 printer status bridge")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    if config["kind"] == "bambu":
        from .bambu_telemetry import BambuPrinter
        printer = BambuPrinter(config["printerIp"], config["serial"], os.environ["KLEOS_BAMBU_ACCESS_CODE"],
                               config["certificateSha256"])
    else:
        printer = ReadOnlyPrinter(config["kind"], config["printerOrigin"], os.environ["KLEOS_PRINTER_API_KEY"])
    bridge = PrinterBridge(printer, config["platformUrl"], config["connectorId"],
                           os.environ["KLEOS_BRIDGE_TOKEN"])
    asyncio.run(bridge.once() if args.once else bridge.run(config.get("intervalSeconds", 30)))


if __name__ == "__main__":
    main()
