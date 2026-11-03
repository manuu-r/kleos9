"""Frozen frontend printer responses over the new read-only connection service."""
import ipaddress
from urllib.parse import urlsplit

from kleos.api.app import TransportContext
from kleos.capabilities.fabrication import MODELS, LocalAccess, PairRequest, ProbeRequest
from kleos.capabilities.fabrication.models import ConnectionState
from kleos.contracts.models import DomainError, WireModel


def loopback_host(host):
    if not isinstance(host, str) or not host or any(c.isspace() or ord(c) < 32 for c in host):
        return False
    if host.lower() == "localhost":
        return True
    try:
        address = ipaddress.ip_address(host)
        return address.is_loopback
    except ValueError:
        return False


def loopback_client(host):
    try:
        return isinstance(host, str) and "%" not in host and ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def origin_tuple(value):
    """Parse an exact HTTP origin, never resolve a supplied hostname."""
    if not isinstance(value, str) or any(c.isspace() or ord(c) < 32 for c in value):
        return None
    try:
        url = urlsplit(value)
        if (url.scheme not in {"http", "https"} or url.username or url.password
                or url.path not in {"", "/"} or url.query or url.fragment or not url.hostname):
            return None
        port = url.port if url.port is not None else (443 if url.scheme == "https" else 80)
        if not 1 <= port <= 65535:
            return None
        return url.scheme, url.hostname.lower(), port
    except ValueError:
        return None


class BambuFacade:
    def __init__(self, access_service, connections, settings, *, preparations=None):
        self.access, self.connections, self.settings = access_service, connections, settings
        self.preparations = preparations

    def _local(self, actor, params, *, enabled=True):
        transport = params.get("_transport")
        local = isinstance(transport, TransportContext)
        if local:
            host = origin_tuple(f"{transport.scheme}://{transport.host}")
            local = (not transport.forwarded and transport.scheme in {"http", "https"}
                     and loopback_client(transport.client_host) and host is not None and loopback_host(host[1])
                     and not any(c in transport.host for c in "/?#@"))
            if transport.origin is not None:
                origin = origin_tuple(transport.origin)
                frontend = origin_tuple(self.settings.frontend_origin)
                preview = origin_tuple(self.settings.preview_origin)
                local = local and origin is not None and origin == frontend and origin != preview
                local = local and loopback_host(origin[1])
        if not local or self.settings.environment not in {"development", "test"}:
            raise DomainError("Printer connections require a direct local administrator session",
                              "bambu_local_only", 403)
        with self.access.store.transaction() as session:
            self.access._member(session, actor, admin=True)
        if enabled and (not self.settings.bambu_local_enabled or not self.connections.enabled):
            raise DomainError("Local printer connection is disabled", "bambu_local_only", 403)
        return LocalAccess(actor=actor, local_admin=True)

    def query(self, actor, operation, params):
        if operation == "bambu_catalog":
            with self.access.store.transaction() as session:
                self.access._member(session, actor)
            slicing = self.preparations.settings if self.preparations else None
            profiles = [{"id": ident, **slicing.profile(ident), "automaticPreparation": True}
                        for ident in slicing.qualified_profiles] if slicing else []
            return {"studioVersion": "not_installed", "models": [
                {"id": model_id, "name": name, "connectionSupported": True, "qualified": False, "nozzlesMm": [.4]}
                for model_id, name in MODELS.items()], "profiles": profiles,
                "limitations": [
                    "Local discovery, pairing and status observation are implemented; physical acceptance is not.",
                    "Offline slicing profiles are qualified only for the recorded fixtures; upload/start and physical acceptance remain blocked."
                    if profiles else "No slicing installation or print dispatch is qualified in the replacement backend."],
                "localPrintingEnabled": False, "directPrintModelIds": []}
        access = self._local(actor, params, enabled=False)
        if operation == "bambu_local":
            if not self.settings.bambu_local_enabled:
                return ConnectionState(enabled=False).model_dump(mode="json", by_alias=True)
            return self.connections.state(access).model_dump(mode="json", by_alias=True)
        raise DomainError("Printer slicing and test preparation are not qualified in this backend",
                          "capability_unavailable", 501)

    async def action(self, actor, operation, params, body):
        access = self._local(actor, params)
        if operation == "bambu_certificate":
            result = await self.connections.probe(access, ProbeRequest.model_validate(body))
        elif operation == "bambu_connect":
            request = PairRequest.model_validate(body)
            if request.service != "mqtt":
                raise DomainError("Pair through the MQTT status service", "invalid_request", 422)
            result = await self.connections.pair(access, request)
        else:
            if operation == "bambu_prepare_test":
                raise DomainError("Printer slicing and test preparation are not qualified in this backend",
                                  "capability_unavailable", 501)
            WireModel.model_validate(body)
            if operation == "bambu_discover":
                result = await self.connections.discover(access)
            elif operation == "bambu_refresh":
                result = await self.connections.refresh(access)
            elif operation == "bambu_forget":
                result = self.connections.forget(access)
            else:
                raise DomainError("Printer operation is unavailable", "capability_unavailable", 501)
        return result.model_dump(mode="json", by_alias=True)
