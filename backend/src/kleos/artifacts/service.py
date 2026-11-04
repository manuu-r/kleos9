"""Versioned project inputs, private delivery and metadata-free image references."""

import base64
import binascii
import hashlib
import io
from uuid import uuid4

from PIL import Image, UnidentifiedImageError
from sqlalchemy import JSON, Integer, String, UniqueConstraint, select
from sqlalchemy.orm import Mapped, mapped_column

from kleos.api import Content
from kleos.artifacts.storage import MAX_FILE_BYTES, source_path
from kleos.contracts.models import DomainError
from kleos.projects.store import Base, fingerprint


class Artifact(Base):
    __tablename__ = "kleos2_artifacts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(240))
    sha256: Mapped[str] = mapped_column(String(64))
    size: Mapped[int] = mapped_column(Integer)
    media_type: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(30))
    lane: Mapped[str] = mapped_column(String(30))
    details: Mapped[dict] = mapped_column(JSON)


class NativePublication(Base):
    __tablename__ = "kleos2_native_publications"
    __table_args__ = (UniqueConstraint("run_id", "task_id", "job_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    task_id: Mapped[str] = mapped_column(String(100))
    job_id: Mapped[str] = mapped_column(String(100))
    fingerprint: Mapped[str] = mapped_column(String(64))
    references: Mapped[list] = mapped_column(JSON)


def artifact_view(row):
    return {
        "id": row.id,
        "name": row.name,
        "lane": row.lane,
        "revision": row.revision,
        "sha256": row.sha256,
        "size": row.size,
        "status": row.status,
        "mediaType": row.media_type,
        "provenance": row.details,
    }


def decode(value, limit=MAX_FILE_BYTES):
    if not isinstance(value, str) or len(value) > (limit + 2) // 3 * 4:
        raise DomainError("File exceeds upload limit", "artifact_limit", 413)
    try:
        content = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error):
        raise DomainError("Invalid file encoding", "invalid_request", 422) from None
    if len(content) > limit:
        raise DomainError("File exceeds upload limit", "artifact_limit", 413)
    return content


class ArtifactService:
    def __init__(self, projects, blobs):
        self.projects, self.blobs = projects, blobs

    def list(self, session, project_id):
        return [
            artifact_view(a)
            for a in session.scalars(
                select(Artifact).where(Artifact.project_id == project_id).order_by(Artifact.id)
            )
        ]

    def upload(self, actor, project_id, body, key):
        file = body.get("file")
        if not isinstance(file, dict):
            raise DomainError("Provide a file", "invalid_request", 422)
        name = source_path(file.get("path"))
        if file.get("encoding", "utf8") == "base64":
            content = decode(file.get("content"))
        elif file.get("encoding", "utf8") == "utf8" and isinstance(file.get("content"), str):
            content = file["content"].encode()
        else:
            raise DomainError("Unsupported encoding", "invalid_request", 422)
        if len(content) > MAX_FILE_BYTES:
            raise DomainError("File exceeds upload limit", "artifact_limit", 413)
        digest = hashlib.sha256(content).hexdigest()
        if digest != body.get("sha256"):
            raise DomainError("Uploaded file checksum differs", "hash_mismatch", 422)
        media = file.get("mediaType", "application/octet-stream")
        if not isinstance(media, str) or len(media) > 100 or any(ord(c) < 32 for c in media):
            raise DomainError("Invalid media type", "invalid_request", 422)

        def operation(session):
            p = self.projects.authorized(session, actor, project_id, write=True)
            if p.data.get("engineeringRevision", 1) != body.get("expectedEngineeringRevision"):
                raise DomainError("Engineering context changed", "revision_conflict")
            self.blobs.put(content)
            row = Artifact(
                id=str(uuid4()),
                project_id=p.id,
                revision=p.data.get("engineeringRevision", 1),
                name=name,
                sha256=digest,
                size=len(content),
                media_type=media,
                status="input",
                lane="research",
                details={"origin": "user_upload", "actorId": actor.user_id},
            )
            session.add(row)
            p.data = {**p.data, "sourceManifest": {**p.data.get("sourceManifest", {}), name: row.id}}
            p.advance_revision()
            self.projects._emit(session, p, "artifact.created", artifact_view(row))
            return {"projectId": p.id, "artifact": artifact_view(row), "revision": p.revision}

        return self.projects._command(
            actor,
            key,
            {
                "operation": "artifact.upload",
                "projectId": project_id,
                "name": name,
                "sha256": digest,
                "expectedEngineeringRevision": body.get("expectedEngineeringRevision"),
            },
            operation,
        )

    def reference_image(self, actor, project_id, body):
        # Authorization precedes decoding untrusted image data or allocating image buffers.
        with self.projects.transaction() as s:
            self.projects.authorized(s, actor, project_id, write=True)
        name = source_path(body.get("name"))
        raw = decode(body.get("contentBase64"), 6 * 1024 * 1024)
        try:
            with Image.open(io.BytesIO(raw)) as image:
                if image.format not in {"PNG", "JPEG", "WEBP"} or image.width * image.height > 16_000_000:
                    raise DomainError("Unsupported image size or format", "invalid_image", 422)
                image.load()
                image.thumbnail((2048, 2048))
                normalized = image.convert("RGB")
                # A fresh image strips EXIF, metadata and embedded provider hints.
                clean = Image.new("RGB", normalized.size)
                clean.paste(normalized)
                output = io.BytesIO()
                clean.save(output, format="PNG")
                content = output.getvalue()
                width, height = clean.size
        except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
            raise DomainError("Invalid image", "invalid_image", 422) from None
        with self.projects.transaction() as s:
            p = self.projects.authorized(s, actor, project_id, write=True)
            image_ids = list(p.data.get("referenceImageIds", []))
            if len(image_ids) >= 8:
                raise DomainError(
                    "A project supports up to eight active reference images", "artifact_limit", 413
                )
            digest = self.blobs.put(content)
            row = Artifact(
                id=str(uuid4()),
                project_id=p.id,
                revision=p.data.get("engineeringRevision", 1),
                name=name,
                sha256=digest,
                size=len(content),
                media_type="image/png",
                status="input",
                lane="research",
                details={
                    "origin": "reference_image",
                    "actorId": actor.user_id,
                    "width": width,
                    "height": height,
                },
            )
            s.add(row)
            p.data = {**p.data, "referenceImageIds": [*image_ids, row.id]}
            p.advance_revision()
            self.projects._emit(s, p, "referenceImage.created", {"imageId": row.id})
            return {
                "id": row.id,
                "name": name,
                "mediaType": "image/png",
                "sha256": digest,
                "size": len(content),
                "width": width,
                "height": height,
            }

    def _authorized(self, session, actor, artifact_id, project_id=None):
        row = session.get(Artifact, artifact_id)
        if row is None or (project_id is not None and row.project_id != project_id):
            raise DomainError("Artifact not found", "not_found", 404)
        self.projects.authorized(session, actor, row.project_id)
        return row

    async def planner_images(self, scope, context):
        """Only server-resolved current project images reach a vision-capable model."""
        from kleos.projects.graph_repository import GraphRepository

        with self.projects.transaction() as session:
            project, _ = GraphRepository(self.projects)._check(session, scope)
            if context.revision != project.context_revision:
                raise DomainError("Image context changed", "stale_context")
            ids = project.data.get("referenceImageIds", [])
            if set(ids) != {ref.id for ref in context.reference_images}:
                raise DomainError("Image selection changed", "stale_context")
            parts, total = [], 0
            for ref in context.reference_images:
                row = self._authorized(session, scope.actor, ref.id, project.id)
                if row.details.get("origin") != "reference_image" or row.sha256 != ref.sha256:
                    raise DomainError("Image reference is invalid", "artifact_scope_conflict")
                total += row.size
                if total > 8 * 1024 * 1024:
                    raise DomainError(
                        "Reference images exceed the model input bound", "model_input_too_large"
                    )
                content = base64.b64encode(self.blobs.get(row.sha256)).decode("ascii")
                parts.extend(
                    [
                        {"type": "text", "text": f"Reference image {row.id}: {row.name}"},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{row.media_type};base64,{content}"},
                        },
                    ]
                )
            return parts

    def download(self, actor, artifact_id):
        with self.projects.transaction() as s:
            row = self._authorized(s, actor, artifact_id)
            return {"url": f"/api/v1/artifacts/{row.id}/content", "sha256": row.sha256, "name": row.name}

    def content(self, actor, artifact_id, project_id=None, reference=False):
        with self.projects.transaction() as s:
            row = self._authorized(s, actor, artifact_id, project_id)
            if reference and row.details.get("origin") != "reference_image":
                raise DomainError("Image not found", "not_found", 404)
            return Content(self.blobs.get(row.sha256), row.media_type, row.name)

    async def publish_native(self, scope, task, files, provenance):
        """Trusted gateway callback: publish fenced candidates, never accept or overwrite sources."""
        return await self._publish_native(scope, task, files, provenance)

    async def publish_compilation(self, scope, task, files, provenance, composition):
        """Server-only final compilation; retains every original source identity."""
        return await self._publish_native(scope, task, files, provenance, composition=composition)

    async def _publish_native(self, scope, task, files, provenance, *, composition=None):
        from kleos.contracts.models import ArtifactRef
        from kleos.projects.graph_repository import GraphRepository

        if not isinstance(provenance, dict) or not all(
            provenance.get(k) for k in ("nativeJobId", "imageDigest", "requestHash")
        ):
            raise DomainError("Native provenance is required", "invalid_provenance")
        if not isinstance(files, list) or len(files) > 256:
            raise DomainError("Native artifact limit exceeded", "artifact_limit")
        prepared, seen, total = [], set(), 0
        for file in files:
            if not isinstance(file, dict):
                raise DomainError("Invalid native artifact", "invalid_result")
            name = source_path(file.get("path"))
            content = file.get("content")
            role = file.get("role")
            if name in seen or role not in {"source", "output"} or not isinstance(content, bytes):
                raise DomainError("Invalid native artifact", "invalid_result")
            if role == "source" and (name not in task.allowed_paths or "expectedSha256" not in file):
                raise DomainError("Native edit is outside authorized task scope", "scope_conflict")
            if role == "output" and not name.startswith("outputs/"):
                raise DomainError("Native export is outside its output directory", "scope_conflict")
            media = file.get("mediaType", "application/octet-stream")
            if not isinstance(media, str) or not media or len(media) > 100 or any(ord(c) < 32 for c in media):
                raise DomainError("Invalid media type", "invalid_result")
            seen.add(name)
            total += len(content)
            if total > 32 * 1024 * 1024:
                raise DomainError("Native artifact limit exceeded", "artifact_limit")
            prepared.append((file, name, content))
        publication_digest = fingerprint(
            {
                "provenance": provenance,
                "task": task.model_dump(mode="json"),
                "files": [
                    {
                        "path": name,
                        "sha256": hashlib.sha256(content).hexdigest(),
                        "role": file["role"],
                        "expectedSha256": file.get("expectedSha256"),
                        "mediaType": file.get("mediaType", "application/octet-stream"),
                    }
                    for file, name, content in prepared
                ],
            }
        )
        with self.projects.transaction() as session:
            project, run = GraphRepository(self.projects)._check(session, scope)
            prior = session.scalar(
                select(NativePublication).where(
                    NativePublication.run_id == run.id,
                    NativePublication.task_id == task.id,
                    NativePublication.job_id == provenance["nativeJobId"],
                )
            )
            composed = None
            if composition is not None:
                from kleos.artifacts.composition import publication_closure
                composed = publication_closure(session, scope, task, prepared, provenance, composition, prior=prior)
            elif 'compositionId' in provenance:
                raise DomainError('Compilation publication requires its exact grant', 'composition_conflict')
            if prior:
                if prior.fingerprint != publication_digest:
                    raise DomainError("Native publication changed during replay", "result_conflict")
                return [ArtifactRef.model_validate(ref) for ref in prior.references]
            baseline = dict(project.data.get("sourceManifest", {}))
            for file, name, _ in prepared:
                if file["role"] == "source":
                    old = session.get(Artifact, baseline[name]) if name in baseline else None
                    if (old.sha256 if old else None) != file["expectedSha256"]:
                        raise DomainError("Native source baseline changed", "revision_conflict")
            references, closure = [], dict(composed if composed is not None else baseline)
            rows = []
            for file, name, content in prepared:
                digest = self.blobs.put(content)
                row = Artifact(
                    id=str(uuid4()),
                    project_id=project.id,
                    revision=scope.revision,
                    name=name,
                    sha256=digest,
                    size=len(content),
                    media_type=file.get("mediaType", "application/octet-stream"),
                    status="candidate",
                    lane=task.capability.value,
                    details={
                        **provenance,
                        "origin": "native_candidate",
                        "runId": run.id,
                        "taskId": task.id,
                        "role": file["role"],
                    },
                )
                session.add(row)
                rows.append(row)
                if file["role"] == "source":
                    closure[name] = row.id
                references.append(
                    ArtifactRef(
                        id=row.id,
                        path=name,
                        sha256=digest,
                        revision=scope.revision,
                        size=len(content),
                        media_type=row.media_type,
                    )
                )
            for row in rows:
                row.details = {**row.details, "sourceClosure": closure}
                self.projects._emit(session, project, "artifact.created", artifact_view(row))
            session.add(
                NativePublication(
                    id=str(uuid4()),
                    run_id=run.id,
                    task_id=task.id,
                    job_id=provenance["nativeJobId"],
                    fingerprint=publication_digest,
                    references=[ref.model_dump(mode="json") for ref in references],
                )
            )
            return references
