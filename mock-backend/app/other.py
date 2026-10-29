"""Simulated connectors, provider jobs and developer qualification."""
from copy import deepcopy
import json
import math
from .simulation import DATA, find, require
from .tasks import repost_waiting


def connector_command(sim, kind, payload):
    c = find(sim.state["connectors"], payload["connectorId"], "Connector")
    action = kind.split(".")[1]
    key = f'{kind}:{c["id"]}'
    def progress(phase, total, delay):
        run = {"phase": phase, "total": total, "progress": 0, "status": "running"}
        sim.connection_runs[c["id"]] = run
        def tick(index):
            if sim.connection_runs.get(c["id"]) is not run: return
            run["progress"] = index
            if index == total:
                run["status"] = "done"
                if phase == "health" and c["linked"]: c.update(health="healthy", lastCheck="just now")
        sim.enqueue(key, [(delay, lambda i=i: tick(i)) for i in range(1, total + 1)])
    if action == "authorize":
        pass  # Intentionally no OAuth exchange or credential storage in this simulation.
    elif action == "test":
        progress("health" if c["linked"] else "tests", 1 if c["linked"] else 5, 900 if c["linked"] else 550)
    elif action == "discover":
        require(payload["host"].strip(), "Enter a printer address")
        require(c["kind"] == "printer", "Connector is not a printer")
        progress("discover", 3, 500)
    elif action == "calibrate":
        run = sim.connection_runs.get(c["id"], {})
        require(run.get("phase") == "discover" and run.get("status") == "done", "Discover the printer first")
        progress("calibrate", 4, 900)
    elif action == "save":
        run = sim.connection_runs.get(c["id"], {})
        require(run.get("phase") == "tests" and run.get("status") == "done", "Run the connection tests first")
        require(payload["capabilities"] and all(cap in c["capabilities"] for cap in payload["capabilities"]) and math.isfinite(payload["scope"]) and payload["scope"] >= 0, "Invalid connection scope")
        c.update(linked=True, enabled=True, health="healthy", lastCheck="just now", capabilities=payload["capabilities"], spendingScope=payload["scope"], mode="demo")
        if c["category"] == "Human tasks": repost_waiting(sim)
    elif action == "savePrinter":
        run = sim.connection_runs.get(c["id"], {})
        require(run.get("phase") == "calibrate" and run.get("status") == "done", "Calibrate the printer first")
        profile = deepcopy(DATA["printers"].get(c["id"]) or c.get("printer"))
        require(profile and payload["host"].strip(), "Printer profile or address missing")
        profile["tolerance"] = "X +0.06, Y +0.04, holes −0.12 mm"
        c.update(linked=True, enabled=True, health="healthy", lastCheck="just now", level="Qualified", mode="demo", name=f'{profile["model"]} (home)', printer=profile, description=f'{c["integration"]} at {payload["host"]}. Kleos9 can send sliced jobs here instead of ordering prints.' + (" You confirm each job on the printer." if payload["confirmEach"] else ""))
    elif action == "setEnabled":
        require(c["linked"] and not c.get("builtIn") and c["integration"] != "Built-in", "Connector cannot be changed")
        c["enabled"] = payload["enabled"]
        if c["enabled"] and c["category"] == "Human tasks": repost_waiting(sim)
    elif action == "disconnect":
        require(not c.get("builtIn") and c["integration"] != "Built-in", "Built-in connector cannot be disconnected")
        c.update(linked=False, enabled=False, health="unchecked")
    else:
        raise ValueError(f"Unsupported command: {kind}")


def provider_command(sim, kind, payload):
    job = find(sim.state["providerJobs"], payload["jobId"], "Provider job")
    key = f'{kind}:{job["id"]}'
    if kind == "provider.quote":
        price, lead = payload["price"], payload["lead"]
        require(job["state"] == "new" and math.isfinite(price) and math.isfinite(lead) and price > 0 and lead > 0, "Enter a valid quote for an open job")
        job.update(state="quoted", quote=price)
        def routed():
            if price <= 30 and lead <= 4: job["state"] = "in_progress"
            else: job.update(state="lost", lossReason=f'Price: ${price:g} vs winning $24 from a provider with similar quality' if price > 30 else f'Lead time: {lead:g} days vs winning 2 days')
        sim.enqueue(key, [(1500, routed)])
    elif kind == "provider.decline":
        require(job["state"] == "new", "Job is no longer open")
        job.update(state="lost", lossReason="Declined by you")
    elif kind == "provider.uploadEvidence":
        require(job["state"] == "in_progress", "Job is not in progress")
        job["state"] = "evidence"
        sim.enqueue(key, [(1400, lambda: job.update(state="done"))])
    else:
        raise ValueError(f"Unsupported command: {kind}")


STEPS = ["init", "fixtures", "conformance", "submit", "qualify"]


def reset_developer(dev):
    dev.update(steps=dict.fromkeys(STEPS, "idle"), terminal=[], fixApplied=False, level="Draft")


def capability(manifest):
    services = manifest.get("services")
    service = services[0] if isinstance(services, list) and services and isinstance(services[0], dict) else None
    tool = manifest.get("tool") if isinstance(manifest.get("tool"), dict) else None
    group = service or tool
    values = group.get("capabilities") if group else None
    if isinstance(values, list) and values and isinstance(values[0], str): return values[0]
    return "fab.print.fdm.home" if isinstance(manifest.get("driver"), dict) else "?"


def developer_command(sim, kind, payload):
    dev = sim.state["dev"]
    action = kind.split(".")[1]
    running = "running" in dev["steps"].values()
    require(not running, "Wait for the current CLI operation to finish")
    if action == "configure":
        selected = payload.get("kind")
        if selected and selected != dev["kind"]:
            require(selected in DATA["templates"], "Unknown connector kind")
            dev.update(kind=selected, manifest=DATA["templates"][selected])
            reset_developer(dev)
        if payload.get("capability") and dev["kind"] == "service" and dev["manifest"] == DATA["templates"]["service"]:
            dev["manifest"] = dev["manifest"].replace("fab.print.tpu_flex", payload["capability"])
    elif action == "manifest":
        dev["manifest"] = payload["manifest"]
        reset_developer(dev)
    elif action == "applyFix":
        require(dev["steps"]["fixtures"] == "fail", "Run fixtures first")
        dev["fixApplied"] = True
        dev["terminal"].append(f'dim:  applied fix: {DATA["fixHints"][dev["kind"]][1]}')
    elif action == "run":
        step, connector_kind = payload["step"], dev["kind"]
        try: manifest = json.loads(dev["manifest"])
        except (ValueError, TypeError): raise ValueError("Enter a valid manifest object") from None
        require(isinstance(manifest, dict), "Enter a valid manifest object")
        index = STEPS.index(step)
        require(index == 0 or dev["steps"][STEPS[index - 1]] == "pass", "Complete the preceding CLI operation first")
        dev["steps"][step] = "running"
        name = manifest.get("provider_slug", "connector")
        cap = capability(manifest)
        template = "MCP tool server" if connector_kind == "tool" else "printer driver" if connector_kind == "printer" else "MCP service server"
        if step == "init":
            lines = [f'$ kleos init {name} --kind {connector_kind}', "dim:  created kleos.manifest.json", f'dim:  created adapter/ ({template} template)', "dim:  started local fake platform on :7700" + (" with a virtual printer" if connector_kind == "printer" else ""), "ok:✔ ready. next: kleos fixtures run"]
        elif step == "fixtures":
            lines = ["$ kleos fixtures run"] + [f'{"err:  ✘" if f.get("fails") and not dev["fixApplied"] else "ok:  ✔"} {f["name"]}' for f in DATA["fixtures"][connector_kind]]
            lines.append("ok:8/8 fixtures passed" if dev["fixApplied"] else f'err:7/8 passed. {DATA["fixHints"][connector_kind][0]}')
        elif step == "conformance":
            lines = ["$ kleos conformance --driver", "ok:  ✔ local-network only, no cloud relay of design files", "ok:  ✔ jobs require printer-side confirmation when enabled", "ok:  ✔ telemetry schema matches manifest", "ok:conformance passed"] if connector_kind == "printer" else ["$ kleos conformance --mcp 2026-07-28", "ok:  ✔ stateless requests (no session header)", "ok:  ✔ long jobs use Tasks extension (tasks/get polling)", "ok:  ✔ auth via client ID metadata document", "ok:  ✔ tool schemas match manifest operations", "ok:  ✔ no approve operation exposed" if connector_kind == "tool" else "ok:  ✔ no credentials echoed in tool output", "ok:conformance passed"]
        elif step == "submit":
            lines = ["$ kleos submit", "dim:  signed package uploaded", f'ok:✔ listed as {name}. Level: Schema-verified', "dim:  next: " + ("run the benchmark briefs" if connector_kind == "tool" else "pass a calibration round-trip" if connector_kind == "printer" else "complete a paid qualification job")]
        else:
            qualifier = ["dim:  running 24 golden briefs through your tool (simulated)", "ok:  ✔ 22/24 candidates passed Kleos9 validators", "ok:  ✔ outputs reproducible across reruns"] if connector_kind == "tool" else ["dim:  sending calibration job to the virtual printer", "ok:  ✔ telemetry and completion reported correctly", "ok:  ✔ offsets measured and returned"] if connector_kind == "printer" else ["dim:  qualification job issued (simulated)", "ok:  ✔ evidence received: photos and dimension report", "ok:  ✔ independent validator: within tolerance"]
            lines = ["$ kleos qualify", *qualifier, f'ok:✔ Qualified for {cap}. It now shows up in Connections.']
        failed = step == "fixtures" and not dev["fixApplied"]
        def finish():
            dev["steps"][step] = "fail" if failed else "pass"
            if step == "conformance": dev["level"] = "Schema-verified"
            if step == "qualify": qualify(sim, dev, manifest, cap)
        sim.enqueue(f"developer.run:", [(220, lambda line=line: dev["terminal"].append(line)) for line in lines] + [(0, finish)])
    else:
        raise ValueError(f"Unsupported command: {kind}")


def qualify(sim, dev, manifest, cap):
    kind = dev["kind"]
    dev["level"] = "Qualified"
    if any(c["id"] == f"dev-{kind}" for c in sim.state["connectors"]): return
    connector = {"id": f"dev-{kind}", "name": manifest.get("display_name", "New connector"), "kind": kind, "author": "You", "category": "Fabrication", "integration": "MCP", "description": "Your newly qualified connector.", "capabilities": [cap], "level": "Qualified", "linked": True, "enabled": True, "mode": "demo", "health": "healthy", "lastCheck": "just now", "spendingScope": 80, "metrics": {"onTime": 100, "quality": 95, "quoteHrs": 1, "jobs": 1}}
    if kind == "tool": connector.update(category="Engineering tools", domain=(manifest.get("tool") if isinstance(manifest.get("tool"), dict) else {}).get("lane", "Mechanical"), spendingScope=0)
    if kind == "printer": connector.update(category="Printers", linked=False, enabled=False, health="unchecked", description="Your driver. Owners of these printers can now connect them.")
    sim.state["connectors"].append(connector)
    signal = next((d for d in sim.state["demand"] if d["capability"] == cap or kind == "printer" and d["kind"] == "printer"), None)
    if signal: signal["qualified"] += 1
