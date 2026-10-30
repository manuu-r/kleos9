"""Measure reads, command acceptance and SSE delivery without making inference calls."""
import argparse
import asyncio
import json
import os
import time
from contextlib import AsyncExitStack
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx


def percentile(values, fraction=.95):
    return sorted(values)[min(len(values)-1, int(len(values)*fraction))] if values else None


async def run(args):
    token = os.environ["KLEOS_LOAD_TOKEN"]
    headers = {"Authorization": f"Bearer {token}"}
    if os.getenv("KLEOS_LOAD_ORGANIZATION"):
        headers["X-Organization-Id"] = os.environ["KLEOS_LOAD_ORGANIZATION"]
    reads, server_reads, commands, events, projects, cursors, ready = [], [], [], [], [], {}, set()
    errors = []
    async with AsyncExitStack() as stack, httpx.AsyncClient(base_url=args.url, headers=headers, timeout=30) as client:
        # Each simulated user has its own stream + request pool, like the real Flutter clients.
        # A single shared 200-connection HTTPX pool adds client-side scheduling latency.
        viewers = [await stack.enter_async_context(httpx.AsyncClient(base_url=args.url, headers=headers,
                    timeout=30, limits=httpx.Limits(max_connections=2, max_keepalive_connections=2)))
                   for _ in range(args.users)]
        catalog = await client.get("/api/v1/model-catalog")
        catalog.raise_for_status()
        policy = catalog.json()["defaultPolicy"]
        async def command(kind, payload, revision=None):
            start = time.perf_counter()
            response = await client.post("/api/v1/commands", json={"type": kind, "payload": payload,
                "expectedRevision": revision}, headers={"Idempotency-Key": f"load-{uuid4()}"})
            commands.append((time.perf_counter()-start)*1000)
            response.raise_for_status()
            return response.json()
        for i in range(args.projects):
            project = await command("project.create", {"brief": f"Load fixture {uuid4()} ({i})"})
            projects.append(project["projectId"])
            workspace = (await client.get(f"/api/v1/projects/{project['projectId']}/workspace")).json()
            cursors[project["projectId"]] = workspace["eventCursor"]
        async def reader(index):
            project = projects[index % len(projects)]
            for _ in range(args.reads):
                start = time.perf_counter()
                response = await viewers[index].get(f"/api/v1/projects/{project}/workspace")
                reads.append((time.perf_counter()-start)*1000)
                if response.headers.get("server-timing", "").startswith("app;dur="):
                    server_reads.append(float(response.headers["server-timing"].split("=")[1]))
                response.raise_for_status()
        async def observer(index):
            project = projects[index % len(projects)]
            try:
                async with viewers[index].stream("GET", f"/api/v1/projects/{project}/events?after={cursors[project]}", timeout=60) as response:
                    response.raise_for_status()
                    ready.add(index)
                    async for line in response.aiter_lines():
                        if line.startswith("data: "):
                            event = json.loads(line[6:])
                            if event.get("type") == "modelPolicy.changed":
                                created = datetime.fromisoformat(event["createdAt"])
                                events.append((datetime.now(UTC)-created.astimezone(UTC)).total_seconds()*1000)
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                errors.append(type(exc).__name__)
        observers = [asyncio.create_task(observer(i)) for i in range(args.users)]
        deadline = time.monotonic()+20
        while len(ready) != args.users and time.monotonic() < deadline:
            await asyncio.sleep(.05)
        await asyncio.gather(*(reader(i) for i in range(args.users)))
        for _ in range(args.events):
            for project in projects:
                workspace = await client.get(f"/api/v1/projects/{project}/workspace")
                workspace.raise_for_status()
                await command("project.setModelPolicy", {"projectId": project, "modelPolicy": policy},
                              workspace.json()["revision"])
            await asyncio.sleep(.6)
        await asyncio.sleep(2)
        for observer_task in observers:
            observer_task.cancel()
        await asyncio.gather(*observers)
        if args.cleanup:
            for project in projects:
                workspace = (await client.get(f"/api/v1/projects/{project}/workspace")).json()
                response = await client.delete(f"/api/v1/projects/{project}", params={"expectedRevision": workspace["revision"]})
                response.raise_for_status()
    report = {"mode": "control-plane-only; no provider/native workload", "users": args.users,
              "projects": projects, "connectedStreams": len(ready), "readSamples": len(reads),
              "commandSamples": len(commands), "eventSamples": len(events), "errors": errors,
              "p95ReadMs": percentile(reads), "p95CommandMs": percentile(commands), "p95EventMs": percentile(events),
              "p95ServerReadMs": percentile(server_reads),
              "eventClockRequirement": "Client/server clocks synchronized; timestamp starts before transaction commit"}
    report["passed"] = (len(ready) == args.users and len(events) == args.users*args.events and not errors
                        and report["p95ReadMs"] < 500 and report["p95CommandMs"] < 1000 and report["p95EventMs"] < 2000)
    Path(args.output).write_text(json.dumps(report, indent=2)+"\n")
    print(json.dumps({k:v for k,v in report.items() if k != "projects"}, indent=2))
    return report["passed"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--users", type=int, default=100)
    parser.add_argument("--projects", type=int, default=10)
    parser.add_argument("--reads", type=int, default=10)
    parser.add_argument("--events", type=int, default=3)
    parser.add_argument("--output", default="load-report.json")
    parser.add_argument("--cleanup", action="store_true")
    raise SystemExit(0 if asyncio.run(run(parser.parse_args())) else 1)
