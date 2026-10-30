"""Opt-in local database/artifact backup verification with no restored workers.

The PostgreSQL snapshot is read-only. Only a uniquely named disposable database
is restored/dropped; private backup files remain below backend/.data.
"""

import hashlib
import json
import os
import stat
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from sqlalchemy.engine import make_url

from kleos.config import Settings

pytestmark = pytest.mark.skipif(os.getenv("KLEOS_TEST_BACKUP_RESTORE") != "1",
                                reason="Opt-in local PostgreSQL backup/restore verification")

TABLES = ("projects", "commands", "events", "artifacts", "external_intents", "outbox", "runs", "tasks",
          "attempts", "invocations", "native_jobs", "validations", "releases", "training_consents", "sources",
          "datasets", "trained_models")


def _fingerprints(connection):
    result = {}
    for table in TABLES:
        digest = hashlib.sha256()
        rows = connection.execute(sql.SQL("SELECT row_to_json(t) FROM {} AS t ORDER BY id").format(
            sql.Identifier(table)))
        count = 0
        for (row,) in rows:
            digest.update(json.dumps(row, sort_keys=True, separators=(",", ":"), default=str).encode())
            digest.update(b"\n")
            count += 1
        result[table] = {"rows": count, "canonicalSha256": digest.hexdigest()}
    return result


def _hash_file(path):
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _run(command, **kwargs):
    result = subprocess.run(command, stderr=subprocess.PIPE, timeout=120, check=False, **kwargs)
    if result.returncode:
        pytest.fail(f"Local backup tool failed with exit code {result.returncode}; diagnostic body suppressed", pytrace=False)
    return result


def test_local_consistent_postgres_backup_restore_and_artifact_integrity():
    settings = Settings()
    url = make_url(settings.database_url)
    assert url.host in {"localhost", "127.0.0.1"} and url.port == 5432
    assert settings.storage_backend == "local"
    source_url = url.set(drivername="postgresql").render_as_string(hide_password=False)
    admin_url = url.set(drivername="postgresql", database="postgres").render_as_string(hide_password=False)
    restore_name = "kleos_restore_verify_" + uuid4().hex[:16]
    assert restore_name != url.database and restore_name.startswith("kleos_restore_verify_")
    target_url = url.set(drivername="postgresql", database=restore_name).render_as_string(hide_password=False)
    container = "kleos9-postgres-1"
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    root = Path(".data/recovery_checks")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    destination = root / f"{stamp}_{uuid4().hex[:12]}"
    destination.mkdir(mode=0o700)
    dump_path = destination / "application.dump"
    artifacts_path = destination / "artifacts"
    artifacts_path.mkdir(mode=0o700)
    report = {"schemaVersion": 1, "startedAt": datetime.now(UTC).isoformat(), "status": "running",
              "scope": "Local Docker PostgreSQL application database and referenced local Artifact-table files",
              "snapshot": "Exported PostgreSQL REPEATABLE READ READ ONLY snapshot shared with pg_dump",
              "backupDirectory": str(destination.resolve()), "disposableDatabase": restore_name,
              "sourceApplicationRowsWritten": 0, "servicesStopped": 0, "restoredWorkersStarted": 0,
              "paidProviderCalls": 0, "awsOrS3Verified": False}
    created = False
    source_connection = target_connection = admin = None
    manifest = []
    try:
        pg_dump_version = _run(["docker", "exec", container, "pg_dump", "--version"], stdout=subprocess.PIPE)
        report["pgDumpVersion"] = pg_dump_version.stdout.decode().strip()
        source_connection = psycopg.connect(source_url, autocommit=True, application_name="kleos_backup_verification")
        source_connection.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
        source_connection.execute("SET LOCAL TIME ZONE 'UTC'")
        snapshot_id = source_connection.execute("SELECT pg_export_snapshot()").fetchone()[0]
        expected = _fingerprints(source_connection)
        records = source_connection.execute("SELECT id, key, sha256, size FROM artifacts ORDER BY id").fetchall()
        assert sum(record[3] for record in records) <= 2 * 1024 ** 3, "Fixture backup exceeded the 2 GiB local-file ceiling"
        with os.fdopen(os.open(dump_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as backup:
            _run(["docker", "exec", container, "pg_dump", "--username", url.username, "--dbname", url.database,
                  "--format=custom", "--no-owner", "--no-privileges", "--snapshot", snapshot_id], stdout=backup)
        base = settings.local_artifact_dir.resolve()
        for artifact_id, key, expected_hash, expected_size in records:
            assert len(expected_hash) == 64 and all(char in "0123456789abcdef" for char in expected_hash)
            source_path = (base / key).resolve()
            assert source_path.is_relative_to(base), "Snapshot artifact path is outside configured storage"
            blob = artifacts_path / expected_hash
            if not blob.exists():
                with source_path.open("rb") as original, os.fdopen(
                        os.open(blob, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as copy:
                    while chunk := original.read(1024 * 1024):
                        copy.write(chunk)
            actual_hash, actual_size = _hash_file(blob)
            assert actual_hash == expected_hash and actual_size == expected_size, "Snapshot artifact integrity mismatch"
            manifest.append({"artifactId": artifact_id, "key": key, "sha256": expected_hash,
                             "size": expected_size, "backupBlob": f"artifacts/{expected_hash}"})
        source_connection.execute("ROLLBACK")
        source_connection.close()
        source_connection = None
        private_manifest = destination / "artifact-manifest.json"
        with os.fdopen(os.open(private_manifest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as output:
            json.dump({"artifacts": manifest, "metadata": expected}, output, indent=2)
        report["dumpSha256"], report["dumpBytes"] = _hash_file(dump_path)
        report["referencedArtifactRows"] = len(manifest)
        report["uniqueArtifactBlobs"] = len(list(artifacts_path.iterdir()))
        report["referencedArtifactBytes"] = sum(item["size"] for item in manifest)
        report["backupPermissions"] = {"directory": oct(stat.S_IMODE(destination.stat().st_mode)),
                                        "dump": oct(stat.S_IMODE(dump_path.stat().st_mode)),
                                        "manifest": oct(stat.S_IMODE(private_manifest.stat().st_mode))}
        assert stat.S_IMODE(destination.stat().st_mode) == 0o700
        assert stat.S_IMODE(dump_path.stat().st_mode) == stat.S_IMODE(private_manifest.stat().st_mode) == 0o600

        admin = psycopg.connect(admin_url, autocommit=True, application_name="kleos_restore_admin_verification")
        admin.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(restore_name)))
        created = True
        with dump_path.open("rb") as backup:
            _run(["docker", "exec", "-i", container, "pg_restore", "--username", url.username,
                  "--dbname", restore_name, "--exit-on-error", "--no-owner", "--no-privileges"],
                 stdin=backup, stdout=subprocess.DEVNULL)
        admin.execute(sql.SQL("ALTER DATABASE {} SET default_transaction_read_only = on").format(sql.Identifier(restore_name)))
        target_connection = psycopg.connect(target_url, autocommit=True, application_name="kleos_frozen_restore_verification")
        target_connection.execute("SET TIME ZONE 'UTC'")
        assert target_connection.execute("SHOW default_transaction_read_only").fetchone()[0] == "on"
        assert target_connection.execute("SHOW transaction_read_only").fetchone()[0] == "on"
        actual = _fingerprints(target_connection)
        assert actual == expected, "Restored application metadata differs from the exported snapshot"
        restored_artifacts = target_connection.execute("SELECT id, sha256, size FROM artifacts ORDER BY id").fetchall()
        indexed_manifest = {item["artifactId"]: item for item in manifest}
        for artifact_id, sha, size in restored_artifacts:
            entry = indexed_manifest[artifact_id]
            assert entry["sha256"] == sha and entry["size"] == size
            assert _hash_file(destination / entry["backupBlob"]) == (sha, size)
        nonterminal = target_connection.execute("""SELECT status, count(*) FROM external_intents
            WHERE status NOT IN ('succeeded','failed','completed','cancelled') GROUP BY status ORDER BY status""").fetchall()
        pending_outbox = target_connection.execute("SELECT count(*) FROM outbox WHERE delivered_at IS NULL").fetchone()[0]
        pending_models = target_connection.execute("SELECT count(*) FROM invocations WHERE status='pending'").fetchone()[0]
        active_native = target_connection.execute("""SELECT count(*) FROM native_jobs
            WHERE status IN ('pending','dispatching','submitted','running')""").fetchone()[0]
        # A harmless zero-row write still proves that PostgreSQL enforces the restore quarantine.
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            target_connection.execute("UPDATE outbox SET failures=failures WHERE FALSE")
        connections = admin.execute("""SELECT count(*) FROM pg_stat_activity WHERE datname=%s
            AND application_name <> 'kleos_frozen_restore_verification'""", (restore_name,)).fetchone()[0]
        assert connections == 0, "Unexpected session connected to the isolated restore"
        time.sleep(2)
        assert _fingerprints(target_connection) == actual, "Frozen restored records changed without authorized dispatch"
        report["metadata"] = actual
        report["metadataMatchesExportedSnapshot"] = True
        report["artifactManifestVerifiedAgainstRestoredRows"] = True
        report["dispatchQuarantine"] = {"databaseDefaultReadOnly": True, "writeAttemptRejected": True,
            "nonterminalExternalIntentsByStatus": dict(nonterminal), "pendingOutboxRows": pending_outbox,
            "pendingModelInvocations": pending_models, "activeNativeJobs": active_native,
            "otherConnectedSessions": 0, "unchangedObservationSeconds": 2,
            "workersOrDispatchersStarted": False,
            "reconciliationPerformed": False,
            "limitation": "Read-only isolation verified; live external outcomes were neither queried nor resumed"}
        report["status"] = "passed"
    finally:
        if source_connection:
            source_connection.close()
        if target_connection:
            target_connection.close()
        if created:
            assert admin is not None
            sessions = admin.execute("SELECT count(*) FROM pg_stat_activity WHERE datname=%s", (restore_name,)).fetchone()[0]
            assert sessions == 0, "Temporary restore retained because an unexpected connection is active"
            admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(restore_name)))
            report["disposableDatabaseDropped"] = True
        if admin:
            admin.close()
        if report["status"] == "running":
            report["status"] = "failed"
        report["completedAt"] = datetime.now(UTC).isoformat()
        report["limitations"] = ["No workers connected to the restored database and no external commitments were resumed",
            "This backs up the application database and Artifact-table files, not the separate DBOS database or transcript/log object closure",
            "Local restore only; no AWS RDS, S3 version recovery, regional disaster recovery, or production restore timing claim",
            "Qdrant indexing, retrieval and cached-query reuse have separate integration evidence; this check did not restore Qdrant"]
        Path("verification").mkdir(exist_ok=True)
        Path("verification/backup-restore-runtime.json").write_text(json.dumps(report, indent=2) + "\n")
