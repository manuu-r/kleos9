"""Incremental source closures retain attribution and exclude obsolete build products."""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from test_execution import execution as execution

from kleos.auth import Principal
from kleos.contracts import AgentOutput, Command, GeneratedFile, JobResult
from kleos.db import Artifact, Attempt, NativeJob, Project, Run, Task, Validation
from kleos.domain import ControlPlane
from kleos.engine import EngineeringEngine
from kleos.source_closure import current_sources, reference
from kleos.source_templates import MANIFEST_SHA256, load_source_templates
from kleos.storage import digest


@pytest.fixture
def sources(execution):
    execution.settings.native_sessions_enabled = True
    execution.settings.max_native_jobs = 4
    with execution.db.transaction() as session:
        project = session.get(Project, "project")
        project.state = {"confirmed": True}
        project.interfaces = {"mechanical": {"units": "mm"}}
        project.model_policy = {lane: {"provider": "google", "model": "fixture"}
                                for lane in ["mechanics", "firmware", "application"]}
        task = session.get(Task, "task")
        task.kind, task.status = "project.generate", "running"
        session.get(Run, "run").status = "running"
        session.get(Attempt, "attempt").context = {
            "requirements": {}, "interfaces": project.interfaces, "inputArtifacts": []}
    engine = EngineeringEngine(execution.db, execution.settings, execution.store, SimpleNamespace(), execution)
    return execution, engine


def extra_attempt(sources, ident, lane="mechanics", kind="project.repair", create_attempt=True):
    execution, _engine = sources
    with execution.db.transaction() as session:
        session.add(Task(id=f"task-{ident}", tenant_id="org", project_id="project", run_id="run", lane=lane,
                         kind=kind, revision=1, fence=1, status="running" if create_attempt else "queued"))
        if create_attempt:
            project = session.get(Project, "project")
            session.add(Attempt(id=ident, tenant_id="org", task_id=f"task-{ident}", number=1,
                                fence=1, policy_revision=1, provider="google", model="repair-model",
                                context={"requirements": deepcopy(project.requirements),
                                         "interfaces": deepcopy(project.interfaces), "inputArtifacts": []}))
    return ident if create_attempt else f"task-{ident}"


def closure(sources, lane="mechanics"):
    execution, _engine = sources
    with execution.db.transaction() as session:
        return [reference(row) for row in current_sources(session, session.get(Project, "project"), lane)]


def test_single_file_repair_retains_other_source_and_original_attribution(sources):
    execution, engine = sources
    engine.store_candidate("attempt", AgentOutput(summary="Initial source pair", files=[
        GeneratedFile(path="enclosure.py", content="original main source"),
        GeneratedFile(path="dimensions.py", content="WIDTH = 40")]))
    original = {ref["name"]: ref for ref in closure(sources)}
    # Old products are diagnostics/manufacturing candidates, never reusable input sources.
    engine.store_candidate("attempt", AgentOutput(summary="Old build output", files=[
        GeneratedFile(path="native/model.step", content="old step")]), _provenance={"nativeJobId": "old-job"})
    ident = extra_attempt(sources, "repair")
    engine.store_candidate(ident, AgentOutput(summary="Change only geometry", files=[
        GeneratedFile(path="enclosure.py", content="updated main source")]))
    revised = {ref["name"]: ref for ref in closure(sources)}
    assert revised["dimensions.py"] == original["dimensions.py"]
    assert revised["dimensions.py"]["provenance"]["model"] != "repair-model"
    assert revised["enclosure.py"]["id"] != original["enclosure.py"]["id"]
    with execution.db.transaction() as session:
        assert session.get(Artifact, original["enclosure.py"]["id"]).status == "stale"
        assert session.scalar(select(Artifact).where(Artifact.name == "native/model.step")).status == "stale"
        context = session.get(Attempt, ident).context
        assert context["sourceArtifacts"] == list(revised.values())
    job_id = execution.submit(ident, "build")
    with execution.db.transaction() as session:
        job = session.get(NativeJob, job_id)
        assert job.request["inputArtifacts"] == list(revised.values())
        assert {ref["name"] for ref in job.request["inputArtifacts"]} == {"dimensions.py", "enclosure.py"}
        rows = [session.get(Artifact, ref["id"]) for ref in job.request["inputArtifacts"]]
        assert {row.attempt_id for row in rows} == {"attempt", "repair"}
    payload = execution.payload(job_id, execution._token(job_id))
    assert {ref["name"] for ref in payload["inputArtifacts"]} == {"dimensions.py", "enclosure.py"}


def test_repeating_unchanged_file_never_reattributes_or_duplicates_source(sources):
    execution, engine = sources
    engine.store_candidate("attempt", AgentOutput(summary="Initial", files=[GeneratedFile(path="params.py", content="X=1")]))
    original = {item["name"]: item for item in closure(sources)}["params.py"]
    ident = extra_attempt(sources, "repair")
    engine.store_candidate(ident, AgentOutput(summary="Unchanged", files=[GeneratedFile(path="params.py", content="X=1")]))
    assert {item["name"]: item for item in closure(sources)}["params.py"] == original
    with execution.db.transaction() as session:
        assert session.query(Artifact).filter(Artifact.name == "params.py").count() == 1


@pytest.mark.parametrize("lane,override_name,override", [
    ("firmware", "src/main.c", "/* User-specific application */\nint main(void) {return 0;}\n"),
    ("application", "lib/main.dart", "// User-specific companion\nvoid main() {}\n"),
])
def test_template_seeding_and_context_preserve_existing_source_overrides(sources, lane, override_name, override):
    execution, engine = sources
    with execution.db.transaction() as session:
        project = session.get(Project, "project")
        project.requirements = {"validationProfile": "watch-telemetry-v1"}
    first = extra_attempt(sources, "custom", lane)
    engine.store_candidate(first, AgentOutput(summary="User source", files=[GeneratedFile(path=override_name, content=override)]))
    old = {item["name"]: item for item in closure(sources, lane)}[override_name]
    task_id = extra_attempt(sources, "template", lane, create_attempt=False)
    attempt_id = engine.begin_attempt(task_id)
    assert attempt_id
    template_files = load_source_templates({"validationProfile": "watch-telemetry-v1"}, lane)
    current = {item["name"]: item for item in closure(sources, lane)}
    assert set(current) == {file["name"] for file in template_files}
    assert current[override_name] == old
    with execution.db.transaction() as session:
        attempt = session.get(Attempt, attempt_id)
        assert attempt.context["baseFiles"] == template_files
        assert attempt.context["sourceArtifacts"] == list(current.values())
        overrides = [item for item in attempt.context["previousSourceFiles"] if item["name"] == override_name]
        assert overrides == [{"name": override_name, "sha256": digest(override.encode()), "content": override}]
        for name, ref in current.items():
            row = session.get(Artifact, ref["id"])
            if name == override_name:
                assert row.attempt_id == first
            else:
                assert row.provenance["origin"] == "qualifiedTemplate"
                assert row.provenance["templateManifestSha256"] == MANIFEST_SHA256
                assert row.provenance["provider"] is None
    # A later repair reuses these exact files and does not create template duplicates.
    followup_task = extra_attempt(sources, "followup", lane, create_attempt=False)
    followup = engine.begin_attempt(followup_task)
    assert closure(sources, lane) == list(current.values())
    with execution.db.transaction() as session:
        context = session.get(Attempt, followup).context
        for file in context["previousSourceFiles"]:
            if file["name"] != override_name:
                assert file["baseFile"] == file["name"]
                assert "content" not in file


def test_native_session_patched_sources_reusable_by_next_build_and_native_retry(sources):
    execution, engine = sources
    engine.store_candidate("attempt", AgentOutput(summary="Initial", files=[
        GeneratedFile(path="enclosure.py", content="original geometry"),
        GeneratedFile(path="dimensions.py", content="WIDTH = 40")]))
    before = {item["name"]: item for item in closure(sources)}
    job_id = execution.submit("attempt", "session")
    patched = "patched geometry after genuine native diagnostics"
    engine.finish_native(job_id, JobResult(status="succeeded", files=[
        GeneratedFile(path="enclosure.py", content=patched),
        GeneratedFile(path="native/model.step", content="candidate step")], measurements={
            "nativeSession": {"sourceHashes": {"enclosure.py": digest(patched.encode()),
                                                "dimensions.py": digest(b"WIDTH = 40")}}}))
    after = {item["name"]: item for item in closure(sources)}
    assert after["dimensions.py"] == before["dimensions.py"]
    assert after["enclosure.py"]["provenance"]["artifactRole"] == "source"
    assert after["enclosure.py"]["provenance"]["nativeJobId"] == job_id
    with execution.db.transaction() as session:
        assert session.get(NativeJob, job_id).result["sourceArtifacts"] == list(after.values())
        assert session.query(Validation).count() == 0
        assert session.get(Project, "project").accepted_release_id is None
        revision = session.get(Project, "project").revision
    control = ControlPlane(execution.db, execution.settings, execution.store)
    response = control.command(Principal("owner", "org", "owner"), Command(type="project.retryNative",
        expected_revision=revision, payload={"projectId": "project", "jobId": job_id}), "reuse-session-sources")
    with execution.db.transaction() as session:
        task = session.scalar(select(Task).where(Task.run_id == response["runId"]))
        retry_attempt = session.scalar(select(Attempt).where(Attempt.task_id == task.id))
        retry_attempt_id = retry_attempt.id
        assert retry_attempt.context["nativeRetry"]["inputArtifacts"] == list(after.values())
    retry_job_id = execution.submit(retry_attempt_id, "build")
    with execution.db.transaction() as session:
        request = session.get(NativeJob, retry_job_id).request
        assert request["inputArtifacts"] == list(after.values())
        assert all(not ref["name"].startswith("native/") for ref in request["inputArtifacts"])
        assert session.query(Validation).count() == 0


def test_ordinary_build_reuses_native_session_source_without_loading_old_outputs(sources):
    execution, engine = sources
    job_id = execution.submit("attempt", "session")
    patched = "updated source from native edit loop"
    engine.finish_native(job_id, JobResult(status="succeeded", files=[
        GeneratedFile(path="enclosure.py", content=patched),
        GeneratedFile(path="native/model.step", content="old manufacturing output")], measurements={
            "nativeSession": {"sourceHashes": {"enclosure.py": digest(patched.encode())}}}))
    expected = closure(sources)
    ident = extra_attempt(sources, "next-build")
    # No newly generated files are required to rebuild the exact current sources.
    engine.store_candidate(ident, AgentOutput(summary="Reuse existing source closure", files=[]))
    next_job = execution.submit(ident, "build")
    with execution.db.transaction() as session:
        assert session.get(NativeJob, next_job).request["inputArtifacts"] == expected
        assert expected[0]["name"] == "enclosure.py"
        assert expected[0]["provenance"]["artifactRole"] == "source"
        assert session.scalar(select(Artifact).where(Artifact.name == "native/model.step")).status == "stale"
        assert session.query(Validation).count() == 0
