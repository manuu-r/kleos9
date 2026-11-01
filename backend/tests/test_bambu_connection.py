import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from starlette.requests import Request

from kleos.auth import Principal
from kleos.bambu_connection import LocalBambuConnections
from kleos.bambu_contracts import BambuAddressRequest, BambuConnectionRequest
from kleos.contracts import DomainError

OWNER = Principal("development", "org-a", "owner")


def request(peer="127.0.0.1", host="127.0.0.1:8000", headers=None):
    return Request({"type": "http", "method": "POST", "scheme": "http", "path": "/api/v1/printers/bambu/local/connect",
        "query_string": b"", "server": ("127.0.0.1", 8000), "client": (peer, 50000),
        "headers": [(b"host", host.encode()), *[(k.encode(), v.encode()) for k, v in (headers or {}).items()]]})


def body(**changes):
    return {"address": "192.168.0.50", "serial": "A1MINIFIXTURE", "accessCode": "fixtureCode123",
            "certificateSha256": "a" * 64, "certificateVerified": True, "modelId": "N1", **changes}


class Printer:
    instances = []
    def __init__(self, address, serial, access_code, fingerprint):
        self.address, self.serial, self.access_code, self.fingerprint = address, serial, access_code, fingerprint
        self.instances.append(self)

    async def status(self):
        return {"kind": "bambu", "connected": True, "state": "idle", "profile": {"modelId": "N1"}}


def service(**kwargs):
    return LocalBambuConnections(SimpleNamespace(bambu_local_connections_enabled=True, environment="development"),
                                 printer_factory=Printer, **kwargs)


@pytest.mark.parametrize("address", ["8.8.8.8", "127.0.0.1", "169.254.169.254", "printer.local", "https://192.168.0.50", "::1"])
def test_only_literal_private_printer_addresses(address):
    with pytest.raises(ValidationError):
        BambuAddressRequest(address=address)


@pytest.mark.parametrize("changes", [{"certificateVerified": False}, {"certificateSha256": "wrong"},
                                    {"serial": "../../topic"}, {"modelId": "unknown"}, {"command": "print"}])
def test_unverified_or_control_inputs_are_rejected(changes):
    with pytest.raises(ValidationError):
        BambuConnectionRequest.model_validate(body(**changes))


@pytest.mark.parametrize("peer,host,headers", [("192.168.0.5", "127.0.0.1:8000", {}),
    ("127.0.0.1", "api.example.com", {}), ("127.0.0.1", "127.0.0.1:8000", {"x-forwarded-for": "127.0.0.1"}),
    ("127.0.0.1", "127.0.0.1:8000", {"origin": "https://evil.example"})])
def test_remote_proxy_and_foreign_origin_cannot_access_lan(peer, host, headers):
    local = service()
    req = request(peer, host, headers)
    assert local.state(req, OWNER)["enabled"] is False
    with pytest.raises(DomainError):
        local.guard(req, OWNER)


def test_hosted_and_disabled_helpers_never_connect():
    local = service()
    for environment, enabled in [("production", True), ("staging", True), ("development", False), ("test", True)]:
        local.settings.environment, local.settings.bambu_local_connections_enabled = environment, enabled
        with pytest.raises(DomainError):
            local.guard(request(), OWNER)


async def test_certificate_inspection_is_unverified_and_receives_no_access_code():
    calls = []
    def inspect(address):
        calls.append(address)
        return {"address": address, "certificateSha256": "a" * 64, "verified": False}
    local = service(inspect_certificate=inspect)
    result = await local.probe(request(), OWNER, BambuAddressRequest(address="192.168.0.50"))
    assert result["verified"] is False and calls == ["192.168.0.50"]
    assert not local.sessions


async def test_connection_is_private_expiring_memory_and_get_never_polls():
    now = [0]
    local = service(clock=lambda: now[0])
    connected = await local.connect(request(), OWNER, BambuConnectionRequest.model_validate(body()))
    assert connected["connected"] and connected["credentialsStored"]
    assert "fixtureCode123" not in json.dumps(connected)
    assert local.state(request(), Principal("other", "org-b", "owner")) == {"enabled": True}
    now[0] = 121
    assert local.state(request(), OWNER)["status"] == "stale"
    refreshed = await local.connect(request(), OWNER)
    assert refreshed["connected"] is True
    now[0] = 1801
    assert local.state(request(), OWNER)["status"] == "expired"
    assert not local.sessions


async def test_wrong_access_or_unreachable_printer_is_not_a_fake_connection():
    class Offline(Printer):
        async def status(self):
            return {"kind": "bambu", "connected": False, "state": "unknown", "profile": {}}
    local = LocalBambuConnections(service().settings, printer_factory=Offline)
    result = await local.connect(request(), OWNER, BambuConnectionRequest.model_validate(body()))
    assert result["connected"] is False and result["credentialsStored"] is False
    assert local.sessions[local.key(OWNER)]["printer"] is None


async def test_forget_fences_an_inflight_connection_and_retains_no_secret():
    started, release = asyncio.Event(), asyncio.Event()
    class Waiting(Printer):
        async def status(self):
            started.set()
            await release.wait()
            return await super().status()
    local = LocalBambuConnections(service().settings, printer_factory=Waiting)
    pending = asyncio.create_task(local.connect(request(), OWNER, BambuConnectionRequest.model_validate(body())))
    await started.wait()
    local.forget(request(), OWNER)
    release.set()
    with pytest.raises(DomainError, match="cleared"):
        await pending
    assert not local.sessions


def test_connection_endpoints_hide_invalid_secrets_and_use_the_authenticated_local_user(platform):
    platform.settings.environment = "development"
    platform.settings.bambu_local_connections_enabled = True
    platform.client.app.state.local_bambu.printer_factory = Printer
    with TestClient(platform.client.app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000)) as client:
        client.headers.update(platform.client.headers)
        path = "/api/v1/printers/bambu/local"
        assert client.get(path).json()["enabled"] is True
        for invalid in [body(accessCode="sensitive-invalid!"), body(command="print"), body(certificateVerified=False)]:
            response = client.post(path + "/connect", json=invalid)
            assert response.status_code == 422 and invalid["accessCode"] not in response.text
        response = client.post(path + "/connect", json=body())
        assert response.status_code == 200 and response.json()["connected"] is True
        assert "fixtureCode123" not in response.text
        assert client.post(path + "/refresh", json={}).json()["connected"] is True
        assert client.delete(path).json()["credentialsStored"] is False
        assert client.post(path + "/refresh", json={}).status_code == 409


def test_read_only_accounts_cannot_enroll_or_clear_local_printers():
    local = service()
    viewer = Principal("viewer", "org-a", "viewer")
    with pytest.raises(DomainError):
        local.guard(request(), viewer)
    with pytest.raises(DomainError):
        local.forget(request(), viewer)
