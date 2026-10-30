"""Opt-in concurrency checks against disposable PostgreSQL databases."""

import asyncio
import hashlib
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import event, select, text
from test_postgres import postgres  # noqa: F401

from kleos.contracts import Command, DomainError
from kleos.db import Artifact, Consent, DailyBudget, Dataset, ExternalIntent, Project, Release, TrainedModel
from kleos.learning import ComputeLedger, LearningService, ModalCompute
from kleos.maintenance import Maintenance

pytestmark = pytest.mark.skipif(not os.getenv("KLEOS_TEST_POSTGRES_URL"), reason="PostgreSQL opt-in required")


def pending_training(environment, reverse_manifest=False):
    # Both projects contribute to a dataset; project B funds an unclaimed
    # training intent. Withdrawal on A must refund B's reservation atomically.
    with environment.db.transaction() as session:
        for suffix in ("a", "b"):
            content = f"Accepted engineering source {suffix}".encode()
            key = f"artifacts/project-{suffix}.txt"
            sha = hashlib.sha256(content).hexdigest()
            environment.store.put(key, content)
            session.add(Project(id=f"project-{suffix}", tenant_id="org", owner_id="test", name="Fixture",
                                brief="Concurrency verification", budget_usd=20,
                                accepted_release_id=f"release-{suffix}"))
            # The ORM has no relationship attributes to order these records;
            # PostgreSQL enforces their foreign keys immediately.
            session.flush()
            session.add(Artifact(id=f"artifact-{suffix}", tenant_id="org", project_id=f"project-{suffix}",
                                 revision=1, lane="mechanics", name="enclosure.py", key=key, sha256=sha,
                                 size=len(content), status="accepted"))
            session.add(Release(id=f"release-{suffix}", tenant_id="org", project_id=f"project-{suffix}",
                                revision=1, mode="digital", manifest={"artifacts": {f"artifact-{suffix}": sha}},
                                approved_by="test"))
            session.add(Consent(id=f"consent-{suffix}", tenant_id="org", project_id=f"project-{suffix}",
                                actor_id="test", opted_in=True))
    learning = LearningService(environment.db, environment.settings, environment.store)
    dataset = learning.create_dataset(environment.principal, ["project-a", "project-b"])
    if reverse_manifest:
        with environment.db.transaction() as session:
            row = session.get(Dataset, dataset["id"])
            row.manifest = {**row.manifest, "projects": list(reversed(row.manifest["projects"]))}
    learning.approve_dataset_rights(environment.principal, dataset["id"], "These operator-owned fixtures permit training.")
    materialized = learning.materialize_training(environment.principal, dataset["id"])
    model = learning.register_candidate(environment.principal, dataset["id"], {
        "baseModel": "fixture", "baseModelPath": "fixture", "weightsLicense": "Apache-2.0", "recipe": "causal_lm_sft",
        "budgetUsd": 5, "maxGpuSeconds": 60})
    ledger = ComputeLedger(environment.db, environment.settings)
    intent_id, _ = ledger.begin(environment.principal, "project-b", "pending-training", "training", {
        "datasetId": dataset["id"], "modelId": model["id"], "datasetBinding": materialized["datasetBinding"]}, reserve=5)
    with environment.db.transaction() as session:
        revisions = {project.id: project.revision for project in session.scalars(select(Project))}
    return learning, dataset, model, ledger, intent_id, revisions


@pytest.mark.parametrize("deletion_operation", ["request", "purge"])
def test_cross_project_consent_withdrawal_and_deletion_share_lock_order(
        postgres, monkeypatch, deletion_operation):  # noqa: F811
    import kleos.training_consent as training
    _, dataset, _, _, intent_id, revisions = pending_training(postgres)

    class EmptyCheckpoints:
        def list_workflows(self, **kwargs):
            return []
    maintenance = Maintenance(postgres.db, postgres.settings, postgres.store, checkpoint_client=EmptyCheckpoints())
    if deletion_operation == "purge":
        maintenance.request_delete(postgres.principal, "project-b", revisions["project-b"])

    withdrawal_ready, release_withdrawal = threading.Event(), threading.Event()
    delete_thread, delete_pid = {}, {}
    original_refund = training.cancel_undispatched_training
    def pause_after_dataset_and_intent_locks(session, intent):
        withdrawal_ready.set()
        assert release_withdrawal.wait(10), "Deletion did not reach its PostgreSQL lock wait"
        return original_refund(session, intent)
    if deletion_operation == "request":
        monkeypatch.setattr(training, "cancel_undispatched_training", pause_after_dataset_and_intent_locks)
    else:
        # Requesting deletion now refunds the pending intent immediately, so
        # there is no remaining training reservation for purge to settle.
        original_exclude = training.exclude_project_training
        def pause_before_exclusion(session, project, **kwargs):
            withdrawal_ready.set()
            assert release_withdrawal.wait(10)
            return original_exclude(session, project, **kwargs)
        monkeypatch.setattr(training, "exclude_project_training", pause_before_exclusion)

    def record_deletion_backend(connection, cursor, statement, parameters, context, executemany):
        if threading.get_ident() == delete_thread.get("id"):
            delete_pid["pid"] = cursor.connection.info.backend_pid
            # An assertion failure must not leave integration-test threads hanging.
            cursor.execute("SET LOCAL lock_timeout = '6s'")
    event.listen(postgres.db.engine, "before_cursor_execute", record_deletion_backend)
    def withdraw():
        return postgres.control.command(postgres.principal, Command(
            type="project.setTrainingConsent", payload={"projectId": "project-a", "optedIn": False},
            expected_revision=revisions["project-a"]), "concurrent-withdrawal")
    def delete():
        delete_thread["id"] = threading.get_ident()
        return (maintenance.request_delete(postgres.principal, "project-b", revisions["project-b"])
                if deletion_operation == "request" else maintenance.purge_project("project-b"))
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            withdrawal = pool.submit(withdraw)
            try:
                assert withdrawal_ready.wait(5)
                deletion = pool.submit(delete)
                # Wait for a real database lock dependency, not a guessed sleep.
                # Previously deletion held B and waited for D/intent, while
                # withdrawal held D/intent and subsequently waited for B.
                blocked = False
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    if delete_pid.get("pid"):
                        with postgres.db.engine.connect() as observer:
                            blocked = observer.scalar(text(
                                "SELECT wait_event_type = 'Lock' FROM pg_stat_activity WHERE pid = :pid"),
                                {"pid": delete_pid["pid"]})
                    if blocked:
                        break
                    if deletion.done():
                        deletion.result()
                    time.sleep(.02)
                assert blocked, "Deletion must wait for the shared organization serialization lock"
            finally:
                release_withdrawal.set()
            withdrawal.result(timeout=10)
            deletion.result(timeout=10)
    finally:
        event.remove(postgres.db.engine, "before_cursor_execute", record_deletion_backend)

    with postgres.db.transaction() as session:
        assert not session.get(Consent, "consent-a").opted_in
        assert session.get(Project, "project-b").deleted_at
        assert session.get(Dataset, dataset["id"]).status == "excluded"
        assert session.get(ExternalIntent, intent_id).status == "failed"
        assert session.get(Project, "project-b").reserved_usd == 0
        assert session.get(Project, "project-b").spent_usd == 0
        assert session.scalar(select(DailyBudget)).reserved == 0


@pytest.mark.parametrize("operation", ["rights", "candidate", "materialize", "start_training"])
def test_reversed_manifest_learning_and_withdrawal_serialize_before_project_locks(
        postgres, monkeypatch, operation):  # noqa: F811
    import kleos.learning as learning_module
    learning, dataset, model, _, intent_id, revisions = pending_training(postgres, reverse_manifest=True)
    learning_ready, release_learning = threading.Event(), threading.Event()
    learning_thread, withdrawal_thread, withdrawal_pid = {}, {}, {}
    original_project = learning_module.get_project
    def pause_after_funding_project(session, project_id, *args, **kwargs):
        project = original_project(session, project_id, *args, **kwargs)
        if threading.get_ident() == learning_thread.get("id") and project_id == "project-b":
            learning_ready.set()
            assert release_learning.wait(10), "Withdrawal did not reach its serialization lock"
        return project
    monkeypatch.setattr(learning_module, "get_project", pause_after_funding_project)
    def record_withdrawal_backend(connection, cursor, statement, parameters, context, executemany):
        if threading.get_ident() == withdrawal_thread.get("id"):
            withdrawal_pid["pid"] = cursor.connection.info.backend_pid
            cursor.execute("SET LOCAL lock_timeout = '6s'")
    event.listen(postgres.db.engine, "before_cursor_execute", record_withdrawal_backend)
    def use_dataset():
        learning_thread["id"] = threading.get_ident()
        if operation == "rights":
            return learning.approve_dataset_rights(postgres.principal, dataset["id"], "Reviewed licensed examples again.")
        if operation == "candidate":
            return learning.register_candidate(postgres.principal, dataset["id"], {
                "baseModel": "fixture", "weightsLicense": "Apache-2.0", "recipe": "causal_lm_sft",
                "budgetUsd": 5, "maxGpuSeconds": 60})
        if operation == "materialize":
            return learning.materialize_training(postgres.principal, dataset["id"])
        try:
            # Local storage intentionally has no signed URL, so no provider can be called.
            return asyncio.run(ModalCompute(postgres.db, postgres.settings, postgres.store, object()).start_training(
                postgres.principal, "project-b", "unused-training", model["id"]))
        except DomainError as exc:
            assert exc.code in {"storage_unconfigured", "consent_required", "consent_changed"}
    def withdraw():
        withdrawal_thread["id"] = threading.get_ident()
        return postgres.control.command(postgres.principal, Command(
            type="project.setTrainingConsent", payload={"projectId": "project-a", "optedIn": False},
            expected_revision=revisions["project-a"]), "withdraw-during-learning")
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            learning_future = pool.submit(use_dataset)
            try:
                assert learning_ready.wait(5)
                withdrawal = pool.submit(withdraw)
                blocked = False
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    if withdrawal_pid.get("pid"):
                        with postgres.db.engine.connect() as observer:
                            row = observer.execute(text(
                                "SELECT wait_event_type, query FROM pg_stat_activity WHERE pid = :pid"),
                                {"pid": withdrawal_pid["pid"]}).mappings().one()
                            if row["wait_event_type"] == "Lock":
                                assert "organizations" in row["query"].lower(), (
                                    "Withdrawal must wait before taking any project locks")
                                blocked = True
                    if blocked:
                        break
                    if withdrawal.done():
                        withdrawal.result()
                    time.sleep(.02)
                assert blocked
            finally:
                release_learning.set()
            learning_future.result(timeout=10)
            withdrawal.result(timeout=10)
    finally:
        event.remove(postgres.db.engine, "before_cursor_execute", record_withdrawal_backend)
    with postgres.db.transaction() as session:
        assert not session.get(Consent, "consent-a").opted_in
        assert session.get(ExternalIntent, intent_id).status == "failed"
        assert session.get(Project, "project-b").reserved_usd == 0
        assert session.get(TrainedModel, model["id"]).status == "candidate"
