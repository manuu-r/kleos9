"""Real digital print preparation, separate from simulated fulfillment/physical acceptance."""

import json

from sqlalchemy import select

from .bambu_contracts import MAX_PACKAGE_BYTES, BambuProfile, model_catalog
from .bambu_slicing import inspect_package
from .contracts import DomainError
from .db import Artifact, ExternalIntent, Release, uid
from .domain import contract_hash, emit, get_project
from .storage import artifact_key, digest


class BambuPrintService:
    def __init__(self, db, settings, store):
        self.db, self.settings, self.store = db, settings, store

    def catalog(self):
        catalog = model_catalog()
        return {"studioVersion": catalog["studioVersion"], "models": catalog["models"],
                "profiles": [{"id": key, **BambuProfile.model_validate(value).model_dump(mode="json", by_alias=True)}
                             for key, value in self.settings.bambu_slice_profiles.items()],
                "limitations": [catalog["qualification"], "Printing is reviewed in Bambu Connect; preparation never claims physical acceptance."]}

    def _current(self, session, project, payload):
        release = session.get(Release, payload["releaseId"])
        source = session.get(Artifact, payload["artifactId"])
        if (not release or release.mode != "digital" or release.project_id != project.id or release.tenant_id != project.tenant_id
                or project.accepted_release_id != release.id or release.revision != project.engineering_revision
                or not source or source.project_id != project.id or source.tenant_id != project.tenant_id
                or source.revision != project.engineering_revision or source.status != "accepted"
                or source.lane != "mechanics" or not source.name.lower().endswith(".stl")
                or release.manifest.get("artifacts", {}).get(source.id) != source.sha256):
            raise DomainError("Select an STL from the current accepted digital release", 409, "print_release_required")
        if contract_hash(release.manifest.get("requirements", {}), release.manifest.get("interfaces", {})) != contract_hash(project.requirements, project.interfaces):
            raise DomainError("The accepted release contract changed; review a new release before slicing", 409, "stale_release")
        if source.size > MAX_PACKAGE_BYTES:
            raise DomainError("This STL exceeds the local slicing transfer limit", 413)
        return source

    def create(self, principal, project_id, body, key):
        with self.db.transaction() as session:
            project = get_project(session, project_id, principal, write=True)
            if body.expected_engineering_revision != project.engineering_revision:
                raise DomainError("Print preparation belongs to an outdated revision", 409, "stale_revision")
            payload = body.model_dump(mode="json", by_alias=True)
            source = self._current(session, project, payload)
            operation = "bambu.prepare:" + digest(key.encode())[:40]
            fingerprint = digest(json.dumps(payload, sort_keys=True).encode())
            previous = session.scalar(select(ExternalIntent).where(ExternalIntent.project_id == project.id,
                ExternalIntent.revision == project.engineering_revision, ExternalIntent.operation == operation))
            if previous:
                if previous.payload["fingerprint"] != fingerprint:
                    raise DomainError("Idempotency key was reused with different print inputs", 409)
                return self.view(previous)
            if body.profile_id not in self.settings.bambu_slice_profiles:
                raise DomainError("Install a qualified printer/material/nozzle profile first", 503, "printer_profile_unconfigured")
            profile = BambuProfile.model_validate(self.settings.bambu_slice_profiles[body.profile_id])
            models = {m["id"]: m for m in model_catalog()["models"]}
            if not profile.qualified or profile.printer_model_id not in models or profile.nozzle_mm not in models[profile.printer_model_id]["nozzlesMm"]:
                raise DomainError("The selected printer/material/nozzle profile needs operator qualification", 409, "printer_profile_unqualified")
            intent = ExternalIntent(id=uid(), tenant_id=project.tenant_id, project_id=project.id,
                operation=operation, revision=project.engineering_revision, mode="digital_preparation", status="awaiting_local_slicing",
                payload={**payload, "fingerprint": fingerprint, "sourceSha256": source.sha256,
                         "profile": profile.model_dump(mode="json", by_alias=True)})
            session.add(intent)
            emit(session, project, "printer.preparationRequested", {"preparationId": intent.id, "sourceSha256": source.sha256})
            session.flush()
            return self.view(intent)

    def _get(self, session, principal, project_id, ident, write=False):
        project = get_project(session, project_id, principal, write=write)
        intent = session.scalar(select(ExternalIntent).where(ExternalIntent.id == ident,
            ExternalIntent.project_id == project.id, ExternalIntent.tenant_id == principal.tenant_id,
            ExternalIntent.mode == "digital_preparation", ExternalIntent.operation.like("bambu.prepare:%")).with_for_update())
        if not intent or intent.revision != project.engineering_revision:
            raise DomainError("Current print preparation not found", 404)
        self._current(session, project, intent.payload)
        return project, intent

    def read(self, principal, project_id, ident):
        with self.db.transaction() as session:
            _, intent = self._get(session, principal, project_id, ident)
            return self.view(intent)

    def list(self, principal, project_id):
        with self.db.transaction() as session:
            project = get_project(session, project_id, principal)
            intents = session.scalars(select(ExternalIntent).where(
                ExternalIntent.tenant_id == principal.tenant_id, ExternalIntent.project_id == project_id,
                ExternalIntent.revision == project.engineering_revision, ExternalIntent.mode == "digital_preparation",
                ExternalIntent.operation.like("bambu.prepare:%")).order_by(ExternalIntent.created_at.desc()).limit(30)).all()
            current = []
            for intent in intents:
                try:
                    self._current(session, project, intent.payload)
                    current.append(self.view(intent))
                except DomainError:
                    continue
            return {"preparations": current}

    def cancel(self, principal, project_id, ident):
        with self.db.transaction() as session:
            project, intent = self._get(session, principal, project_id, ident, write=True)
            if intent.status != "cancelled":
                intent.status = "cancelled"
                emit(session, project, "printer.preparationCancelled", {"preparationId": intent.id})
            return self.view(intent)

    def complete(self, principal, project_id, ident, body):
        with self.db.transaction() as session:
            project, intent = self._get(session, principal, project_id, ident, write=True)
            if intent.status == "cancelled":
                raise DomainError("Cancelled preparation cannot publish", 409)
            if body.bundle_sha256 != intent.payload["profile"]["bundleSha256"]:
                raise DomainError("Slicing profile changed", 409)
            if intent.status == "ready_for_operator":
                if body.sha256 != intent.receipt["sha256"]:
                    raise DomainError("Preparation result changed", 409)
                return self.view(intent)
            upload = session.get(Artifact, body.artifact_id)
            if (not upload or upload.tenant_id != principal.tenant_id or upload.project_id != project.id
                    or upload.revision != project.engineering_revision or upload.status != "input" or upload.sha256 != body.sha256
                    or upload.size > MAX_PACKAGE_BYTES):
                raise DomainError("Upload the sliced package to this project and revision", 422)
            try:
                raw = self.store.get_limited(upload.key, MAX_PACKAGE_BYTES)
            except (ValueError, OSError) as exc:
                raise DomainError("Sliced package is missing or exceeds its read limit", 422) from exc
            if digest(raw) != body.sha256:
                raise DomainError("Sliced file hash mismatch", 422)
            try:
                measurements = inspect_package(raw)
            except (ValueError, OSError, KeyError) as exc:
                raise DomainError("Invalid single-plate sliced package", 422) from exc
            name = f"bambu/{intent.id}.gcode.3mf"
            key = artifact_key(project.tenant_id, project.id, intent.id, name, raw)
            self.store.put(key, raw, "application/zip")
            artifact = Artifact(id=uid(), tenant_id=project.tenant_id, project_id=project.id,
                revision=project.engineering_revision, lane="mechanics", name=name, key=key,
                sha256=body.sha256, size=len(raw), media_type="application/zip", status="prepared",
                provenance={"artifactRole": "printPreparation", "releaseId": intent.payload["releaseId"],
                            "profile": intent.payload["profile"], "authority": "local_adapter_report",
                            "physicalAcceptance": False},
                dependencies=[{"id": intent.payload["artifactId"], "sha256": intent.payload["sourceSha256"]}])
            session.add(artifact)
            intent.receipt = {"artifactId": artifact.id, "sha256": body.sha256, "measurements": measurements}
            intent.status = "ready_for_operator"
            emit(session, project, "printer.preparationReady", {"preparationId": ident, "artifactId": artifact.id})
            return self.view(intent)

    @staticmethod
    def view(intent):
        return {"id": intent.id, "projectId": intent.project_id, "engineeringRevision": intent.revision,
                "status": intent.status, "sourceArtifactId": intent.payload["artifactId"],
                "sourceSha256": intent.payload["sourceSha256"], "profileId": intent.payload["profileId"],
                "profile": intent.payload["profile"], "preparedArtifactId": intent.receipt.get("artifactId"),
                "preparedSha256": intent.receipt.get("sha256"), "measurements": intent.receipt.get("measurements", {}),
                "physicalAcceptance": False}
