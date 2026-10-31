"""Authorized reads of current independent evidence; no simulation on GET."""

import json

from sqlalchemy import func, select

from .circuit_simulation import canonical_sha256
from .circuit_simulation_runtime import CHECK_NAMES, MAX_WAVEFORM_BYTES, WAVEFORM_NAME
from .db import Artifact, Run, Validation
from .domain import contract_hash, get_project
from .storage import digest


class CircuitSimulationView:
    def __init__(self, database, store):
        self.database, self.store = database, store

    def read(self, principal, project_id):
        with self.database.transaction(read_only=True) as session:
            project = get_project(session, project_id, principal, lock=False)
            unavailable = {"revision": project.revision, "engineeringRevision": project.engineering_revision,
                           "status": "unavailable", "artifactId": None, "artifactSha256": None,
                           "result": None, "reason": "No current independent circuit simulation evidence"}
            artifact = session.scalar(select(Artifact).where(
                Artifact.tenant_id == principal.tenant_id, Artifact.project_id == project_id,
                Artifact.revision == project.engineering_revision, Artifact.status == "evidence",
                Artifact.name == WAVEFORM_NAME).order_by(Artifact.created_at.desc(), Artifact.id.desc()).limit(1))
            if not artifact or artifact.provenance.get("artifactRole") != "validationEvidence":
                return unavailable
            rebuilt_at = session.scalar(select(func.max(Run.created_at)).where(
                Run.project_id == project_id, Run.revision == project.engineering_revision, Run.kind == "project.retryNative"))
            current_contract = contract_hash(project.requirements, project.interfaces)
            if (artifact.provenance.get("contractHash") != current_contract
                    or rebuilt_at and artifact.created_at < rebuilt_at):
                return unavailable
            current = {a.id: a.sha256 for a in session.scalars(select(Artifact).where(
                Artifact.project_id == project_id, Artifact.revision == project.engineering_revision,
                Artifact.status.in_(["candidate", "accepted"])))}
            if current != {a["id"]: a["sha256"] for a in artifact.dependencies}:
                return unavailable
            checks = session.scalars(select(Validation).where(
                Validation.project_id == project_id, Validation.revision == project.engineering_revision,
                Validation.evidence_kind == "digital", Validation.name.in_(CHECK_NAMES))).all()
            bound = {check.name: check for check in checks
                     if check.details.get("waveformArtifactId") == artifact.id
                     and check.details.get("waveformSha256") == artifact.sha256
                     and check.details.get("contractHash") == current_contract
                     and check.artifact_hashes == current}
            if set(bound) != set(CHECK_NAMES):
                return unavailable
            try:
                raw = self.store.get_limited(artifact.key, MAX_WAVEFORM_BYTES)
                if digest(raw) != artifact.sha256:
                    raise ValueError("Waveform storage hash mismatch")
                payload = json.loads(raw)
                if not isinstance(payload, dict):
                    raise ValueError("Waveform envelope must be an object")
                result_hash = payload.pop("resultsSha256")
                if (canonical_sha256(payload) != result_hash or payload["schemaVersion"] != "circuit-waveform-v1"
                        or payload["evidenceKind"] != "digital" or payload["physicalAcceptance"] is not False
                        or {c["name"]: c["status"] for c in payload["checks"]}
                        != {name: check.status for name, check in bound.items()}):
                    raise ValueError("Waveform evidence envelope is invalid")
                payload["resultsSha256"] = result_hash
            except (OSError, ValueError, KeyError, TypeError):
                return {**unavailable, "reason": "Stored circuit evidence failed integrity checks"}
            return {"revision": project.revision, "engineeringRevision": project.engineering_revision,
                    "status": "available", "artifactId": artifact.id, "artifactSha256": artifact.sha256,
                    "result": payload, "reason": None}
