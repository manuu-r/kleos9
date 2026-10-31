"""Opt-in, bounded live acceptance driver; never substitutes fabricated engineering evidence."""
import argparse
import asyncio
import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx

from kleos.config import get_settings
from kleos.contracts import Lane
from kleos.domain import ASYNC_LANES


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["foundation", "generate", "verify", "inspect"])
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--brief", type=Path)
    parser.add_argument("--project")
    parser.add_argument("--lane", choices=["mechanics", "electronics", "firmware", "application"])
    parser.add_argument("--report", type=Path, default=Path(".data/live-project-report.json"))
    parser.add_argument("--allow-paid", action="store_true")
    parser.add_argument("--expect-blocked", action="store_true",
                        help="Verify an incomplete candidate closure stays blocked; never a release acceptance")
    parser.add_argument("--timeout", type=int, default=1200)
    args = parser.parse_args()
    if args.expect_blocked and args.phase != "verify":
        parser.error("--expect-blocked is only valid for independent verification")
    if args.phase != "inspect" and not args.allow_paid:
        parser.error("Live execution requires --allow-paid and configured operator allowances")
    if args.phase == "foundation" and not args.brief:
        parser.error("foundation requires --brief")
    if args.phase != "foundation" and not args.project:
        parser.error("This phase requires --project")
    settings = get_settings()
    if settings.environment not in {"development", "test"} or settings.auth_mode != "development":
        raise SystemExit("This driver only runs against an explicitly configured development environment")
    if args.report.exists() and json.loads(args.report.read_text()).get("pendingCommand"):
        raise SystemExit("An uncertain command is recorded in this report; reconcile its original idempotency key first")
    report = {"startedAt": datetime.now(UTC).isoformat(), "phase": args.phase, "checks": [], "runs": []}
    project = args.project

    def save():
        report["projectId"] = project
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n")

    def record(name, passed, **details):
        result = {"name": name, "passed": passed, **details}
        report["checks"].append(result)
        print(json.dumps(result), flush=True)
        save()
        if not passed:
            raise RuntimeError(f"Acceptance check failed: {name}")

    async with httpx.AsyncClient(base_url=args.url, timeout=30, headers={
        "Authorization": f"Bearer {settings.development_token.get_secret_value()}"
    }) as client:
        async def get(path):
            for attempt in range(3):
                try:
                    response = await client.get(path)
                    response.raise_for_status()
                    return response.json()
                except httpx.TransportError:
                    if attempt == 2:
                        raise
                    await asyncio.sleep(1)

        async def workspace():
            return await get(f"/api/v1/projects/{project}/workspace")

        async def wait(run_id):
            deadline, previous = time.monotonic() + args.timeout, None
            while time.monotonic() < deadline:
                run = await get(f"/api/v1/runs/{run_id}")
                progress = {"runId": run_id, "status": run["status"],
                            "tasks": [{"lane": task["lane"], "status": task["status"]} for task in run["tasks"]]}
                if progress != previous:
                    print(json.dumps(progress), flush=True)
                    previous = progress
                if run["status"] in {"succeeded", "blocked", "failed", "cancelled"}:
                    report["runs"].append(run)
                    save()
                    return run
                await asyncio.sleep(2)
            raise TimeoutError(f"Run {run_id} remains active; inspect before retrying")

        async def command(kind, payload=None, duplicate=False, expected=None):
            body = {"type": kind, "payload": payload or {"projectId": project}}
            if project and kind != "project.create":
                body["expectedRevision"] = expected if expected is not None else (await workspace())["revision"]
            key = str(uuid4())
            report["pendingCommand"] = {"idempotencyKey": key, "body": body}
            save()
            for attempt in range(3):
                try:
                    response = await client.post("/api/v1/commands", json=body, headers={"Idempotency-Key": key})
                    break
                except httpx.TransportError:
                    if attempt == 2:
                        raise
                    await asyncio.sleep(1)
            if response.status_code < 500:
                report.pop("pendingCommand")
            save()
            if response.status_code != 202:
                # Server domain errors are redacted; never dump request headers or credential-bearing URLs.
                raise RuntimeError(f"{kind}: HTTP {response.status_code}, {response.json().get('code', 'unknown')}")
            receipt = response.json()
            report.setdefault("commandReceipts", []).append({"idempotencyKey": key, "receipt": receipt})
            save()
            if duplicate:
                replay = await client.post("/api/v1/commands", json=body, headers={"Idempotency-Key": key})
                record(f"{kind}: durable duplicate receipt", replay.status_code == 202 and replay.json() == receipt)
            if kind in ASYNC_LANES:
                return await wait(receipt["runId"])
            return receipt

        try:
            readiness_deadline = time.monotonic() + 60
            while True:
                try:
                    ready = await client.get("/api/v1/ready")
                    if ready.status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                if time.monotonic() >= readiness_deadline:
                    raise RuntimeError("Local API is not ready")
                await asyncio.sleep(1)
            catalog = await get("/api/v1/model-catalog")
            model = next(item for item in catalog["models"] if item["provider"] == "meta")
            record("Muse Model API configured", model["configured"], model=model["model"])
            record("Unauthorized requests rejected", (await client.get("/api/v1/projects",
                   headers={"Authorization": "Bearer invalid-fixture-token"})).status_code == 401)
            if args.phase == "foundation":
                policy = {lane.value: {"provider": "meta", "model": model["model"]} for lane in Lane}
                if project is None:
                    receipt = await command("project.create", {"brief": args.brief.read_text(), "modelPolicy": policy}, duplicate=True)
                    project = receipt["projectId"]
                save()
                run = await command("project.research", duplicate=True)
                record("Live grounded research", run["status"] == "succeeded", runId=run["id"])
                run = await command("project.requestReview")
                record("Live concept proposal", run["status"] == "succeeded", runId=run["id"])
                proposal = (await workspace())["project"]["state"].get("proposal", {})
                record("Proposal contains explicit requirements", bool(proposal.get("requirements")))
                await command("project.confirm")
                run = await command("project.solveInterfaces")
                record("Live interface solution", run["status"] == "succeeded", runId=run["id"])
                record("Interfaces recorded", bool((await workspace())["project"]["interfaces"]))
            elif args.phase == "generate":
                run = await command("project.generate", {"projectId": project, **({"lane": args.lane} if args.lane else {})},
                                    duplicate=True)
                # Blockers are valid observations, but never an acceptance pass.
                record("Live engineering generation and native builds", run["status"] == "succeeded", runId=run["id"])
            elif args.phase == "verify":
                run = await command("project.verify", duplicate=True)
                checks = (await workspace())["checks"]
                report["validationChecks"] = checks
                save()
                if args.expect_blocked:
                    record("Incomplete engineering closure remains blocked", run["status"] == "blocked"
                           and any(check["status"] in {"FAIL", "UNKNOWN"} for check in checks), runId=run["id"])
                    for kind in ("project.acceptRelease", "project.placeOrders", "project.acceptPhysical", "project.ship"):
                        response = await client.post("/api/v1/commands", json={"type": kind,
                            "payload": {"projectId": project}, "expectedRevision": (await workspace())["revision"]},
                            headers={"Idempotency-Key": str(uuid4())})
                        record(f"Unqualified closure cannot pass {kind}", response.status_code == 409)
                else:
                    record("Independent verification completed", run["status"] == "succeeded", runId=run["id"])
            state = await workspace()
            for artifact in state["artifacts"]:
                response = await client.get(f"/api/v1/artifacts/{artifact['id']}/content")
                record(f"Artifact integrity: {artifact['name']}", response.status_code == 200 and
                       hashlib.sha256(response.content).hexdigest() == artifact["sha256"], artifactId=artifact["id"])
            events = await get(f"/api/v1/projects/{project}/events/history?after=0")
            record("Ordered event history", bool(events) and [event["sequence"] for event in events] ==
                   sorted(set(event["sequence"] for event in events)))
            report["workspace"] = state
            report["status"] = "passed"
        except Exception as exc:
            report["status"], report["errorType"] = "blocked", type(exc).__name__
            print(json.dumps({"status": "blocked", "errorType": type(exc).__name__, "projectId": project}), flush=True)
            raise
        finally:
            report["finishedAt"] = datetime.now(UTC).isoformat()
            save()


if __name__ == "__main__":
    asyncio.run(main())
