"""Native fairness and pinned resource admission, using fake ECS/Docker transports."""
import json
import os
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from test_execution import FakeECS
from test_execution import execution as execution

from kleos.config import Settings
from kleos.db import Attempt, Base, Database, NativeJob, Organization, Project, Run, Task, now
from kleos.execution import DockerExecution, ECSExecution


def pending(execution, project, count=1):
    execution.settings.native_sessions_enabled = True
    with execution.db.transaction() as session:
        if not session.get(Project, project):
            session.add(Project(id=project, tenant_id="org", owner_id="owner", name=project,
                                brief="Admission fixture", budget_usd=100))
            session.flush()
        session.add(Run(id=f"run-{project}", tenant_id="org", project_id=project, command_id="fixture",
                        kind="project.generate", revision=1, status="running"))
        session.flush()
        for index in range(count):
            task_id, attempt_id = f"t-{project}-{index}", f"a-{project}-{index}"
            session.add(Task(id=task_id, tenant_id="org", project_id=project, run_id=f"run-{project}",
                             lane="mechanics", kind="project.generate", revision=1, fence=1, status="running"))
            session.flush()
            session.add(Attempt(id=attempt_id, tenant_id="org", task_id=task_id, number=1, fence=1,
                                policy_revision=1, provider="fixture", model="fixture", context={}))
    return [execution.submit(f"a-{project}-{index}", "session") for index in range(count)]


def active_by_project(execution):
    with execution.db.transaction() as session:
        return Counter(session.scalars(select(Task.project_id).join(NativeJob, NativeJob.task_id == Task.id)
                                       .where(NativeJob.status.in_(["submitted", "running", "dispatching"]))))


def test_default_native_admission_limits_are_eight_global_four_per_project(monkeypatch):
    monkeypatch.delenv("KLEOS_MAX_NATIVE_JOBS", raising=False)
    monkeypatch.delenv("KLEOS_MAX_NATIVE_JOBS_PER_PROJECT", raising=False)
    settings = Settings(_env_file=None)
    assert settings.max_native_jobs == 8
    assert settings.max_native_jobs_per_project == 4


def test_least_active_projects_share_slots_instead_of_monopolizing_fifo(execution):
    execution.settings.max_native_jobs = 8
    execution.settings.max_native_jobs_per_project = 4
    for name in ["alpha", "beta", "gamma"]:
        pending(execution, name, 6)
    execution.dispatch_pending()
    counts = active_by_project(execution)
    assert sum(counts.values()) == 8
    assert set(counts) == {"alpha", "beta", "gamma"}
    assert max(counts.values()) - min(counts.values()) <= 1
    assert len(execution.ecs.calls) == 8


def test_saturated_oldest_project_does_not_block_another_project(execution):
    execution.settings.max_native_jobs, execution.settings.max_native_jobs_per_project = 8, 4
    oldest = pending(execution, "alpha", 5)
    for job_id in oldest[:4]:
        with execution.db.transaction() as session:
            session.get(NativeJob, job_id).status = "running"
    other = pending(execution, "beta")[0]
    execution.dispatch_pending()
    with execution.db.transaction() as session:
        assert session.get(NativeJob, oldest[4]).status == "pending"
        assert session.get(NativeJob, other).status == "submitted"
    assert active_by_project(execution) == {"alpha": 4, "beta": 1}


@pytest.mark.parametrize("status,dispatched", [("dispatching", True), ("cancelled", True), ("cancelled", False)])
def test_uncertain_and_cancelled_capacity_counts_until_termination_is_observed(execution, status, dispatched):
    execution.settings.max_native_jobs, execution.settings.max_native_jobs_per_project = 1, 1
    occupied = pending(execution, "alpha")[0]
    queued = pending(execution, "beta")[0]
    with execution.db.transaction() as session:
        job = session.get(NativeJob, occupied)
        job.status = status
        job.request = {**job.request, "reservation": {**job.request["reservation"], "dispatchAttempted": dispatched},
                       # Capacity remains conservative across backend migrations.
                       "dispatch": {**job.request["dispatch"], "backend": "docker"}}
    execution.dispatch_pending()
    with execution.db.transaction() as session:
        assert session.get(NativeJob, queued).status == ("submitted" if status == "cancelled" and not dispatched else "pending")
        if status == "cancelled" and dispatched:
            session.get(NativeJob, occupied).result = {"terminationObservedAt": now().isoformat()}
    if status == "cancelled" and dispatched:
        execution.dispatch_pending()
        with execution.db.transaction() as session:
            assert session.get(NativeJob, queued).status == "submitted"


def test_project_cap_also_counts_dispatched_cancellation_tombstones(execution):
    execution.settings.max_native_jobs, execution.settings.max_native_jobs_per_project = 8, 1
    jobs = pending(execution, "alpha", 2)
    with execution.db.transaction() as session:
        job = session.get(NativeJob, jobs[0])
        job.status = "cancelled"
        job.request = {**job.request, "reservation": {**job.request["reservation"], "dispatchAttempted": True}}
    other = pending(execution, "beta")[0]
    execution.dispatch_pending()
    with execution.db.transaction() as session:
        assert session.get(NativeJob, jobs[1]).status == "pending"
        assert session.get(NativeJob, other).status == "submitted"


def test_expired_and_stale_pending_work_cannot_consume_new_capacity(execution):
    execution.settings.max_native_jobs, execution.settings.max_native_jobs_per_project = 8, 4
    old = pending(execution, "alpha", 2)
    queued = pending(execution, "beta")[0]
    with execution.db.transaction() as session:
        session.get(NativeJob, old[0]).expires_at = now() - timedelta(seconds=1)
        task = session.get(Task, session.get(NativeJob, old[1]).task_id)
        task.fence += 1
    execution.dispatch_pending()
    with execution.db.transaction() as session:
        assert session.get(NativeJob, old[0]).status == "pending"
        assert session.get(NativeJob, old[1]).status == "cancelled"
        assert session.get(NativeJob, queued).status == "submitted"


def test_ecs_resources_are_pinned_before_dispatch_and_stable_after_config_change(execution):
    original = {"cpuUnits": 2048, "memoryMiB": 4096, "tmpMiB": 1024,
                "pidsLimit": 128, "ephemeralStorageGiB": 40}
    execution.settings.native_resource_profiles["mechanics"] = original
    job = execution.submit("attempt", "build")
    execution.settings.native_resource_profiles["mechanics"] = {
        "cpuUnits": 4096, "memoryMiB": 8192, "tmpMiB": 2048,
        "pidsLimit": 256, "ephemeralStorageGiB": 60}
    execution.dispatch_pending()
    with execution.db.transaction() as session:
        assert session.get(NativeJob, job).request["resources"] == original
    call = execution.ecs.calls[0]
    assert call["overrides"]["cpu"] == "2048" and call["overrides"]["memory"] == "4096"


def test_docker_limits_reuse_persisted_profile_and_keep_isolation(execution, tmp_path):
    resources = {"cpuUnits": 2048, "memoryMiB": 4096, "tmpMiB": 1024,
                 "pidsLimit": 128, "ephemeralStorageGiB": 40}
    settings = execution.settings.model_copy(update={
        "execution_backend": "docker", "docker_jobs_dir": tmp_path / "jobs",
        "docker_native_images": {"mechanics": "fixture-mechanics"},
        "native_resource_profiles": {"_default": resources, "mechanics": resources}})
    created = []

    def docker(request):
        if "/images/" in request.url.path:
            return httpx.Response(200, json={"Id": "sha256:" + "b" * 64})
        if request.url.path.endswith("/json"):
            return httpx.Response(404, json={})
        if request.url.path.endswith("/containers/create"):
            created.append(json.loads(request.content))
            return httpx.Response(201, json={"Id": "fixture-container"})
        if request.url.path.endswith("/start"):
            return httpx.Response(204)
        pytest.fail("Unexpected fake Docker request")

    client = httpx.Client(transport=httpx.MockTransport(docker), base_url="http://docker/v1.45")
    native = DockerExecution(execution.db, settings, execution.store, client)
    job_id = native.submit("attempt", "build")
    settings.native_resource_profiles["mechanics"] = {
        **resources, "cpuUnits": 4096, "memoryMiB": 8192, "tmpMiB": 2048, "pidsLimit": 256}
    native.dispatch_pending()
    assert len(created) == 1
    limits = created[0]["HostConfig"]
    assert limits["Memory"] == 4096 * 1024**2
    assert limits["NanoCpus"] == 2_000_000_000
    assert limits["PidsLimit"] == 128
    assert limits["Tmpfs"]["/tmp"] == f"rw,exec,nosuid,size={1024 * 1024**2}"
    assert limits["NetworkMode"] == "none"
    assert limits["SecurityOpt"] == ["no-new-privileges:true"]
    assert limits["ReadonlyRootfs"] is True
    with execution.db.transaction() as session:
        assert session.get(NativeJob, job_id).request["resources"] == resources


@pytest.mark.skipif(not os.getenv("KLEOS_NATIVE_CAPACITY_TEST_DATABASE_URL"),
                    reason="Explicit disposable PostgreSQL URL required for concurrent row/advisory lock drill")
def test_postgresql_concurrent_dispatchers_never_oversubscribe(execution):
    url = make_url(os.environ["KLEOS_NATIVE_CAPACITY_TEST_DATABASE_URL"])
    if url.get_backend_name() != "postgresql" or "test" not in (url.database or "").lower():
        pytest.fail("Capacity drill requires a dedicated PostgreSQL database named for tests")
    admin = Database(url.render_as_string(hide_password=False))
    schema = "native_capacity_" + uuid4().hex
    isolated = None
    try:
        with admin.engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        isolated = Database(url.update_query_dict({"options": f"-csearch_path={schema}"})
                            .render_as_string(hide_password=False))
        Base.metadata.create_all(isolated.engine)
        with isolated.transaction() as session:
            session.add(Organization(id="org", name="Isolated capacity drill"))
        native = ECSExecution(isolated, execution.settings, execution.store, FakeECS())
        native.settings.max_native_jobs, native.settings.max_native_jobs_per_project = 8, 4
        for project in ["alpha", "beta", "gamma"]:
            pending(native, project, 4)
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda _: native.dispatch_pending(), range(4)))
        assert sum(active_by_project(native).values()) == 8
        assert max(active_by_project(native).values()) <= 4
        assert len({call["clientToken"] for call in native.ecs.calls}) == len(native.ecs.calls) == 8
    finally:
        if isolated:
            isolated.close()
        with admin.engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin.close()
