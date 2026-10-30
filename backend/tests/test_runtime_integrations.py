"""Opt-in local PostgreSQL/Qdrant checks; live embeddings need a second explicit flag.

Each test uses a unique PostgreSQL schema, Qdrant collection, and artifact directory.
The application's public schema, active outbox, and user projects are never mutated.
"""

import hashlib
import hmac
import json
import os
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.schema import CreateSchema, DropSchema

from kleos.api import create_app
from kleos.auth import Principal
from kleos.bridge import PrinterBridge, ReadOnlyPrinter
from kleos.config import Settings
from kleos.contracts import DomainError
from kleos.db import (
    Artifact,
    Attempt,
    Base,
    Connector,
    Consent,
    Database,
    Dataset,
    Event,
    ExternalIntent,
    Invocation,
    Membership,
    Organization,
    Project,
    Release,
    Run,
    Source,
    Task,
    TrainedModel,
)
from kleos.domain import ControlPlane
from kleos.learning import LearningService
from kleos.maintenance import Maintenance
from kleos.memory import GeminiEmbedder, MemoryIndex
from kleos.storage import ArtifactStore

pytestmark = pytest.mark.skipif(os.getenv("KLEOS_TEST_RUNTIME_INTEGRATIONS") != "1",
                                reason="Opt-in real local PostgreSQL/Qdrant integration checks")


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    # These tests isolate application lifecycle data; dedicated DBOS tests own checkpoint proofs.
    class EmptyCheckpointClient:
        def __init__(self, **kwargs):
            pass
        def list_workflows(self, **kwargs):
            return []
        def destroy(self):
            pass
    monkeypatch.setattr("kleos.checkpoint_retention.DBOSClient", EmptyCheckpointClient)
    settings = Settings(environment="test", storage_backend="local", execution_backend="disabled",
                        local_artifact_dir=tmp_path / "artifacts", project_budget_usd=.25,
                        daily_budget_usd=.25, embedding_reservation_usd=.01, model_timeout_seconds=30,
                        auth_mode="development", development_token=SecretStr(uuid.uuid4().hex),
                        otlp_endpoint=None, digikey_client_id="", digikey_client_secret=None)
    assert settings.database_url.startswith("postgresql"), "Runtime checks require local PostgreSQL"
    db = Database(settings.database_url)
    schema = "runtime_integrations_" + uuid.uuid4().hex
    with db.engine.begin() as connection:
        connection.execute(CreateSchema(schema))
    db.engine = db.engine.execution_options(schema_translate_map={None: schema})
    db.sessions.configure(bind=db.engine)
    store = ArtifactStore(settings)
    indexes = []
    try:
        Base.metadata.create_all(db.engine)
        with db.transaction() as session:
            session.add_all([Organization(id="org", name="Runtime verification"),
                             Organization(id="other", name="Isolation verification")])
            session.flush()
            session.add_all([Membership(subject="development", tenant_id="org", role="owner"),
                             Membership(subject="development", tenant_id="other", role="owner")])
            for number in (1, 2):
                project_id = f"project-{number}"
                session.add(Project(id=project_id, tenant_id="org", owner_id="development", name="Runtime fixture",
                                    brief="Verification fixture only", budget_usd=.25))
        app = create_app(settings, db, store)
        token = settings.development_token.get_secret_value()
        with TestClient(app, headers={"Authorization": f"Bearer {token}", "X-Organization-Id": "org"}) as client:
            yield SimpleNamespace(db=db, settings=settings, store=store, app=app, client=client,
                                  user=Principal("development", "org", "owner"), indexes=indexes)
    finally:
        for index in indexes:
            if index.client.collection_exists(index.collection):
                index.client.delete_collection(index.collection)
            index.client.close()
        with db.engine.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        db.close()


def seed_accepted_fixtures(runtime):
    """Digital fixture records exercise lifecycle plumbing, not engineering qualification."""
    with runtime.db.transaction() as session:
        for number in (1, 2):
            project_id, artifact_id = f"project-{number}", f"artifact-{number}"
            content = f"Licensed digital verification fixture {number}".encode()
            key = f"artifacts/org/{project_id}/{artifact_id}.txt"
            sha = hashlib.sha256(content).hexdigest()
            runtime.store.put(key, content)
            session.add(Artifact(id=artifact_id, tenant_id="org", project_id=project_id, revision=1,
                                 lane="mechanics", name="fixture.txt", key=key, sha256=sha,
                                 size=len(content), status="accepted"))
            session.add(Release(id=f"release-{number}", tenant_id="org", project_id=project_id, revision=1,
                                mode="digital", manifest={"artifacts": {artifact_id: sha}}, approved_by="fixture"))
            session.get(Project, project_id).accepted_release_id = f"release-{number}"
            session.add(Consent(id=f"consent-{number}", tenant_id="org", project_id=project_id,
                                actor_id="development", opted_in=True))


@pytest.mark.skipif(os.getenv("KLEOS_TEST_LIVE_EMBEDDINGS") != "1", reason="Requires explicit paid embedding opt-in")
def test_live_gemini_embedding_qdrant_retrieval_revocation_and_budget(runtime):
    assert runtime.settings.gemini_api_key, "Gemini embedding credential is not configured"
    class BoundedEmbedder(GeminiEmbedder):
        calls = 0
        def embed(self, content, query=False):
            self.calls += 1
            assert self.calls <= 2 and len(content) < 500, "Live embedding test exceeded its call/input ceiling"
            failure = None
            try:
                return super().embed(content, query)
            except Exception as error:
                failure = type(error).__name__
            # Raise outside the exception handler so pytest cannot render the provider exception context.
            pytest.fail(f"Live embedding failed: {failure}; provider body suppressed", pytrace=False)
    embedder = BoundedEmbedder(runtime.settings)
    index = MemoryIndex(runtime.db, runtime.settings, embedder)
    index.collection = "runtime_" + uuid.uuid4().hex
    index.alias = index.collection + "_alias"
    runtime.indexes.append(index)
    source_id = str(uuid.uuid4())
    with runtime.db.transaction() as session:
        session.add(Source(id=source_id, tenant_id="org", project_id="project-1", revision=1,
                           uri="https://example.com/verification", sha256="verification-only",
                           text="The smartwatch battery measures 20 by 15 by 4 millimetres.", authority="research"))
    index.index(source_id)
    found = index.retrieve(runtime.user, "project-1", "smartwatch battery dimensions")
    assert found["sources"][0]["sourceId"] == source_id
    assert found["sources"][0]["authority"] == "research"
    with pytest.raises(DomainError, match="not found"):
        index.retrieve(Principal("intruder", "other", "owner"), "project-1", "secret")
    index.revoke(runtime.user, "project-1", source_id)
    assert index.retrieve(runtime.user, "project-1", "smartwatch battery dimensions")["sources"] == []
    index.delete_points([source_id])
    assert index.client.count(index.collection, exact=True).count == 0
    assert embedder.calls == 2
    with runtime.db.transaction() as session:
        intents = session.scalars(select(ExternalIntent)).all()
        assert len(intents) == 2 and all(intent.status == "succeeded" for intent in intents)
        assert all(len(intent.receipt["vector"]) == 768 for intent in intents)
        project = session.get(Project, "project-1")
        assert project.spent_usd == pytest.approx(.02) and project.reserved_usd == 0


@pytest.mark.parametrize("url,text", [
    ("https://example.com", "Example Domain"),
    ("https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf", "Dummy PDF file"),
])
def test_real_restricted_research_fetch_and_pdf(runtime, url, text):
    response = runtime.client.post("/api/v1/projects/project-1/sources", json={"url": url})
    assert response.status_code == 201, f"Research fetch status {response.status_code}; body suppressed"
    result = response.json()
    assert result["authority"] == "research" and result["revision"] == 1
    with runtime.db.transaction() as session:
        source = session.get(Source, result["id"])
        assert text.lower() in source.text.lower()
        assert len(source.sha256) == 64 and source.uri.startswith("https://")
    assert runtime.client.get("/api/v1/projects/project-1/sources",
                              headers={"X-Organization-Id": "other"}).status_code == 404
    assert runtime.client.post("/api/v1/projects/project-1/sources", json={
        "url": "https://169.254.169.254/latest/meta-data"}).status_code == 422


@pytest.mark.parametrize("kind", ["octoprint", "moonraker"])
async def test_local_readonly_printer_bridge_into_authenticated_platform(runtime, kind):
    requests = []
    class PrinterServer(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            requests.append((self.command, self.path))
            payloads = {
                "/api/printer": {"state": {"text": "Operational"}},
                "/api/printerprofiles": {"profiles": {"fixture": {"current": True, "name": "Fixture printer"}}},
                "/printer/info": {"result": {"state": "ready", "software_version": "fixture"}},
            }
            body = json.dumps(payloads[self.path]).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    with runtime.db.transaction() as session:
        session.add(Connector(id="bridge", tenant_id="org", name="Read-only fixture", enabled=True,
                              manifest={"transport": "bridge"}, level="schema_verified"))
    issued = runtime.client.post("/api/v1/bridges/bridge/token")
    assert issued.status_code == 200
    server = ThreadingHTTPServer(("127.0.0.1", 0), PrinterServer)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        origin = f"http://127.0.0.1:{server.server_port}"
        printer = ReadOnlyPrinter(kind, origin, "local-fixture-key")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=runtime.app)) as client:
            bridge = PrinterBridge(printer, "https://verification.example", "bridge", issued.json()["bridgeToken"], client)
            assert (await bridge.once())["accepted"]
        with runtime.db.transaction() as session:
            telemetry = session.get(Connector, "bridge").manifest["telemetry"]
            assert telemetry["connected"] and not telemetry["physicalAcceptance"]
        assert requests and all(method == "GET" for method, _ in requests)
        assert runtime.client.post("/api/v1/bridges/bridge/telemetry", json={
            "kind": kind, "connected": True, "state": "ready", "observedAt": datetime.now(UTC).isoformat(),
            "command": "print"}).status_code == 422
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_authenticated_webhook_portal_and_unconfigured_feature_gates(runtime):
    runtime.settings.webhook_secrets = {"connector": "runtime-fixture-webhook-secret"}
    with runtime.db.transaction() as session:
        session.add(Connector(id="connector", tenant_id="org", name="Fixture", enabled=True, manifest={}, level="qualified"))
        session.add(ExternalIntent(id="physical-task", tenant_id="org", project_id="project-1", revision=1,
                                   operation="physical_tasks", mode="simulated", status="completed", receipt={}))
    body = json.dumps({"projectId": "project-1", "status": "delivered", "physicalAcceptance": True}).encode()
    timestamp, event_id = str(int(time.time())), "event-fixture"
    signature = hmac.new(runtime.settings.webhook_secrets["connector"].encode(),
                         f"{timestamp}.{event_id}.".encode() + body, hashlib.sha256).hexdigest()
    headers = {"X-Kleos-Timestamp": timestamp, "X-Kleos-Event-Id": event_id, "X-Kleos-Signature": signature}
    first = runtime.client.post("/api/v1/webhooks/connector", content=body, headers=headers)
    assert first.status_code == 202
    assert runtime.client.post("/api/v1/webhooks/connector", content=body, headers=headers).json()["duplicate"]
    assert runtime.client.post("/api/v1/webhooks/connector", content=body,
                               headers={**headers, "X-Kleos-Signature": "bad"}).status_code == 401
    runtime.app.state.integrations.connectors.process_webhook(first.json()["inboxId"])
    tasks = runtime.client.get("/api/v1/projects/project-1/portal/tasks").json()
    assert tasks["tasks"][0]["evidenceKind"] == "synthetic"
    observation = runtime.client.post("/api/v1/portal/tasks/physical-task/observations", json={
        "status": "completed", "note": "Simulated physical observation", "expectedEngineeringRevision": 1})
    assert observation.status_code == 200 and observation.json()["receipt"]["physicalAcceptance"] is False
    assert runtime.client.post("/api/v1/connectors", json={
        "name": "Unsafe", "version": "1", "transport": "portal", "capabilities": ["printing.start"],
        "mode": "live"}).status_code == 422
    assert runtime.client.get("/api/v1/catalog/parts?q=battery").status_code == 503
    seed_accepted_fixtures(runtime)
    with runtime.db.transaction() as session:
        session.add(Dataset(id="fixture-dataset", tenant_id="org", manifest={}))
        session.flush()
        session.add(TrainedModel(id="fixture-model", dataset_id="fixture-dataset", status="promoted", config={}))
    modal = runtime.client.post("/api/v1/projects/project-1/compute/inference", headers={"Idempotency-Key": "modal-fixture"},
                                json={"modelId": "fixture-model", "artifactIds": ["artifact-1"]})
    assert modal.status_code == 503 and modal.json()["code"] == "storage_unconfigured"
    with runtime.db.transaction() as session:
        assert not session.scalars(select(ExternalIntent).where(ExternalIntent.mode == "compute")).all()


def test_postgres_consent_dataset_exclusion_retention_and_explicit_deletion(runtime):
    seed_accepted_fixtures(runtime)
    learning = LearningService(runtime.db, runtime.settings, runtime.store)
    dataset = learning.create_dataset(runtime.user, ["project-1", "project-2"])
    learning.approve_dataset_rights(runtime.user, dataset["id"], "These are operator-owned licensed test fixtures.")
    materialized = learning.materialize_training(runtime.user, dataset["id"])
    old = datetime.now(UTC) - timedelta(days=31)
    transcript = "transcripts/org/project-1/expired.json"
    runtime.store.put_json(transcript, {"prompt": "Expired runtime verification capture"})
    with runtime.db.transaction() as session:
        session.add(Run(id="old-run", tenant_id="org", project_id="project-1", command_id="fixture",
                        kind="research", revision=1, status="succeeded", created_at=old))
        session.flush()
        session.add(Task(id="old-task", tenant_id="org", project_id="project-1", run_id="old-run",
                         lane="research", kind="research", revision=1, status="succeeded", created_at=old))
        session.flush()
        session.add(Attempt(id="old-attempt", tenant_id="org", task_id="old-task", number=1, fence=1,
                            policy_revision=1, provider="google", model="fixture", status="succeeded",
                            context={"prompt": "Expired fixture context"}, created_at=old))
        session.flush()
        session.add(Invocation(id="old-invocation", tenant_id="org", project_id="project-1",
                               attempt_id="old-attempt", operation="generate", provider="google", model="fixture",
                               status="succeeded", transcript_key=transcript, created_at=old))
        session.get(Consent, "consent-1").opted_in = False
        project = session.get(Project, "project-1")
        project.event_sequence += 1
        session.add(Event(tenant_id="org", project_id="project-1", sequence=project.event_sequence,
                          type="runtime.oldEvent", data={"privateText": "Do not archive this body"},
                          created_at=datetime.now(UTC) - timedelta(days=100)))
    maintenance = Maintenance(runtime.db, runtime.settings, runtime.store)
    retained = maintenance.retention()
    assert retained["excludedDatasets"] == 1 and retained["transcripts"] == 1
    with pytest.raises(FileNotFoundError):
        runtime.store.get(transcript)
    with pytest.raises(FileNotFoundError):
        runtime.store.get(materialized["key"])
    with pytest.raises(DomainError) as expired:
        ControlPlane(runtime.db, runtime.settings, runtime.store).events(runtime.user, "project-1", 0)
    assert expired.value.status == 410
    assert runtime.store.get("artifacts/org/project-1/artifact-1.txt")
    orphan = "transcripts/org/project-1/orphan.json"
    runtime.store.put_json(orphan, {"runtime": "orphan verification"})
    with runtime.db.transaction() as session:
        revision = session.get(Project, "project-1").revision
    response = runtime.client.delete(f"/api/v1/projects/project-1?expectedRevision={revision}")
    assert response.status_code == 202
    assert runtime.client.get("/api/v1/projects/project-1/workspace").status_code == 404
    assert maintenance.purge_project("project-1")["status"] == "purged"
    with pytest.raises(FileNotFoundError):
        runtime.store.get(orphan)
    with pytest.raises(FileNotFoundError):
        runtime.store.get("artifacts/org/project-1/artifact-1.txt")
    assert runtime.store.get("artifacts/org/project-2/artifact-2.txt")
    with runtime.db.transaction() as session:
        assert session.get(Dataset, dataset["id"]).status == "excluded"
        assert session.get(Dataset, dataset["id"]).manifest["derivedCopyPurged"]
