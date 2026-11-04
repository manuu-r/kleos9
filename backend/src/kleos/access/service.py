"""Persistent account, session and organization authority for the new backend."""
import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import Boolean, Float, Integer, String, delete, func, select, text, update
from sqlalchemy.orm import Mapped, mapped_column

from kleos.api import AuthResult, Cookie
from kleos.contracts.models import Actor, DomainError
from kleos.projects.store import Base

COOKIE_NAME = 'kleos_session'
ROLES = {'owner', 'admin', 'member', 'viewer'}


class Account(Base):
    __tablename__ = 'kleos2_accounts'
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    name: Mapped[str] = mapped_column(String(160), default='')
    password_hash: Mapped[str] = mapped_column(String(256))


class Organization(Base):
    __tablename__ = 'kleos2_organizations'
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    name: Mapped[str] = mapped_column(String(160))


class OrganizationMember(Base):
    __tablename__ = 'kleos2_organization_members'
    tenant_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    role: Mapped[str] = mapped_column(String(20))


class Session(Base):
    __tablename__ = 'kleos2_sessions'
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(100), index=True)
    expires: Mapped[float] = mapped_column(Float)
    browser: Mapped[bool] = mapped_column(Boolean)


class Invite(Base):
    __tablename__ = 'kleos2_invites'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    role: Mapped[str] = mapped_column(String(20))
    email: Mapped[str | None] = mapped_column(String(320))
    expires: Mapped[float] = mapped_column(Float)
    used: Mapped[bool] = mapped_column(Boolean, default=False)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


class LoginThrottle(Base):
    __tablename__ = 'kleos2_login_throttle'
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    failures: Mapped[int] = mapped_column(Integer, default=0)
    reset_at: Mapped[float] = mapped_column(Float)


@dataclass(frozen=True)
class AccessSettings:
    secret_key: str
    environment: str = 'production'
    development_no_login: bool = False
    setup_token: str | None = None
    session_seconds: int = 86400
    native_session_seconds: int = 30 * 86400

    def __post_init__(self):
        if len(self.secret_key) < 32:
            raise ValueError('Access secret must have at least 32 characters')
        if self.development_no_login and self.environment not in {'development', 'test'}:
            raise ValueError('No-login mode is restricted to local development/test')
        if not 60 <= self.session_seconds <= 30 * 86400 or not 60 <= self.native_session_seconds <= 30 * 86400:
            raise ValueError('Session lifetimes must be bounded')


def sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


def email_address(value):
    if not isinstance(value, str) or not 3 <= len(value.strip()) <= 320 or value.count('@') != 1:
        raise DomainError('Provide a valid email', 'invalid_request', 422)
    value = value.strip().lower()
    if any(char.isspace() or ord(char) < 32 for char in value):
        raise DomainError('Provide a valid email', 'invalid_request', 422)
    return value


def password_hash(value):
    if not isinstance(value, str) or not 12 <= len(value) <= 256:
        raise DomainError('Password must contain 12–256 characters', 'invalid_password', 422)
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(value.encode(), salt=salt, n=16384, r=8, p=1, dklen=32)
    return f'scrypt:{salt.hex()}:{digest.hex()}'


def password_matches(value, encoded):
    if not isinstance(value, str) or len(value) > 256:
        return False
    try:
        _, salt, expected = encoded.split(':')
        actual = hashlib.scrypt(value.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1, dklen=32)
        return hmac.compare_digest(actual.hex(), expected)
    except (ValueError, TypeError):
        return False


class AccessService:
    def __init__(self, store, settings: AccessSettings):
        self.store, self.settings = store, settings
        # Uniform password work for nonexistent accounts without persisting a fake account.
        self._dummy_hash = password_hash(secrets.token_urlsafe(32))

    def _lock(self, session, namespace):
        if self.store.engine.dialect.name == 'postgresql':
            key = int.from_bytes(hashlib.sha256(namespace.encode()).digest()[:8], 'big', signed=True)
            session.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': key})

    def _development(self, session):
        if not self.settings.development_no_login:
            return None
        self._lock(session, 'development-account')
        if session.get(Account, 'development') is None:
            session.add(Account(id='development', email='development@localhost.invalid', name='Local developer',
                                password_hash=self._dummy_hash))
            session.add(Organization(id='development', name='Local development'))
            session.add(OrganizationMember(tenant_id='development', user_id='development', role='owner'))
            session.flush()
        return 'development'

    def _credential(self, headers, cookies):
        headers = {k.lower(): v for k, v in headers.items()}
        authorization = headers.get('authorization', '')
        if authorization:
            if not authorization.startswith('Bearer '):
                raise DomainError('Sign in required', 'unauthorized', 401)
            return authorization[7:], False
        return cookies.get(COOKIE_NAME, ''), True

    def _identity(self, session, headers, cookies, method='GET'):
        token, browser = self._credential(headers, cookies)
        if not token:
            ident = self._development(session)
            if ident:
                return ident, None
            raise DomainError('Sign in required', 'unauthorized', 401)
        if len(token) > 256:
            raise DomainError('Invalid session', 'unauthorized', 401)
        stored = session.get(Session, sha(token))
        if stored is None or stored.expires <= time.time() or stored.browser != browser:
            raise DomainError('Session expired', 'unauthorized', 401)
        if browser and method.upper() not in {'GET', 'HEAD', 'OPTIONS'}:
            normalized = {k.lower(): v for k, v in headers.items()}
            csrf = normalized.get('x-csrf-token', '')
            if not hmac.compare_digest(csrf, self._csrf(token)):
                raise DomainError('Session request verification failed', 'csrf_required', 403)
        return stored.user_id, token

    def _csrf(self, token):
        return hmac.new(self.settings.secret_key.encode(), ('csrf:' + token).encode(), hashlib.sha256).hexdigest()

    def _member(self, session, actor, *, admin=False):
        member = session.get(OrganizationMember, (actor.tenant_id, actor.user_id))
        if member is None:
            raise DomainError('Organization access denied', 'forbidden', 403)
        if admin and member.role not in {'owner', 'admin'}:
            raise DomainError('Organization administrator required', 'forbidden', 403)
        return member

    def authenticate(self, headers, cookies, method):
        normalized = {k.lower(): v for k, v in headers.items()}
        with self.store.transaction() as session:
            user, _ = self._identity(session, normalized, cookies, method)
            tenant = normalized.get('x-organization-id')
            if tenant is None:
                memberships = list(session.scalars(select(OrganizationMember).where(OrganizationMember.user_id == user)))
                if len(memberships) != 1:
                    raise DomainError('Select an organization', 'organization_required', 409)
                tenant = memberships[0].tenant_id
            actor = Actor(tenant_id=tenant, user_id=user)
            member = self._member(session, actor)
            if method.upper() not in {'GET', 'HEAD', 'OPTIONS'} and member.role == 'viewer':
                raise DomainError('Organization is read-only', 'forbidden', 403)
            return actor

    def _issue(self, session, account, native):
        token = secrets.token_urlsafe(32)
        lifetime = self.settings.native_session_seconds if native else self.settings.session_seconds
        session.add(Session(token_hash=sha(token), user_id=account.id, expires=time.time() + lifetime, browser=not native))
        body = {'subject': account.id}
        if native:
            body['token'] = token
            return AuthResult(body)
        return AuthResult(body, cookies=(Cookie(COOKIE_NAME, token, secure=self.settings.environment not in {'development', 'test'},
                                                max_age=lifetime),))

    def setup(self, body):
        configured = self.settings.setup_token
        if not configured or not hmac.compare_digest(str(body.get('setupToken', '')), configured):
            raise DomainError('Setup unavailable', 'forbidden', 403)
        email = email_address(body.get('email'))
        encoded = password_hash(body.get('password'))
        organization = body.get('organization', '')
        name = body.get('name', '')
        if not isinstance(organization, str) or not 1 <= len(organization.strip()) <= 160:
            raise DomainError('Provide an organization name', 'invalid_request', 422)
        if not isinstance(name, str) or len(name) > 160:
            raise DomainError('Name is too long', 'invalid_request', 422)
        with self.store.transaction() as session:
            self._lock(session, 'initial-owner-setup')
            if session.scalar(select(func.count()).select_from(Account)):
                raise DomainError('Setup is already complete', 'setup_complete', 409)
            account = Account(id=str(uuid4()), email=email, password_hash=encoded, name=name)
            tenant = Organization(id=str(uuid4()), name=organization.strip())
            session.add_all([account, tenant, OrganizationMember(tenant_id=tenant.id, user_id=account.id, role='owner')])
            return self._issue(session, account, body.get('native') is True)

    def signup(self, body):
        email = email_address(body.get('email'))
        encoded = password_hash(body.get('password'))
        name = body.get('name', '')
        if not isinstance(name, str) or len(name) > 160:
            raise DomainError('Name is too long', 'invalid_request', 422)
        code = body.get('inviteCode', '')
        if not isinstance(code, str) or len(code) > 256:
            raise DomainError('Invalid invite', 'invalid_invite', 403)
        with self.store.transaction() as session:
            self._lock(session, 'signup:' + email)
            invite = session.scalar(select(Invite).where(Invite.token_hash == sha(code)).with_for_update())
            if not invite or invite.used or invite.revoked or invite.expires <= time.time() or invite.email not in (None, email):
                raise DomainError('Invite is invalid or expired', 'invalid_invite', 403)
            if session.scalar(select(Account).where(Account.email == email)):
                raise DomainError('Account already exists; sign in', 'account_exists', 409)
            consumed = session.execute(update(Invite).where(Invite.id == invite.id, Invite.used.is_(False),
                Invite.revoked.is_(False)).values(used=True))
            if consumed.rowcount != 1:
                raise DomainError('Invite already used', 'invalid_invite', 409)
            account = Account(id=str(uuid4()), email=email, name=name, password_hash=encoded)
            session.add(account)
            session.add(OrganizationMember(tenant_id=invite.tenant_id, user_id=account.id, role=invite.role))
            return self._issue(session, account, body.get('native') is True)

    def login(self, body):
        email = email_address(body.get('email'))
        failure = None
        result = None
        with self.store.transaction() as session:
            self._lock(session, 'login:' + email)
            key = sha(email)
            throttle = session.get(LoginThrottle, key)
            now = time.time()
            if throttle is None:
                throttle = LoginThrottle(key=key, failures=0, reset_at=now + 900)
                session.add(throttle)
            if throttle.reset_at <= now:
                throttle.failures, throttle.reset_at = 0, now + 900
            if throttle.failures >= 10:
                failure = DomainError('Too many sign-in attempts; try later', 'login_locked', 429)
            else:
                account = session.scalar(select(Account).where(Account.email == email))
                matched = password_matches(body.get('password'), account.password_hash if account else self._dummy_hash)
                if not account or not matched:
                    throttle.failures += 1
                    failure = DomainError('Email or password is incorrect', 'invalid_credentials', 401)
                else:
                    throttle.failures = 0
                    result = self._issue(session, account, body.get('native') is True)
        if failure:
            raise failure
        return result

    def logout(self, headers, cookies):
        with self.store.transaction() as session:
            _, token = self._identity(session, headers, cookies, 'POST')
            if token:
                session.execute(delete(Session).where(Session.token_hash == sha(token)))
        return AuthResult({'signedOut': True}, cookies=(Cookie(COOKIE_NAME, delete=True,
            secure=self.settings.environment not in {'development', 'test'}),))

    def auth(self, operation, body, headers, cookies):
        if operation == 'config':
            with self.store.transaction() as session:
                empty = not session.scalar(select(func.count()).select_from(Account))
            return AuthResult({'mode': 'development' if self.settings.development_no_login else 'password',
                'loginRequired': not self.settings.development_no_login,
                'setupRequired': bool(empty and self.settings.setup_token)})
        if operation in {'setup', 'signup', 'login'}:
            return getattr(self, operation)(body)
        if operation == 'logout':
            return self.logout(headers, cookies)
        if operation == 'organizations':
            with self.store.transaction() as session:
                user, _ = self._identity(session, headers, cookies)
                rows = session.execute(select(Organization, OrganizationMember).join(OrganizationMember,
                    OrganizationMember.tenant_id == Organization.id).where(OrganizationMember.user_id == user)).all()
                return AuthResult({'subject': user, 'organizations': [
                    {'id': org.id, 'name': org.name, 'role': member.role} for org, member in rows]})
        if operation == 'session':
            actor = self.authenticate(headers, cookies, 'GET')
            with self.store.transaction() as session:
                member = self._member(session, actor)
            token, browser = self._credential(headers, cookies)
            return AuthResult({'subject': actor.user_id, 'tenantId': actor.tenant_id, 'role': member.role,
                'loginRequired': not self.settings.development_no_login,
                'csrfToken': self._csrf(token) if token and browser else None})
        raise DomainError('Authentication operation unavailable', 'unsupported', 501)

    def query(self, actor, operation, params=None):
        with self.store.transaction() as session:
            self._member(session, actor, admin=True)
            if operation == 'members':
                rows = session.execute(select(Account, OrganizationMember).join(OrganizationMember,
                    OrganizationMember.user_id == Account.id).where(OrganizationMember.tenant_id == actor.tenant_id)).all()
                return {'members': [{'subject': a.id, 'email': a.email, 'name': a.name, 'role': m.role,
                    'you': a.id == actor.user_id, 'locked': bool((t := session.get(LoginThrottle, sha(a.email)))
                    and t.failures >= 10 and t.reset_at > time.time())} for a, m in rows]}
            if operation == 'invites':
                return {'invites': [{'id': i.id, 'role': i.role, 'email': i.email,
                    'status': 'revoked' if i.revoked else 'used' if i.used else 'expired' if i.expires <= time.time() else 'active',
                    'expiresAt': datetime.fromtimestamp(i.expires, UTC).isoformat()}
                    for i in session.scalars(select(Invite).where(Invite.tenant_id == actor.tenant_id))]}
        raise DomainError('Account query unavailable', 'unsupported', 501)

    def action(self, actor, operation, params, body):
        with self.store.transaction() as session:
            session.scalar(select(Organization).where(Organization.id == actor.tenant_id).with_for_update())
            member = self._member(session, actor, admin=True)
            if operation == 'create_invite':
                role, hours = body.get('role', 'member'), body.get('expiresHours', 72)
                if role not in ROLES or (member.role != 'owner' and role in {'owner', 'admin'}):
                    raise DomainError('Cannot grant that role', 'forbidden', 403)
                if isinstance(hours, bool) or not isinstance(hours, int) or not 1 <= hours <= 720:
                    raise DomainError('Invite expiry must be 1–720 hours', 'invalid_request', 422)
                email = email_address(body['email']) if body.get('email') else None
                code = secrets.token_urlsafe(32)
                session.add(Invite(id=str(uuid4()), token_hash=sha(code), tenant_id=actor.tenant_id,
                    role=role, email=email, expires=time.time() + hours * 3600, used=False, revoked=False))
                return {'code': code, 'role': role, 'email': email, 'expiresInHours': hours}
            if operation == 'delete_invite':
                invite = session.get(Invite, params['inviteId'])
                if not invite or invite.tenant_id != actor.tenant_id:
                    raise DomainError('Invite not found', 'not_found', 404)
                if member.role != 'owner' and invite.role in {'owner', 'admin'}:
                    raise DomainError('Owner required', 'forbidden', 403)
                invite.revoked = True
                return {'revoked': True}
            subject = params.get('subject')
            target = session.get(OrganizationMember, (actor.tenant_id, subject)) if subject else None
            if target is None:
                raise DomainError('Member not found', 'not_found', 404)
            if member.role != 'owner' and target.role in {'owner', 'admin'}:
                raise DomainError('Owner required', 'forbidden', 403)
            if operation in {'change_role', 'remove_member'}:
                role = body.get('role') if operation == 'change_role' else None
                if operation == 'change_role' and (role not in ROLES or member.role != 'owner' and role in {'owner', 'admin'}):
                    raise DomainError('Cannot grant that role', 'forbidden', 403)
                if target.role == 'owner' and role != 'owner':
                    owners = session.scalar(select(func.count()).select_from(OrganizationMember).where(
                        OrganizationMember.tenant_id == actor.tenant_id, OrganizationMember.role == 'owner'))
                    if owners <= 1:
                        raise DomainError('Organization needs an owner', 'last_owner', 409)
                if operation == 'remove_member':
                    session.delete(target)
                    return {'removed': True}
                target.role = role
                return {'role': role}
            if operation == 'reset_password':
                account = session.get(Account, subject)
                temporary = secrets.token_urlsafe(20)
                account.password_hash = password_hash(temporary)
                session.execute(delete(Session).where(Session.user_id == subject))
                session.execute(delete(LoginThrottle).where(LoginThrottle.key == sha(account.email)))
                return {'temporaryPassword': temporary}
        raise DomainError('Account action unavailable', 'unsupported', 501)
