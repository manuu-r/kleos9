"""Organization administration: members and signup invites for owners and admins."""
from typing import Literal

from fastapi import Depends
from pydantic import Field
from sqlalchemy import delete, func, select

from .auth import Principal, authenticate, issue_invite, reset_account_password
from .contracts import DomainError, WireModel
from .db import Account, Invite, Membership, Project, ProjectMembership, SessionRecord, now

Role = Literal["owner", "admin", "member", "viewer"]
ADMIN_ROLES = {"owner", "admin"}


class InviteRequest(WireModel):
    role: Role = "member"
    email: str | None = Field(default=None, max_length=320)
    expires_hours: int = Field(default=168, ge=1, le=720)


class RoleRequest(WireModel):
    role: Role


def require_owner_for(principal, role):
    # Only owners may create or change owners and admins; admins manage members and viewers.
    if role in ADMIN_ROLES and principal.role != "owner":
        raise DomainError("Only an owner can manage owners and admins", 403, "forbidden")


def owner_count(session, tenant_id):
    return session.scalar(select(func.count()).select_from(Membership).where(
        Membership.tenant_id == tenant_id, Membership.role == "owner"))


def target_membership(session, principal, subject):
    membership = session.scalar(select(Membership).where(
        Membership.tenant_id == principal.tenant_id, Membership.subject == subject))
    if not membership:
        raise DomainError("Member not found", 404, "not_found")
    require_owner_for(principal, membership.role)
    return membership


def register_admin_routes(app, db):
    def admin(principal: Principal = Depends(authenticate)):
        principal.require_admin()
        return principal

    @app.get("/api/v1/auth/members")
    def members(principal: Principal = Depends(admin)):
        with db.transaction() as session:
            rows = session.execute(select(Membership, Account).outerjoin(
                Account, Account.id == Membership.subject).where(Membership.tenant_id == principal.tenant_id)
                .order_by(Membership.created_at, Membership.subject)).all()
            return {"members": [{
                "subject": member.subject, "email": account.email if account else None,
                "name": account.name if account else "", "role": member.role,
                "createdAt": member.created_at.isoformat(), "you": member.subject == principal.subject,
                "locked": bool(account and account.locked_until
                               and account.locked_until.replace(tzinfo=now().tzinfo) > now()),
            } for member, account in rows]}

    @app.post("/api/v1/auth/members/{subject}/role")
    def change_role(subject: str, body: RoleRequest, principal: Principal = Depends(admin)):
        require_owner_for(principal, body.role)
        with db.transaction() as session:
            membership = target_membership(session, principal, subject)
            if membership.role == "owner" and body.role != "owner" and owner_count(
                    session, principal.tenant_id) <= 1:
                raise DomainError("An organization needs at least one owner", 409, "last_owner")
            membership.role = body.role
        return {"subject": subject, "role": body.role}

    @app.delete("/api/v1/auth/members/{subject}")
    def remove_member(subject: str, principal: Principal = Depends(admin)):
        if subject == principal.subject:
            raise DomainError("You cannot remove yourself", 409, "self_removal")
        with db.transaction() as session:
            membership = target_membership(session, principal, subject)
            if membership.role == "owner" and owner_count(session, principal.tenant_id) <= 1:
                raise DomainError("An organization needs at least one owner", 409, "last_owner")
            session.execute(delete(ProjectMembership).where(
                ProjectMembership.subject == subject,
                ProjectMembership.project_id.in_(select(Project.id).where(Project.tenant_id == principal.tenant_id))))
            session.delete(membership)
            session.flush()
            if not session.scalar(select(Membership.id).where(Membership.subject == subject)):
                session.execute(delete(SessionRecord).where(SessionRecord.subject == subject))
        return {"subject": subject, "removed": True}

    @app.post("/api/v1/auth/members/{subject}/reset-password")
    def reset_member_password(subject: str, principal: Principal = Depends(admin)):
        with db.transaction() as session:
            target_membership(session, principal, subject)
            account = session.get(Account, subject)
            if not account:
                raise DomainError("This member has no password login", 409, "no_account")
            return {"subject": subject, "email": account.email,
                    "temporaryPassword": reset_account_password(session, account)}

    @app.get("/api/v1/auth/invites")
    def invites(principal: Principal = Depends(admin)):
        with db.transaction() as session:
            rows = session.scalars(select(Invite).where(Invite.tenant_id == principal.tenant_id)
                                   .order_by(Invite.created_at.desc()).limit(100)).all()
            return {"invites": [{
                "id": invite.id, "role": invite.role, "email": invite.email, "createdBy": invite.created_by,
                "createdAt": invite.created_at.isoformat(), "expiresAt": invite.expires_at.isoformat(),
                "status": ("redeemed" if invite.redeemed_at else
                           "expired" if invite.expires_at.replace(tzinfo=now().tzinfo) <= now() else "pending"),
            } for invite in rows]}

    @app.post("/api/v1/auth/invites")
    def create_invite(body: InviteRequest, principal: Principal = Depends(admin)):
        require_owner_for(principal, body.role)
        with db.transaction() as session:
            code = issue_invite(session, principal.tenant_id, body.role, body.expires_hours, body.email,
                                created_by=principal.subject)
        return {"code": code, "role": body.role, "email": body.email, "expiresInHours": body.expires_hours}

    @app.delete("/api/v1/auth/invites/{invite_id}")
    def revoke_invite(invite_id: str, principal: Principal = Depends(admin)):
        with db.transaction() as session:
            invite = session.scalar(select(Invite).where(
                Invite.id == invite_id, Invite.tenant_id == principal.tenant_id))
            if not invite:
                raise DomainError("Invite not found", 404, "not_found")
            require_owner_for(principal, invite.role)
            if invite.redeemed_at:
                raise DomainError("This invite was already used", 409, "invite_redeemed")
            session.delete(invite)
        return {"id": invite_id, "revoked": True}
