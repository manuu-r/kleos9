"""Real local Docker adapter smoke in an isolated temporary ledger, without inference."""
import base64
import json
import sys
import tempfile
import time
from pathlib import Path

from kleos.config import Settings
from kleos.db import Artifact, Attempt, Base, Database, NativeJob, Organization, Project, Run, Task
from kleos.execution import DockerExecution
from kleos.storage import ArtifactStore, digest


def main():
    lane = sys.argv[1] if len(sys.argv) > 1 else "mechanics"
    board = sys.argv[2] if len(sys.argv) > 2 else "native_sim"
    if lane not in {"mechanics", "firmware", "application"}:
        raise ValueError("Unsupported trusted fixture")
    with tempfile.TemporaryDirectory(prefix="kleos-docker-smoke-") as temporary:
        settings = Settings(
            _env_file=None, environment="test", execution_backend="docker",
            database_url=f"sqlite:///{temporary}/ledger.db", session_key="isolated-smoke-only",
            local_artifact_dir=Path(temporary) / "artifacts",
            docker_native_images={lane: f"kleos-{lane}:development"},
            project_budget_usd=10, daily_budget_usd=10,
        )
        db, store = Database(settings.database_url), ArtifactStore(settings)
        Base.metadata.create_all(db.engine)
        if lane == "mechanics":
            sources = {"enclosure.py": b"from build123d import Box\nassembly = Box(40, 30, 10) - Box(38, 28, 8)\n"}
        else:
            template = Path("/fixtures/watch-telemetry-v1") / lane
            sources = {str(path.relative_to(template)): path.read_bytes()
                       for path in template.rglob("*") if path.is_file()}
            if not sources:
                raise ValueError("Mount the checked-in templates read-only at /fixtures")
        with db.transaction() as session:
            session.add(Organization(id="org", name="Local smoke"))
            session.add(Project(id="project", tenant_id="org", owner_id="fixture", name="CAD fixture",
                                brief="Trusted engineering fixture", budget_usd=10,
                                interfaces={"firmware": {"board": board}}))
            session.add(Run(id="run", tenant_id="org", project_id="project", command_id="fixture",
                            kind="build", revision=1))
            session.add(Task(id="task", tenant_id="org", project_id="project", run_id="run",
                             lane=lane, kind="build", revision=1, fence=1))
            session.add(Attempt(id="attempt", tenant_id="org", task_id="task", number=1, fence=1,
                                policy_revision=1, provider="fixture", model="no-inference", context={}))
            for number, (name, source) in enumerate(sources.items()):
                key = "fixture/" + name
                store.put(key, source)
                session.add(Artifact(id=f"source-{number}", tenant_id="org", project_id="project", task_id="task",
                                     attempt_id="attempt", revision=1, lane=lane, name=name,
                                     key=key, sha256=digest(source), size=len(source)))
        execution = DockerExecution(db, settings, store)
        job_id = execution.submit("attempt", "build")
        execution.dispatch_pending()
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            completions = execution.reconcile()
            if completions:
                returned_id, result = completions[0]
                assert returned_id == job_id, "Wrong job completion"
                if result.status != "succeeded":
                    raise RuntimeError(json.dumps(result.model_dump(mode="json", exclude={"files"})))
                if lane == "mechanics":
                    assert {item.path for item in result.files} == {"native/outputs/model.step", "native/outputs/model.stl"}
                elif lane == "firmware":
                    assert any(item.path.endswith(".elf") for item in result.files)
                else:
                    assert any(item.path.endswith("main.dart.js") for item in result.files)
                measured = {item.path: len(base64.b64decode(item.content)) for item in result.files}
                with db.transaction() as session:
                    session.get(NativeJob, job_id).status = "succeeded"
                execution.reconcile()
                print(json.dumps({"jobId": job_id, "lane": lane, "board": board if lane == "firmware" else None,
                                  "status": result.status, "realOutputBytes": measured}))
                return
            time.sleep(1)
        execution.cancel_run("run")
        raise TimeoutError("Real local CAD smoke exceeded its deadline")


if __name__ == "__main__":
    main()
