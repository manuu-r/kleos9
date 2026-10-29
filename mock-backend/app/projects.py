"""Ten-stage build workflow using deterministic simulated results."""
from copy import deepcopy
import math
import random
from .simulation import DATA, complete, find, has_feature, interfaces_for, invalidate, log, new_hash, outcome, require, uid

TRACKS = ["Parts & availability", "Materials & printing", "Electronics & power budget", "Firmware & app support"]
LANE_LOG = {
    "Mechanical": ["Reading interface spec v1", "Building case.py with build123d", "Pocket for battery pack + core module", "Snap-fit lip and button windows", "Exported STEP + print profile (PETG, 0.2 mm)"],
    "Electronics": ["Writing monitor.ato from parts + pin map", "Compiling to KiCad schematic", "Requesting layout from AutoLayout PCB AI (candidate)", "Received 3 layout candidates, picked lowest via count", "Exported gerbers + BOM"],
    "Firmware": ["Generating Zephyr devicetree from pin map", "Display, buttons, sensors drivers", "Battery config + sleep modes", "Building monitor_fw.hex"],
    "App": ["Generating GATT client from protocol v1", "Wi-Fi setup, live readings, alert settings", "Built Android + iOS packages"],
}
STAGES = {"answer": 0, "research": 1, "toggleFeature": 2, "requestReview": 2, "confirm": 2, "solveInterfaces": 3, "selectTool": 4, "generate": 4, "verify": 5, "repair": 5, "swapBattery": 5, "acceptRelease": 5, "requestQuotes": 6, "selectPrint": 6, "placeOrders": 6, "createPhysicalTasks": 7, "acceptPhysical": 7, "prepareKit": 8, "acceptKit": 8, "ship": 9, "assemblyStep": 9, "activate": 9}


def project_command(sim, kind, payload):
    from .tasks import create_task, post_task
    p = sim.project(payload["projectId"])
    action = kind.split(".")[1]
    require(action in STAGES, "Unknown project command")
    stage = STAGES[action]
    require(p["stage"] == stage and not p["done"][stage], "This stage is not available for changes")
    key = f'{kind}:{p["id"]}'
    busy = p["verifyRun"] == "running" or any(c["state"] == "RUNNING" for c in p["checks"]) or any(a["status"] == "generating" for a in p["artifacts"]) or f'project.repair:{p["id"]}' in sim.active

    if action == "answer":
        question = next((q for q in DATA["questions"] if q["key"] not in p["answers"]), None)
        values = payload["values"]
        require(question and question["key"] == payload["questionKey"] and values and (question.get("multi") or len(values) == 1) and all(v in question["options"] for v in values), "Choose a valid answer")
        p["answers"][question["key"]] = list(dict.fromkeys(values))
        log(p, f'Answered: {", ".join(values)}', "user")
        if question["key"] == DATA["questions"][-1]["key"]:
            def summarize():
                a = p["answers"]
                p["batteryMah"] = 3000 if a["battery"][0].startswith("2 weeks") else 1200 if a["battery"][0].startswith("USB") else 2000
                p["requirements"] = [{"label": label, "value": ", ".join(a[k])} for label, k in [("Features", "features"), ("Display", "display"), ("Case size", "size"), ("Battery target", "battery"), ("Finish", "finish"), ("Assembly", "assembly"), ("Budget", "budget")]]
                p["assumptions"] = ["Indoor use on a desk or shelf", "Joins your home Wi-Fi for alerts", "USB-C charging and data", "Printed shells preferred (print-first policy)"]
                p["unknowns"] = ["CO2 accuracy before calibration", "Button feel through a printed deck"]
                log(p, "Requirements v1 recorded with assumptions and unknowns")
                complete(p, 0)
            sim.enqueue(key, [(900, summarize)])
    elif action == "research":
        require(p["feasibility"] == "idle", "Research already started")
        p["feasibility"] = "running"
        p["research"] = dict.fromkeys(TRACKS, 0)
        log(p, "Feasibility research started across 4 tracks")
        def tick(index):
            p["research"] = {track: min(100, index * (11 + k * 2)) for k, track in enumerate(TRACKS)}
            if index == 3: log(p, "Checked live stock for 7 candidate parts via PartsDirect (demo)")
            if index == 5: log(p, "Power budget: 11 days estimated on 2000 mAh with Wi-Fi alerts")
        def finish():
            p["research"] = dict.fromkeys(TRACKS, 100)
            notes = {"Fine dust (PM2.5)": "Needs a small fan and an airflow channel. Adds noise and power draw.", "CO2 level": "Sensor card reads about ±40 ppm once calibrated.", "Alerts on your phone": "Needs a small companion app over Wi-Fi."}
            p["features"] = [{"name": feature, "status": "limited" if feature == "Fine dust (PM2.5)" else "supported", "note": notes.get(feature, "Standard parts and firmware libraries.")} for feature in p["answers"].get("features", [])]
            p["feasibility"] = "done"
            log(p, "Proposal ready: concept, features, parts, estimates and limitations")
            complete(p, 1)
        sim.enqueue(key, [(450, lambda i=i: tick(i)) for i in range(1, 9)] + [(500, finish)])
    elif action == "toggleFeature":
        feature = next((f for f in p["features"] if f["name"] == payload["name"]), None)
        require(feature, "Feature missing")
        feature["status"] = ("limited" if feature["name"] == "Fine dust (PM2.5)" else "supported") if feature["status"] == "dropped" else "dropped"
        log(p, f'{"Removed" if feature["status"] == "dropped" else "Restored"} feature: {feature["name"]}', "user")
    elif action == "requestReview":
        require(not p.get("reviewTaskId"), "Review already requested")
        ident = create_task(sim, p, "human.engineering_review", "Customer asked for a human engineering review of the concept")
        p["reviewTaskId"] = ident
        post_task(sim, ident)
    elif action == "confirm":
        p["confirmed"] = True
        log(p, "Customer confirmed concept. Generation budget unlocked.", "user")
        complete(p, 2)
    elif action == "solveInterfaces":
        p["interfaces"] = interfaces_for(p)
        log(p, "Interface specification drafted from requirements and parts")
        steps = []
        for row in p["interfaces"]:
            steps.extend([(250, lambda r=row: r.update(state="RUNNING")), (350, lambda r=row: r.update(state="PASS"))])
        def finish_interfaces():
            p["interfacesAgreed"] = True
            log(p, "Solver: all interface constraints satisfiable. Interface spec v1 agreed.", "check")
            complete(p, 3)
        sim.enqueue(key, steps + [(300, finish_interfaces)])
    elif action == "selectTool":
        require(p["generation"] == "idle", "Generation already started")
        tool = next((c for c in sim.state["connectors"] if c["id"] == payload["toolId"] and c["kind"] == "tool" and c.get("domain") == payload["lane"] and c["linked"] and c["enabled"]), None)
        require(tool, "Tool unavailable")
        p["tools"][payload["lane"]] = tool["id"]
        log(p, f'{payload["lane"]} tool set to {tool["name"]}', "user")
    elif action == "generate":
        require(p["generation"] == "idle", "Generation already started")
        lanes = ["Mechanical", "Electronics", "Firmware"] + (["App"] if has_feature(p, "Alerts on your phone") else [])
        p["generation"] = "running"
        p["artifacts"] = [deepcopy(a) for a in DATA["artifacts"] if a["domain"] != "App" or "App" in lanes]
        for a in p["artifacts"]: a["status"] = "generating"
        p["genProgress"] = dict.fromkeys(lanes, 0)
        p["genLog"] = [f'[{lane}] Using {next((c["name"] for c in sim.state["connectors"] if c["id"] == p["tools"].get(lane)), "Kleos9 App")}' for lane in lanes]
        log(p, f'Parallel generation started: {", ".join(lanes)}')
        def gen_tick(index):
            for lane in lanes:
                lines = list(LANE_LOG[lane])
                if lane == "Electronics" and p["tools"].get(lane) != "autolayout": lines[2:4] = ["Basic placement and routing", "Routed 2-layer board, 0 unrouted nets"]
                if lane == "Mechanical" and p["tools"].get(lane) == "textcad": lines[1] = "Facet Text-to-CAD generating case shell from interface spec"
                if index < len(lines): p["genLog"].append(f'[{lane}] {lines[index]}')
                p["genProgress"][lane] = min(100, round((index + 1) / len(lines) * 100))
        def generated():
            for artifact in p["artifacts"]: artifact.update(status="ready", hash=new_hash())
            p["checks"] = [deepcopy(c) for c in DATA["checks"] if c["id"] != "c.fwhr" or any(f["name"] == "CO2 level" and f["status"] != "dropped" for f in p["features"])]
            p["generation"] = "done"
            log(p, f'{len(p["artifacts"])} candidate artifacts persisted with input dependencies')
            complete(p, 4)
        sim.enqueue(key, [(700, lambda i=i: gen_tick(i)) for i in range(5)] + [(500, generated)])
    elif action == "verify":
        require(p["verifyRun"] == "idle", "Verification already started")
        p["verifyRun"] = "running"
        log(p, "Independent verification started on persisted files", "check")
        steps = []
        for check in p["checks"]:
            def settle(c=check):
                c.update(outcome(c, p, False))
                if c["state"] == "FAIL": log(p, f'FAIL: {c["name"]}: {c["detail"]}', "check")
            steps.extend([(180, lambda c=check: c.update(state="RUNNING")), (320, settle)])
        def verified():
            p["verifyRun"] = "done"
            if any(c["state"] == "FAIL" for c in p["checks"]): log(p, "Repair task created for Mechanical: widen the USB-C notch by 0.2 mm")
        sim.enqueue(key, steps + [(500, verified)])
    elif action == "repair":
        require(not busy and p["verifyRun"] == "done", "Wait for verification to finish")
        if any(c["state"] == "FAIL" for c in p["checks"]): invalidate(p, "mech.case")
        artifacts = [a for a in p["artifacts"] if a["status"] == "stale"]
        checks = [c for c in p["checks"] if c["state"] in ("STALE", "FAIL")]
        steps = []
        if artifacts:
            for a in artifacts: a["status"] = "generating"
            log(p, f'Regenerating only affected artifacts: {len(artifacts)} of {len(p["artifacts"])}')
            def regenerate():
                for a in artifacts: a.update(status="ready", version=a["version"] + 1, hash=new_hash())
            steps.append((1200, regenerate))
        for check in checks:
            steps.extend([(200, lambda c=check: c.update(state="RUNNING")), (300, lambda c=check: c.update(outcome(c, p, True)))])
        sim.enqueue(key, steps)
    elif action == "swapBattery":
        require(not busy and p["verifyRun"] == "done", "Wait for verification to finish")
        before = p["batteryMah"]
        p["batteryMah"] += 500
        names = invalidate(p, "battery")
        log(p, f'Change request: battery {before} → {p["batteryMah"]} mAh. Stale: {", ".join(names)}', "user")
    elif action == "acceptRelease":
        require(not busy and p["verifyRun"] == "done" and p["checks"] and all(c["state"] in ("PASS", "UNKNOWN") for c in p["checks"]), "Resolve blocking checks first")
        p["manifestHash"] = new_hash() + new_hash()
        for a in p["artifacts"]: a["status"] = "accepted"
        unknowns = sum(c["state"] == "UNKNOWN" for c in p["checks"])
        log(p, f'Digital release accepted. Manifest {p["manifestHash"][:12]} published. {unknowns} UNKNOWN carried to physical tests.', "check")
        complete(p, 5)
    elif action == "requestQuotes":
        require(p["procurement"] == "idle", "Quotes already requested")
        p["procurement"] = "quoting"
        log(p, "Quote requests sent to linked, qualified connectors (demo)", "order")
        def quoted():
            p["quotes"] = build_quotes(sim.state["connectors"])
            p["procurement"] = "quoted"
            p["casePrint"] = "home" if home_printer(sim) else "order"
            log(p, f'{len(p["quotes"])} quotes received; routing selected one per item', "order")
        sim.enqueue(key, [(1600, quoted)])
    elif action == "selectPrint":
        require(p["procurement"] == "quoted", "Quotes are not ready")
        require(payload["mode"] == "order" or home_printer(sim), "Connect a printer first")
        p["casePrint"] = payload["mode"]
        log(p, "Case will be printed at home" if payload["mode"] == "home" else "Case will be ordered", "user")
    elif action == "placeOrders":
        place_orders(sim, p, key)
    elif action == "createPhysicalTasks":
        require(not p["physicalTaskIds"], "Physical tasks already exist")
        capabilities = {"c.fwhr": "human.functional_test", "c.buttons": "human.fit_check"}
        for check in p["checks"]:
            if check.get("resolveBy") == "physical":
                ident = create_task(sim, p, capabilities[check["id"]], f'{check["name"]} was UNKNOWN: {check["detail"]}', check["id"])
                p["physicalTaskIds"].append(ident)
                post_task(sim, ident)
        log(p, f'{len(p["physicalTaskIds"])} human tasks created from UNKNOWN results', "human")
    elif action == "acceptPhysical":
        require(all(sim.task(ident)["state"] == "ACCEPTED" for ident in p["physicalTaskIds"]), "Accept the physical evidence first")
        require(not any(c.get("resolveBy") == "physical" and c["state"] != "PASS" for c in p["checks"]), "Accept the physical evidence first")
        log(p, "Physical evidence accepted for prototype specimen #1", "check")
        complete(p, 7)
    elif action == "prepareKit":
        require(not p["kitTaskIds"], "Kit tasks already exist")
        for capability, trigger in [("human.kit_pack", "Workflow requires a packing check against the manifest"), ("human.assembly_check", "Workflow requires a first-time builder to complete an assembly trial")]:
            ident = create_task(sim, p, capability, trigger)
            p["kitTaskIds"].append(ident)
            post_task(sim, ident)
        log(p, "Kit preparation started at KitPack Studio (demo); guide generated from accepted CAD")
    elif action == "acceptKit":
        require(len(p["kitTaskIds"]) == 2 and all(sim.task(ident)["state"] == "ACCEPTED" for ident in p["kitTaskIds"]), "Accept both kit tasks first")
        p["kitAccepted"] = True
        log(p, "Final kit, guide and packing accepted", "check")
        complete(p, 8)
    elif action == "ship":
        require(p["kitAccepted"] and not p["shipment"], "Kit unavailable or already shipped")
        p["shipment"] = {"step": 0, "tracking": f'PL{random.randint(100000000, 999999999)}'}
        log(p, "Shipment label created via ParcelLink (demo)", "order")
        def shipping(index):
            p["shipment"]["step"] = index
            if index == len(DATA["shipSteps"]) - 1: log(p, "Kit delivered", "order")
        sim.enqueue(key, [(900, lambda i=i: shipping(i)) for i in range(1, len(DATA["shipSteps"]))])
    elif action == "assemblyStep":
        step = payload["step"]
        require(p["shipment"] and p["shipment"]["step"] == 4 and not p["assemblyDone"] and 0 <= step <= len(DATA["guide"]) and abs(step - p["guideStep"]) == 1, "Invalid assembly step")
        p["guideStep"] = step
        if step == len(DATA["guide"]):
            p["assemblyDone"] = True
            log(p, "Customer completed assembly (all 6 steps)", "user")
    elif action == "activate":
        require(p["assemblyDone"] and p["activation"] == "idle", "Finish assembly before activation")
        p["activation"] = "running"
        p["selfTest"] = [{"name": name, "state": "PENDING"} for name in DATA["selfTests"]]
        steps = []
        for row in p["selfTest"]: steps.extend([(300, lambda r=row: r.update(state="RUNNING")), (450, lambda r=row: r.update(state="PASS"))])
        def activated():
            p["activation"] = "done"
            log(p, "Device activated. Self-test passed and recorded in device history.", "check")
            complete(p, 9)
        sim.enqueue(key, steps + [(300, activated)])


def home_printer(sim):
    return next((c for c in sim.state["connectors"] if c["kind"] == "printer" and c["linked"] and c["enabled"]), None)


def build_quotes(connectors):
    quotes = []
    for item in DATA["items"]:
        candidates = [c for c in connectors if c["linked"] and c["enabled"] and c["integration"] != "Built-in" and item["capability"] in c["capabilities"]]
        rows = [{"id": uid("q"), "item": item["item"], "connectorId": c["id"], "price": math.floor(item["base"] * (1 + i * .17 - .08) + .5), "leadDays": 2 + (i * 2 + item["base"]) % 4, "quality": c["metrics"]["quality"], "onTime": c["metrics"]["onTime"], "fit": "full", "selected": False} for i, c in enumerate(candidates)]
        def score(row):
            connector = find(connectors, row["connectorId"])
            return row["quality"] * .4 + row["onTime"] * .3 - row["price"] * .3 - row["leadDays"] * 2 if connector["level"] in ("Qualified", "Preferred") else -1
        best = max(rows, key=score) if rows else None
        if best and score(best) < 0: best = None
        for row in rows:
            connector = find(connectors, row["connectorId"])
            if row is best:
                row["selected"] = True
                row["reason"] = f'Best combined score: quality {row["quality"]}, on-time {row["onTime"]}%, ${row["price"]}, {row["leadDays"]} days'
            elif connector["level"] not in ("Qualified", "Preferred"): row["reason"] = f'{connector["level"]}: quote-only until qualified'
            else: row["reason"] = "Cheaper, but lower quality / on-time history" if best and row["price"] < best["price"] else "Higher total cost or longer lead time"
        quotes.extend(rows)
    return quotes


def finish_if_ready(p):
    if p["orders"] and all(o["state"] == "RECEIVED" for o in p["orders"]) and (p["casePrint"] != "home" or (p["homePrint"] and p["homePrint"]["state"] == "done")) and p["procurement"] != "done":
        p["procurement"] = "done"
        log(p, "Prototype parts complete", "order")
        complete(p, 6)


def place_orders(sim, p, key):
    require(p["procurement"] == "quoted" and not p["orders"], "Orders already placed or quotes unavailable")
    selected = [q for q in p["quotes"] if q["selected"] and not (p["casePrint"] == "home" and q["item"].startswith("Shells print"))]
    required = [item for item in DATA["items"] if not (p["casePrint"] == "home" and item["item"].startswith("Shells print"))]
    require(all(any(q["item"] == item["item"] for q in selected) for item in required), "Some required parts have no eligible quote; connect a qualified provider first")
    total = sum(q["price"] for q in selected)
    require(selected and total <= p["mandate"]["limit"], "Order exceeds mandate or has no eligible quotes")
    require(all(any(c["id"] == q["connectorId"] and c["linked"] and c["enabled"] and c["level"] in ("Qualified", "Preferred") for c in sim.state["connectors"]) for q in selected), "An order provider is unavailable")
    printer = home_printer(sim)
    require(p["casePrint"] != "home" or printer, "Home printer unavailable")
    orders = [{"id": uid("oi"), "item": q["item"], "connectorId": q["connectorId"], "amount": q["price"], "idemKey": uid("idem"), "state": "INTENT"} for q in selected]
    p["orders"] = orders
    p["procurement"] = "ordering"
    p["mandate"]["reserved"] = total
    log(p, f'Order intents persisted and ${total} reserved under mandate (simulated)', "order")
    pcb = next((o for o in orders if o["connectorId"] == "circuitworks"), orders[min(1, len(orders) - 1)])
    steps = [(350, lambda o=o: o.update(state="DISPATCHED")) for o in orders]
    for order in orders:
        if order is pcb:
            def uncertain(o=order):
                o["state"] = "UNCERTAIN"
                log(p, f'{find(sim.state["connectors"], o["connectorId"])["name"]}: acknowledgement lost (timeout). Not retrying blindly.', "order")
            steps.append((500, uncertain))
        else: steps.append((300, lambda o=order: o.update(state="CONFIRMED", externalRef=f'EXT-{random.randint(10000, 99999)}')))
    def reconcile():
        pcb["state"] = "RECONCILING"
        log(p, f'Reconciling via status lookup with {pcb["idemKey"]}', "order")
    def reconciled():
        pcb.update(state="CONFIRMED", externalRef=f'EXT-{random.randint(10000, 99999)}')
        log(p, "Order found at provider. Confirmed without a duplicate purchase.", "order")
    steps.extend([(900, reconcile), (1300, reconciled)])
    for state in ("SHIPPED", "RECEIVED"):
        steps.extend((300, lambda o=o, state=state: o.update(state=state)) for o in orders)
    def received():
        p["mandate"]["spent"] += p["mandate"]["reserved"]
        p["mandate"]["reserved"] = 0
        log(p, "Ordered parts received at KitPack Studio. Receiving records match expected list.", "order")
        finish_if_ready(p)
    sim.enqueue(key, steps + [(300, received)])
    if p["casePrint"] == "home":
        p["homePrint"] = {"state": "slicing", "progress": 0, "layer": 0, "layers": 142, "nozzle": 24}
        hp = p["homePrint"]
        log(p, f'Slicing the shells for {printer["printer"]["model"]} with its tolerance profile', "order")
        print_steps = [(800, lambda: hp.update(state="sending", nozzle=160)), (700, lambda: hp.update(state="printing", nozzle=245))]
        print_steps.extend((450, lambda i=i: hp.update(progress=round(i / 12 * 100), layer=round(i / 12 * 142))) for i in range(1, 13))
        def printed():
            hp.update(state="done", nozzle=40)
            log(p, "Home print finished: both shells. Recorded as specimen #1.", "order")
            finish_if_ready(p)
        sim.enqueue(f'home-print:{p["id"]}', print_steps + [(400, printed)])
