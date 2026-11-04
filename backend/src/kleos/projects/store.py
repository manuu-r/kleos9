"""Transactional project authority. LangGraph checkpoints never replace these records."""

import copy
import hashlib
import json
from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import JSON, Integer, String, UniqueConstraint, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from kleos.contracts.models import Actor, ArtifactRef, DomainError, ProjectContext


class Base(DeclarativeBase):
    pass


class Project(Base):
    __tablename__ = "kleos2_projects"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    owner_id: Mapped[str] = mapped_column(String(100))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    event_sequence: Mapped[int] = mapped_column(Integer, default=0)
    data: Mapped[dict] = mapped_column(JSON)

    @property
    def context_revision(self):
        return self.data.get("contextRevision", self.revision)

    def advance_revision(self, *, context=True):
        previous = self.context_revision
        self.revision += 1
        self.data = {**self.data, "contextRevision": self.revision if context else previous,
            "engineeringRevision": self.revision if context else self.data.get("engineeringRevision", previous)}


class Membership(Base):
    __tablename__ = "kleos2_project_members"
    project_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    role: Mapped[str] = mapped_column(String(20))


class Event(Base):
    __tablename__ = "kleos2_events"
    project_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    sequence: Mapped[int] = mapped_column(Integer, primary_key=True)
    data: Mapped[dict] = mapped_column(JSON)


class Objective(Base):
    __tablename__ = "kleos2_objectives"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30), default="pending")
    data: Mapped[dict] = mapped_column(JSON)


class Receipt(Base):
    __tablename__ = "kleos2_command_receipts"
    __table_args__ = (UniqueConstraint("tenant_id", "user_id", "key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(100))
    user_id: Mapped[str] = mapped_column(String(100))
    key: Mapped[str] = mapped_column(String(128))
    fingerprint: Mapped[str] = mapped_column(String(64))
    result: Mapped[dict] = mapped_column(JSON)


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class ProjectStore:
    def __init__(self, url: str, *, require_account_authority=False):
        self.engine = create_engine(url)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        # Standalone domain fixtures may omit account tables. Runtime always enables this.
        self.require_account_authority = require_account_authority

    def initialize_for_tests(self):
        """Production uses the new schema's migrations, never automatic table creation."""
        from kleos.infrastructure.schema import metadata
        metadata().create_all(self.engine)

    @contextmanager
    def transaction(self):
        with self.sessions.begin() as session:
            yield session

    def authorized(self, session, actor: Actor, project_id: str, *, write=False):
        project = session.scalar(
            select(Project)
            .where(Project.id == project_id, Project.tenant_id == actor.tenant_id)
            .with_for_update()
        )
        membership = session.get(Membership, (project_id, actor.user_id)) if project else None
        if (project is None or membership is None or project.data.get('internalPurpose') is not None
                or project.data.get('deletion') is not None):
            raise DomainError("Project not found", "not_found", 404)
        if write and membership.role not in {"owner", "editor"}:
            raise DomainError("Project is read-only", "forbidden", 403)
        try:
            self.authorize_account(session, actor, write=write)
        except DomainError:
            if not write:
                raise DomainError('Project not found', 'not_found', 404) from None
            raise
        return project

    def authorize_account(self, session, actor, *, write=False):
        if not self.require_account_authority:
            return
        from kleos.access.service import Account, OrganizationMember

        # Existing-project callers lock Project first. Keep this order for workers,
        # previews and repository publication while revocation updates membership.
        account = session.get(Account, actor.user_id, with_for_update=True, populate_existing=True)
        member = session.get(OrganizationMember, (actor.tenant_id, actor.user_id),
                             with_for_update=True, populate_existing=True)
        if account is None or member is None or (write and member.role not in {'owner', 'admin', 'member'}):
            raise DomainError('Current organization access is required', 'forbidden', 403)

    def _emit(self, session, project, kind, payload):
        project.event_sequence += 1
        session.add(
            Event(
                project_id=project.id,
                sequence=project.event_sequence,
                data={
                    "type": kind,
                    "payload": payload,
                    "revision": project.revision,
                    "createdAt": datetime.now(UTC).isoformat(),
                },
            )
        )

    @staticmethod
    def clear_pending(project, run_id, status):
        state = project.data.get("state", {})
        planning = state.get("planning", {})
        if planning.get("runId") == run_id:
            project.data = {
                **project.data,
                "status": status,
                "state": {**state, "planning": {**planning, "pending": False}},
            }

    def _command(self, actor, key, body, operation, *, replay_authorizer=None):
        if not isinstance(key, str) or not 1 <= len(key) <= 128:
            raise DomainError("Provide an idempotency key", "invalid_request", 422)
        digest = fingerprint(body)
        with self.transaction() as session:
            # Serialize actor commands across project creation and mutations on PostgreSQL.
            if self.engine.dialect.name == "postgresql":
                from sqlalchemy import text

                lock = int.from_bytes(
                    hashlib.sha256((actor.tenant_id + ":" + actor.user_id).encode()).digest()[:8],
                    "big",
                    signed=True,
                )
                session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock})
            prior = session.scalar(
                select(Receipt).where(
                    Receipt.tenant_id == actor.tenant_id, Receipt.user_id == actor.user_id, Receipt.key == key
                )
            )
            if prior:
                if prior.fingerprint != digest:
                    raise DomainError("Idempotency key was used for different input", "idempotency_conflict")
                if replay_authorizer is not None:
                    replay_authorizer(session, prior.result)
                if prior.result.get("projectId"):
                    self.authorized(session, actor, prior.result["projectId"])
                return prior.result
            command_id = str(uuid4())
            result = {"commandId": command_id, **operation(session)}
            session.add(
                Receipt(
                    id=command_id,
                    tenant_id=actor.tenant_id,
                    user_id=actor.user_id,
                    key=key,
                    fingerprint=digest,
                    result=result,
                )
            )
            return result

    def create(self, actor, brief, key, *, model_policy=None):
        if not isinstance(brief, str) or not brief.strip() or len(brief) > 12000:
            raise DomainError("Provide a bounded project brief", "invalid_request", 422)

        def operation(session):
            self.authorize_account(session, actor, write=True)
            project = Project(
                id=str(uuid4()),
                tenant_id=actor.tenant_id,
                owner_id=actor.user_id,
                revision=1,
                event_sequence=0,
                data={
                    "brief": brief,
                    "name": brief[:80],
                    "createdAt": datetime.now(UTC).isoformat(),
                    "requirements": {},
                    "interfaces": {},
                    "approvedDesign": None,
                    "decisions": [],
                    "modelPolicy": model_policy or {},
                },
            )
            session.add(project)
            session.add(Membership(project_id=project.id, user_id=actor.user_id, role="owner"))
            self._emit(session, project, "project.created", {"projectId": project.id})
            return {"projectId": project.id, "revision": 1}

        body = {"type": "create", "brief": brief}
        if model_policy is not None:
            body["modelPolicy"] = model_policy
        return self._command(actor, key, body, operation)

    def context(self, actor, project_id):
        with self.transaction() as session:
            p = self.authorized(session, actor, project_id)
            from kleos.artifacts.service import Artifact

            source_ids = list(p.data.get("sourceManifest", {}).values())
            image_ids = list(p.data.get("referenceImageIds", []))
            rows = (
                list(
                    session.scalars(
                        select(Artifact).where(
                            Artifact.project_id == p.id, Artifact.id.in_(source_ids + image_ids)
                        )
                    )
                )
                if source_ids or image_ids
                else []
            )
            from kleos.artifacts.repository_metadata import resolve_repository_metadata
            repository_metadata, repository_metadata_hash = resolve_repository_metadata(session, p)
            return ProjectContext(
                tenant_id=p.tenant_id,
                project_id=p.id,
                revision=p.context_revision,
                brief=p.data["brief"],
                requirements=p.data["requirements"],
                interfaces=p.data["interfaces"],
                approved_design=p.data.get("approvedDesign"),
                decisions=p.data.get("decisions", []),
                repository_metadata=repository_metadata,
                repository_metadata_hash=repository_metadata_hash,
                artifacts=[
                    ArtifactRef(
                        id=a.id,
                        path=a.name,
                        sha256=a.sha256,
                        revision=a.revision,
                        media_type=a.media_type,
                        size=a.size,
                    )
                    for a in rows
                    if a.id in source_ids
                ],
                reference_images=[
                    ArtifactRef(
                        id=a.id,
                        path=a.name,
                        sha256=a.sha256,
                        revision=a.revision,
                        media_type=a.media_type,
                        size=a.size,
                    )
                    for a in rows
                    if a.id in image_ids
                ],
            )

    def submit(self, actor, project_id, objective, expected_revision, key):
        if not isinstance(objective, str) or not objective.strip() or len(objective) > 12000:
            raise DomainError("Provide a bounded objective", "invalid_request", 422)

        def operation(session):
            p = self.authorized(session, actor, project_id, write=True)
            if p.revision != expected_revision:
                raise DomainError("Project changed; refresh before submitting", "revision_conflict")
            item = Objective(
                id=str(uuid4()),
                project_id=p.id,
                revision=p.revision + 1,
                status="pending",
                data={
                    "objective": objective,
                    "tasks": [],
                    "submittedBy": actor.user_id,
                    "modelPolicySnapshot": dict(p.data.get("modelPolicy", {})),
                    "toolPolicySnapshot": copy.deepcopy({"policy": p.data.get("state", {}).get("toolPolicy", {}),
                                                         "bindings": p.data.get("toolPolicyBindings", {})}),
                    "createdAt": datetime.now(UTC).isoformat(),
                },
            )
            session.add(item)
            p.advance_revision()
            self._emit(session, p, "objective.submitted", {"runId": item.id, "objective": objective})
            return {"projectId": p.id, "runId": item.id, "revision": p.revision}

        return self._command(
            actor,
            key,
            {
                "type": "submit",
                "projectId": project_id,
                "objective": objective,
                "expectedRevision": expected_revision,
            },
            operation,
        )

    def events(self, actor, project_id, after=0):
        with self.transaction() as session:
            self.authorized(session, actor, project_id)
            return [
                {"sequence": e.sequence, **e.data}
                for e in session.scalars(
                    select(Event)
                    .where(Event.project_id == project_id, Event.sequence > after)
                    .order_by(Event.sequence)
                    .limit(200)
                )
            ]

    def cancel(self, actor, project_id, run_id, expected_revision, key):
        def operation(session):
            project = self.authorized(session, actor, project_id, write=True)
            run = session.get(Objective, run_id)
            if run is None or run.project_id != project_id:
                raise DomainError("Objective not found", "not_found", 404)
            if project.revision != expected_revision:
                raise DomainError("Project changed; refresh before cancelling", "revision_conflict")
            if run.status not in {"succeeded", "cancelled"}:
                run.status = "cancelled"
                self.clear_pending(project, run.id, "cancelled")
                self._emit(session, project, "objective.cancelled", {"runId": run.id})
            return {"projectId": project.id, "runId": run.id, "revision": project.revision}

        return self._command(
            actor,
            key,
            {
                "operation": "run.cancel",
                "projectId": project_id,
                "runId": run_id,
                "expectedRevision": expected_revision,
            },
            operation,
        )
