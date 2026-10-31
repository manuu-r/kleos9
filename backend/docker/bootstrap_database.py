"""Idempotent operator bootstrap. Runs once inside the VPC; never logs secrets.

Application task roles cannot perform this operation. Use the dedicated
bootstrap task, then apply migrations before activating services.
"""
import json
import os
import secrets
from urllib.parse import quote, urlsplit

import boto3
import psycopg
from psycopg import sql


def main():
    client = boto3.client("secretsmanager", region_name=os.environ["AWS_REGION"])
    target = os.environ["RUNTIME_SECRET_ARN"]
    admin = json.loads(client.get_secret_value(SecretId=os.environ["ADMIN_SECRET_ARN"])["SecretString"])
    try:
        runtime = json.loads(client.get_secret_value(SecretId=target)["SecretString"])
    except client.exceptions.ResourceNotFoundException:
        runtime = {}
    host = os.environ["DATABASE_HOST"]
    roles = [
        ("kleos_app", "kleos", "KLEOS_DATABASE_URL", "postgresql+psycopg"),
        ("kleos_workflows", "kleos_dbos", "KLEOS_DBOS_DATABASE_URL", "postgresql"),
        ("kleos_conductor", "conductor", "CONDUCTOR_DATABASE_URL", "postgresql"),
    ]
    with psycopg.connect(host=host, dbname="postgres", user=admin["username"],
                        password=admin["password"], sslmode="require", autocommit=True) as connection:
        for role, database, key, scheme in roles:
            from urllib.parse import unquote
            old_password = urlsplit(runtime[key]).password if runtime.get(key) else None
            password = unquote(old_password) if old_password else secrets.token_urlsafe(48)
            if not connection.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,)).fetchone():
                connection.execute(sql.SQL("CREATE ROLE {} LOGIN").format(sql.Identifier(role)))
            connection.execute(sql.SQL("ALTER ROLE {} PASSWORD {}").format(sql.Identifier(role), sql.Literal(password)))
            connection.execute(sql.SQL("GRANT {} TO {}").format(sql.Identifier(role), sql.Identifier(admin["username"])))
            if not connection.execute("SELECT 1 FROM pg_database WHERE datname=%s", (database,)).fetchone():
                connection.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(
                    sql.Identifier(database), sql.Identifier(role)))
            else:
                connection.execute(sql.SQL("ALTER DATABASE {} OWNER TO {}").format(
                    sql.Identifier(database), sql.Identifier(role)))
            connection.execute(sql.SQL("REVOKE CONNECT ON DATABASE {} FROM PUBLIC").format(sql.Identifier(database)))
            connection.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                sql.Identifier(database), sql.Identifier(role)))
            runtime[key] = f"{scheme}://{role}:{quote(password, safe='')}@{host}:5432/{database}?sslmode=require"
    runtime.setdefault("KLEOS_SESSION_KEY", secrets.token_urlsafe(48))
    for key in ("KLEOS_META_API_KEY", "KLEOS_GEMINI_API_KEY", "KLEOS_CONDUCTOR_KEY",
                "KLEOS_MODAL_LLM_URL", "KLEOS_MODAL_LLM_KEY",
                "KLEOS_DIGIKEY_CLIENT_ID", "KLEOS_DIGIKEY_CLIENT_SECRET", "MODAL_TOKEN_ID", "MODAL_TOKEN_SECRET"):
        runtime.setdefault(key, "")
    runtime.setdefault("KLEOS_WEBHOOK_SECRETS", "{}")
    client.put_secret_value(SecretId=target, SecretString=json.dumps(runtime))
    print("Three databases and restricted database identities are ready. Runtime secret updated.")


if __name__ == "__main__":
    main()
