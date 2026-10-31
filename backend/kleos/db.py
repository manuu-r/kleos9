from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


def now():
    return datetime.now(UTC)


def uid():
    return str(uuid4())


class Base(DeclarativeBase):
    pass


class Record:
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Organization(Record, Base):
    __tablename__ = "organizations"
    name: Mapped[str] = mapped_column(String(200))


class Membership(Record, Base):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("subject", "tenant_id"),)
    subject: Mapped[str] = mapped_column(String(200), index=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    role: Mapped[str] = mapped_column(String(30), default="member")


class Account(Record, Base):
    """A password login. Its id is the subject used by memberships and sessions."""

    __tablename__ = "accounts"
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    password_hash: Mapped[str] = mapped_column(String(300))
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Invite(Record, Base):
    """A single-use signup code. Only its SHA-256 digest is stored."""

    __tablename__ = "invites"
    code_hash: Mapped[str] = mapped_column(String(64), unique=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    role: Mapped[str] = mapped_column(String(30), default="member")
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    created_by: Mapped[str] = mapped_column(String(200), default="cli")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    redeemed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    redeemed_by: Mapped[str | None] = mapped_column(String(36), nullable=True)


class Project(Record, Base):
    __tablename__ = "projects"
    tenant_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    owner_id: Mapped[str] = mapped_column(String(200))
    name: Mapped[str] = mapped_column(String(200))
    brief: Mapped[str] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    engineering_revision: Mapped[int] = mapped_column(Integer, default=1)
    policy_revision: Mapped[int] = mapped_column(Integer, default=1)
    event_sequence: Mapped[int] = mapped_column(Integer, default=0)
    event_floor: Mapped[int] = mapped_column(Integer, default=0)
    stage: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(30), default="draft")
    model_policy: Mapped[dict] = mapped_column(JSON, default=dict)
    requirements: Mapped[dict] = mapped_column(JSON, default=dict)
    interfaces: Mapped[dict] = mapped_column(JSON, default=dict)
    state: Mapped[dict] = mapped_column(JSON, default=dict)
    budget_usd: Mapped[float] = mapped_column(Float, default=0)
    spent_usd: Mapped[float] = mapped_column(Float, default=0)
    reserved_usd: Mapped[float] = mapped_column(Float, default=0)
    accepted_release_id: Mapped[str | None] = mapped_column(String(36))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProjectMembership(Record, Base):
    __tablename__ = "project_memberships"
    __table_args__ = (UniqueConstraint("subject", "project_id"),)
    subject: Mapped[str] = mapped_column(String(200), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    role: Mapped[str] = mapped_column(String(30), default="member")


class CommandReceipt(Record, Base):
    __tablename__ = "commands"
    __table_args__ = (UniqueConstraint("tenant_id", "actor_id", "idempotency_key"),)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    actor_id: Mapped[str] = mapped_column(String(200))
    idempotency_key: Mapped[str] = mapped_column(String(200))
    fingerprint: Mapped[str] = mapped_column(String(64))
    type: Mapped[str] = mapped_column(String(100))
    project_id: Mapped[str | None] = mapped_column(String(36), index=True)
    response: Mapped[dict] = mapped_column(JSON)


class Outbox(Record, Base):
    __tablename__ = "outbox"
    __table_args__ = (UniqueConstraint("key"),)
    key: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(40), index=True)
    payload: Mapped[dict] = mapped_column(JSON)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failures: Mapped[int] = mapped_column(Integer, default=0)


class Run(Record, Base):
    __tablename__ = "runs"
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    command_id: Mapped[str] = mapped_column(String(36))
    kind: Mapped[str] = mapped_column(String(60))
    revision: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    trace_id: Mapped[str] = mapped_column(String(64), default=uid)
    blocker: Mapped[str | None] = mapped_column(Text)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Task(Record, Base):
    __tablename__ = "tasks"
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    lane: Mapped[str] = mapped_column(String(40))
    kind: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(30), default="queued")
    revision: Mapped[int] = mapped_column(Integer)
    fence: Mapped[int] = mapped_column(Integer, default=0)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    dependencies: Mapped[list] = mapped_column(JSON, default=list)
    result: Mapped[dict] = mapped_column(JSON, default=dict)


class Attempt(Record, Base):
    __tablename__ = "attempts"
    __table_args__ = (UniqueConstraint("task_id", "number"),)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    fence: Mapped[int] = mapped_column(Integer)
    policy_revision: Mapped[int] = mapped_column(Integer)
    provider: Mapped[str] = mapped_column(String(40))
    model: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(30), default="running")
    context: Mapped[dict] = mapped_column(JSON)
    output: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text)


class Invocation(Record, Base):
    __tablename__ = "invocations"
    __table_args__ = (UniqueConstraint("attempt_id", "operation"),)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    attempt_id: Mapped[str] = mapped_column(ForeignKey("attempts.id"))
    operation: Mapped[str] = mapped_column(String(60))
    provider: Mapped[str] = mapped_column(String(40))
    model: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(30), default="pending")
    reserved_usd: Mapped[float] = mapped_column(Float, default=0)
    estimated_usd: Mapped[float] = mapped_column(Float, default=0)
    usage: Mapped[dict] = mapped_column(JSON, default=dict)
    transcript_key: Mapped[str | None] = mapped_column(Text)
    external_id: Mapped[str | None] = mapped_column(String(200))
    output: Mapped[dict] = mapped_column(JSON, default=dict)


class DailyBudget(Base):
    __tablename__ = "daily_budgets"
    day: Mapped[str] = mapped_column(String(10), primary_key=True)
    spent: Mapped[float] = mapped_column(Float, default=0)
    reserved: Mapped[float] = mapped_column(Float, default=0)


class Artifact(Record, Base):
    __tablename__ = "artifacts"
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    task_id: Mapped[str | None] = mapped_column(String(36))
    attempt_id: Mapped[str | None] = mapped_column(String(36))
    revision: Mapped[int] = mapped_column(Integer)
    lane: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(250))
    key: Mapped[str] = mapped_column(Text, unique=True)
    sha256: Mapped[str] = mapped_column(String(64))
    size: Mapped[int] = mapped_column(Integer)
    media_type: Mapped[str] = mapped_column(String(100), default="application/octet-stream")
    status: Mapped[str] = mapped_column(String(30), default="candidate")
    provenance: Mapped[dict] = mapped_column(JSON, default=dict)
    dependencies: Mapped[list] = mapped_column(JSON, default=list)


class Validation(Record, Base):
    __tablename__ = "validations"
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    task_id: Mapped[str] = mapped_column(String(36))
    revision: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(30))
    evidence_kind: Mapped[str] = mapped_column(String(30), default="digital")
    artifact_hashes: Mapped[dict] = mapped_column(JSON, default=dict)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    validator: Mapped[str] = mapped_column(String(150))


class Release(Record, Base):
    __tablename__ = "releases"
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    mode: Mapped[str] = mapped_column(String(30))
    manifest: Mapped[dict] = mapped_column(JSON)
    approved_by: Mapped[str] = mapped_column(String(200))


class Event(Record, Base):
    __tablename__ = "events"
    __table_args__ = (UniqueConstraint("project_id", "sequence"), Index("event_replay", "project_id", "sequence"))
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    project_id: Mapped[str] = mapped_column(String(36))
    sequence: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(100))
    data: Mapped[dict] = mapped_column(JSON)
    trace_id: Mapped[str | None] = mapped_column(String(64))


class ExternalIntent(Record, Base):
    __tablename__ = "external_intents"
    __table_args__ = (UniqueConstraint("project_id", "operation", "revision"),)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    operation: Mapped[str] = mapped_column(String(100))
    revision: Mapped[int] = mapped_column(Integer)
    mode: Mapped[str] = mapped_column(String(30), default="simulated")
    status: Mapped[str] = mapped_column(String(30), default="draft")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    receipt: Mapped[dict] = mapped_column(JSON, default=dict)


class Connector(Record, Base):
    __tablename__ = "connectors"
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    name: Mapped[str] = mapped_column(String(100))
    manifest: Mapped[dict] = mapped_column(JSON)
    level: Mapped[str] = mapped_column(String(30), default="listed")
    enabled: Mapped[bool] = mapped_column(default=False)


class Inbox(Record, Base):
    __tablename__ = "inbox"
    __table_args__ = (UniqueConstraint("connector_id", "external_event_id"),)
    connector_id: Mapped[str] = mapped_column(String(36))
    external_event_id: Mapped[str] = mapped_column(String(200))
    payload: Mapped[dict] = mapped_column(JSON)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Source(Record, Base):
    __tablename__ = "sources"
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    uri: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64))
    text: Mapped[str] = mapped_column(Text)
    authority: Mapped[str] = mapped_column(String(30), default="research")
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked: Mapped[bool] = mapped_column(default=False)


class NativeJob(Record, Base):
    __tablename__ = "native_jobs"
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    task_id: Mapped[str] = mapped_column(String(36), index=True)
    attempt_id: Mapped[str] = mapped_column(String(36), unique=True)
    purpose: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(30), default="pending")
    external_id: Mapped[str | None] = mapped_column(String(200))
    token_hash: Mapped[str] = mapped_column(String(64))
    request: Mapped[dict] = mapped_column(JSON)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class NativeSessionStep(Record, Base):
    __tablename__ = "native_session_steps"
    __table_args__ = (
        UniqueConstraint("job_id", "request_key"),
        UniqueConstraint("job_id", "sequence"),
    )
    job_id: Mapped[str] = mapped_column(ForeignKey("native_jobs.id", ondelete="CASCADE"), index=True)
    attempt_id: Mapped[str] = mapped_column(ForeignKey("attempts.id", ondelete="CASCADE"), index=True)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    request_key: Mapped[str] = mapped_column(String(200))
    sequence: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30), default="queued")
    action: Mapped[str] = mapped_column(String(30))
    arguments: Mapped[dict] = mapped_column(JSON, default=dict)
    request_hash: Mapped[str] = mapped_column(String(64))
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    result_hash: Mapped[str | None] = mapped_column(String(64))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Consent(Record, Base):
    __tablename__ = "training_consents"
    __table_args__ = (UniqueConstraint("tenant_id", "project_id"),)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    project_id: Mapped[str] = mapped_column(String(36))
    actor_id: Mapped[str] = mapped_column(String(200))
    opted_in: Mapped[bool] = mapped_column(default=False)
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Dataset(Record, Base):
    __tablename__ = "datasets"
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    manifest: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(30), default="draft")


class TrainedModel(Record, Base):
    __tablename__ = "trained_models"
    dataset_id: Mapped[str] = mapped_column(ForeignKey("datasets.id"))
    status: Mapped[str] = mapped_column(String(30), default="candidate")
    config: Mapped[dict] = mapped_column(JSON)
    evaluation: Mapped[dict] = mapped_column(JSON, default=dict)
    promoted_by: Mapped[str | None] = mapped_column(String(200))


class SessionRecord(Base):
    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    subject: Mapped[str] = mapped_column(String(200))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    csrf_hash: Mapped[str] = mapped_column(String(64))


class Database:
    def __init__(self, url: str):
        kw = ({"connect_args": {"check_same_thread": False}} if url.startswith("sqlite")
              else {"pool_size": 20, "max_overflow": 0, "pool_timeout": 10})
        self.engine = create_engine(url, pool_pre_ping=True, **kw)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)

    @contextmanager
    def transaction(self, read_only=False):
        with self.sessions.begin() as session:
            if read_only and self.engine.dialect.name == "postgresql":
                session.connection(execution_options={"isolation_level": "REPEATABLE READ", "postgresql_readonly": True})
            yield session

    def close(self):
        self.engine.dispose()
