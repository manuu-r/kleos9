import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache

from fastapi import Request
from sqlalchemy import and_, delete, select

from .contracts import DomainError
from .db import Account, Invite, Membership, Project, ProjectMembership, SessionRecord, now

WEB_SESSION = timedelta(hours=8)
NATIVE_SESSION = timedelta(days=30)
MAX_FAILED_LOGINS = 10
LOCKOUT = timedelta(minutes=15)
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


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


def normalize_email(email):
    email = email.strip().lower()
    if len(email) > 320 or not _EMAIL.match(email):
        raise DomainError("Enter a valid email address", 422, "invalid_email")
    return email


def hash_password(password, n=2**15, r=8, p=1):
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, maxmem=128 * n * r * 2, dklen=32)
    return f"scrypt${n}${r}${p}${salt.hex()}${digest.hex()}"


def verify_password(password, stored):
    try:
        scheme, n, r, p, salt, expected = stored.split("$")
        n, r, p = int(n), int(r), int(p)
        digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=n, r=r, p=p,
                                maxmem=128 * n * r * 2, dklen=32)
        return scheme == "scrypt" and hmac.compare_digest(digest.hex(), expected)
    except ValueError:
        return False


@lru_cache
def _unknown_account_hash():
    # Unknown emails still pay for a hash so response time does not reveal accounts.
    return hash_password(secrets.token_urlsafe(16))


def check_password(account, password):
    return verify_password(password, account.password_hash if account else _unknown_account_hash())


def hash_code(code):
    return hashlib.sha256(code.strip().encode()).hexdigest()


def issue_invite(session, tenant_id, role, hours=168, email=None, created_by="cli"):
    """Create a single-use invite and return the code, which is never stored in plain text."""
    code = "k9-" + secrets.token_urlsafe(16)
    session.add(Invite(code_hash=hash_code(code), tenant_id=tenant_id, role=role,
                       email=normalize_email(email) if email else None, created_by=created_by,
                       expires_at=now() + timedelta(hours=hours)))
    return code


def reset_account_password(session, account):
    """Set a one-time temporary password, unlock the account and end its sessions."""
    temporary = secrets.token_urlsafe(12)
    account.password_hash = hash_password(temporary)
    account.failed_logins, account.locked_until = 0, None
    session.execute(delete(SessionRecord).where(SessionRecord.subject == account.id))
    return temporary


def reset_password(session, email):
    account = session.scalar(select(Account).where(Account.email == normalize_email(email)))
    if not account:
        raise DomainError("No account has that email address", 404, "not_found")
    return reset_account_password(session, account)


def start_session(session, subject, native=False):
    """Persist a session; only the SHA-256 digest of the secret identifiers is stored."""
    sid, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    expires = now() + (NATIVE_SESSION if native else WEB_SESSION)
    session.add(SessionRecord(id=hashlib.sha256(sid.encode()).hexdigest(), subject=subject,
                              csrf_hash=hashlib.sha256(csrf.encode()).hexdigest(), expires_at=expires))
    return sid, csrf, expires


def find_session(session, sid):
    record = session.get(SessionRecord, hashlib.sha256(sid.encode()).hexdigest()) if sid else None
    if not record or record.expires_at.replace(tzinfo=UTC) <= datetime.now(UTC):
        raise DomainError("Sign in to continue", 401, "unauthenticated")
    return record


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
            # Bearer sessions are not ambient credentials, so they need no CSRF token.
            subject = find_session(session, token).subject
        else:
            record = find_session(session, request.cookies.get("kleos_session", ""))
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
