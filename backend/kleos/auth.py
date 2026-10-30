import hashlib
import hmac
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache

import jwt
from fastapi import Request
from sqlalchemy import and_, select

from .contracts import DomainError
from .db import Membership, Project, ProjectMembership, SessionRecord


@dataclass(frozen=True)
class Principal:
    subject: str
    tenant_id: str
    role: str

    def require_write(self):
        if self.role not in {"owner", "admin", "member"}:
            raise DomainError("This account has read-only access", 403, "forbidden")

    def require_admin(self):
        if self.role not in {"owner", "admin"}:
            raise DomainError("An organization administrator is required", 403, "forbidden")


@dataclass(frozen=True)
class WorkspaceAccess:
    """A fresh authorization result for this request, never a cached permission."""

    principal: Principal
    project_id: str
    revision: int


def resolve_workspace_access(session, subject, project_id, tenant=None):
    # Keep every organization membership in the result: choosing a project must not
    # implicitly select an organization for a multi-organization account.
    query = select(Membership, Project.id, Project.owner_id, Project.revision, ProjectMembership.id).outerjoin(
        Project, and_(Project.id == project_id, Project.tenant_id == Membership.tenant_id,
                      Project.deleted_at.is_(None))).outerjoin(
        ProjectMembership, and_(ProjectMembership.project_id == Project.id,
                                ProjectMembership.subject == subject)).where(Membership.subject == subject)
    if tenant:
        query = query.where(Membership.tenant_id == tenant)
    rows = session.execute(query).all()
    if not rows:
        raise DomainError("This account has not been invited", 403, "invite_required")
    if len(rows) > 1 and not tenant:
        raise DomainError("Select an organization using X-Organization-Id", 400, "organization_required")
    membership, found_id, owner_id, revision, project_membership = rows[0]
    if not found_id or (membership.role not in {"owner", "admin"}
                        and owner_id != subject and not project_membership):
        raise DomainError("Project not found", 404, "not_found")
    return WorkspaceAccess(Principal(subject, membership.tenant_id, membership.role), found_id, revision)


@lru_cache
def jwks_client(issuer):
    return jwt.PyJWKClient(f"{issuer}/.well-known/jwks.json", cache_keys=True)


def cognito_claims(token, settings, token_use="access"):
    if not settings.cognito_pool_id or not settings.cognito_client_id:
        raise DomainError("Authentication is not configured", 503, "auth_unconfigured")
    issuer = f"https://cognito-idp.{settings.cognito_region}.amazonaws.com/{settings.cognito_pool_id}"
    try:
        key = jwks_client(issuer).get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token, key.key, algorithms=["RS256"], issuer=issuer,
            audience=settings.cognito_client_id if token_use == "id" else None,
            options={"verify_aud": token_use == "id", "require": ["exp", "sub", "iss", "token_use"]},
        )
        if claims["token_use"] != token_use:
            raise ValueError("Wrong token type")
        if token_use == "access" and claims.get("client_id") != settings.cognito_client_id:
            raise ValueError("Wrong client")
        return claims
    except (jwt.PyJWTError, ValueError) as exc:
        raise DomainError("Sign in again", 401, "unauthenticated") from exc


def resolve_membership(session, subject, tenant=None):
    query = select(Membership).where(Membership.subject == subject)
    if tenant:
        query = query.where(Membership.tenant_id == tenant)
    memberships = session.scalars(query).all()
    if not memberships:
        raise DomainError("This account has not been invited", 403, "invite_required")
    if len(memberships) > 1 and not tenant:
        raise DomainError("Select an organization using X-Organization-Id", 400, "organization_required")
    membership = memberships[0]
    return Principal(subject, membership.tenant_id, membership.role)


def authenticate_subject(request: Request) -> str:
    settings, db = request.app.state.settings, request.app.state.db
    if settings.development_no_login and settings.auth_mode == "development" and settings.environment in {"development", "test"}:
        return "development"
    auth = request.headers.get("authorization", "")
    token = auth.removeprefix("Bearer ") if auth.startswith("Bearer ") else ""
    with db.transaction() as session:
        if token and settings.auth_mode == "development":
            if not hmac.compare_digest(token, settings.development_token.get_secret_value()):
                raise DomainError("Invalid development token", 401, "unauthenticated")
            subject = "development"
        elif token:
            subject = cognito_claims(token, settings)["sub"]
        else:
            sid = request.cookies.get("kleos_session", "")
            record = session.get(SessionRecord, hashlib.sha256(sid.encode()).hexdigest()) if sid else None
            if not record or record.expires_at.replace(tzinfo=UTC) <= datetime.now(UTC):
                raise DomainError("Sign in to continue", 401, "unauthenticated")
            if request.method not in {"GET", "HEAD", "OPTIONS"}:
                csrf = request.headers.get("x-csrf-token", "")
                if not hmac.compare_digest(hashlib.sha256(csrf.encode()).hexdigest(), record.csrf_hash):
                    raise DomainError("Invalid session request", 403, "csrf")
            subject = record.subject
        return subject


def authenticate(request: Request) -> Principal:
    subject = authenticate_subject(request)
    with request.app.state.db.transaction() as session:
        route = request.scope.get("route")
        if request.method == "GET" and getattr(route, "path", None) == "/api/v1/projects/{project_id}/workspace":
            access = resolve_workspace_access(session, subject, request.path_params["project_id"],
                                              request.headers.get("x-organization-id"))
            request.state.workspace_access = access
            return access.principal
        return resolve_membership(session, subject, request.headers.get("x-organization-id"))
