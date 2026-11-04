import base64
import hashlib
import io

import httpx
import pytest
from PIL import Image, PngImagePlugin

from kleos.access.service import AccessService, AccessSettings
from kleos.api import create_app
from kleos.api.services import ApplicationServices
from kleos.artifacts.service import ArtifactService
from kleos.artifacts.storage import BlobStore
from kleos.contracts.models import Actor, DomainError
from kleos.projects.store import ProjectStore


@pytest.fixture
def services(tmp_path):
    store = ProjectStore(f"sqlite:///{tmp_path}/fresh.db")
    store.initialize_for_tests()
    access = AccessService(
        store, AccessSettings(secret_key="x" * 40, environment="test", development_no_login=True)
    )
    artifacts = ArtifactService(store, BlobStore(tmp_path / "files"))
    return ApplicationServices(store, access, artifacts=artifacts)


async def test_real_http_create_objective_workspace_and_events(services):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(services)), base_url="http://local"
    ) as c:
        headers = {"Idempotency-Key": "create"}
        result = await c.post(
            "/api/v1/commands",
            headers=headers,
            json={"type": "project.create", "payload": {"brief": "Existing PCB"}},
        )
        assert result.status_code == 202, result.text
        receipt = result.json()
        project = receipt["projectId"]
        again = await c.post(
            "/api/v1/commands",
            headers=headers,
            json={"type": "project.create", "payload": {"brief": "Existing PCB"}},
        )
        assert again.json() == receipt
        submit = await c.post(
            f"/api/v1/projects/{project}/objectives",
            headers={"Idempotency-Key": "route"},
            json={"objective": "Route this imported board only", "expectedRevision": 1},
        )
        assert submit.status_code == 200, submit.text
        workspace = (await c.get(f"/api/v1/projects/{project}/workspace")).json()
        assert workspace["revision"] == 2 and workspace["runs"][0]["status"] == "pending"
        assert workspace["project"]["stage"] == 0  # no mandatory full-device flow
        events = (await c.get(f"/api/v1/projects/{project}/events/history")).json()["events"]
        assert events[-1]["data"]["objective"] == "Route this imported board only"
        assert (await c.get("/api/v1/projects")).json()["projects"][0]["id"] == project
        assert (
            await c.post(
                "/api/v1/commands",
                headers={"Idempotency-Key": "unknown"},
                json={"type": "invented.operation", "payload": {}},
            )
        ).status_code == 501


async def test_existing_training_control_roundtrips_without_dispatch(services):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(services)), base_url='http://local') as client:
        created = await client.post('/api/v1/commands', headers={'Idempotency-Key': 'consent-project'},
            json={'type': 'project.create', 'payload': {'brief': 'Preserve existing project'}})
        project = created.json()['projectId']
        for revision, opted_in in ((1, True), (2, False)):
            body = {'type': 'project.setTrainingConsent', 'expectedRevision': revision,
                    'payload': {'projectId': project, 'optedIn': opted_in}}
            headers = {'Idempotency-Key': 'consent-' + str(revision)}
            changed = await client.post('/api/v1/commands', headers=headers, json=body)
            assert changed.status_code == 202
            assert (await client.post('/api/v1/commands', headers=headers, json=body)).json() == changed.json()
            view = (await client.get(f'/api/v1/projects/{project}/workspace')).json()
            assert view['trainingOptedIn'] is opted_in and view['revision'] == revision + 1
            assert view['runs'] == [] and view['artifacts'] == []


async def test_private_upload_checksum_and_image_metadata_stripping(services):
    actor = services.authenticate({}, {}, "GET")
    project = services.projects.create(actor, "Imported device", "create")["projectId"]
    content = b"board source"
    digest = hashlib.sha256(content).hexdigest()
    response = services.action(
        actor,
        "upload_artifact",
        {"projectId": project},
        {
            "file": {"path": "board.kicad_pcb", "content": content.decode(), "encoding": "utf8"},
            "sha256": digest,
            "expectedEngineeringRevision": 1,
        },
        "upload",
    )
    ident = response["artifact"]["id"]
    assert services.content(actor, "artifact_content", {"artifactId": ident}).data == content
    assert len(services.query(actor, "workspace", {"projectId": project})["artifacts"]) == 1
    with pytest.raises(DomainError):
        services.content(
            Actor(tenant_id="elsewhere", user_id=actor.user_id), "artifact_content", {"artifactId": ident}
        )
    image = Image.new("RGB", (12, 10))
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("private", "remove")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", pnginfo=metadata)
    ref = services.action(
        actor,
        "upload_reference_image",
        {"projectId": project},
        {
            "name": "sketch.png",
            "mediaType": "image/png",
            "contentBase64": base64.b64encode(buffer.getvalue()).decode(),
        },
        None,
    )
    clean = services.content(
        actor, "reference_image_content", {"projectId": project, "imageId": ref["id"]}
    ).data
    assert not Image.open(io.BytesIO(clean)).info
    other = services.projects.create(actor, "Other project", "other")["projectId"]
    with pytest.raises(DomainError):
        services.content(actor, "reference_image_content", {"projectId": other, "imageId": ref["id"]})


def test_stale_source_upload_is_fenced_by_visible_engineering_revision(services):
    actor = services.authenticate({}, {}, 'GET')
    project = services.projects.create(actor, 'Imported source', 'create')['projectId']
    body = {'file': {'path': 'part.py', 'content': 'first', 'encoding': 'utf8'},
            'sha256': hashlib.sha256(b'first').hexdigest(), 'expectedEngineeringRevision': 1}
    first = services.artifacts.upload(actor, project, body, 'first')
    view = services.views.workspace(actor, project)['project']
    assert view['engineeringRevision'] == 2
    stale = {**body, 'file': {**body['file'], 'content': 'stale'},
             'sha256': hashlib.sha256(b'stale').hexdigest()}
    with pytest.raises(DomainError, match='Engineering context changed'):
        services.artifacts.upload(actor, project, stale, 'stale')
    assert services.artifacts.upload(actor, project, body, 'first') == first
    with services.projects.transaction() as session:
        row = services.projects.authorized(session, actor, project, write=True)
        row.advance_revision(context=False)
    assert services.views.workspace(actor, project)['project']['engineeringRevision'] == 2
    accepted = services.artifacts.upload(actor, project, {**stale, 'expectedEngineeringRevision': 2}, 'current')
    assert accepted['revision'] == 4
    assert services.views.workspace(actor, project)['project']['engineeringRevision'] == 4


def test_workspace_lists_newest_objective_first_for_existing_retry_control(services):
    actor = services.authenticate({}, {}, 'GET')
    project = services.projects.create(actor, 'Existing project', 'create')['projectId']
    first = services.projects.submit(actor, project, 'First objective', 1, 'first')
    second = services.projects.submit(actor, project, 'New objective', first['revision'], 'second')
    view = services.views.workspace(actor, project)
    assert [run['id'] for run in view['runs']] == [second['runId'], first['runId']]
