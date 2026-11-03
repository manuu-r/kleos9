"""Explicit opt-in smoke of actual CLI processes and HTTP against disposable PostgreSQL."""

import os
import secrets
import socket
import subprocess
import time
from pathlib import Path

import httpx
import pytest
from sqlalchemy.engine import make_url


def test_real_cli_migrate_serve_and_http_admission(tmp_path):
    raw = os.environ.get("KLEOS_TEST_POSTGRES_URL")
    if not raw:
        pytest.skip("KLEOS_TEST_POSTGRES_URL is required for real process startup")
    url = make_url(raw)
    if not url.database.startswith(("kleos_rewrite_verify_", "kleos_test_")):
        pytest.fail("Use a disposable rewrite test database")
    backend = Path(__file__).parents[1]
    env = {key: value for key, value in os.environ.items() if not key.startswith("KLEOS_")}
    env.update(
        {
            "KLEOS_DATABASE_URL": url.set(drivername="postgresql+psycopg").render_as_string(
                hide_password=False
            ),
            "KLEOS_ENVIRONMENT": "test",
            "KLEOS_DEVELOPMENT_NO_LOGIN": "true",
            "KLEOS_SESSION_SECRET": secrets.token_hex(32),
            "KLEOS_ARTIFACT_ROOT": str(tmp_path / "artifacts"),
        }
    )
    command = [str(backend / ".venv/bin/python"), "-m", "kleos.cli"]
    migration = subprocess.run([*command, "migrate"], cwd=backend, env=env, capture_output=True, timeout=30)
    assert migration.returncode == 0, "Fresh runtime migration command failed"
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
    process = subprocess.Popen(
        [*command, "serve", "--port", str(port)],
        cwd=backend,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=2, trust_env=False) as client:
            deadline, readiness = time.monotonic() + 15, None
            while time.monotonic() < deadline:
                assert process.poll() is None, "HTTP process stopped before readiness"
                try:
                    readiness = client.get("/api/v1/ready")
                    if readiness.status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                time.sleep(0.05)
            assert readiness is not None and readiness.status_code == 200
            assert readiness.json()["apiAvailable"] is True
            assert readiness.json()["frontendContractComplete"] is False
            assert readiness.json()["physicalActions"] is False
            created = client.post(
                "/api/v1/commands",
                headers={"Idempotency-Key": secrets.token_hex(16)},
                json={
                    "type": "project.create",
                    "payload": {"brief": "CLI smoke fixture", "autoStart": False},
                },
            )
            assert created.status_code == 202
            project = created.json()["projectId"]
            submitted = client.post(
                f"/api/v1/projects/{project}/objectives",
                headers={"Idempotency-Key": secrets.token_hex(16)},
                json={"objective": "Only create a missing bracket", "expectedRevision": 1},
            )
            assert submitted.status_code == 200
            workspace = client.get(f"/api/v1/projects/{project}/workspace").json()
            assert workspace["revision"] == 2 and workspace["runs"][0]["status"] == "pending"
            assert (
                client.get(f"/api/v1/projects/{project}/events/history").json()["events"][-1]["type"]
                == "objective.submitted"
            )
            # No worker or provider is started by the API process itself.
    finally:
        if process.poll() is None:
            process.terminate()
        try:
            process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=5)
