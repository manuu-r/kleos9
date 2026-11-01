"""Explicit, loopback-development-only read-only printer enrollment.

Credentials live in bounded, expiring process memory. Hosted APIs cannot enable
this LAN path. No printer commands are published and no device files are sent.
"""

import asyncio
import hashlib
import ipaddress
import socket
import ssl
import time

from fastapi import Depends, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .auth import Principal, authenticate
from .bambu_contracts import (
    BambuAddressRequest,
    BambuCertificateView,
    BambuConnectionRequest,
    BambuLocalState,
)
from .bambu_telemetry import BambuPrinter
from .contracts import DomainError

PREFIX = "/api/v1/printers/bambu/local"


def loopback_request(request):
    try:
        origin = request.headers.get("origin")
        if origin and origin not in {"http://127.0.0.1:5180", "http://localhost:5180"}:
            return False
        return (request.client is not None and ipaddress.ip_address(request.client.host).is_loopback
                and request.url.hostname in {"127.0.0.1", "localhost", "::1"}
                and not any(h in request.headers for h in ("forwarded", "x-forwarded-for", "x-forwarded-host")))
    except ValueError:
        return False


def certificate(address):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    # Inspection is untrusted and sends no credentials. Enrollment still needs an independently verified pin.
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    with socket.create_connection((address, 8883), timeout=5) as connection:
        with context.wrap_socket(connection, server_hostname=None) as stream:
            return {"address": address, "certificateSha256": hashlib.sha256(stream.getpeercert(binary_form=True)).hexdigest(),
                    "verified": False}


class LocalBambuConnections:
    def __init__(self, settings, printer_factory=BambuPrinter, inspect_certificate=certificate, clock=time.monotonic):
        self.settings, self.printer_factory, self.inspect_certificate = settings, printer_factory, inspect_certificate
        self.sessions, self.fences = {}, {}
        self.clock = clock
        self.capacity = asyncio.Semaphore(2)

    def enabled(self, request):
        return (self.settings.bambu_local_connections_enabled and self.settings.environment == "development"
                and loopback_request(request))

    def guard(self, request, principal):
        if not self.enabled(request):
            raise DomainError("Printer access codes can only be used by the explicitly enabled local development API", 403,
                              "bambu_local_only")
        principal.require_admin()

    @staticmethod
    def key(principal):
        return principal.tenant_id, principal.subject

    def discard(self, key):
        session = self.sessions.pop(key, None)
        if session:
            session["expiry"].cancel()
        self.fences[key] = self.fences.get(key, 0) + 1

    def state(self, request, principal):
        if not self.enabled(request):
            return {"enabled": False}
        key = self.key(principal)
        session = self.sessions.get(key)
        if session and self.clock() >= session["expires"]:
            self.discard(key)
            return {"enabled": True, "status": "expired", "message": "Local connection expired; re-enter the access code"}
        if not session:
            return {"enabled": True}
        view = dict(session["view"])
        if self.clock() - session["observed"] > 120:
            view.update(connected=False, status="stale", message="Status is stale; choose Refresh status",
                        observation={})
        return view

    async def probe(self, request, principal, body):
        self.guard(request, principal)
        if self.capacity.locked():
            raise DomainError("A printer connection check is already running; retry shortly", 429)
        async with self.capacity:
            try:
                return await asyncio.to_thread(self.inspect_certificate, body.address)
            except (OSError, ValueError, TimeoutError) as exc:
                raise DomainError("Printer TLS endpoint is unreachable. Check power, IP address and LAN mode", 422,
                                  "bambu_unreachable") from exc

    async def connect(self, request, principal, body=None):
        self.guard(request, principal)
        key = self.key(principal)
        self.state(request, principal)
        old = self.sessions.get(key)
        if body is None:
            if not old or old["printer"] is None:
                raise DomainError("Enter the printer connection details first", 409)
            printer, metadata = old["printer"], old["metadata"]
        else:
            for expired_key, session in list(self.sessions.items()):
                if self.clock() >= session["expires"]:
                    self.discard(expired_key)
            if key not in self.sessions and len(self.sessions) >= 16:
                raise DomainError("Local connection capacity reached", 429)
            printer = self.printer_factory(body.address, body.serial, body.access_code.get_secret_value(), body.certificate_sha256)
            metadata = {"address": body.address, "serial": body.serial, "modelId": body.model_id}
        fence = self.fences.get(key, 0) + 1
        self.fences[key] = fence
        if self.capacity.locked():
            raise DomainError("A printer connection check is already running; retry shortly", 429)
        async with self.capacity:
            observation = await printer.status()
        if self.fences.get(key) != fence:
            raise DomainError("The local connection was cleared or changed during this check", 409)
        connected = observation.get("connected") is True
        view = {"enabled": True, **metadata, "connected": connected,
                "status": "connected" if connected else "not_connected",
                "message": "Read-only printer status received" if connected else
                    "No status received. Check power, LAN access, serial, access code and verified fingerprint",
                "observation": observation if connected else {}, "credentialsStored": connected}
        previous = self.sessions.get(key)
        if previous:
            previous["expiry"].cancel()
        expires = old["expires"] if body is None and old else self.clock() + 1800
        expiry = asyncio.get_running_loop().call_later(max(0, expires - self.clock()), self.discard, key)
        self.sessions[key] = {"printer": printer if connected else None, "metadata": metadata, "view": view,
                              "observed": self.clock(), "expires": expires, "expiry": expiry}
        return view

    def forget(self, request, principal):
        self.guard(request, principal)
        key = self.key(principal)
        self.discard(key)
        return {"enabled": True, "message": "Connection details forgotten; no command was sent to the printer"}


def register_local_bambu_routes(app, settings):
    local = LocalBambuConnections(settings)
    app.state.local_bambu = local

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        if request.url.path.startswith(PREFIX):
            return JSONResponse(status_code=422, content={"message": "Check the printer address, serial, access code, model and verified certificate fingerprint",
                                                         "code": "invalid_bambu_connection"})
        return await request_validation_exception_handler(request, exc)

    @app.get(PREFIX, response_model=BambuLocalState)
    async def state(request: Request, principal: Principal = Depends(authenticate)):
        return local.state(request, principal)

    @app.post(PREFIX + "/certificate", response_model=BambuCertificateView)
    async def inspect(request: Request, body: BambuAddressRequest, principal: Principal = Depends(authenticate)):
        return await local.probe(request, principal, body)

    @app.post(PREFIX + "/connect", response_model=BambuLocalState)
    async def connect(request: Request, body: BambuConnectionRequest, principal: Principal = Depends(authenticate)):
        return await local.connect(request, principal, body)

    @app.post(PREFIX + "/refresh", response_model=BambuLocalState)
    async def refresh(request: Request, principal: Principal = Depends(authenticate)):
        return await local.connect(request, principal)

    @app.delete(PREFIX, response_model=BambuLocalState)
    async def forget(request: Request, principal: Principal = Depends(authenticate)):
        return local.forget(request, principal)
