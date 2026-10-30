import hashlib
import json

import pytest
from qdrant_client import QdrantClient

from kleos.auth import Principal
from kleos.config import Settings
from kleos.contracts import DomainError
from kleos.db import (
    Artifact,
    Base,
    Consent,
    DailyBudget,
    Database,
    Dataset,
    Organization,
    Project,
    Release,
    Source,
    TrainedModel,
)
from kleos.learning import ComputeLedger, LearningService, ModalCompute
from kleos.memory import GeminiEmbedder, MemoryIndex
from kleos.storage import ArtifactStore


@pytest.fixture
def learning_env(tmp_path, monkeypatch):
    class EmptyCheckpointClient:
        def __init__(self, **kwargs):
            pass
        def list_workflows(self, **kwargs):
            return []
        def destroy(self):
            pass
    monkeypatch.setattr("kleos.checkpoint_retention.DBOSClient", EmptyCheckpointClient)
    db = Database(f"sqlite:///{tmp_path}/learning.db")
    Base.metadata.create_all(db.engine)
    settings = Settings(environment="test", local_artifact_dir=tmp_path / "artifacts",
                        project_budget_usd=100, daily_budget_usd=1000)
    store = ArtifactStore(settings)
    user = Principal("owner", "org", "owner")
    with db.transaction() as session:
        session.add(Organization(id="org", name="Test"))
        for number in (1, 2):
            project_id, artifact_id = f"project-{number}", f"artifact-{number}"
            content = f"validated example {number}".encode()
            key, sha = f"artifacts/{project_id}.txt", hashlib.sha256(content).hexdigest()
            store.put(key, content)
            session.add(Project(id=project_id, tenant_id="org", owner_id="owner", name="Watch", brief="Watch",
                                budget_usd=100, accepted_release_id=f"release-{number}"))
            session.add(Artifact(id=artifact_id, tenant_id="org", project_id=project_id, revision=1,
                                 lane="mechanics", name="enclosure.py", key=key, sha256=sha,
                                 size=len(content), status="accepted"))
            session.add(Release(id=f"release-{number}", tenant_id="org", project_id=project_id, revision=1,
                                mode="digital", manifest={"artifacts": {artifact_id: sha}}, approved_by="owner"))
            session.add(Consent(id=f"consent-{number}", tenant_id="org", project_id=project_id,
                                actor_id="owner", opted_in=True))
    yield db, settings, store, user
    db.close()


class Embedder:
    def __init__(self):
        self.calls = 0
    def embed(self, text, query=False):
        self.calls += 1
        return [float(hashlib.sha256(text.encode()).digest()[0]) + 1] + [1.0] * 767


@pytest.mark.parametrize("model", ["gemini-embedding-2", "gemini-embedding-001"])
def test_embedding_request_format_matches_model_generation(monkeypatch, model):
    from types import SimpleNamespace

    from google import genai
    from pydantic import SecretStr

    calls = []
    class Client:
        def __init__(self, **kwargs):
            self.models = self
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def embed_content(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(embeddings=[SimpleNamespace(values=[1.0] * 768)])
    monkeypatch.setattr(genai, "Client", Client)
    embedder = GeminiEmbedder(Settings(_env_file=None, environment="test", embedding_model=model,
                                      gemini_api_key=SecretStr("fixture-key")))
    embedder.embed("battery dimensions")
    embedder.embed("battery dimensions", query=True)
    assert all(c["config"].output_dimensionality == 768 and c["config"].auto_truncate is None for c in calls)
    if model == "gemini-embedding-2":
        assert [c["contents"] for c in calls] == ["title: none | text: battery dimensions",
                                                  "task: search result | query: battery dimensions"]
        assert all(c["config"].task_type is None for c in calls)
    else:
        assert all(c["contents"] == "battery dimensions" for c in calls)
        assert [c["config"].task_type for c in calls] == ["RETRIEVAL_DOCUMENT", "RETRIEVAL_QUERY"]


def test_memory_checks_database_authority_even_before_index_deletion(learning_env):
    db, settings, _, user = learning_env
    with db.transaction() as session:
        session.add(Source(id="11111111-1111-1111-1111-111111111111", tenant_id="org",
                           project_id="project-1", revision=1, uri="https://example.com/data", sha256="abc",
                           text="Battery dimensions are 20 by 15 by 4 millimetres", authority="research"))
    embedder = Embedder()
    index = MemoryIndex(db, settings, embedder, QdrantClient(":memory:"))
    index.index("11111111-1111-1111-1111-111111111111")
    found = index.retrieve(user, "project-1", "battery dimensions")
    assert found["sources"][0]["authority"] == "research"
    assert found["sources"][0]["sha256"] == "abc"
    index.revoke(user, "project-1", "11111111-1111-1111-1111-111111111111")
    assert index.retrieve(user, "project-1", "battery dimensions")["sources"] == []
    assert embedder.calls == 2  # identical query vector is reused, never re-billed.
    with db.transaction() as session:
        assert session.get(Project, "project-1").spent_usd == pytest.approx(settings.embedding_reservation_usd * 2)
    with pytest.raises(DomainError, match="not found"):
        index.retrieve(Principal("intruder", "other", "owner"), "project-1", "secret")


def test_embedding_allowance_and_input_ceiling_fail_before_dispatch(learning_env):
    db, settings, _, user = learning_env
    embedder = Embedder()
    index = MemoryIndex(db, settings, embedder, QdrantClient(":memory:"))
    with pytest.raises(DomainError, match="6000"):
        index._embed(user, "project-1", "x" * 6001, False, 1)
    settings.embedding_reservation_usd = .000001
    with pytest.raises(DomainError, match="maximum request cost"):
        index._embed(user, "project-1", "battery", False, 1)
    assert embedder.calls == 0
    with db.transaction() as session:
        assert session.get(Project, "project-1").reserved_usd == 0


def test_memory_does_not_reuse_evidence_for_new_revision(learning_env):
    db, settings, _, user = learning_env
    with db.transaction() as session:
        session.add(Source(id="22222222-2222-2222-2222-222222222222", tenant_id="org",
                           project_id="project-1", revision=1, uri="https://example.com", sha256="old",
                           text="Old dimensions", authority="research"))
    index = MemoryIndex(db, settings, Embedder(), QdrantClient(":memory:"))
    index.index("22222222-2222-2222-2222-222222222222")
    with db.transaction() as session:
        session.get(Project, "project-1").engineering_revision = 2
    assert index.retrieve(user, "project-1", "dimensions")["sources"] == []


def test_compute_reservations_survive_ambiguous_submission_and_settle_once(learning_env):
    db, settings, _, user = learning_env
    ledger = ComputeLedger(db, settings)
    intent_id, fresh = ledger.begin(user, "project-1", "stable", "modal", {"model": "one"})
    assert fresh
    with pytest.raises(DomainError, match="uncertain"):
        ledger.begin(user, "project-1", "stable", "modal", {"model": "one"})
    with pytest.raises(DomainError, match="different"):
        ledger.begin(user, "project-1", "stable", "modal", {"model": "two"})
    ledger.submitted(intent_id, "fc-123")
    assert ledger.begin(user, "project-1", "stable", "modal", {"model": "one"}) == (intent_id, False)
    ledger.finish(intent_id, {"result": "candidate"})
    ledger.finish(intent_id, {"result": "candidate"})
    with db.transaction() as session:
        assert session.get(Project, "project-1").reserved_usd == 0
        assert session.get(Project, "project-1").spent_usd == 1
        assert session.query(DailyBudget).one().spent == 1


def test_no_customer_dataset_without_current_consent_and_accepted_artifacts(learning_env):
    db, settings, store, user = learning_env
    service = LearningService(db, settings, store)
    with db.transaction() as session:
        session.get(Consent, "consent-2").opted_in = False
    with pytest.raises(DomainError, match="opt in"):
        service.create_dataset(user, ["project-1", "project-2"])
    with db.transaction() as session:
        session.get(Consent, "consent-2").opted_in = True
        session.get(Artifact, "artifact-2").sha256 = "changed"
    with pytest.raises(DomainError, match="eligible"):
        service.create_dataset(user, ["project-1", "project-2"])


def test_training_split_is_project_disjoint_and_requires_rights_review(learning_env):
    db, settings, store, user = learning_env
    service = LearningService(db, settings, store)
    dataset = service.create_dataset(user, ["project-1", "project-2"])
    splits = {p["projectId"]: p["split"] for p in dataset["manifest"]["projects"]}
    assert set(splits.values()) == {"training", "evaluation"}
    with pytest.raises(DomainError, match="not approved"):
        service.materialize_training(user, dataset["id"])
    service.approve_dataset_rights(user, dataset["id"], "All examples are owned and licensed for this training.")
    materialized = service.materialize_training(user, dataset["id"])
    records = [json.loads(line) for line in store.get(materialized["key"]).decode().splitlines()]
    assert records and all(record["split"] == "training" for record in records)
    assert all(splits[record["projectId"]] == "training" for record in records)
    with db.transaction() as session:
        session.get(Consent, "consent-1").version += 1
    with pytest.raises(DomainError, match="consent changed"):
        service.materialize_training(user, dataset["id"])


def test_candidate_cannot_skip_quality_or_shadow_canary_gates(learning_env):
    db, settings, store, user = learning_env
    service = LearningService(db, settings, store)
    dataset = service.create_dataset(user, ["project-1", "project-2"])
    service.approve_dataset_rights(user, dataset["id"], "Licensed and consented customer-owned examples.")
    candidate = service.register_candidate(user, dataset["id"], {
        "baseModel": "licensed-model", "weightsLicense": "Apache-2.0", "recipe": "causal_lm_sft",
        "budgetUsd": 10, "maxGpuSeconds": 600})
    with pytest.raises(DomainError, match="engineering gates"):
        service.promote(user, candidate["id"])
    evaluation = {"heldOutExamples": 25, "criticalFailures": 0, "passRate": .9, "baselinePassRate": .8,
                  "costRatio": .7, "latencyRatio": .8, "reportArtifactId": "artifact-1"}
    service.record_evaluation(user, candidate["id"], evaluation)
    with pytest.raises(DomainError, match="through shadow"):
        service.promote(user, candidate["id"], "promoted")
    assert service.promote(user, candidate["id"], "shadow")["status"] == "shadow"
    with pytest.raises(DomainError, match="successful shadow"):
        service.promote(user, candidate["id"], "canary")
    evaluation["shadowPassed"] = True
    service.record_evaluation(user, candidate["id"], evaluation)
    assert service.promote(user, candidate["id"], "canary")["status"] == "canary"
    evaluation["canaryPassed"] = True
    service.record_evaluation(user, candidate["id"], evaluation)
    assert service.promote(user, candidate["id"], "promoted")["status"] == "promoted"
    service.rollback(user, candidate["id"])
    with db.transaction() as session:
        assert session.get(TrainedModel, candidate["id"]).status == "retired"


async def test_modal_call_identity_is_saved_and_output_copied_to_durable_storage(learning_env):
    db, settings, store, user = learning_env
    class SignedStore:
        def download_url(self, key, expires=300):
            return f"https://bucket.s3.amazonaws.com/{key}?signed=secret"
        def put_json(self, key, value):
            store.put_json(key, value)
    class FakeModal:
        calls = 0
        async def spawn(self, request):
            self.calls += 1
            assert request["inputs"][0]["sha256"]
            return "fc-one"
        async def result(self, call_id):
            assert call_id == "fc-one"
            return {"output": "Candidate model response"}
    with db.transaction() as session:
        session.add(Dataset(id="dataset", tenant_id="org", manifest={}))
        session.add(TrainedModel(id="model", dataset_id="dataset", status="promoted", config={}))
    client = FakeModal()
    compute = ModalCompute(db, settings, SignedStore(), client)
    job = await compute.submit(user, "project-1", "operation", "model", ["artifact-1"])
    duplicate = await compute.submit(user, "project-1", "operation", "model", ["artifact-1"])
    assert client.calls == 1 and duplicate["id"] == job["id"]
    done = await compute.poll(user, job["id"])
    assert done["status"] == "succeeded"
    assert json.loads(store.get(done["receipt"]["resultKey"]))["output"] == "Candidate model response"
    assert "signed=secret" not in json.dumps(done)


def test_uncertain_compute_requires_operator_reconciliation(learning_env):
    db, settings, _, user = learning_env
    ledger = ComputeLedger(db, settings)
    intent_id, _ = ledger.begin(user, "project-1", "ambiguous", "modal", {})
    ledger.uncertain(intent_id)
    with pytest.raises(DomainError, match="administrator"):
        ledger.reconcile(Principal("member", "org", "member"), intent_id, "Provider confirms the call completed", "fc-found")
    result = ledger.reconcile(user, intent_id, "Provider confirms this matching operation identifier", "fc-found")
    assert result["status"] == "submitted" and result["receipt"]["externalId"] == "fc-found"
    second_id, _ = ledger.begin(user, "project-1", "missing", "embedding", {})
    ledger.uncertain(second_id)
    result = ledger.reconcile(user, second_id, "Provider response lost; retain conservative allowance debit")
    assert result["status"] == "failed" and result["receipt"]["allowanceDebitUsd"] == 1


def test_auxiliary_compute_uses_shared_hosted_capacity_and_finite_budgets(learning_env):
    db, settings, _, user = learning_env
    settings.max_model_calls = 1
    ledger = ComputeLedger(db, settings)
    ledger.begin(user, "project-1", "in-flight", "embedding", {})
    with pytest.raises(DomainError) as error:
        ledger.begin(user, "project-2", "waiting", "embedding", {})
    assert error.value.status == 429
    with pytest.raises(DomainError, match="positive"):
        ledger.begin(user, "project-2", "bad-budget", "modal", {}, reserve=float("nan"))
    with pytest.raises(DomainError, match="outdated"):
        ledger.begin(user, "project-2", "old", "modal", {"revision": 5})


def test_failed_quality_candidate_and_negative_metrics_cannot_promote(learning_env):
    db, settings, store, user = learning_env
    learning = LearningService(db, settings, store)
    dataset = learning.create_dataset(user, ["project-1", "project-2"])
    learning.approve_dataset_rights(user, dataset["id"], "Dataset examples are licensed for training use.")
    model = learning.register_candidate(user, dataset["id"], {
        "baseModel": "base", "weightsLicense": "Apache-2.0", "recipe": "causal_lm_sft", "budgetUsd": 10, "maxGpuSeconds": 600})
    metrics = {"heldOutExamples": 25, "criticalFailures": 1, "passRate": .9, "baselinePassRate": .8,
               "costRatio": .7, "latencyRatio": .8, "reportArtifactId": "artifact-1"}
    learning.record_evaluation(user, model["id"], metrics)
    with pytest.raises(DomainError, match="engineering gates"):
        learning.promote(user, model["id"])
    with pytest.raises(DomainError, match="invalid"):
        learning.record_evaluation(user, model["id"], {**metrics, "costRatio": -1})
    with pytest.raises(DomainError, match="Invalid candidate"):
        learning.register_candidate(user, dataset["id"], {"budgetUsd": "invalid"})


def test_failed_embedding_needs_explicit_reconciled_rebuild_before_retry(learning_env):
    from kleos.db import ExternalIntent
    db, settings, _, user = learning_env
    class FailingEmbedder(Embedder):
        fail = True
        def embed(self, text, query=False):
            if self.fail:
                raise RuntimeError("response lost")
            return super().embed(text, query)
    provider = FailingEmbedder()
    index = MemoryIndex(db, settings, provider, QdrantClient(":memory:"))
    with pytest.raises(RuntimeError):
        index.retrieve(user, "project-1", "battery")
    with db.transaction() as session:
        failed = session.query(ExternalIntent).one()
        failed_id = failed.id
    with pytest.raises(DomainError, match="uncertain"):
        index.retrieve(user, "project-1", "battery")
    ComputeLedger(db, settings).reconcile(user, failed_id, "Provider response is lost; record conservative cost.")
    with pytest.raises(DomainError, match="administrator"):
        index.retrieve(user, "project-1", "battery")
    index.rebuild(user, "project-1")
    provider.fail = False
    assert index.retrieve(user, "project-1", "battery")["sources"] == []
    assert index.retrieve(user, "project-1", "battery")["sources"] == []
    assert provider.calls == 1


def test_modal_and_training_admission_have_independent_capacity_limits(learning_env):
    db, settings, _, user = learning_env
    ledger = ComputeLedger(db, settings)
    ledger.begin(user, "project-1", "custom-one", "modal", {})
    ledger.begin(user, "project-2", "custom-two", "modal", {})
    with pytest.raises(DomainError) as error:
        ledger.begin(user, "project-1", "custom-three", "modal", {})
    assert error.value.status == 429
    ledger.begin(user, "project-1", "training-one", "training", {})
    with pytest.raises(DomainError) as error:
        ledger.begin(user, "project-2", "training-two", "training", {})
    assert error.value.status == 429
