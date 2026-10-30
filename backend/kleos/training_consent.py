"""Consent fences for training admission, cancellation, and late provider receipts."""

import hashlib
import json

from sqlalchemy import select

from .db import Consent, DailyBudget, Dataset, ExternalIntent, Organization, Outbox, Project, now


def lock_training_organization(session, tenant_id):
    """Take the consent serialization lock before any project/dataset row locks."""
    transaction = session.get_transaction()
    held = session.info.get("training_organization_locks", {})
    if transaction is not None and held.get(tenant_id) is transaction:
        return
    session.scalar(select(Organization).where(Organization.id == tenant_id).with_for_update())
    session.info.setdefault("training_organization_locks", {})[tenant_id] = session.get_transaction()


def dataset_binding(dataset):
    return {
        "manifestSha256": hashlib.sha256(json.dumps(
            dataset.manifest, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        "projects": [{key: entry[key] for key in (
            "projectId", "revision", "consentId", "consentVersion")}
            for entry in dataset.manifest["projects"]],
    }


def training_inputs_current(session, intent):
    inputs = intent.payload.get("input", {})
    dataset = session.get(Dataset, inputs.get("datasetId"))
    binding = inputs.get("datasetBinding")
    if not dataset or dataset.tenant_id != intent.tenant_id or dataset.status != "ready" or not binding:
        return False
    if binding != dataset_binding(dataset):
        return False
    for entry in binding["projects"]:
        consent = session.get(Consent, entry["consentId"])
        project = session.get(Project, entry["projectId"])
        if (not consent or consent.tenant_id != intent.tenant_id or not consent.opted_in
                or consent.project_id != entry["projectId"] or consent.version != entry["consentVersion"]
                or not project or project.deleted_at or project.engineering_revision != entry["revision"]):
            return False
    return True


def request_training_cancel(session, intent, reason):
    intent.receipt = {**intent.receipt, "cancelRequired": True, "cancelReason": reason}
    external_id = hashlib.sha256(intent.receipt.get("externalId", "pending").encode()).hexdigest()[:32]
    key = f"cancel-training:{intent.id}:{external_id}"
    if not session.scalar(select(Outbox.id).where(Outbox.key == key)):
        session.add(Outbox(key=key, kind="cancel_training", payload={"intentId": intent.id}))


def reconcile_training_cancellations(db, at=None):
    """Re-enqueue still-required cancellations after a bounded workflow exhausted retries."""
    at = at or now()
    count = 0
    with db.transaction() as session:
        intents = session.scalars(select(ExternalIntent).where(
            ExternalIntent.mode == "compute", ExternalIntent.operation.like("training:%"),
            ExternalIntent.status.in_(["reserved", "submitted", "uncertain"]),
            ExternalIntent.receipt["cancelRequired"].as_boolean().is_(True)
        ).order_by(ExternalIntent.id).limit(250)).all()
        for intent in intents:
            if not intent.receipt.get("externalId"):
                continue
            key = f"cancel-training-retry:{intent.id}:{at.strftime('%Y-%m-%d-%H')}"
            if not session.scalar(select(Outbox.id).where(Outbox.key == key)):
                session.add(Outbox(key=key, kind="cancel_training", payload={"intentId": intent.id}))
                count += 1
    return count


def cancel_undispatched_training(session, intent):
    # Callers hold the organization lock and have verified that no dispatch
    # claim exists. A failed pre-dispatch eligibility check must not leak budget.
    funding = session.scalar(select(Project).where(Project.id == intent.project_id).with_for_update())
    daily = session.scalar(select(DailyBudget).where(
        DailyBudget.day == intent.payload["day"]).with_for_update())
    amount = intent.payload["reservedUsd"]
    funding.reserved_usd = max(0, funding.reserved_usd - amount)
    daily.reserved = max(0, daily.reserved - amount)
    intent.status = "failed"
    intent.receipt = {**intent.receipt, "cancelled": True, "failure": "training_inputs_changed",
                      "allowanceDebitUsd": 0, "costBasis": "cancelled_before_dispatch"}


def exclude_project_training(session, project, reason="training_consent_changed"):
    """Called under the organization's command lock; already-trained model lineage is retained."""
    dataset_ids = set()
    cause = "excludedByProjectDeletion" if reason == "project_deleted" else "excludedByConsentChange"
    for dataset in session.scalars(select(Dataset).where(Dataset.tenant_id == project.tenant_id)):
        if any(entry["projectId"] == project.id for entry in dataset.manifest.get("projects", [])):
            dataset.status = "excluded"
            dataset.manifest = {**dataset.manifest, cause: project.id}
            dataset_ids.add(dataset.id)
    for intent in session.scalars(select(ExternalIntent).where(
            ExternalIntent.tenant_id == project.tenant_id, ExternalIntent.mode == "compute",
            ExternalIntent.operation.like("training:%"),
            ExternalIntent.status.in_(["reserved", "submitted", "uncertain"])).with_for_update()):
        if intent.payload.get("input", {}).get("datasetId") not in dataset_ids:
            continue
        if intent.status == "reserved" and not intent.receipt.get("dispatchClaimedAt"):
            # No external submission was authorized, so this reservation can be refunded.
            cancel_undispatched_training(session, intent)
        else:
            # A dispatch claim may already be in flight. Fence its result immediately
            # and cancel when its provider call ID is known; never claim to undo work.
            request_training_cancel(session, intent, reason)
