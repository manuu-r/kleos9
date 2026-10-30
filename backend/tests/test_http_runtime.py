"""Opt-in no-paid acceptance through the running local Docker API.

Only the fixture project created in this test is mutated or deleted. Credentials are
read from the local token file and never included in the verification report.
"""

import hashlib
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func, select

from kleos.checkpoint_retention import CheckpointRetention
from kleos.config import Settings
from kleos.db import Artifact, Database, ExternalIntent, Invocation, Outbox, Project, Run

pytestmark = pytest.mark.skipif(os.getenv("KLEOS_TEST_HTTP_RUNTIME") != "1",
                                reason="Opt-in actual Docker HTTP API acceptance")


def test_actual_http_api_no_paid_calls_and_scoped_cleanup():
    base = os.getenv("KLEOS_TEST_HTTP_BASE_URL", "http://127.0.0.1:8000")
    assert base in {"http://127.0.0.1:8000", "http://localhost:8000"}, "This fixture is restricted to the local API"
    token_path = Path(os.getenv("KLEOS_TEST_HTTP_TOKEN_FILE", ".data/local-dev-token"))
    token = token_path.read_text().strip()
    assert token, "Development token file is empty"
    settings = Settings()
    db = Database(settings.database_url)
    report = {"schemaVersion": 1, "startedAt": datetime.now(UTC).isoformat(), "baseUrl": base,
              "transport": "Actual HTTP socket to Docker-hosted FastAPI; development bearer authentication",
              "paidProviderCalls": 0, "realWorldActions": 0, "checks": [], "status": "running"}
    project_id, artifact_id = None, None
    create_body, create_key = None, None
    output = Path("verification/http-runtime.json")

    def passed(name, **observations):
        report["checks"].append({"name": name, "status": "passed", **observations})

    with httpx.Client(base_url=base, timeout=15, trust_env=False, follow_redirects=False,
                      headers={"Authorization": f"Bearer {token}"}) as client:
        def workspace():
            response = client.get(f"/api/v1/projects/{project_id}/workspace")
            assert response.status_code == 200
            return response.json()

        def command(kind, payload, revision=None, key=None):
            request = {"type": kind, "payload": payload}
            if revision is not None:
                request["expectedRevision"] = revision
            return client.post("/api/v1/commands", json=request,
                                headers={"Idempotency-Key": key or "http-fixture-" + uuid4().hex})

        try:
            health = client.get("/api/v1/health")
            assert health.status_code == 200 and health.json()["status"] == "ok"
            report["apiVersion"] = health.json()["version"]
            assert client.get("/api/v1/ready").status_code == 200
            organizations = client.get("/api/v1/auth/organizations")
            assert organizations.status_code == 200
            owners = [entry for entry in organizations.json()["organizations"] if entry["role"] in {"owner", "admin"}]
            assert owners, "The runtime fixture needs an invited administrator"
            tenant_id = owners[0]["id"]
            client.headers["X-Organization-Id"] = tenant_id
            assert client.get("/api/v1/model-catalog", headers={"Authorization": ""}).status_code == 401
            catalog_response = client.get("/api/v1/model-catalog")
            assert catalog_response.status_code == 200
            catalog = catalog_response.json()
            choices = {entry["provider"]: entry for entry in catalog["models"]}
            assert choices["google"]["configured"] is True
            assert choices["meta"]["configured"] is False and choices["anthropic"]["configured"] is False
            assert all(entry["creditBalance"] is None for entry in choices.values())
            passed("health_authentication_model_catalog", models=[{
                "provider": entry["provider"], "model": entry["model"], "configured": entry["configured"]}
                for entry in choices.values()])

            create_body = {"type": "project.create", "payload": {
                "brief": "HTTP verification fixture - no AI work - " + uuid4().hex}}
            create_key = "http-fixture-create-" + uuid4().hex
            response = client.post("/api/v1/commands", json=create_body, headers={"Idempotency-Key": create_key})
            assert response.status_code == 202
            receipt = response.json()
            project_id = receipt["projectId"]
            report["fixtureProjectId"] = project_id
            assert receipt["runId"] is None
            duplicate = client.post("/api/v1/commands", json=create_body, headers={"Idempotency-Key": create_key})
            assert duplicate.status_code == 202 and duplicate.json() == receipt
            conflict = client.post("/api/v1/commands", json={"type": "project.create", "payload": {
                "brief": "Conflicting fixture payload must never create a project"}},
                headers={"Idempotency-Key": create_key})
            assert conflict.status_code == 409 and conflict.json()["code"] == "idempotency_conflict"
            initial = workspace()
            assert client.get(f"/api/v1/projects/{project_id}/workspace",
                              headers={"X-Organization-Id": str(uuid4())}).status_code == 403
            passed("command_idempotency_conflict_and_tenant_authorization")

            policy = initial["project"]["modelPolicy"]
            policy["coordination"] = {"provider": "google", "model": choices["google"]["model"]}
            changed = command("project.setModelPolicy", {"projectId": project_id, "modelPolicy": policy}, initial["revision"])
            assert changed.status_code == 202
            updated = workspace()
            assert updated["project"]["policyRevision"] == initial["project"]["policyRevision"] + 1
            assert updated["project"]["modelPolicy"] == policy and updated["runs"] == []
            stale = command("project.setTrainingConsent", {"projectId": project_id, "optedIn": True}, initial["revision"])
            assert stale.status_code == 409 and stale.json()["code"] == "stale_revision"
            for opted_in in (True, False):
                assert command("project.setTrainingConsent", {"projectId": project_id, "optedIn": opted_in},
                               workspace()["revision"]).status_code == 202
                assert workspace()["trainingOptedIn"] is opted_in
            passed("model_policy_versioning_stale_revision_and_training_consent")

            content = b"HTTP-only artifact verification fixture. No model inference.\n"
            sha = hashlib.sha256(content).hexdigest()
            upload_body = {"file": {"path": "inputs/runtime-verification.txt", "content": content.decode(),
                                     "mediaType": "text/plain", "encoding": "utf8"},
                           "sha256": sha, "expectedEngineeringRevision": workspace()["project"]["engineeringRevision"]}
            uploaded = client.post(f"/api/v1/projects/{project_id}/artifacts", json=upload_body)
            assert uploaded.status_code == 201
            artifact_id = uploaded.json()["id"]
            assert uploaded.json()["sha256"] == sha and uploaded.json()["status"] == "input"
            again = client.post(f"/api/v1/projects/{project_id}/artifacts", json=upload_body)
            assert again.status_code == 201 and again.json()["id"] == artifact_id
            manifest = client.get(f"/api/v1/artifacts/{artifact_id}/download")
            assert manifest.status_code == 200 and manifest.json()["sha256"] == sha
            url = manifest.json()["url"]
            assert url.startswith("/api/v1/artifacts/")
            downloaded = client.get(url)
            assert downloaded.status_code == 200 and downloaded.content == content
            assert hashlib.sha256(downloaded.content).hexdigest() == sha
            assert client.get(url, headers={"Authorization": ""}).status_code == 401
            bad_hash = client.post(f"/api/v1/projects/{project_id}/artifacts", json={**upload_body, "sha256": "0" * 64})
            assert bad_hash.status_code == 422 and bad_hash.json()["code"] == "artifact_integrity"
            bad_path = client.post(f"/api/v1/projects/{project_id}/artifacts", json={**upload_body,
                "file": {**upload_body["file"], "path": "../outside.txt"}})
            assert bad_path.status_code == 422
            assert client.post(f"/api/v1/projects/{project_id}/artifacts", json={**upload_body,
                "expectedEngineeringRevision": upload_body["expectedEngineeringRevision"] + 1}).status_code == 409
            passed("artifact_upload_deduplication_hash_download_authorization_and_path_guards", bytes=len(content))

            history = client.get(f"/api/v1/projects/{project_id}/events/history?after=0")
            assert history.status_code == 200
            events = history.json()
            sequences = [event["sequence"] for event in events]
            assert sequences == list(range(1, len(events) + 1))
            stream = client.get(f"/api/v1/projects/{project_id}/events?after=0&once=true")
            assert stream.status_code == 200 and stream.headers["content-type"].startswith("text/event-stream")
            event_ids = [int(line[4:]) for line in stream.text.splitlines() if line.startswith("id: ")]
            data = [json.loads(line[6:]) for line in stream.text.splitlines() if line.startswith("data: ")]
            assert event_ids == sequences and data == events
            resumed = client.get(f"/api/v1/projects/{project_id}/events?once=true",
                                 headers={"Last-Event-ID": str(sequences[-2])})
            assert [int(line[4:]) for line in resumed.text.splitlines() if line.startswith("id: ")] == [sequences[-1]]
            assert workspace()["eventCursor"] == sequences[-1]
            passed("ordered_http_sse_history_and_last_event_id_reconnect", eventCount=len(events))

            assert client.get("/api/v1/catalog/parts?q=battery").status_code == 503
            unavailable_modal = client.post(f"/api/v1/projects/{project_id}/compute/inference", json={
                "modelId": str(uuid4()), "artifactIds": [artifact_id]}, headers={"Idempotency-Key": "http-modal-" + uuid4().hex})
            assert unavailable_modal.status_code == 409
            unsigned = client.post("/api/v1/webhooks/" + str(uuid4()), json={"projectId": project_id})
            assert unsigned.status_code == 422
            live = client.post("/api/v1/connectors", json={"name": "Blocked live HTTP verification fixture",
                "version": "1", "transport": "portal", "mode": "live", "capabilities": ["orders.commit"]})
            assert live.status_code == 422
            purchase = command("project.placeOrders", {"projectId": project_id, "mode": "live"}, workspace()["revision"])
            assert purchase.status_code == 409
            assert workspace()["project"]["budget"]["spentEstimateUsd"] == 0
            with db.transaction(read_only=True) as session:
                assert session.scalar(select(func.count(Run.id)).where(Run.project_id == project_id)) == 0
                assert session.scalar(select(func.count(Invocation.id)).where(Invocation.project_id == project_id)) == 0
                assert session.scalar(select(func.count(ExternalIntent.id)).where(ExternalIntent.project_id == project_id)) == 0
            passed("unavailable_services_unsigned_webhook_live_purchase_and_zero_paid_work",
                   digiKeyStatus=503, modalModelUnavailableStatus=409, unsignedWebhookStatus=422,
                   liveConnectorStatus=422, purchaseReleaseGateStatus=409)
            report["status"] = "passed"
        finally:
            try:
                if project_id:
                    current = client.get(f"/api/v1/projects/{project_id}/workspace")
                    if current.status_code == 200:
                        deleted = client.delete(f"/api/v1/projects/{project_id}",
                                                 params={"expectedRevision": current.json()["revision"]})
                        assert deleted.status_code == 202
                    else:
                        assert current.status_code == 404
                    assert client.get(f"/api/v1/projects/{project_id}/workspace").status_code == 404
                    assert client.get(f"/api/v1/projects/{project_id}/events/history").status_code == 404
                    if artifact_id:
                        assert client.get(f"/api/v1/artifacts/{artifact_id}/download").status_code == 404
                    deadline = time.monotonic() + 40
                    final_status = None
                    cleaner = CheckpointRetention(db, settings)
                    while time.monotonic() < deadline:
                        with db.transaction(read_only=True) as session:
                            project = session.get(Project, project_id)
                            assert project and project.deleted_at
                            deletion = project.state.get("deletion", {})
                            final_status = deletion.get("status")
                            app_purged = deletion.get("applicationPurged")
                        if final_status == "purged":
                            break
                        if app_purged:
                            cleaner.sweep(project_id=project_id)  # Explicit fixture-only terminal checkpoint cleanup.
                        time.sleep(.5)
                    assert final_status == "purged", "Fixture deletion has not finished; no unrelated cleanup was attempted"
                    with db.transaction(read_only=True) as session:
                        assert session.scalar(select(func.count(Artifact.id)).where(Artifact.project_id == project_id)) == 0
                        pending = session.scalar(select(func.count(Outbox.id)).where(Outbox.kind == "checkpoint_cleanup",
                            Outbox.payload["projectId"].as_string() == project_id,
                            Outbox.payload["status"].as_string() != "complete"))
                        assert pending == 0
                        assert session.get(Project, project_id).state["deletion"].get("dbosCheckpointsPurgedAt")
                    passed("fixture_deletion_immediate_denial_and_eventual_application_checkpoint_erasure")
                    replay = client.post("/api/v1/commands", json=create_body, headers={"Idempotency-Key": create_key})
                    assert replay.status_code == 410 and replay.json()["code"] == "project_deleted"
                    assert client.get(f"/api/v1/projects/{project_id}/workspace").status_code == 404
                    with db.transaction(read_only=True) as session:
                        assert session.scalar(select(func.count(Project.id)).where(
                            Project.brief == create_body["payload"]["brief"], Project.deleted_at.is_(None))) == 0
                    passed("erased_command_retry_returns_410_without_resurrection")
                    report["cleanup"] = {"projectStatus": final_status, "pendingCheckpointReceipts": 0,
                                         "remainingArtifactRows": 0, "scope": "Created fixture only"}
            except Exception as error:
                report["status"] = "failed"
                report["cleanupErrorType"] = type(error).__name__
                raise
            finally:
                if report["status"] == "running":
                    report["status"] = "failed"
                report["completedAt"] = datetime.now(UTC).isoformat()
                output.parent.mkdir(exist_ok=True)
                output.write_text(json.dumps(report, indent=2) + "\n")
                db.close()
