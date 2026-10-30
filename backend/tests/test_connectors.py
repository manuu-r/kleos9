import hashlib
import hmac
import json
import socket
import time
from datetime import UTC, datetime

import httpx
import pytest
from pydantic import SecretStr

from kleos.auth import Principal
from kleos.bridge import PrinterBridge, ReadOnlyPrinter, validate_local_printer
from kleos.config import Settings
from kleos.connectors import (
    ConnectorService,
    DigiKeyCatalog,
    FetchResponse,
    RemoteConnector,
    ResearchService,
    RestrictedFetcher,
    conformance,
    public_destination,
)
from kleos.contracts import ConnectorManifest, DomainError
from kleos.db import Base, Connector, Database, Event, Inbox, Organization, Outbox, Project, Source


def resolve(address):
    return lambda host, port, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))]


@pytest.fixture
def connector_env(tmp_path):
    db = Database(f"sqlite:///{tmp_path}/connectors.db")
    Base.metadata.create_all(db.engine)
    settings = Settings(_env_file=None, environment="test", daily_budget_usd=100)
    with db.transaction() as session:
        session.add(Organization(id="org", name="Test"))
        session.add(Project(id="project", tenant_id="org", owner_id="owner", name="Watch", brief="Watch"))
    yield db, settings, Principal("owner", "org", "owner")
    db.close()


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.3", "169.254.169.254", "0.0.0.0", "::1",
                                          "::ffff:127.0.0.1", "192.168.0.2", "100.64.0.1"])
def test_remote_fetch_blocks_non_public_addresses(address):
    with pytest.raises(DomainError, match="destinations"):
        public_destination("https://public.example/a", resolve(address))


@pytest.mark.parametrize("url", ["http://example.com", "file:///etc/passwd", "https://user:pass@example.com",
                               "https://example.com:8443", "https://example.com/#secret"])
def test_remote_fetch_rejects_unsafe_schemes_credentials_ports(url):
    with pytest.raises(DomainError):
        public_destination(url, resolve("8.8.8.8"))


def test_dns_pin_retains_original_host_for_tls():
    assert public_destination("https://datasheet.example/spec?a=1", resolve("8.8.8.8")) == (
        "datasheet.example", "8.8.8.8", 443, "/spec?a=1")


async def test_redirect_to_metadata_is_rechecked():
    class Response:
        status = 302
        def getheaders(self):
            return [("Location", "https://169.254.169.254/latest/meta-data")]
    class Connection:
        def __init__(self, *args):
            pass
        def request(self, *args, **kwargs):
            pass
        def getresponse(self):
            return Response()
        def close(self):
            pass
    def resolver(host, port, **kwargs):
        return resolve("169.254.169.254" if host == "169.254.169.254" else "8.8.8.8")(host, port)
    with pytest.raises(DomainError, match="destinations"):
        await RestrictedFetcher(resolver=resolver, connection_factory=Connection).request("https://example.com")


async def test_fetch_pins_connection_and_bounds_body():
    calls = []
    class Response:
        status = 200
        def getheaders(self):
            return [("Content-Type", "text/plain")]
        def read(self, limit):
            return b"x" * limit
    class Connection:
        def __init__(self, host, address, port, timeout):
            calls.append((host, address))
        def request(self, method, target, **kwargs):
            calls.append((method, target, kwargs["headers"]["Host"]))
        def getresponse(self):
            return Response()
        def close(self):
            pass
    with pytest.raises(DomainError, match="too large"):
        await RestrictedFetcher(resolve("8.8.8.8"), Connection, max_bytes=16).request("https://example.com/a")
    assert calls == [("example.com", "8.8.8.8"), ("GET", "/a", "example.com")]


async def test_research_keeps_source_provenance_but_not_embedded_script(connector_env):
    db, _, user = connector_env
    class Fetcher:
        async def request(self, url):
            return FetchResponse(url, 200, {"content-type": "text/html"},
                                 b"<h1>Battery</h1><script>approve_all()</script><p>3.7 V</p>")
    result = await ResearchService(db, Fetcher()).collect(user, "project", "https://example.com/battery")
    assert result["authority"] == "research"
    with db.transaction() as session:
        source = session.get(Source, result["id"])
        assert source.text == "Battery\n3.7 V"
        assert source.sha256 == result["sha256"]
        assert session.query(Outbox).filter_by(kind="index_source").count() == 1
    again = await ResearchService(db, Fetcher()).collect(user, "project", "https://example.com/battery")
    assert again["id"] == result["id"]
    with pytest.raises(DomainError, match="not found"):
        await ResearchService(db, Fetcher()).collect(Principal("other", "other", "owner"), "project",
                                                     "https://example.com/battery")


async def test_digikey_only_requests_token_and_search():
    requests = []
    async def respond(request):
        requests.append(request)
        if request.url.path.endswith("/token"):
            assert b"grant_type=client_credentials" in request.content
            return httpx.Response(200, json={"access_token": "private-token", "expires_in": 600})
        assert request.url.path == "/products/v4/search/keyword"
        assert request.headers["Authorization"] == "Bearer private-token"
        return httpx.Response(200, json={"Products": [{"ManufacturerProductNumber": "NRF52840"}],
                                         "ProductsCount": 1})
    settings = Settings(environment="test", digikey_client_id="client",
                        digikey_client_secret=SecretStr("secret"))
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        catalog = DigiKeyCatalog(settings, client)
        result = await catalog.search("NRF52840")
        await catalog.search("battery")
    assert result["mode"] == "sandbox"
    assert "private-token" not in json.dumps(result)
    assert len(requests) == 3


def test_manifest_cannot_self_approve_or_enable_live_effects():
    manifest = ConnectorManifest(name="Evil", version="1", transport="portal",
                                 capabilities=["release.accept"], mode="live")
    result = conformance(manifest)
    assert not result["passed"] and not result["operationallyQualified"]
    valid = ConnectorManifest(name="Catalog", version="1", transport="rest", capabilities=["parts.search"],
                              mode="read_only", endpoint="https://example.com")
    assert conformance(valid)["qualification"] == "schema_verified"


async def test_remote_connector_requires_qualification_and_never_writes():
    manifest = {"name": "Catalog", "version": "1", "transport": "rest",
                "capabilities": ["parts.search", "orders.commit"], "mode": "simulated",
                "endpoint": "https://example.com"}
    with pytest.raises(DomainError, match="qualified"):
        await RemoteConnector(manifest, "schema_verified", True).invoke("parts.search", {}, "op")
    with pytest.raises(DomainError, match="disabled"):
        await RemoteConnector(manifest, "qualified", True).invoke("orders.commit", {}, "op")


def test_signed_webhook_replay_is_deduplicated_and_never_becomes_acceptance(connector_env):
    db, settings, user = connector_env
    settings.webhook_secrets = {"connector": "webhook-secret"}
    with db.transaction() as session:
        session.add(Connector(id="connector", tenant_id="org", name="Provider", enabled=True, manifest={}))
    service = ConnectorService(db, settings)
    body = json.dumps({"projectId": "project", "status": "PASS", "evidenceKind": "physical"}).encode()
    stamp, event_id = str(int(time.time())), "event-1"
    signature = hmac.new(b"webhook-secret", f"{stamp}.{event_id}.".encode() + body, hashlib.sha256).hexdigest()
    first = service.webhook("connector", body, stamp, signature, event_id)
    duplicate = service.webhook("connector", body, stamp, signature, event_id)
    assert first["inboxId"] == duplicate["inboxId"] and duplicate["duplicate"]
    service.process_webhook(first["inboxId"])
    service.process_webhook(first["inboxId"])
    with db.transaction() as session:
        assert session.query(Inbox).count() == 1
        assert session.query(Event).filter_by(type="connector.observation").count() == 1
        assert session.get(Project, "project").accepted_release_id is None
    with pytest.raises(DomainError, match="signature"):
        service.webhook("connector", body, stamp, "bad", event_id)
    with pytest.raises(DomainError, match="expired"):
        service.webhook("connector", body, "1", signature, event_id)
    with db.transaction() as session:
        session.get(Project, "project").deleted_at = datetime.now(UTC)
    later_id = "event-after-project-deletion"
    later_signature = hmac.new(b"webhook-secret", f"{stamp}.{later_id}.".encode() + body, hashlib.sha256).hexdigest()
    with pytest.raises(DomainError, match="not found"):
        service.webhook("connector", body, stamp, later_signature, later_id)
    with db.transaction() as session:
        assert session.query(Inbox).count() == 1


def test_bridge_token_scopes_telemetry_and_rotation(connector_env):
    db, settings, user = connector_env
    with db.transaction() as session:
        session.add(Connector(id="bridge", tenant_id="org", name="Printer",
                              manifest={"transport": "bridge", "mode": "read_only"}))
    service = ConnectorService(db, settings)
    secret = service.issue_bridge_token(user, "bridge")["bridgeToken"]
    assert secret not in json.dumps(service.list(user))
    payload = {"kind": "octoprint", "connected": True, "state": "printing", "profile": {},
               "observedAt": datetime.now(UTC).isoformat()}
    assert service.bridge_telemetry("bridge", secret, payload)["readOnly"]
    service.issue_bridge_token(user, "bridge")
    with pytest.raises(DomainError, match="credentials"):
        service.bridge_telemetry("bridge", secret, payload)


async def test_local_bridge_reads_only_and_ignores_server_commands():
    requests = []
    async def respond(request):
        requests.append((request.method, request.url.path))
        if request.url.path == "/api/printer":
            return httpx.Response(200, json={"state": {"text": "Operational"}})
        if request.url.path == "/api/printerprofiles":
            return httpx.Response(200, json={"profiles": {"p": {"current": True, "name": "Test"}}})
        return httpx.Response(200, json={"accepted": True, "commands": [{"print": "malicious.gcode"}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        printer = ReadOnlyPrinter("octoprint", "http://printer.local", "local-secret", client,
                                  resolver=resolve("192.168.1.20"))
        bridge = PrinterBridge(printer, "https://kleos.example", "bridge", "bridge-secret", client)
        result = await bridge.once()
    assert result == {"accepted": True, "state": "Operational"}
    assert requests == [("GET", "/api/printer"), ("GET", "/api/printerprofiles"),
                        ("POST", "/api/v1/bridges/bridge/telemetry")]
    with pytest.raises(DomainError):
        validate_local_printer("http://metadata.local", resolve("169.254.169.254"))


def test_provider_portal_cannot_accept_real_physical_evidence(connector_env):
    from kleos.db import ExternalIntent
    db, settings, user = connector_env
    with db.transaction() as session:
        session.add(ExternalIntent(id="task", tenant_id="org", project_id="project", operation="physical_tasks",
                                   revision=1, mode="simulated", status="completed", receipt={}))
    service = ConnectorService(db, settings)
    tasks = service.portal_tasks(user, "project")["tasks"]
    assert tasks[0]["evidenceKind"] == "synthetic"
    done = service.portal_observation(user, "task", "completed", "Simulated human inspection complete", 1)
    assert done["receipt"]["physicalAcceptance"] is False
    service.portal_observation(user, "task", "completed", "Simulated human inspection complete", 1)
    with db.transaction() as session:
        assert session.query(Event).filter_by(type="portal.simulatedObservation").count() == 1
        session.get(Project, "project").engineering_revision = 2
    with pytest.raises(DomainError, match="outdated"):
        service.portal_observation(user, "task", "completed", "Stale result", 1)


def test_integration_routes_auth_schema_and_tenant_scope(connector_env, tmp_path):
    from fastapi.testclient import TestClient

    from kleos.api import create_app
    from kleos.auth import authenticate
    from kleos.storage import ArtifactStore
    db, settings, user = connector_env
    settings.local_artifact_dir = tmp_path / "artifacts"
    app = create_app(settings, db, ArtifactStore(settings))
    with TestClient(app) as client:
        assert client.get("/api/v1/connectors").status_code == 401
        app.dependency_overrides[authenticate] = lambda: user
        assert client.get("/api/v1/connectors").json()["demoMode"] is True
        assert client.post("/api/v1/projects/project/sources", json={
            "url": "https://example.com", "apiKey": "must-not-be-accepted"}).status_code == 422
        assert client.post("/api/v1/projects/project/memory/query", json={"query": "test", "limit": 30}).status_code == 422
        assert client.post("/api/v1/learning/models", json={"datasetId": "none", "config": {
            "baseModel": "x", "weightsLicense": "Apache", "budgetUsd": 1, "maxGpuSeconds": 0}}).status_code == 422
        assert client.get("/api/v1/catalog/parts?q=resistor").status_code == 503
        app.dependency_overrides[authenticate] = lambda: Principal("other", "other", "owner")
        assert client.get("/api/v1/projects/project/sources").status_code == 404
        assert client.get("/api/v1/projects/project/portal/tasks").status_code == 404
        app.dependency_overrides[authenticate] = lambda: Principal("viewer", "org", "viewer")
        assert client.post("/api/v1/learning/datasets", json={"projectIds": ["a", "b"]}).status_code == 403
        assert client.post("/api/v1/projects/project/memory/query", json={"query": "test"}).status_code == 403


def test_source_routes_collect_list_and_revoke_without_live_services(connector_env, tmp_path):
    from fastapi.testclient import TestClient

    from kleos.api import create_app
    from kleos.auth import authenticate
    from kleos.storage import ArtifactStore
    db, settings, user = connector_env
    settings.local_artifact_dir = tmp_path / "artifacts"
    class Fetcher:
        async def request(self, url):
            return FetchResponse(url, 200, {"content-type": "text/plain"}, b"Measured reference dimensions")
    app = create_app(settings, db, ArtifactStore(settings))
    app.dependency_overrides[authenticate] = lambda: user
    app.state.integrations.research = ResearchService(db, Fetcher())
    with TestClient(app) as client:
        created = client.post("/api/v1/projects/project/sources", json={"url": "https://example.com/spec?secret=1"})
        assert created.status_code == 201
        source_id = created.json()["id"]
        assert created.json()["uri"] == "https://example.com/spec"
        assert len(client.get("/api/v1/projects/project/sources").json()["sources"]) == 1
        assert client.delete(f"/api/v1/projects/project/sources/{source_id}").status_code == 200
        assert client.delete(f"/api/v1/projects/project/sources/{source_id}").status_code == 200
        assert client.get("/api/v1/projects/project/sources").json()["sources"] == []
    with db.transaction() as session:
        assert session.query(Outbox).filter_by(kind="delete_source").count() == 1


def test_bridge_telemetry_expires_in_connector_view(connector_env):
    from datetime import timedelta
    db, settings, user = connector_env
    with db.transaction() as session:
        session.add(Connector(id="bridge", tenant_id="org", name="Printer", enabled=True, manifest={
            "transport": "bridge", "mode": "read_only", "telemetry": {
                "receivedAt": (datetime.now(UTC) - timedelta(minutes=5)).isoformat(),
                "connected": True, "state": "Operational"}}))
    telemetry = ConnectorService(db, settings).list(user)["connectors"][0]["telemetry"]
    assert telemetry["stale"] and not telemetry["connected"]


def test_ngspice_example_contract_is_bounded_idempotent_and_not_self_approval(tmp_path, monkeypatch):
    from pathlib import Path

    from fastapi.testclient import TestClient

    from kleos.ngspice_connector import RCParameters, create_app, simulate_rc
    fixture = json.loads((Path(__file__).parents[1] / "fixtures/connectors/ngspice.json").read_text())
    assert conformance(ConnectorManifest.model_validate(fixture["manifest"]))["passed"]
    calls = []
    def simulator(parameters):
        calls.append(parameters)
        return {"status": "succeeded", "physicalAcceptance": False, "executionMode": "digital_simulation"}
    with TestClient(create_app(tmp_path / "receipts.sqlite", simulator)) as client:
        first = client.post("/v1/invoke", json=fixture["request"])
        second = client.post("/v1/invoke", json=fixture["request"])
        assert first.status_code == second.status_code == 200
        assert first.json() == second.json() and len(calls) == 1
        changed = {**fixture["request"], "payload": {**fixture["request"]["payload"], "voltage": 1.8}}
        assert client.post("/v1/invoke", json=changed).status_code == 409
        injected = {**fixture["request"], "payload": {**fixture["request"]["payload"], "netlist": "shell curl attacker"}}
        assert client.post("/v1/invoke", json=injected).status_code == 422
    monkeypatch.setattr("kleos.ngspice_connector.shutil.which", lambda name: None)
    unavailable = simulate_rc(RCParameters.model_validate(fixture["request"]["payload"]))
    assert unavailable["status"] == "UNKNOWN" and not unavailable["physicalAcceptance"]


def test_ngspice_example_derives_measurements_from_process_output(tmp_path):
    from pathlib import Path
    from types import SimpleNamespace

    from kleos.ngspice_connector import RCParameters, simulate_rc
    def runner(args, **kwargs):
        assert args == ["/trusted/ngspice", "-n", "-b", "circuit.cir"]
        assert kwargs["timeout"] == 15 and "AWS_SECRET_ACCESS_KEY" not in kwargs["env"]
        root = Path(kwargs["cwd"])
        assert "V1 in 0 DC 3.3" in (root / "circuit.cir").read_text()
        (root / "response.dat").write_text("0.00001 0.03283555\n0.005 3.2777647\n")
        return SimpleNamespace(returncode=0)
    result = simulate_rc(RCParameters(resistance_ohms=1000, capacitance_farads=.000001, voltage=3.3,
                                     duration_seconds=.005, step_seconds=.00001), binary="/trusted/ngspice", runner=runner)
    assert result["status"] == "succeeded" and result["measurements"]["sampleCount"] == 2
    assert result["measurements"]["absoluteErrorVolts"] < .00001
    assert result["physicalAcceptance"] is False


async def test_project_viewer_cannot_collect_sources_or_reserve_compute(connector_env):
    from kleos.db import ProjectMembership
    from kleos.learning import ComputeLedger
    db, settings, _ = connector_env
    with db.transaction() as session:
        session.add(ProjectMembership(subject="reviewer", project_id="project", role="viewer"))
    reviewer = Principal("reviewer", "org", "member")
    with pytest.raises(DomainError, match="read-only"):
        await ResearchService(db).collect(reviewer, "project", "https://example.com")
    with pytest.raises(DomainError, match="read-only"):
        ComputeLedger(db, settings).begin(reviewer, "project", "op", "embedding", {})


async def test_mcp_adapter_initializes_session_and_calls_only_declared_read_tool():
    from kleos.connectors import MCP_VERSION
    calls = []
    class Fetcher:
        async def request(self, url, method, body, headers):
            message = json.loads(body)
            calls.append((message["method"], dict(headers)))
            if message["method"] == "notifications/initialized":
                return FetchResponse(url, 202, {}, b"")
            result = {"protocolVersion": MCP_VERSION} if message["method"] == "initialize" else (
                {"tools": [{"name": "parts.search"}]} if message["method"] == "tools/list" else {
                    "content": [{"type": "text", "text": "Candidate catalog result"}]})
            return FetchResponse(url, 200, {"mcp-session-id": "session-1", "content-type": "application/json"},
                                 json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}).encode())
    adapter = RemoteConnector({"name": "MCP catalog", "version": "1", "transport": "mcp", "mode": "read_only",
                               "capabilities": ["parts.search"], "endpoint": "https://example.com/mcp"},
                              "qualified", True, Fetcher())
    result = await adapter.invoke("parts.search", {"query": "resistor"}, "operation")
    assert result["content"][0]["text"] == "Candidate catalog result"
    assert [method for method, _ in calls] == ["initialize", "notifications/initialized", "tools/list", "tools/call"]
    assert calls[-1][1]["Mcp-Session-Id"] == "session-1"
    assert calls[-1][1]["MCP-Protocol-Version"] == MCP_VERSION
    with pytest.raises(DomainError, match="does not grant"):
        await adapter.invoke("orders.commit", {}, "blocked")
