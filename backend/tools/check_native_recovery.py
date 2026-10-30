"""Opt-in local Docker rebuild and worker-recovery drill using an existing source closure."""
import argparse
import hashlib
import json
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
from sqlalchemy import func, select

from kleos.config import get_settings
from kleos.db import Database, Invocation, NativeJob, Project


def docker(*args):
    result = subprocess.run(["/usr/local/bin/docker", *args], capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise RuntimeError(f"Docker operation failed: {args[0]}")
    return result.stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--job", required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--allow-worker-restart", action="store_true")
    args = parser.parse_args()
    if not args.allow_worker_restart:
        parser.error("Explicit --allow-worker-restart is required")
    settings = get_settings()
    if settings.environment != "development" or settings.auth_mode != "development":
        raise SystemExit("Only the local development stack may be tested")
    if args.report.exists():
        raise SystemExit("Preserve the previous report and reconcile its command before another drill")
    container = "kleos9-engineering-1"
    worker = json.loads(docker("inspect", container))[0]
    labels = worker["Config"]["Labels"]
    assert labels["com.docker.compose.project"] == "kleos9"
    assert labels["com.docker.compose.service"] == "engineering" and worker["State"]["Running"]
    report = {"startedAt": datetime.now(UTC).isoformat(), "projectId": args.project,
              "sourceJobId": args.job, "checks": [], "status": "running"}

    def save():
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n")

    def check(name, passed, **detail):
        entry = {"name": name, "passed": bool(passed), **detail}
        report["checks"].append(entry)
        print(json.dumps(entry), flush=True)
        save()
        if not passed:
            raise RuntimeError(name)

    db = Database(settings.database_url)
    with db.transaction(read_only=True) as session:
        before_calls = session.scalar(select(func.count(Invocation.id)).where(Invocation.project_id == args.project))
        before_spent = session.get(Project, args.project).spent_usd
        source = session.get(NativeJob, args.job)
        assert source and source.request["projectId"] == args.project
        original_inputs = source.request["inputArtifacts"]
        original_tool = source.request["toolVersion"]
    report["baseline"] = {"invocationCount": before_calls, "allowanceSpentUsd": before_spent,
                          "workerStartedAt": worker["State"]["StartedAt"]}
    try:
        with httpx.Client(base_url="http://127.0.0.1:8000", timeout=30, headers={
            "Authorization": f"Bearer {settings.development_token.get_secret_value()}"
        }) as client:
            def get(path):
                response = client.get(path)
                response.raise_for_status()
                return response.json()

            workspace = get(f"/api/v1/projects/{args.project}/workspace")
            body = {"type": "project.retryNative", "payload": {"projectId": args.project, "jobId": args.job},
                    "expectedRevision": workspace["revision"]}
            key = f"native-recovery-{uuid4()}"
            report["pendingCommand"] = {"body": body, "idempotencyKey": key}
            save()
            response = client.post("/api/v1/commands", json=body, headers={"Idempotency-Key": key})
            response.raise_for_status()
            receipt = response.json()
            report["commandReceipt"] = receipt
            report.pop("pendingCommand")
            save()
            crashed = False
            deadline = time.monotonic() + 900
            while time.monotonic() < deadline:
                run = get(f"/api/v1/runs/{receipt['runId']}")
                jobs = [job for task in run["tasks"] for job in task["nativeJobs"]]
                if not crashed and jobs and jobs[0]["status"] == "running":
                    native_id = f"kleos-native-{jobs[0]['id']}"
                    native = json.loads(docker("inspect", native_id))[0]
                    if native["State"]["Running"]:
                        report["nativeJobId"] = jobs[0]["id"]
                        report["nativeContainerId"] = native["Id"]
                        # Interrupt only this task's trusted dispatcher. The isolated
                        # native job and all unrelated containers stay running.
                        docker("kill", "--signal", "SIGKILL", container)
                        crashed = True
                        check("Engineering worker killed while the same native job was running", True)
                        # Docker CLI kill is an intentional stop and can suppress
                        # restart policies; explicitly restart this exact worker.
                        docker("start", container)
                        replay = client.post("/api/v1/commands", json=body, headers={"Idempotency-Key": key})
                        check("Duplicate command after worker crash returns the same receipt",
                              replay.status_code == 202 and replay.json() == receipt)
                if run["status"] in {"succeeded", "blocked", "cancelled", "failed"}:
                    report["run"] = run
                    break
                time.sleep(2)
            else:
                raise TimeoutError("Rebuild remains active; inspect the saved command before retrying")
            check("Worker crash was exercised", crashed)
            check("Same durable rebuild succeeds after recovery", run["status"] == "succeeded")
            current_worker = json.loads(docker("inspect", container))[0]
            check("Trusted worker restarted", current_worker["State"]["Running"]
                  and current_worker["State"]["StartedAt"] > worker["State"]["StartedAt"])
            with db.transaction(read_only=True) as session:
                job = session.get(NativeJob, report["nativeJobId"])
                project = session.get(Project, args.project)
                after_calls = session.scalar(select(func.count(Invocation.id)).where(Invocation.project_id == args.project))
                check("Rebuild reuses exact source references and pins a new tool image",
                      job.request["inputArtifacts"] == original_inputs and job.request["toolVersion"] != original_tool,
                      toolVersion=job.request["toolVersion"])
                check("No new hosted model or embedding calls", after_calls == before_calls, callsBefore=before_calls,
                      callsAfter=after_calls)
                check("Exactly one native allowance debit", abs(project.spent_usd - before_spent
                      - settings.native_job_reservation_usd) < 0.000001, allowanceDebitUsd=project.spent_usd-before_spent)
            state = get(f"/api/v1/projects/{args.project}/workspace")
            outputs = [artifact for artifact in state["artifacts"] if
                       artifact.get("provenance", {}).get("nativeJobId") == report["nativeJobId"]]
            check("Native artifacts published once", len(outputs) == len({a["name"] for a in outputs}) and bool(outputs))
            for artifact in outputs:
                downloaded = client.get(f"/api/v1/artifacts/{artifact['id']}/content")
                check(f"Artifact integrity: {artifact['name']}", downloaded.status_code == 200 and
                      hashlib.sha256(downloaded.content).hexdigest() == artifact["sha256"], artifactId=artifact["id"])
            report["outputs"] = outputs
            report["status"] = "passed"
    except Exception as exc:
        report["status"], report["errorType"] = "failed", type(exc).__name__
        raise
    finally:
        report["finishedAt"] = datetime.now(UTC).isoformat()
        save()
        db.close()


if __name__ == "__main__":
    main()
