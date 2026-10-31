import asyncio
import base64
import hashlib
import hmac
import json
import time
from contextlib import asynccontextmanager
from datetime import UTC
from typing import Literal

from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import Field
from sqlalchemy import select, update
from starlette.concurrency import run_in_threadpool

from .admin_api import register_admin_routes
from .auth import (
    LOCKOUT,
    MAX_FAILED_LOGINS,
    WEB_SESSION,
    Principal,
    WorkspaceAccess,
    authenticate,
    authenticate_subject,
    check_password,
    hash_code,
    hash_password,
    normalize_email,
    start_session,
)
from .companion_preview import CompanionPreviewRequest, CompanionPreviews, CompanionPreviewView
from .config import Settings, get_settings
from .contracts import ArtifactUpload, Command, CommandAccepted, DomainError, JobResult, WireModel
from .db import (
    Account,
    Artifact,
    Attempt,
    DailyBudget,
    Database,
    Invite,
    Invocation,
    Membership,
    NativeJob,
    Organization,
    ProjectMembership,
    SessionRecord,
    Task,
    now,
    uid,
)
from .domain import ControlPlane, emit, get_project, lock_platform
from .engine import EngineeringEngine
from .engineering_preview import EngineeringPreview
from .integration_api import register_routes
from .packaging import ReleasePackaging
from .storage import ArtifactStore, artifact_key, digest
from .telemetry import configure, metric, redact
from .views import (
    ArtifactDownloadView,
    EngineeringToolCatalogView,
    EventView,
    ModelCatalogView,
    ProjectsView,
    RunView,
    WorkspaceView,
)


class SignupRequest(WireModel):
    invite_code: str = Field(min_length=8, max_length=200)
    email: str = Field(max_length=320)
    password: str = Field(min_length=12, max_length=256)
    name: str = Field(default="", max_length=200)
    native: bool = False


class LoginRequest(WireModel):
    email: str = Field(max_length=320)
    password: str = Field(max_length=256)
    native: bool = False


class SetupRequest(WireModel):
    setup_token: str = Field(min_length=1, max_length=256)
    organization: str = Field(min_length=1, max_length=200)
    email: str = Field(max_length=320)
    password: str = Field(min_length=12, max_length=256)
    name: str = Field(default="", max_length=200)
    native: bool = False


class InvocationReconciliation(WireModel):
    note: str = Field(min_length=20, max_length=2000)
    actual_cost_usd: float | None = Field(default=None, ge=0, allow_inf_nan=False)


class ProjectAccessRequest(WireModel):
    subject: str = Field(min_length=1, max_length=200)
    role: Literal["member", "viewer"]


MAX_USER_INPUT_BYTES = 16 * 1024 * 1024
MAX_UPLOAD_REQUEST_BYTES = 24 * 1024 * 1024 + 64 * 1024


def create_app(settings: Settings | None = None, database=None, storage=None):
    settings = settings or get_settings()
    db = database or Database(settings.database_url)
    store = storage or ArtifactStore(settings)
    control = ControlPlane(db, settings, store)
    engineering_previews = EngineeringPreview(db, store)
    companion_previews = CompanionPreviews(db, settings, store)

    @asynccontextmanager
    async def lifespan(app):
        configure(settings.otlp_endpoint, settings)
        yield
        if database is None:
            db.close()

    app = FastAPI(title="Kleos9 Engineering API", version="1.0.0", lifespan=lifespan)
    if settings.otlp_endpoint:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
        FastAPIInstrumentor.instrument_app(app, excluded_urls="/api/v1/health,/api/v1/ready",
            http_capture_headers_server_request=[], http_capture_headers_server_response=[],
            http_capture_headers_sanitize_fields=["authorization", "cookie", "set-cookie", "x-api-key", "x-csrf-token"])
        SQLAlchemyInstrumentor().instrument(engine=db.engine)
    app.state.settings, app.state.db, app.state.store, app.state.control = settings, db, store, control
    app.state.companion_previews = companion_previews
    event_reads = {}

    async def shared_events(principal, project_id, cursor):
        # Every subscriber was authorized before opening the stream and is rechecked below.
        # Share the same committed-event read across subscribers at the same project cursor.
        key = (principal.tenant_id, project_id, cursor)
        cached = event_reads.get(key)
        if cached is None or time.monotonic() - cached[0] >= .45:
            if len(event_reads) >= 256:
                event_reads.pop(next(iter(event_reads)), None)
            task = asyncio.create_task(asyncio.to_thread(control.events, principal, project_id, cursor))
            event_reads[key] = (time.monotonic(), task)
        return await asyncio.shield(event_reads[key][1])
    register_routes(app, db, settings, store)
    register_admin_routes(app, db)

    @app.exception_handler(DomainError)
    async def domain_error(_request, exc):
        if exc.code in {"gate_failed", "stale_attempt", "stale_validation", "forbidden", "budget_exhausted"}:
            metric("GateViolation", 1, Environment=settings.environment)
        return JSONResponse(status_code=exc.status, content={"message": exc.message, "code": exc.code})

    @app.middleware("http")
    async def request_limits(request, call_next):
        started = time.perf_counter()
        if request.method in {"POST", "PUT", "PATCH"}:
            limit = 1024 * 1024
            parts = request.url.path.split("/")
            is_native_result = (request.method == "POST" and len(parts) == 7
                                and parts[1:5] == ["api", "v1", "internal", "jobs"]
                                and parts[5] and parts[6] == "result")
            if is_native_result:
                authorization = request.headers.get("authorization", "")
                token = authorization.removeprefix("Bearer ") if authorization.startswith("Bearer ") else ""
                try:
                    await run_in_threadpool(execution().authorize, parts[5], token)
                except DomainError as exc:
                    return await domain_error(request, exc)
                limit = 64 * 1024 * 1024
            is_upload = (request.method == "POST" and len(parts) == 6
                         and parts[1:4] == ["api", "v1", "projects"] and parts[4] and parts[5] == "artifacts")
            if is_upload:
                # The larger envelope is available only to an authenticated
                # project writer. Authenticate also enforces cookie-session CSRF.
                def authorize_upload():
                    principal = authenticate(request)
                    with db.transaction() as session:
                        get_project(session, parts[4], principal, write=True, lock=False)
                try:
                    await run_in_threadpool(authorize_upload)
                except DomainError as exc:
                    return await domain_error(request, exc)
                limit = MAX_UPLOAD_REQUEST_BYTES
            try:
                length = int(request.headers.get("content-length", 0))
            except ValueError:
                return JSONResponse(status_code=400, content={"message": "Invalid content length"})
            if length > limit:
                return JSONResponse(status_code=413, content={"message": "Request too large"})
            body = bytearray()
            async for chunk in request.stream():
                if len(body) + len(chunk) > limit:
                    return JSONResponse(status_code=413, content={"message": "Request too large"})
                body.extend(chunk)
            request._body = bytes(body)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Server-Timing"] = f"app;dur={(time.perf_counter() - started)*1000:.2f}"
        return response

    # CORS must also wrap early authorization/size errors from request_limits.
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=True,
                       allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
                       allow_headers=["Authorization", "Content-Type", "Idempotency-Key",
                                      "X-Organization-Id", "X-CSRF-Token", "Last-Event-ID"])

    @app.get("/api/v1/health")
    def health():
        return {"status": "ok", "mode": "real_engineering_simulated_fulfillment", "version": "0.1.0"}

    @app.get("/api/v1/engineering-tools", response_model=EngineeringToolCatalogView)
    def engineering_tools(principal: Principal = Depends(authenticate)):
        return control.tool_registry.catalog(principal.tenant_id)

    @app.get("/api/v1/ready")
    def ready():
        with db.transaction() as session:
            session.execute(select(1))
        return {"status": "ready"}

    @app.get("/api/v1/model-catalog", response_model=ModelCatalogView)
    def catalog(principal: Principal = Depends(authenticate)):
        return control.registry.catalog()

    @app.post("/api/v1/commands", status_code=202, response_model=CommandAccepted)
    def command(command: Command, principal: Principal = Depends(authenticate),
                idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=200)):
        return control.command(principal, command, idempotency_key)

    @app.get("/api/v1/projects", response_model=ProjectsView)
    def projects(principal: Principal = Depends(authenticate), after: str | None = None,
                 limit: int = Query(default=25, ge=1, le=100)):
        return control.projects(principal, after, limit)

    @app.get("/api/v1/projects/{project_id}/workspace", response_model=WorkspaceView)
    async def workspace(request: Request, project_id: str, principal: Principal = Depends(authenticate)):
        access = getattr(request.state, "workspace_access", None)
        if isinstance(access, WorkspaceAccess) and access.principal == principal and access.project_id == project_id:
            snapshot = control._workspace_cache.get((principal.tenant_id, project_id, access.revision))
            if snapshot is not None:
                # The joined authorization query observed this exact immutable revision.
                # A cache miss must still build snapshot + cursor in one repeatable-read transaction.
                return snapshot
        return await run_in_threadpool(control.workspace, principal, project_id)

    @app.get("/api/v1/projects/{project_id}/engineering-preview")
    def engineering_preview(project_id: str, principal: Principal = Depends(authenticate)):
        return engineering_previews.read(principal, project_id)

    @app.post('/api/v1/projects/{project_id}/companion-preview', response_model=CompanionPreviewView)
    def companion_preview(project_id: str, body: CompanionPreviewRequest,
                          principal: Principal = Depends(authenticate)):
        return companion_previews.mint(principal, project_id, body.artifact_id)

    @app.get("/api/v1/projects/{project_id}/members")
    def project_members(project_id: str, principal: Principal = Depends(authenticate)):
        principal.require_admin()
        with db.transaction() as session:
            project = get_project(session, project_id, principal)
            rows = session.scalars(select(ProjectMembership).where(ProjectMembership.project_id == project_id)).all()
            return {"ownerId": project.owner_id, "members": [{"subject": m.subject, "role": m.role} for m in rows]}

    @app.post("/api/v1/projects/{project_id}/members")
    def project_access(project_id: str, body: ProjectAccessRequest, principal: Principal = Depends(authenticate)):
        principal.require_admin()
        with db.transaction() as session:
            project = get_project(session, project_id, principal, write=True)
            if not session.scalar(select(Membership.id).where(Membership.subject == body.subject,
                                                              Membership.tenant_id == principal.tenant_id)):
                raise DomainError("Invite this person to the organization before granting project access", 409)
            membership = session.scalar(select(ProjectMembership).where(ProjectMembership.project_id == project_id,
                                                                         ProjectMembership.subject == body.subject))
            if membership and membership.role == body.role:
                return {"subject": body.subject, "role": body.role}
            if membership:
                membership.role = body.role
            else:
                session.add(ProjectMembership(project_id=project_id, subject=body.subject, role=body.role))
            emit(session, project, "audit.projectAccessChanged", {"actor": principal.subject,
                                                                 "subject": body.subject, "role": body.role})
            return {"subject": body.subject, "role": body.role}

    @app.delete("/api/v1/projects/{project_id}/members/{subject}")
    def revoke_project_access(project_id: str, subject: str, principal: Principal = Depends(authenticate)):
        principal.require_admin()
        with db.transaction() as session:
            project = get_project(session, project_id, principal, write=True)
            membership = session.scalar(select(ProjectMembership).where(ProjectMembership.project_id == project_id,
                                                                         ProjectMembership.subject == subject))
            if membership:
                session.delete(membership)
                emit(session, project, "audit.projectAccessRevoked", {"actor": principal.subject, "subject": subject})
            return {"revoked": True}

    @app.get("/api/v1/projects/{project_id}/events/history", response_model=list[EventView])
    def event_history(project_id: str, after: int = Query(default=0, ge=0),
                      principal: Principal = Depends(authenticate)):
        return control.events(principal, project_id, after)

    @app.get("/api/v1/projects/{project_id}/events")
    async def events(project_id: str, request: Request, principal: Principal = Depends(authenticate),
                     after: int = Query(default=0, ge=0), once: bool = False):
        last = request.headers.get("last-event-id")
        if last:
            try:
                after = max(after, int(last))
            except ValueError:
                raise DomainError("Invalid event cursor", 400)
        initial = await asyncio.to_thread(control.events, principal, project_id, after)
        async def stream():
            cursor, rows, idle = after, initial, 0
            current_principal = principal
            while True:
                for event in rows:
                    cursor = event["sequence"]
                    yield f"id: {cursor}\nevent: activity\ndata: {json.dumps(event)}\n\n"
                if once or await request.is_disconnected():
                    break
                await asyncio.sleep(0.5)
                idle += 1
                if idle % 30 == 0:
                    yield ": heartbeat\n\n"
                    # Recheck membership/session revocation on persistent streams.
                    try:
                        current_principal = await asyncio.to_thread(authenticate, request)
                        # Recheck project access even when a cached event page is available.
                        await asyncio.to_thread(control.events, current_principal, project_id, cursor)
                    except DomainError:
                        yield 'event: auth_expired\ndata: {}\n\n'
                        break
                try:
                    rows = await shared_events(current_principal, project_id, cursor)
                except DomainError as exc:
                    event = "cursor_expired" if exc.status == 410 else "auth_expired"
                    yield f"event: {event}\ndata: {{}}\n\n"
                    break
        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"X-Accel-Buffering": "no"})

    @app.get("/api/v1/runs/{run_id}", response_model=RunView)
    def run(run_id: str, principal: Principal = Depends(authenticate)):
        return control.inspect_run(principal, run_id)

    def authorized_artifact(artifact_id, principal):
        with db.transaction() as session:
            artifact = session.scalar(select(Artifact).where(
                Artifact.id == artifact_id, Artifact.tenant_id == principal.tenant_id))
            if not artifact:
                raise DomainError("Artifact not found", 404)
            get_project(session, artifact.project_id, principal)
            return artifact

    @app.get("/api/v1/artifacts/{artifact_id}/download", response_model=ArtifactDownloadView)
    def download(artifact_id: str, principal: Principal = Depends(authenticate)):
        artifact = authorized_artifact(artifact_id, principal)
        return {"url": store.download_url(artifact.key) or f"/api/v1/artifacts/{artifact.id}/content",
                "sha256": artifact.sha256, "name": artifact.name}

    @app.post("/api/v1/projects/{project_id}/artifacts", status_code=201)
    def upload_artifact(project_id: str, body: ArtifactUpload, principal: Principal = Depends(authenticate)):
        principal.require_write()
        if body.file.encoding == "base64" and len(body.file.content) > 4 * ((MAX_USER_INPUT_BYTES + 2) // 3):
            raise DomainError("Input artifacts must not exceed 16 MiB", 413, "artifact_too_large")
        try:
            content = (base64.b64decode(body.file.content, validate=True) if body.file.encoding == "base64"
                       else body.file.content.encode())
        except ValueError as exc:
            raise DomainError("Invalid base64 file", 422) from exc
        if len(content) > MAX_USER_INPUT_BYTES:
            raise DomainError("Input artifacts must not exceed 16 MiB", 413, "artifact_too_large")
        if digest(content) != body.sha256:
            raise DomainError("Upload did not match its SHA-256 manifest", 422, "artifact_integrity")
        with db.transaction() as session:
            project = get_project(session, project_id, principal, write=True)
            if project.engineering_revision != body.expected_engineering_revision:
                raise DomainError("Upload belongs to an outdated revision", 409, "stale_revision")
            key = artifact_key(principal.tenant_id, project_id, f"input-{project.engineering_revision}",
                               body.file.path, content)
            previous = session.scalar(select(Artifact).where(Artifact.key == key))
            if previous:
                return control._artifact_view(previous)
            store.put(key, content, body.file.media_type)
            artifact = Artifact(tenant_id=principal.tenant_id, project_id=project_id,
                                revision=project.engineering_revision, lane="input", name=body.file.path,
                                key=key, sha256=body.sha256, size=len(content), media_type=body.file.media_type,
                                status="input", provenance={"actor": principal.subject, "authority": "user_input"})
            session.add(artifact)
            session.flush()
            emit(session, project, "artifact.uploaded", {"artifactId": artifact.id, "sha256": artifact.sha256})
            return control._artifact_view(artifact)

    @app.get("/api/v1/artifacts/{artifact_id}/content")
    def content(artifact_id: str, principal: Principal = Depends(authenticate)):
        artifact = authorized_artifact(artifact_id, principal)
        return Response(store.get(artifact.key), media_type=artifact.media_type,
                        headers={"Content-Disposition": "attachment"})

    @app.get("/api/v1/releases/{release_id}/download")
    def release_download(release_id: str, principal: Principal = Depends(authenticate)):
        package = ReleasePackaging(db, store).bundle(principal, release_id)
        return Response(package, media_type="application/zip", headers={
            "Content-Disposition": f'attachment; filename="kleos-release-{release_id}.zip"'})

    @app.get("/api/v1/invocations/{invocation_id}/transcript")
    def transcript(invocation_id: str, principal: Principal = Depends(authenticate)):
        principal.require_admin()
        with db.transaction() as session:
            invocation = session.scalar(select(Invocation).where(
                Invocation.id == invocation_id, Invocation.tenant_id == principal.tenant_id))
            if not invocation or not invocation.transcript_key:
                raise DomainError("Transcript unavailable or expired", 404)
            project = get_project(session, invocation.project_id, principal)
            emit(session, project, "audit.transcriptRead", {"actor": principal.subject,
                                                          "invocationId": invocation.id})
            key = invocation.transcript_key
        return json.loads(store.get(key))

    @app.post("/api/v1/invocations/{invocation_id}/reconcile")
    def reconcile_invocation(invocation_id: str, body: InvocationReconciliation,
                             principal: Principal = Depends(authenticate)):
        principal.require_admin()
        with db.transaction() as session:
            lock_platform(session)
            invocation = session.scalar(select(Invocation).where(Invocation.id == invocation_id,
                                            Invocation.tenant_id == principal.tenant_id))
            if not invocation:
                raise DomainError("Invocation not found", 404)
            project = get_project(session, invocation.project_id, principal, write=True)
            if invocation.status == "uncertain_settled":
                return {"id": invocation.id, "status": invocation.status}
            attempt = session.get(Attempt, invocation.attempt_id)
            task = session.get(Task, attempt.task_id)
            if invocation.status not in {"pending", "uncertain"} or task.status not in {"blocked", "failed", "cancelled"}:
                raise DomainError("Cancel or stop the task before reconciling an uncertain invocation", 409)
            amount = body.actual_cost_usd if body.actual_cost_usd is not None else invocation.reserved_usd
            daily = session.get(DailyBudget, invocation.usage["reservationDay"])
            project.reserved_usd = max(0, project.reserved_usd - invocation.reserved_usd)
            daily.reserved = max(0, daily.reserved - invocation.reserved_usd)
            project.spent_usd += amount
            daily.spent += amount
            invocation.status, invocation.estimated_usd = "uncertain_settled", amount
            invocation.usage = {**invocation.usage, "reconciliation": redact(body.note),
                                "reconciledBy": principal.subject, "costBasis": "operator_reconciliation"}
            task.fence += 1
            emit(session, project, "audit.invocationReconciled", {"invocationId": invocation.id,
                                                               "actor": principal.subject, "debitUsd": amount})
            return {"id": invocation.id, "status": invocation.status}

    @app.get("/api/v1/native-jobs/{job_id}/logs")
    def native_logs(job_id: str, principal: Principal = Depends(authenticate)):
        principal.require_admin()
        with db.transaction() as session:
            job = session.scalar(select(NativeJob).where(NativeJob.id == job_id, NativeJob.tenant_id == principal.tenant_id))
            if not job or not job.result.get("logKey"):
                raise DomainError("Native logs unavailable or expired", 404)
            project = get_project(session, session.get(Task, job.task_id).project_id, principal)
            emit(session, project, "audit.nativeLogsRead", {"actor": principal.subject, "jobId": job.id})
            key = job.result["logKey"]
        return json.loads(store.get(key))

    def execution():
        from .execution import execution_factory
        return execution_factory(db, settings, store)

    from .native_sessions import NativeSessions, register_session_routes
    register_session_routes(app, execution, NativeSessions(db, settings))

    @app.get("/api/v1/internal/jobs/{job_id}")
    def job_request(job_id: str, authorization: str = Header(default="")):
        token = authorization.removeprefix("Bearer ") if authorization.startswith("Bearer ") else ""
        return execution().payload(job_id, token)

    @app.post("/api/v1/internal/jobs/{job_id}/result")
    def job_result(job_id: str, result: JobResult, authorization: str = Header(default="")):
        token = authorization.removeprefix("Bearer ") if authorization.startswith("Bearer ") else ""
        execution().authorize(job_id, token)
        engine = EngineeringEngine(db, settings, store, None)
        engine.finish_native(job_id, result)
        return {"accepted": True}

    @app.get("/api/v1/auth/config")
    def auth_config():
        with db.transaction() as session:
            setup_required = bool(settings.setup_token and settings.auth_mode == "password"
                                  and not session.scalar(select(Account.id).limit(1)))
        return {"authMode": settings.auth_mode, "loginRequired": not settings.development_no_login,
                "signupRequiresInvite": True, "setupRequired": setup_required}

    @app.get("/api/v1/auth/session")
    def auth_session(request: Request, principal: Principal = Depends(authenticate)):
        return {"subject": principal.subject, "tenantId": principal.tenant_id, "role": principal.role,
                "loginRequired": not settings.development_no_login,
                "csrfToken": request.cookies.get("kleos_csrf")}

    @app.get("/api/v1/auth/organizations")
    def organizations(subject: str = Depends(authenticate_subject)):
        with db.transaction() as session:
            rows = session.execute(select(Organization, Membership).join(
                Membership, Membership.tenant_id == Organization.id).where(Membership.subject == subject)
                .order_by(Organization.name, Organization.id)).all()
            if not rows:
                raise DomainError("This account has not been invited", 403, "invite_required")
            return {"subject": subject, "organizations": [{"id": org.id, "name": org.name, "role": member.role}
                                      for org, member in rows]}

    def session_response(subject, session, native):
        sid, csrf, expires = start_session(session, subject, native)
        body = {"subject": subject, "expiresAt": expires.isoformat(),
                "token": sid if native else None, "csrfToken": None if native else csrf}
        response = JSONResponse(body)
        if not native:
            secure = settings.environment != "development"
            max_age = int(WEB_SESSION.total_seconds())
            response.set_cookie("kleos_session", sid, max_age=max_age, httponly=True, secure=secure, samesite="lax")
            response.set_cookie("kleos_csrf", csrf, max_age=max_age, secure=secure, samesite="lax")
        return response

    @app.post("/api/v1/auth/signup")
    def signup(body: SignupRequest):
        email = normalize_email(body.email)
        digest = hash_password(body.password)
        with db.transaction() as session:
            invite = session.scalar(select(Invite).where(Invite.code_hash == hash_code(body.invite_code)))
            if (not invite or invite.redeemed_at or invite.expires_at.replace(tzinfo=UTC) <= now()
                    or (invite.email and invite.email != email)):
                raise DomainError("This invite code is not valid or has expired", 400, "invite_invalid")
            if session.scalar(select(Account.id).where(Account.email == email)):
                raise DomainError("An account with this email already exists. Sign in instead.", 409, "account_exists")
            account = Account(email=email, name=body.name.strip(), password_hash=digest)
            session.add(account)
            session.flush()
            # The conditional update makes a code single-use even under concurrent redemption.
            claimed = session.execute(update(Invite).where(Invite.id == invite.id, Invite.redeemed_at.is_(None))
                                      .values(redeemed_at=now(), redeemed_by=account.id)).rowcount
            if claimed != 1:
                raise DomainError("This invite code is not valid or has expired", 400, "invite_invalid")
            session.add(Membership(subject=account.id, tenant_id=invite.tenant_id, role=invite.role))
            return session_response(account.id, session, body.native)

    @app.post("/api/v1/auth/login")
    def login(body: LoginRequest):
        invalid = DomainError("Invalid email or password", 401, "invalid_credentials")
        try:
            email = normalize_email(body.email)
        except DomainError:
            raise invalid from None
        with db.transaction() as session:
            account = session.scalar(select(Account).where(Account.email == email))
            locked = bool(account and account.locked_until and account.locked_until.replace(tzinfo=UTC) > now())
            # A locked account rejects even the right password, without revealing that it is locked.
            if check_password(account, body.password) and account and not locked:
                account.failed_logins, account.locked_until = 0, None
                return session_response(account.id, session, body.native)
            if account and not locked:
                account.failed_logins += 1
                if account.failed_logins >= MAX_FAILED_LOGINS:
                    account.failed_logins, account.locked_until = 0, now() + LOCKOUT
        # Raised after the transaction commits so the failure count is kept.
        raise invalid

    @app.post("/api/v1/auth/setup")
    def setup(body: SetupRequest):
        """Create the first owner. Available only with the configured token and while no account exists."""
        configured = settings.setup_token
        if not configured or not hmac.compare_digest(body.setup_token.encode(), configured.get_secret_value().encode()):
            raise DomainError("Setup is not available", 403, "setup_unavailable")
        email = normalize_email(body.email)
        digest = hash_password(body.password)
        with db.transaction() as session:
            if session.scalar(select(Account.id).limit(1)):
                raise DomainError("Setup is not available", 403, "setup_unavailable")
            organization = Organization(id=uid(), name=body.organization.strip())
            account = Account(email=email, name=body.name.strip(), password_hash=digest)
            session.add_all([organization, account])
            session.flush()
            session.add(Membership(subject=account.id, tenant_id=organization.id, role="owner"))
            return session_response(account.id, session, body.native)

    @app.post("/api/v1/auth/logout")
    def logout(request: Request, subject: str = Depends(authenticate_subject)):
        auth = request.headers.get("authorization", "")
        sid = auth.removeprefix("Bearer ") if auth.startswith("Bearer ") else request.cookies.get("kleos_session", "")
        with db.transaction() as session:
            record = session.get(SessionRecord, hashlib.sha256(sid.encode()).hexdigest()) if sid else None
            if record:
                session.delete(record)
        response = JSONResponse({"ok": True})
        response.delete_cookie("kleos_session")
        response.delete_cookie("kleos_csrf")
        return response

    return app


app = create_app()
