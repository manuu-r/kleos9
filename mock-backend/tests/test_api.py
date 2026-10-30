from concurrent.futures import ThreadPoolExecutor
import json
from uuid import uuid4
from fastapi.testclient import TestClient
import pytest
from app.main import create_app


def post(client, kind, payload, key=None):
    return client.post("/api/commands", headers={"Idempotency-Key": key or uuid4().hex}, json={"type": kind, "payload": payload})


def test_state_health_command_idempotency_and_conflict():
    with TestClient(create_app(time_scale=0)) as client:
        assert client.get("/api/health").json()["realIntegrations"] is False
        before = client.get("/api/state").json()
        key = uuid4().hex
        first = post(client, "project.create", {"brief": "Local API device"}, key)
        retry = post(client, "project.create", {"brief": "Local API device"}, key)
        assert first.status_code == retry.status_code == 200
        assert first.json() == retry.json()
        assert len(client.get("/api/state").json()["state"]["projects"]) == 1
        assert post(client, "project.create", {"brief": "A different device"}, key).status_code == 409
        assert first.json()["snapshot"]["revision"] > before["revision"]
        assert first.json()["snapshot"]["demo"]["speed"] == 1


def test_concurrent_identical_request_is_executed_once():
    with TestClient(create_app(time_scale=0)) as client:
        key = uuid4().hex
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: post(client, "project.create", {"brief": "One build"}, key), range(4)))
        assert all(response.status_code == 200 for response in results)
        assert len({response.json()["result"]["projectId"] for response in results}) == 1
        assert len(client.get("/api/state").json()["state"]["projects"]) == 1


def test_errors_cors_and_demo_controls():
    with TestClient(create_app(time_scale=0)) as client:
        assert client.post("/api/commands", json={"type": "agent.nextMessage", "payload": {}}).status_code == 422
        response = post(client, "project.create", {"brief": ""})
        assert response.status_code == 400 and "message" in response.json()
        assert post(client, "unknown.command", {}).status_code == 422
        assert post(client, "project.create", {"brief": 42}).status_code == 422
        assert client.post("/api/commands", content="{}", headers={"Content-Type": "text/plain"}).status_code == 415
        assert client.post("/api/demo/speed", json={"speed": 4}).json()["demo"]["speed"] == 4
        assert client.post("/api/demo/speed", json={"speed": -2}).status_code == 422
        assert client.post("/api/demo/speed", json={"speed": "fast"}).status_code == 422
        assert client.post("/api/demo/reset", json={}).status_code == 200
        response = client.post("/api/demo/reset", json={}, headers={"Origin": "https://foreign.invalid"})
        assert response.status_code == 403
        preflight = client.options("/api/commands", headers={"Origin": "http://127.0.0.1:5180", "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "Content-Type,Idempotency-Key"})
        assert preflight.status_code == 200
        assert preflight.headers["access-control-allow-origin"] == "http://127.0.0.1:5180"
        assert client.get("/openapi.json").status_code == 200


def test_reset_cancels_pending_job_and_preserves_monotonic_revision():
    with TestClient(create_app()) as client:
        post(client, "connector.test", {"connectorId": "autolayout"})
        running = client.get("/api/state").json()
        assert running["state"]["connectionRuns"]["autolayout"]["status"] == "running"
        reset = client.post("/api/demo/reset", json={}).json()
        assert reset["revision"] > running["revision"]
        assert not reset["state"]["connectionRuns"]
        assert not client.app.state.simulation.active


def test_delete_build_clears_only_its_simulated_data_and_is_idempotent():
    with TestClient(create_app(time_scale=0)) as client:
        one = post(client, "project.create", {"brief": "Delete this simulation"}).json()["result"]["projectId"]
        two = post(client, "project.create", {"brief": "Keep this simulation"}).json()["result"]["projectId"]
        key = uuid4().hex
        deleted = post(client, "project.delete", {"projectId": one}, key)
        assert deleted.status_code == 200
        assert deleted.json()["result"]["deleted"] is True
        assert post(client, "project.delete", {"projectId": one}, key).json() == deleted.json()
        state = client.get("/api/state").json()["state"]
        assert [p["id"] for p in state["projects"]] == [two]
        assert len(state["humanTasks"]) == 3
        assert len(state["connectors"]) == 23
