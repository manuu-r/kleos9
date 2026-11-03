"""Independent acceptance commands and private immutable digital release packages."""

import io
import json
import zipfile

from sqlalchemy import select

from kleos.api import Content
from kleos.artifacts.service import Artifact
from kleos.artifacts.storage import source_path
from kleos.contracts.models import ArtifactRef, DomainError
from kleos.projects.store import Receipt, fingerprint
from kleos.validation.service import Candidate, Release


class ReleaseFacade:
    def __init__(self, projects, artifacts, validation):
        self.projects, self.artifacts, self.validation = projects, artifacts, validation

    def accept(self, actor, body, key):
        payload = body.get("payload", {})
        project_id, candidate_id = payload.get("projectId"), payload.get("candidateId")
        if candidate_id is None:
            with self.projects.transaction() as session:
                project = self.projects.authorized(session, actor, project_id, write=True)
                prior = session.scalar(select(Receipt).where(
                    Receipt.tenant_id == actor.tenant_id,
                    Receipt.user_id == actor.user_id, Receipt.key == key))
                if prior and prior.result.get("projectId") == project.id and prior.result.get("releaseId"):
                    accepted = session.get(Release, prior.result["releaseId"])
                    if accepted and accepted.project_id == project.id:
                        candidate_id = accepted.candidate_id
                candidates = list(
                    session.scalars(
                        select(Candidate.id).where(
                            Candidate.project_id == project.id,
                            Candidate.revision == project.context_revision,
                            Candidate.status == "qualified",
                        )
                    )
                )
                if candidate_id is None and len(candidates) != 1:
                    raise DomainError(
                        "Select one independently qualified release candidate", "release_not_qualified"
                    )
                if candidate_id is None:
                    candidate_id = candidates[0]
        receipt = self.validation.accept(actor, project_id, candidate_id, body.get("expectedRevision"), key)
        return {name: receipt[name] for name in ("commandId", "projectId", "revision")}

    def content(self, actor, release_id):
        with self.projects.transaction() as session:
            release = session.get(Release, release_id)
            if release is None:
                raise DomainError("Release not found", "not_found", 404)
            self.projects.authorized(session, actor, release.project_id)
            manifest = release.data
            if fingerprint({k: v for k, v in manifest.items() if k != "manifestHash"}) != manifest.get(
                "manifestHash"
            ):
                raise DomainError("Release manifest failed integrity verification", "artifact_integrity")
            references = [("sources", item) for item in manifest.get("sources", [])]
            references += [("artifacts", item) for item in manifest.get("outputs", [])]
            if not references or len(references) > 512:
                raise DomainError("Release exceeds package bounds", "artifact_limit", 413)
            files, seen, size = [], set(), 0
            for folder, raw in references:
                ref = ArtifactRef.model_validate(raw)
                path = folder + "/" + source_path(ref.path)
                if path in seen:
                    raise DomainError("Release contains ambiguous file paths", "artifact_integrity")
                seen.add(path)
                row = session.get(Artifact, ref.id)
                if row is None or row.project_id != release.project_id:
                    raise DomainError("Release file is unavailable", "artifact_integrity")
                exact = ArtifactRef(
                    id=row.id,
                    path=row.name,
                    sha256=row.sha256,
                    revision=row.revision,
                    media_type=row.media_type,
                    size=row.size,
                )
                if ref != exact:
                    raise DomainError("Release file provenance changed", "artifact_integrity")
                size += ref.size
                if size > 64 * 1024 * 1024:
                    raise DomainError("Release exceeds package bounds", "artifact_limit", 413)
                content = self.artifacts.blobs.get(ref.sha256)
                if len(content) != ref.size:
                    raise DomainError("Release file size changed", "artifact_integrity")
                files.append((path, content))
            files.append(("manifest.json", json.dumps(manifest, sort_keys=True, indent=2).encode()))
            output = io.BytesIO()
            with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for path, content in sorted(files):
                    entry = zipfile.ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
                    entry.external_attr = 0o100600 << 16
                    entry.compress_type = zipfile.ZIP_DEFLATED
                    archive.writestr(entry, content)
            return Content(output.getvalue(), "application/zip", f"release-{release_id}.zip")
