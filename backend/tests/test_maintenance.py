from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from test_memory_learning import learning_env as learning_env

from kleos.auth import Principal
from kleos.contracts import DomainError
from kleos.db import (
    Artifact,
    Attempt,
    Consent,
    Dataset,
    Event,
    Invocation,
    Outbox,
    Project,
    Run,
    Source,
    Task,
    TrainedModel,
)
from kleos.domain import ControlPlane
from kleos.learning import LearningService
from kleos.maintenance import Maintenance


def test_retention_expires_transcripts_but_preserves_accepted_artifacts(learning_env):
    db, settings, store, user = learning_env
    current = datetime.now(UTC)
    old = current - timedelta(days=31)
    store.put_json("transcripts/old.json", {"prompt": "private old text"})
    store.put_json("transcripts/fresh.json", {"prompt": "current text"})
    with db.transaction() as session:
        session.add(Run(id="run", tenant_id="org", project_id="project-1", command_id="c", kind="test", revision=1))
        session.add(Task(id="task", tenant_id="org", project_id="project-1", run_id="run", lane="research",
                         kind="research", revision=1, status="succeeded", created_at=old, result={"private": "output"}))
        session.add(Attempt(id="attempt", tenant_id="org", task_id="task", number=1, fence=1, policy_revision=1,
                            provider="google", model="test", status="succeeded", context={"prompt": "secret"},
                            output={"response": "text"}, created_at=old))
        for name, timestamp in (("old", old), ("fresh", current)):
            session.add(Invocation(id=name, tenant_id="org", project_id="project-1", attempt_id="attempt",
                                   operation=name, provider="google", model="test", status="succeeded",
                                   transcript_key=f"transcripts/{name}.json", output={"response": name}, created_at=timestamp))
        session.get(Artifact, "artifact-1").created_at = current - timedelta(days=500)
    report = Maintenance(db, settings, store).retention(at=current)
    assert report["transcripts"] == 1
    with pytest.raises(FileNotFoundError):
        store.get("transcripts/old.json")
    assert store.get("transcripts/fresh.json")
    assert store.get("artifacts/project-1.txt") == b"validated example 1"
    with db.transaction() as session:
        assert session.get(Invocation, "old").transcript_key is None
        assert session.get(Attempt, "attempt").context == {"retentionExpired": True}
        assert session.get(Artifact, "artifact-1").status == "accepted"


def test_events_archive_metadata_and_expire_replay_cursor(learning_env):
    db, settings, store, user = learning_env
    current = datetime.now(UTC)
    with db.transaction() as session:
        project = session.get(Project, "project-1")
        project.event_sequence = 3
        for sequence, age in ((1, 400), (2, 100), (3, 1)):
            session.add(Event(id=f"event-{sequence}", tenant_id="org", project_id="project-1", sequence=sequence,
                              type="test.event", data={"private": "never archive event body"},
                              created_at=current - timedelta(days=age)))
    maintenance = Maintenance(db, settings, store)
    assert maintenance.retention(at=current)["events"] == 2
    with db.transaction() as session:
        assert session.get(Project, "project-1").event_floor == 2
        archive = session.scalar(select(Outbox).where(Outbox.kind == "event_archive"))
        key = archive.payload["key"]
    assert "private" not in store.get(key).decode()
    with pytest.raises(DomainError) as error:
        ControlPlane(db, settings, store).events(user, "project-1", 0)
    assert error.value.status == 410
    maintenance.retention(at=current + timedelta(days=266))
    with pytest.raises(FileNotFoundError):
        store.get(key)
    assert store.get("artifacts/project-1.txt")


def test_project_deletion_revokes_access_and_purges_derived_copies_with_lineage(learning_env):
    db, settings, store, user = learning_env
    learning = LearningService(db, settings, store)
    dataset = learning.create_dataset(user, ["project-1", "project-2"])
    learning.approve_dataset_rights(user, dataset["id"], "All text examples are licensed for training use.")
    materialized = learning.materialize_training(user, dataset["id"])
    candidate = learning.register_candidate(user, dataset["id"], {
        "baseModel": "licensed", "weightsLicense": "Apache", "recipe": "causal_lm_sft", "budgetUsd": 10, "maxGpuSeconds": 600})
    with db.transaction() as session:
        session.add(Source(id="source", tenant_id="org", project_id="project-1", revision=1,
                           uri="https://example.com", sha256="abc", text="private source", authority="research"))
    class Index:
        ids = []
        def delete_points(self, ids):
            self.ids.extend(ids)
    index = Index()
    maintenance = Maintenance(db, settings, store, memory=index)
    deleted = maintenance.request_delete(user, "project-1", 1)
    assert deleted["deleted"]
    with pytest.raises(DomainError):
        ControlPlane(db, settings, store).workspace(user, "project-1")
    with db.transaction() as session:
        assert session.get(Source, "source").revoked
        assert not session.get(Consent, "consent-1").opted_in
        assert session.get(TrainedModel, candidate["id"]).status == "retired"
        assert session.get(Dataset, dataset["id"]).status == "excluded"
    assert maintenance.purge_project("project-1")["status"] == "purged"
    assert maintenance.purge_project("project-1")["status"] == "purged"
    assert index.ids == ["source"]
    with pytest.raises(FileNotFoundError):
        store.get(materialized["key"])
    with pytest.raises(FileNotFoundError):
        store.get("artifacts/project-1.txt")
    assert store.get("artifacts/project-2.txt")
    with db.transaction() as session:
        assert session.get(Source, "source") is None
        assert session.get(Artifact, "artifact-1") is None
        assert session.get(Project, "project-1").brief == ""
        assert session.get(Dataset, dataset["id"]).manifest["projects"][0]["projectId"] == "project-1"


def test_project_erasure_requires_explicit_admin_authorization_and_expected_revision(learning_env):
    db, settings, store, user = learning_env
    maintenance = Maintenance(db, settings, store)
    with pytest.raises(DomainError, match="explicitly"):
        maintenance.purge_project("project-1")
    with pytest.raises(DomainError, match="administrator"):
        maintenance.request_delete(Principal("member", "org", "member"), "project-1", 1)
    with pytest.raises(DomainError, match="changed"):
        maintenance.request_delete(user, "project-1", 2)
    assert store.get("artifacts/project-1.txt")


def test_consent_withdrawal_excludes_and_removes_future_training_copy(learning_env):
    db, settings, store, user = learning_env
    learning = LearningService(db, settings, store)
    dataset = learning.create_dataset(user, ["project-1", "project-2"])
    learning.approve_dataset_rights(user, dataset["id"], "Training rights were reviewed by the operator.")
    materialized = learning.materialize_training(user, dataset["id"])
    with db.transaction() as session:
        session.get(Consent, "consent-1").opted_in = False
    assert Maintenance(db, settings, store).retention()["excludedDatasets"] == 1
    with pytest.raises(FileNotFoundError):
        store.get(materialized["key"])
    with db.transaction() as session:
        assert session.get(Dataset, dataset["id"]).status == "excluded"
        assert session.get(Dataset, dataset["id"]).manifest["derivedCopyPurged"]


def test_s3_erasure_deletes_versions_and_markers_only_for_exact_key(learning_env):
    db, settings, store, _ = learning_env
    calls = []
    class S3:
        def get_paginator(self, name):
            assert name == "list_object_versions"
            return self
        def paginate(self, **kwargs):
            return [{"Versions": [{"Key": "target", "VersionId": "1"}, {"Key": "target-extra", "VersionId": "2"}],
                     "DeleteMarkers": [{"Key": "target", "VersionId": "3"}]}]
        def delete_objects(self, **kwargs):
            calls.append(kwargs["Delete"]["Objects"])
            return {}
    store.s3 = S3()
    Maintenance(db, settings, store).purge_object("target")
    assert calls == [[{"Key": "target", "VersionId": "1"}, {"Key": "target", "VersionId": "3"}]]


def test_late_modal_submission_after_deletion_is_durably_cancelled(learning_env):
    from kleos.learning import ComputeLedger
    db, settings, store, user = learning_env
    ledger = ComputeLedger(db, settings)
    intent_id, _ = ledger.begin(user, "project-1", "late", "modal", {})
    cancelled = []
    maintenance = Maintenance(db, settings, store, cancel_modal=cancelled.append)
    with db.transaction() as session:
        revision = session.get(Project, "project-1").revision
    maintenance.request_delete(user, "project-1", revision)
    maintenance.purge_project("project-1")
    ledger.submitted(intent_id, "fc-late")
    with db.transaction() as session:
        assert session.scalar(select(Outbox).where(Outbox.key == f"delete-late-compute:{intent_id}"))
    maintenance.purge_project("project-1")
    assert cancelled == ["fc-late"]
    with db.transaction() as session:
        assert session.get(Project, "project-1").spent_usd == 1
        assert session.get(Project, "project-1").reserved_usd == 0


def test_deletion_cancels_training_funded_by_another_project(learning_env):
    from kleos.learning import ComputeLedger
    db, settings, store, user = learning_env
    learning = LearningService(db, settings, store)
    dataset = learning.create_dataset(user, ["project-1", "project-2"])
    learning.approve_dataset_rights(user, dataset["id"], "Explicitly licensed examples for model improvement.")
    model = learning.register_candidate(user, dataset["id"], {
        "baseModel": "model", "weightsLicense": "Apache", "recipe": "causal_lm_sft", "budgetUsd": 5, "maxGpuSeconds": 60})
    ledger = ComputeLedger(db, settings)
    intent_id, _ = ledger.begin(user, "project-2", "train", "training", {"datasetId": dataset["id"], "modelId": model["id"]})
    ledger.submitted(intent_id, "fc-training")
    cancelled = []
    maintenance = Maintenance(db, settings, store, cancel_modal=cancelled.append)
    maintenance.request_delete(user, "project-1", 1)
    maintenance.purge_project("project-1")
    assert cancelled == ["fc-training"]
    assert ledger.view(user, intent_id)["status"] == "failed"
    assert store.get("artifacts/project-2.txt")


async def test_modal_result_cannot_resurrect_data_after_project_deletion(learning_env):
    from kleos.learning import ModalCompute
    db, settings, store, user = learning_env
    store.download_url = lambda key, expires=300: "https://bucket.s3.amazonaws.com/input?signature=secret"
    with db.transaction() as session:
        session.add(Dataset(id="models", tenant_id="org", manifest={}))
        session.add(TrainedModel(id="model", dataset_id="models", status="promoted", config={}))
    class Client:
        async def spawn(self, request):
            return "fc-ready"
        async def result(self, call_id):
            with db.transaction() as session:
                revision = session.get(Project, "project-1").revision
            Maintenance(db, settings, store).request_delete(user, "project-1", revision)
            return {"output": "must not be stored"}
    compute = ModalCompute(db, settings, store, Client())
    intent = await compute.submit(user, "project-1", "inference", "model", ["artifact-1"])
    with pytest.raises(DomainError, match="not found"):
        await compute.poll(user, intent["id"])
    with pytest.raises(FileNotFoundError):
        store.get(f"compute/org/project-1/{intent['id']}/result.json")


def test_delete_api_returns_durable_accepted_receipt_and_hides_project(learning_env):
    from fastapi.testclient import TestClient

    from kleos.api import create_app
    from kleos.auth import authenticate
    db, settings, store, user = learning_env
    app = create_app(settings, db, store)
    app.dependency_overrides[authenticate] = lambda: user
    with TestClient(app) as client:
        assert client.delete("/api/v1/projects/project-1?expectedRevision=99").status_code == 409
        response = client.delete("/api/v1/projects/project-1?expectedRevision=1")
        assert response.status_code == 202 and response.json()["deleted"]
        assert client.get("/api/v1/projects/project-1/workspace").status_code == 404
        assert client.delete("/api/v1/projects/project-1?expectedRevision=1").status_code == 202
    with db.transaction() as session:
        assert len(list(session.scalars(select(Outbox).where(Outbox.kind == "delete_project")))) == 1


def test_explicit_deletion_sweeps_orphan_files_without_database_references(learning_env):
    db, settings, store, user = learning_env
    orphans = [f"{namespace}/org/project-1/lost-transaction/data.bin"
               for namespace in ("artifacts", "logs", "transcripts", "compute", "event-archives")]
    for key in orphans:
        store.put(key, b"orphan private content")
    other = "artifacts/org/project-10/retained/data.bin"
    store.put(other, b"different project")
    maintenance = Maintenance(db, settings, store)
    maintenance.request_delete(user, "project-1", 1)
    maintenance.purge_project("project-1")
    for key in orphans:
        with pytest.raises(FileNotFoundError):
            store.get(key)
    assert store.get(other) == b"different project"


def test_prefix_erasure_covers_every_version_and_preserves_neighbor_projects(learning_env):
    db, settings, store, _ = learning_env
    deleted = []
    class S3:
        def get_paginator(self, _name):
            return self
        def paginate(self, **kwargs):
            assert kwargs["Prefix"] == "artifacts/org/project-1/"
            return [{"Versions": [{"Key": "artifacts/org/project-1/orphan", "VersionId": "old"},
                                  {"Key": "artifacts/org/project-10/other", "VersionId": "neighbor"}],
                     "DeleteMarkers": [{"Key": "artifacts/org/project-1/orphan", "VersionId": "marker"}]}]
        def delete_objects(self, **kwargs):
            deleted.extend(kwargs["Delete"]["Objects"])
            return {}
    store.s3 = S3()
    Maintenance(db, settings, store).purge_prefix("artifacts/org/project-1/")
    assert {entry["VersionId"] for entry in deleted} == {"old", "marker"}


def test_erasure_keeps_only_operational_docker_cancellation_metadata():
    from types import SimpleNamespace
    job = SimpleNamespace(id="job", request={"dispatch": {
        "backend": "docker", "volume": "scoped-native-volume", "cluster": "pinned-cluster",
        "headers": {"Authorization": "private"}, "inputPath": "/private/source"},
        "reservation": {"dispatchAttempted": True, "settled": True}, "source": "private code"},
        result={"localCleaned": True, "terminationObservedAt": "timestamp", "logKey": "private-log"})
    retained = Maintenance.operational_job_request(job)
    assert retained["dispatch"] == {"backend": "docker", "volume": "scoped-native-volume", "cluster": "pinned-cluster"}
    assert retained["reservation"]["dispatchAttempted"]
    assert "private" not in str(retained)
    assert Maintenance.operational_job_result(job) == {"localCleaned": True, "terminationObservedAt": "timestamp"}
