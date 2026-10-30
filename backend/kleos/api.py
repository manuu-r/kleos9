import asyncio
import base64
import hashlib
import json
import secrets
import time
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Literal
from urllib.parse import urlencode

import httpx
from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse, Response, StreamingResponse
from itsdangerous import BadSignature, URLSafeTimedSerializer
from pydantic import Field
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from .auth import Principal, WorkspaceAccess, authenticate, authenticate_subject, cognito_claims
from .config import Settings, get_settings
from .contracts import ArtifactUpload, Command, CommandAccepted, DomainError, JobResult, WireModel
from .db import (
    Artifact,
    Attempt,
    DailyBudget,
    Database,
    Invocation,
    Membership,
    NativeJob,
    Organization,
    ProjectMembership,
    SessionRecord,
    Task,
    now,
)
from .domain import ControlPlane, emit, get_project, lock_platform
from .engine import EngineeringEngine
from .integration_api import register_routes
from .packaging import ReleasePackaging
from .storage import ArtifactStore, artifact_key, digest
from .telemetry import configure, metric, redact
from .views import ArtifactDownloadView, EventView, ModelCatalogView, ProjectsView, RunView, WorkspaceView


class InvocationReconciliation(WireModel):
    note: str = Field(min_length=20, max_length=2000)
    actual_cost_usd: float | None = Field(default=None, ge=0, allow_inf_nan=False)


class ProjectAccessRequest(WireModel):
    subject: str = Field(min_length=1, max_length=200)
    role: Literal["member", "viewer"]


def create_app(settings: Settings | None = None, database=None, storage=None):
    settings = settings or get_settings()
    db = database or Database(settings.database_url)
    store = storage or ArtifactStore(settings)
    control = ControlPlane(db, settings, store)

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
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=True,
                       allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
                       allow_headers=["Authorization", "Content-Type", "Idempotency-Key",
                                      "X-Organization-Id", "X-CSRF-Token", "Last-Event-ID"])

    @app.exception_handler(DomainError)
    async def domain_error(_request, exc):
        if exc.code in {"gate_failed", "stale_attempt", "stale_validation", "forbidden", "budget_exhausted"}:
            metric("GateViolation", 1, Environment=settings.environment)
        return JSONResponse(status_code=exc.status, content={"message": exc.message, "code": exc.code})

    @app.middleware("http")
    async def request_limits(request, call_next):
        started = time.perf_counter()
        if request.method in {"POST", "PUT", "PATCH"}:
            limit = 64 * 1024 * 1024 if request.url.path.startswith("/api/v1/internal/jobs/") else 1024 * 1024
            try:
                length = int(request.headers.get("content-length", 0))
            except ValueError:
                return JSONResponse(status_code=400, content={"message": "Invalid content length"})
            if length > limit:
                return JSONResponse(status_code=413, content={"message": "Request too large"})
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > limit:
                    return JSONResponse(status_code=413, content={"message": "Request too large"})
            request._body = bytes(body)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Server-Timing"] = f"app;dur={(time.perf_counter() - started)*1000:.2f}"
        return response

    @app.get("/api/v1/health")
    def health():
        return {"status": "ok", "mode": "real_engineering_simulated_fulfillment", "version": "0.1.0"}

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
        try:
            content = (base64.b64decode(body.file.content, validate=True) if body.file.encoding == "base64"
                       else body.file.content.encode())
        except ValueError as exc:
            raise DomainError("Invalid base64 file", 422) from exc
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
            if invocation.status != "pending" or task.status not in {"blocked", "failed", "cancelled"}:
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
        issuer = (f"https://cognito-idp.{settings.cognito_region}.amazonaws.com/{settings.cognito_pool_id}"
                  if settings.cognito_pool_id else None)
        domain = settings.cognito_domain.rstrip("/")
        return {"authMode": settings.auth_mode, "loginRequired": not settings.development_no_login, "issuer": issuer, "clientId": settings.cognito_client_id,
                "redirectUrl": settings.auth_callback_url, "nativeRedirectUrl": "kleos9://auth",
                "authorizationEndpoint": f"{domain}/oauth2/authorize" if domain else None,
                "tokenEndpoint": f"{domain}/oauth2/token" if domain else None}

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

    def signer():
        if not settings.session_key:
            raise DomainError("Web authentication is not configured", 503, "auth_unconfigured")
        return URLSafeTimedSerializer(settings.session_key.get_secret_value(), salt="kleos-oauth")

    @app.get("/api/v1/auth/login")
    def login():
        if not settings.cognito_domain:
            raise DomainError("Cognito domain is not configured", 503, "auth_unconfigured")
        state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
        nonce = secrets.token_urlsafe(32)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        response = RedirectResponse(settings.cognito_domain.rstrip("/") + "/oauth2/authorize?" + urlencode({
            "response_type": "code", "client_id": settings.cognito_client_id,
            "redirect_uri": settings.auth_callback_url, "scope": "openid email profile",
            "state": state, "nonce": nonce, "code_challenge": challenge, "code_challenge_method": "S256",
        }))
        response.set_cookie("kleos_oauth", signer().dumps({"state": state, "verifier": verifier, "nonce": nonce}),
                            max_age=600, httponly=True, secure=settings.environment != "development", samesite="lax")
        return response

    @app.get("/api/v1/auth/callback")
    async def callback(request: Request, code: str, state: str):
        try:
            flow = signer().loads(request.cookies.get("kleos_oauth", ""), max_age=600)
        except BadSignature as exc:
            raise DomainError("Sign-in request expired", 401) from exc
        if not secrets.compare_digest(state, flow["state"]):
            raise DomainError("Invalid sign-in state", 401)
        async with httpx.AsyncClient(timeout=20) as client:
            result = await client.post(settings.cognito_domain.rstrip("/") + "/oauth2/token", data={
                "grant_type": "authorization_code", "client_id": settings.cognito_client_id,
                "code": code, "redirect_uri": settings.auth_callback_url, "code_verifier": flow["verifier"],
            })
        if result.status_code != 200:
            raise DomainError("Sign-in exchange failed", 401)
        claims = await asyncio.to_thread(cognito_claims, result.json()["id_token"], settings, "id")
        if claims.get("nonce") != flow["nonce"]:
            raise DomainError("Invalid sign-in nonce", 401)
        sid, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with db.transaction() as session:
            if not session.scalar(select(Membership.id).where(Membership.subject == claims["sub"])):
                raise DomainError("This account has not been invited", 403, "invite_required")
            session.add(SessionRecord(id=hashlib.sha256(sid.encode()).hexdigest(), subject=claims["sub"],
                                      csrf_hash=hashlib.sha256(csrf.encode()).hexdigest(),
                                      expires_at=now() + timedelta(hours=8)))
        response = RedirectResponse(settings.frontend_url)
        secure = settings.environment != "development"
        response.set_cookie("kleos_session", sid, max_age=28800, httponly=True, secure=secure, samesite="lax")
        response.set_cookie("kleos_csrf", csrf, max_age=28800, secure=secure, samesite="lax")
        response.delete_cookie("kleos_oauth")
        return response

    @app.post("/api/v1/auth/logout")
    def logout(request: Request, subject: str = Depends(authenticate_subject)):
        sid = request.cookies.get("kleos_session", "")
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
