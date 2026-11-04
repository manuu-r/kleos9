import base64
import io
from datetime import UTC, datetime, timedelta

import pytest
from PIL import Image

from kleos.artifacts.service import ArtifactService
from kleos.artifacts.storage import BlobStore
from kleos.contracts.models import Actor, DomainError
from kleos.orchestration.engine import PlanDecision, RunScope
from kleos.orchestration.planner import ModelPlanner
from kleos.projects.store import ProjectStore


class RecordedModel:
    def __init__(self):
        self.messages = []

    def with_structured_output(self, _):
        return self

    def bind(self, **kwargs):
        return self

    async def ainvoke(self, messages):
        self.messages = messages
        return PlanDecision(question="What is the bracket's mounting-hole spacing in millimetres?")


@pytest.fixture
def images(tmp_path):
    store = ProjectStore(f"sqlite:///{tmp_path}/images.db")
    store.initialize_for_tests()
    service = ArtifactService(store, BlobStore(tmp_path / "blobs"))
    actor = Actor(tenant_id="tenant", user_id="owner")
    project = store.create(actor, "Use my bracket sketch", "create")["projectId"]
    buffer = io.BytesIO()
    Image.new("RGB", (10, 10), "white").save(buffer, "PNG")
    image = service.reference_image(
        actor,
        project,
        {
            "name": "sketch.png",
            "contentBase64": base64.b64encode(buffer.getvalue()).decode(),
        },
    )
    run = store.submit(actor, project, "Design bracket from sketch", 2, "run")
    scope = RunScope(
        actor=actor,
        project_id=project,
        run_id=run["runId"],
        revision=3,
        deadline_at=datetime.now(UTC) + timedelta(minutes=10),
    )
    return service, scope, store.context(actor, project), image


async def test_planner_receives_actual_normalized_image_with_source_identity(images):
    service, scope, context, image = images
    assert context.artifacts == []  # image reference is not an editable source file
    assert context.reference_images[0].id == image["id"]
    model = RecordedModel()
    planner = ModelPlanner(model, ["cad"], image_loader=service.planner_images)
    result = await planner.plan(scope, "Design bracket", context, [], [], None)
    assert result.question
    parts = model.messages[1]["content"]
    assert image["id"] in parts[1]["text"]
    data = base64.b64decode(parts[2]["image_url"]["url"].split(",", 1)[1])
    assert data == service.blobs.get(image["sha256"])


async def test_referenced_images_are_never_silently_dropped(images):
    _, scope, context, _ = images
    model = RecordedModel()
    with pytest.raises(DomainError, match="vision input loader"):
        await ModelPlanner(model, ["cad"]).plan(scope, "Design bracket", context, [], [], None)
    assert not model.messages


async def test_image_loading_rechecks_project_and_exact_references(images):
    service, scope, context, _ = images
    with pytest.raises(DomainError):
        await service.planner_images(
            scope.model_copy(update={"actor": Actor(tenant_id="other", user_id="owner")}), context
        )
    changed = context.model_copy(update={"reference_images": []})
    with pytest.raises(DomainError, match="selection changed"):
        await service.planner_images(scope, changed)
