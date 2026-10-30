import argparse
import json

from sqlalchemy import select

from .auth import issue_invite, reset_password
from .config import get_settings
from .db import Database, Membership, Organization, uid


def main():
    parser = argparse.ArgumentParser(description="Kleos9 administration")
    sub = parser.add_subparsers(dest="command", required=True)
    invite = sub.add_parser("create-invite", help="Print a single-use signup code for an organization")
    invite.add_argument("--organization", default="Kleos beta")
    invite.add_argument("--role", choices=["owner", "admin", "member", "viewer"], default="member")
    invite.add_argument("--email", help="Restrict the code to this email address")
    invite.add_argument("--hours", type=int, default=168, help="Hours until the code expires")
    reset = sub.add_parser("reset-password", help="Print a temporary password for an existing account")
    reset.add_argument("--email", required=True)
    sub.add_parser("bootstrap-dev", help="Create the development membership")
    sub.add_parser("export-contracts")
    args = parser.parse_args()
    if args.command == "export-contracts":
        from pathlib import Path

        from .api import create_app
        path = Path("contracts")
        path.mkdir(exist_ok=True)
        (path / "openapi.json").write_text(json.dumps(create_app().openapi(), indent=2) + "\n")
        return
    settings = get_settings()
    db = Database(settings.database_url)
    with db.transaction() as session:
        if args.command == "reset-password":
            result = {"email": args.email, "temporaryPassword": reset_password(session, args.email)}
        else:
            name = "Kleos development" if args.command == "bootstrap-dev" else args.organization
            org = session.scalar(select(Organization).where(Organization.name == name))
            if not org:
                org = Organization(id=uid(), name=name)
                session.add(org)
                session.flush()
            if args.command == "create-invite":
                result = {"organizationId": org.id, "role": args.role, "expiresInHours": args.hours,
                          "inviteCode": issue_invite(session, org.id, args.role, args.hours, args.email)}
            else:
                subject, role = "development", "owner"
                membership = session.scalar(select(Membership).where(
                    Membership.subject == subject, Membership.tenant_id == org.id))
                if membership:
                    membership.role = role
                else:
                    session.add(Membership(subject=subject, tenant_id=org.id, role=role))
                result = {"organizationId": org.id, "subject": subject, "role": role}
    db.close()
    print(json.dumps(result))


if __name__ == "__main__":
    main()
