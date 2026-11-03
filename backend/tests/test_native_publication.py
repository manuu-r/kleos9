import hashlib
from datetime import UTC, datetime, timedelta

import pytest

from kleos.artifacts.service import Artifact, ArtifactService
from kleos.artifacts.storage import BlobStore
from kleos.contracts.models import Actor, DomainError, TaskResult, TaskSpec
from kleos.orchestration.engine import RunScope
from kleos.projects.graph_repository import GraphRepository
from kleos.projects.store import Project, ProjectStore


@pytest.fixture
def native(tmp_path):
    store = ProjectStore(f"sqlite:///{tmp_path}/native.db")
    store.initialize_for_tests()
    service = ArtifactService(store, BlobStore(tmp_path / "blobs"))
    actor = Actor(tenant_id="tenant", user_id="owner")
    project = store.create(actor, "Replace bracket only", "create")["projectId"]
    inputs = {}
    for path, content in [("cad/bracket.py", "old bracket"), ("pcb/board.kicad_pcb", "unchanged board")]:
        result = service.upload(
            actor,
            project,
            {
                "file": {"path": path, "content": content},
                "sha256": hashlib.sha256(content.encode()).hexdigest(),
                "expectedEngineeringRevision": store.context(actor, project).revision,
            },
            path,
        )
        inputs[path] = result["artifact"]
    run = store.submit(actor, project, "Replace bracket", 3, "run")
    scope = RunScope(
        actor=actor,
        project_id=project,
        run_id=run["runId"],
        revision=4,
        deadline_at=datetime.now(UTC) + timedelta(minutes=10),
    )
    task = TaskSpec(
        id="bracket",
        objective="Replace bracket from scratch",
        capability="cad",
        allowed_paths=["cad/bracket.py"],
    )
    files = [
        {
            "path": "cad/bracket.py",
            "content": b"new bracket",
            "role": "source",
            "expectedSha256": inputs["cad/bracket.py"]["sha256"],
        },
        {"path": "outputs/bracket.step", "content": b"STEP", "role": "output"},
    ]
    provenance = {"nativeJobId": "native-one", "imageDigest": "sha256:" + "a" * 64, "requestHash": "b" * 64}
    return service, scope, task, files, provenance, inputs


async def test_candidates_keep_exact_untouched_sources_and_replay_after_restart(native):
    service, scope, task, files, provenance, inputs = native
    refs = await service.publish_native(scope, task, files, provenance)
    reopened = ArtifactService(ProjectStore(str(service.projects.engine.url)), service.blobs)
    assert await reopened.publish_native(scope, task, files, provenance) == refs
    with service.projects.transaction() as s:
        project = s.get(Project, scope.project_id)
        assert project.revision == 4
        assert project.data["sourceManifest"] == {path: row["id"] for path, row in inputs.items()}
        output = s.get(Artifact, refs[1].id)
        assert output.status == "candidate"
        assert output.details["sourceClosure"] == {
            "cad/bracket.py": refs[0].id,
            "pcb/board.kicad_pcb": inputs["pcb/board.kicad_pcb"]["id"],
        }
    events = service.projects.events(scope.actor, scope.project_id)
    assert sum(e["payload"].get("status") == "candidate" for e in events) == 2
    files[0]["content"] = b"changed replay"
    with pytest.raises(DomainError, match="changed during replay"):
        await service.publish_native(scope, task, files, provenance)


async def test_cancelled_run_cannot_publish_or_replay_candidates(native):
    service, scope, task, files, provenance, _ = native
    await service.publish_native(scope, task, files, provenance)
    GraphRepository(service.projects).cancel(scope.actor, scope.project_id, scope.run_id)
    with pytest.raises(DomainError, match="no longer active"):
        await service.publish_native(scope, task, files, provenance)


async def test_dependency_inputs_require_registered_exact_artifacts(native):
    service, scope, task, files, provenance, inputs = native
    refs = await service.publish_native(scope, task, files, provenance)
    repository = GraphRepository(service.projects)
    with pytest.raises(DomainError, match="authorized task input"):
        await repository.resolve_artifacts(scope, [refs[0].id])
    with pytest.raises(DomainError, match="changed artifact"):
        await repository.publish(
            scope,
            TaskResult(
                task_id=task.id,
                status="succeeded",
                summary="Forged",
                artifacts=[refs[0].model_copy(update={"sha256": "f" * 64})],
            ),
        )
    await repository.publish(
        scope, TaskResult(task_id=task.id, status="succeeded", summary="Candidate", artifacts=refs)
    )
    selected = await repository.resolve_artifacts(scope, [refs[0].id, inputs["pcb/board.kicad_pcb"]["id"]])
    assert selected[0] == refs[0]
    assert selected[1].sha256 == inputs["pcb/board.kicad_pcb"]["sha256"]


@pytest.mark.parametrize("change", ["outside", "baseline", "export", "media"])
async def test_native_publication_rejects_invalid_scope_or_baseline(native, change):
    service, scope, task, files, provenance, _ = native
    if change == "outside":
        files[0]["path"] = "pcb/board.kicad_pcb"
    elif change == "baseline":
        files[0]["expectedSha256"] = None
    elif change == "export":
        files[1]["path"] = "pcb/board.kicad_pcb"
    else:
        files[1]["mediaType"] = "application/test\r\nheader: bad"
    with pytest.raises(DomainError):
        await service.publish_native(scope, task, files, provenance)
    assert all(
        e["payload"].get("status") != "candidate"
        for e in service.projects.events(scope.actor, scope.project_id)
    )
