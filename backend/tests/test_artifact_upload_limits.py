import base64
import hashlib
import json
from datetime import timedelta

import pytest
from sqlalchemy import select
from test_control_plane import create
from test_safety_and_recovery import native_job

from kleos.api import MAX_UPLOAD_REQUEST_BYTES, MAX_USER_INPUT_BYTES
from kleos.db import Artifact, Membership, NativeJob, Project, ProjectMembership, SessionRecord, now
from kleos.storage import digest


async def raw_post(app, path, headers, receive):
    messages = []
    async def send(message):
        messages.append(message)
    await app({"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
               "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": b"",
               "root_path": "", "headers": headers, "client": ("testclient", 1234),
               "server": ("testserver", 80)}, receive, send)
    start = next(message for message in messages if message["type"] == "http.response.start")
    body = b"".join(message.get("body", b"") for message in messages if message["type"] == "http.response.body")
    return start["status"], dict(start["headers"]), json.loads(body)


@pytest.mark.parametrize("size", [1024 * 1024 + 1, MAX_USER_INPUT_BYTES])
def test_authorized_large_uploads_remain_untrusted_inputs(platform, size):
    project_id = create(platform)["projectId"]
    content = b"x" * size
    response = platform.client.post(f"/api/v1/projects/{project_id}/artifacts", json={
        "file": {"path": "device.step", "content": base64.b64encode(content).decode(), "encoding": "base64"},
        "sha256": digest(content), "expectedEngineeringRevision": 1,
    })
    assert response.status_code == 201
    assert response.json()["status"] == "input"
    with platform.db.transaction() as session:
        artifact = session.query(Artifact).one()
        assert artifact.size == size and artifact.lane == "input"
        assert artifact.provenance["authority"] == "user_input"
        assert platform.store.get(artifact.key) == content


@pytest.mark.parametrize("encoding", ["base64", "utf8"])
def test_decoded_upload_ceiling_is_enforced_before_hashing_or_storage(platform, monkeypatch, encoding):
    project_id = create(platform)["projectId"]
    oversized = b"x" * (MAX_USER_INPUT_BYTES + 1)
    content = base64.b64encode(oversized).decode() if encoding == "base64" else oversized.decode()
    def must_not_hash(_content):
        raise AssertionError("Oversized input must be rejected before hashing")
    monkeypatch.setattr("kleos.api.digest", must_not_hash)
    response = platform.client.post(f"/api/v1/projects/{project_id}/artifacts", json={
        "file": {"path": "too-large.bin", "content": content, "encoding": encoding},
        "sha256": "0" * 64, "expectedEngineeringRevision": 1,
    })
    assert response.status_code == 413 and response.json()["code"] == "artifact_too_large"
    with platform.db.transaction() as session:
        assert session.query(Artifact).count() == 0
    assert not platform.settings.local_artifact_dir.exists()


@pytest.mark.parametrize(("access", "status", "code"), [
    ("anonymous", 401, "unauthenticated"),
    ("cross_tenant", 404, "not_found"),
    ("organization_viewer", 403, "forbidden"),
    ("project_viewer", 403, "forbidden"),
    ("csrf", 403, "csrf"),
])
async def test_upload_authorization_rejects_before_reading_request_body(platform, access, status, code):
    project_id = create(platform)["projectId"]
    headers = [(b"content-length", b"2097152"), (b"origin", b"http://localhost:5180")]
    if access not in {"anonymous", "csrf"}:
        headers.append((b"authorization", b"Bearer test-token-not-for-deployment"))
    with platform.db.transaction() as session:
        if access == "cross_tenant":
            session.get(Project, project_id).tenant_id = "org-b"
        if access in {"organization_viewer", "project_viewer"}:
            membership = session.scalar(select(Membership).where(Membership.subject == "development"))
            membership.role = "viewer" if access == "organization_viewer" else "member"
        if access == "project_viewer":
            session.get(Project, project_id).owner_id = "another-owner"
            session.add(ProjectMembership(project_id=project_id, subject="development", role="viewer"))
        if access == "csrf":
            sid = "cookie-session-upload"
            session.add(SessionRecord(id=hashlib.sha256(sid.encode()).hexdigest(), subject="development",
                                      csrf_hash=hashlib.sha256(b"required-csrf-token").hexdigest(),
                                      expires_at=now() + timedelta(hours=1)))
            headers.append((b"cookie", f"kleos_session={sid}".encode()))
    async def must_not_read():
        raise AssertionError("Unauthorized uploads must not be read or buffered")
    actual_status, response_headers, body = await raw_post(
        platform.client.app, f"/api/v1/projects/{project_id}/artifacts", headers, must_not_read)
    assert actual_status == status and body["code"] == code
    assert response_headers[b"access-control-allow-origin"] == b"http://localhost:5180"
    assert response_headers[b"access-control-allow-credentials"] == b"true"


@pytest.mark.parametrize("upload", [True, False])
async def test_request_envelope_limits_fail_before_buffering_and_keep_cors(platform, upload):
    project_id = create(platform)["projectId"]
    path = f"/api/v1/projects/{project_id}/artifacts" if upload else "/api/v1/commands"
    limit = MAX_UPLOAD_REQUEST_BYTES if upload else 1024 * 1024
    async def must_not_read():
        raise AssertionError("Known oversized envelopes must not be buffered")
    status, headers, _ = await raw_post(platform.client.app, path, [
        (b"authorization", b"Bearer test-token-not-for-deployment"),
        (b"content-length", str(limit + 1).encode()), (b"origin", b"http://localhost:5180"),
    ], must_not_read)
    assert status == 413 and headers[b"access-control-allow-origin"] == b"http://localhost:5180"


async def test_chunked_upload_cannot_bypass_request_envelope_limit(platform):
    project_id = create(platform)["projectId"]
    reads = 0
    async def receive():
        nonlocal reads
        reads += 1
        assert reads <= 25
        return {"type": "http.request", "body": b"x" * (1024 * 1024), "more_body": True}
    status, _, _ = await raw_post(platform.client.app, f"/api/v1/projects/{project_id}/artifacts", [
        (b"authorization", b"Bearer test-token-not-for-deployment"),
    ], receive)
    assert status == 413 and reads == 25


@pytest.mark.parametrize("authorization", [b"", b"Bearer wrong-token"])
async def test_native_result_authority_is_checked_before_large_body(platform, authorization):
    native_job(platform)
    async def must_not_read():
        raise AssertionError("An unauthenticated native callback must not be buffered")
    status, _, _ = await raw_post(platform.client.app, "/api/v1/internal/jobs/native-fixture/result", [
        (b"authorization", authorization), (b"content-length", b"50000000"),
    ], must_not_read)
    assert status == 401


def test_scoped_native_callback_still_accepts_large_candidate_result(platform):
    native_job(platform)
    token = "scoped-native-result-fixture"
    with platform.db.transaction() as session:
        session.get(NativeJob, "native-fixture").token_hash = digest(token.encode())
    response = platform.client.post("/api/v1/internal/jobs/native-fixture/result",
        headers={"Authorization": "Bearer " + token}, json={"status": "succeeded", "files": [
            {"path": "native/large.bin", "content": "x" * (1024 * 1024 + 1)}]})
    assert response.status_code == 200 and response.json()["accepted"]
