"""HTTP safety and frozen UI response shapes; printer I/O is injected fixtures."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kleos.access.service import AccessService, AccessSettings, Organization, OrganizationMember
from kleos.api import create_app
from kleos.api.app import TransportContext
from kleos.api.bambu import BambuFacade
from kleos.api.services import ApplicationServices
from kleos.capabilities.fabrication import BambuConnections
from kleos.capabilities.fabrication.models import CertificateView, DiscoveredPrinter, DiscoveryView
from kleos.contracts.models import Actor, DomainError
from kleos.infrastructure.settings import Settings
from kleos.projects.store import ProjectStore

PIN = "a" * 64
PAIR = {"address": "192.168.1.33", "serial": "SERIAL123", "accessCode": "ABCD1234", "modelId": "N1"}
BASE = "/api/v1/printers/bambu"


class FixtureTransport:
    def __init__(self, calls):
        self.calls = calls

    def observe(self, request, fingerprint):
        self.calls.append(("observe", request.address, fingerprint))
        return {"kind": "bambu", "connected": True, "state": "idle", "profile": {"physicalAcceptance": False}}


@pytest.fixture
def api(tmp_path):
    store = ProjectStore(f"sqlite:///{tmp_path / 'printer-api.db'}")
    store.initialize_for_tests()
    settings = Settings(environment="test", development_no_login=True, bambu_local_enabled=True)
    access = AccessService(store, AccessSettings(secret_key="x" * 32, environment="test", development_no_login=True))
    calls = []
    def verifier(address, serial, service):
        calls.append(("verify", address, serial, service))
        return CertificateView(address=address, certificate_sha256=PIN, verified=True, service=service)
    def inspector(address, service):
        calls.append(("probe", address, service))
        return CertificateView(address=address, certificate_sha256=PIN, verified=False, service=service)
    def discovery():
        calls.append(("discover",))
        return DiscoveryView(devices=[DiscoveredPrinter(address="192.168.1.33", serial="SERIAL123",
            name="Printer", model_id="N1", firmware_version="fixture-version")])
    connections = BambuConnections(enabled=True, transport=FixtureTransport(calls), verifier=verifier,
                                   inspector=inspector, discovery=discovery)
    facade = BambuFacade(access, connections, settings)
    app = create_app(ApplicationServices(store, access, bambu=facade))
    with TestClient(app, base_url="http://localhost:8000", client=("127.0.0.1", 54000)) as client:
        yield client, app, facade, calls, store
    connections.close()
    store.engine.dispose()


def contract_keys(name, result):
    frozen = json.loads((Path(__file__).parents[1] / "contracts/openapi.json").read_text())
    schema = frozen["components"]["schemas"][name]
    assert set(schema.get("required", [])) <= set(result)
    assert set(result) <= set(schema["properties"])


def test_catalog_is_honest_and_matches_frozen_contract(api):
    response = api[0].get(BASE + "/catalog")
    assert response.status_code == 200
    body = response.json()
    contract_keys("BambuCatalogView", body)
    assert {model["id"] for model in body["models"]} == {"N1", "N2S", "C12", "BL-P001"}
    assert body["studioVersion"] == "not_installed" and body["profiles"] == []
    assert not body["localPrintingEnabled"] and body["directPrintModelIds"] == []
    assert not api[3]


def test_full_read_only_http_lifecycle_preserves_frontend_shapes(api):
    client, _, _, calls, _ = api
    response = client.post(BASE + "/local/discover", json={})
    assert response.status_code == 200
    contract_keys("BambuDiscoveryView", response.json())
    contract_keys("BambuDiscoveredPrinter", response.json()["devices"][0])
    assert response.json()["qualification"] == "unverified_lan_advertisement"
    certificate = client.post(BASE + "/local/certificate", json={"address": PAIR["address"]})
    assert certificate.status_code == 200
    contract_keys("BambuCertificateView", certificate.json())
    assert certificate.json()["verified"] is False
    paired = client.post(BASE + "/local/connect", json=PAIR)
    assert paired.status_code == 200
    contract_keys("BambuLocalState", paired.json())
    assert paired.json()["connected"] and paired.json()["modelId"] == "N1"
    assert paired.json()["directPrinting"] is False
    assert PAIR["accessCode"] not in paired.text and "accessCode" not in paired.text
    assert client.get(BASE + "/local").json()["paired"] is True
    assert client.post(BASE + "/local/refresh", json={}).json()["connected"] is True
    assert client.delete(BASE + "/local").json()["paired"] is False
    assert client.get(BASE + "/local").json()["paired"] is False
    assert [entry[0] for entry in calls] == ["discover", "probe", "verify", "observe", "observe"]


@pytest.mark.parametrize("headers", [
    {"Host": "evil.invalid"}, {"Host": "localhost.evil.invalid"}, {"Host": "localhost/"},
    {"Host": "localhost:0"}, {"Host": "user@localhost"},
    {"Origin": "https://evil.invalid"}, {"Origin": "null"}, {"Origin": "http://localhost:8004"},
    {"Origin": "http://127.0.0.1:5180"}, {"Origin": "http://localhost:5180.evil.invalid"},
    {"Forwarded": "for=127.0.0.1"}, {"X-Forwarded-For": "127.0.0.1"}, {"X-Forwarded-Proto": "http"},
    {"X-Forwarded-Host": "localhost"},
])
def test_untrusted_host_origin_and_proxy_headers_cannot_reach_printers(api, headers):
    response = api[0].post(BASE + "/local/discover", headers=headers, json={})
    assert response.status_code == 403 and response.json()["code"] == "bambu_local_only"
    assert not api[3]


@pytest.mark.parametrize("address", ["192.168.1.5", "203.0.113.1", "localhost", "::ffff:192.168.1.5"])
def test_real_client_must_be_literal_loopback_and_query_cannot_forge_context(api, address):
    with TestClient(api[1], base_url="http://localhost:8000", client=(address, 50000)) as remote:
        response = remote.post(BASE + "/local/discover?_transport=localhost", json={"clientHost": "127.0.0.1"})
    assert response.status_code == 403 and not api[3]


def test_configured_origin_and_ipv6_loopback_are_supported(api):
    with TestClient(api[1], base_url="http://[::1]:8000", client=("::1", 50000)) as client:
        response = client.post(BASE + "/local/discover", headers={"Origin": "http://localhost:5180"}, json={})
    assert response.status_code == 200
    assert api[3] == [("discover",)]


def test_disabled_feature_exposes_no_credentials_and_never_calls_network(api):
    client, _, facade, calls, _ = api
    facade.settings.bambu_local_enabled = False
    state = client.get(BASE + "/local")
    assert state.status_code == 200 and state.json()["enabled"] is False
    assert client.post(BASE + "/local/connect", json=PAIR).status_code == 403
    assert not calls


def test_production_environment_cannot_use_local_credentials(api):
    client, _, facade, calls, _ = api
    facade.settings.environment = "production"
    assert client.get(BASE + "/local").status_code == 403
    assert client.post(BASE + "/local/connect", json=PAIR).status_code == 403
    assert not calls


def test_organization_admin_required_and_wrong_tenant_isolated(api):
    client, _, _, calls, store = api
    client.get(BASE + "/catalog")  # Initializes the trusted development account.
    with store.transaction() as session:
        session.get(OrganizationMember, ("development", "development")).role = "member"
    assert client.get(BASE + "/local").status_code == 403
    assert client.post(BASE + "/local/discover", json={}).status_code == 403
    assert client.get(BASE + "/local", headers={"X-Organization-Id": "other"}).status_code == 403
    assert not calls


def test_pairing_state_is_tenant_scoped(api):
    client, _, _, _, store = api
    assert client.post(BASE + "/local/connect", json=PAIR).status_code == 200
    with store.transaction() as session:
        session.add(Organization(id="other", name="Other"))
        session.add(OrganizationMember(tenant_id="other", user_id="development", role="owner"))
    response = client.get(BASE + "/local", headers={"X-Organization-Id": "other"})
    assert response.status_code == 200 and response.json()["paired"] is False
    original = client.get(BASE + "/local", headers={"X-Organization-Id": "development"})
    assert original.json()["paired"] is True


def test_pairing_validation_never_echoes_access_codes(api):
    response = api[0].post(BASE + "/local/connect", json={**PAIR, "accessCode": "private invalid code"})
    assert response.status_code == 422
    assert "private" not in response.text and "accessCode" not in response.text
    assert not api[3]


def test_unqualified_slicing_is_explicit_not_fake_success(api):
    response = api[0].post(BASE + "/local/test-prints", json={"profileId": "made-up", "printerModelId": "N1"})
    assert response.status_code == 501 and response.json()["code"] == "capability_unavailable"
    assert not api[3]


def test_trusted_transport_object_is_required_even_for_direct_facade_calls(api):
    facade = api[2]
    actor = Actor(tenant_id="development", user_id="development")
    with pytest.raises(DomainError):
        facade.query(actor, "bambu_local", {"_transport": {"client_host": "127.0.0.1", "host": "localhost"}})
    with pytest.raises(DomainError):
        facade.query(actor, "bambu_local", {"_transport": TransportContext("127.0.0.1", "localhost", None, True, "http")})
