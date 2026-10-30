import argparse
import json

from sqlalchemy import select

from .config import get_settings
from .db import Database, Membership, Organization, uid


def main():
    parser = argparse.ArgumentParser(description="Kleos9 administration")
    sub = parser.add_subparsers(dest="command", required=True)
    invite = sub.add_parser("invite", help="Grant an existing Cognito subject access")
    invite.add_argument("--subject", required=True)
    invite.add_argument("--organization", default="Kleos beta")
    invite.add_argument("--role", choices=["owner", "admin", "member", "viewer"], default="member")
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
        subject = "development" if args.command == "bootstrap-dev" else args.subject
        name = "Kleos development" if args.command == "bootstrap-dev" else args.organization
        role = "owner" if args.command == "bootstrap-dev" else args.role
        org = session.scalar(select(Organization).where(Organization.name == name))
        if not org:
            org = Organization(id=uid(), name=name)
            session.add(org)
            session.flush()
        membership = session.scalar(select(Membership).where(
            Membership.subject == subject, Membership.tenant_id == org.id))
        if membership:
            membership.role = role
        else:
            session.add(Membership(subject=subject, tenant_id=org.id, role=role))
        print(json.dumps({"organizationId": org.id, "subject": subject, "role": role}))
    db.close()


if __name__ == "__main__":
    main()
