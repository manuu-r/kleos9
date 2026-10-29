"""Simulated human marketplace work and evidence validation."""
from copy import deepcopy
import random
from .simulation import DATA, log, note, require, uid

NAMES = ["Asha P.", "Marco D.", "Lin W.", "Sam O.", "Priya N.", "Jonas K.", "Fatima R."]


def applicants(capability):
    requirements = DATA["specs"][capability]["requirements"]
    return [{"id": uid("ap"), "name": name, "rating": round(random.uniform(4.4, 5), 1), "jobs": random.randint(5, 94), "skills": requirements[:2] if i != 2 else requirements[1:2], "meets": i != 2, **({"missing": requirements[0]} if i == 2 and requirements else {})} for i, name in enumerate(random.sample(NAMES, 4))]


def create_task(sim, project, capability, trigger, gate_check_id=None):
    spec = DATA["specs"][capability]
    task = {"id": uid("ht"), "projectId": project["id"], "projectName": project["name"], "capability": capability, "title": spec["title"], "publicDescription": spec["publicDescription"], "trigger": trigger, "platform": None, "state": "DRAFT", "fee": spec["fee"], "deadline": "in 3 days", "requirements": deepcopy(spec["requirements"]), "evidenceSchema": deepcopy(spec["evidenceSchema"]), "applicants": [], "filesShared": False, "revisionCount": 0, "history": []}
    if gate_check_id: task["gateCheckId"] = gate_check_id
    note(task, f"Drafted: {trigger}")
    sim.state["humanTasks"].insert(0, task)
    log(project, f'Human task drafted: {task["title"]}', "human")
    return task["id"]


def platform_for(sim, capability):
    rah = next((c for c in sim.state["connectors"] if c["id"] == "rentahuman"), None)
    if rah and rah["linked"] and rah["enabled"] and any(capability.startswith(c.rstrip("*")) for c in rah["capabilities"]):
        return "RentAHuman"
    return None


def post_task(sim, ident):
    task = sim.task(ident)
    def scope():
        task["state"] = "SCOPED"
        note(task, "Work package scoped: deliverables, evidence schema, deadline")
    def authorize():
        task["state"] = "AUTHORIZED"
        note(task, f'Mandate check passed: ${task["fee"]} within human-task scope (simulated)')
    def post():
        platform = platform_for(sim, task["capability"])
        if not platform:
            task["state"] = "WAITING_FOR_CONNECTOR"
            note(task, "No human-task platform linked for this project. Waiting.")
            return
        task.update(platform=platform, externalRef=f'RAH-B{random.randint(10000, 99999)}', state="POSTED")
        note(task, f'Posted to {platform} as bounty {task["externalRef"]} (public description only, no design files)')
        project = next((p for p in sim.state["projects"] if p["id"] == task["projectId"]), None)
        if project: log(project, f'Job posted to {platform}: {task["title"]}', "human")
    def applied():
        if task["state"] != "POSTED": return
        task.update(applicants=applicants(task["capability"]), state="APPLICATIONS")
        note(task, f'{len(task["applicants"])} applicants; filtered by code against stated requirements')
    sim.enqueue(f'post-task:{ident}', [(500, scope), (600, authorize), (700, post), (1400, applied)])


def repost_waiting(sim):
    for task in list(sim.state["humanTasks"]):
        if task["state"] == "WAITING_FOR_CONNECTOR":
            task["state"] = "AUTHORIZED"
            note(task, "Platform linked; re-posting")
            post_task(sim, task["id"])


def task_command(sim, kind, payload):
    task = sim.task(payload["taskId"])
    key = f'{kind}:{task["id"]}'
    action = kind.split(".")[1]
    if action == "usePortal":
        require(task["state"] == "WAITING_FOR_CONNECTOR", "Task is not waiting for a connector")
        task.update(platform="Hosted portal", state="POSTED")
        note(task, "Posted to Kleos9 hosted portal")
        def apply():
            task.update(applicants=applicants(task["capability"]), state="APPLICATIONS")
            note(task, f'{len(task["applicants"])} applicants; filtered by code against stated requirements')
        sim.enqueue(key, [(1200, apply)])
    elif action == "assign":
        applicant = next((a for a in task["applicants"] if a["id"] == payload["applicantId"] and a["meets"]), None)
        require(task["state"] == "APPLICATIONS" and applicant, "Applicant does not meet task requirements")
        task.update(assigneeId=applicant["id"], state="ASSIGNED")
        note(task, f'Assigned to {applicant["name"]}')
        def share():
            task["filesShared"] = True
            note(task, "Confidential files shared via scoped, expiring link (after assignment)")
        def start():
            task["state"] = "IN_PROGRESS"
            note(task, "Worker started")
        sim.enqueue(key, [(700, share), (700, start)])
    elif action == "submitEvidence":
        require(task["state"] == "IN_PROGRESS", "Task is not in progress")
        spec = DATA["specs"][task["capability"]]
        def submit():
            task["evidence"] = deepcopy(spec.get("firstEvidence", spec["evidence"]) if task["revisionCount"] == 0 else spec["evidence"])
            task["state"] = "EVIDENCE_SUBMITTED"
            note(task, "Evidence received via signed webhook; event stored before processing")
        def validate():
            evidence = task["evidence"]
            provided = len(evidence["photos"]) + len(evidence["readings"])
            required = len(spec["evidence"]["photos"]) + len(spec["evidence"]["readings"])
            task["validation"] = [{"label": "Signature and timestamp verified", "ok": True}, {"label": "Revision and specimen ID match", "ok": True}, {"label": "All required evidence fields present", "ok": provided >= required}, {"label": "Readings have units and are within limits", "ok": all(r["ok"] for r in evidence["readings"])}]
            task["state"] = "REVIEW"
            note(task, "Independent validator: evidence complete" if all(v["ok"] for v in task["validation"]) else "Independent validator: evidence incomplete")
        sim.enqueue(key, [(1200, submit), (900, validate)])
    elif action == "accept":
        require(task["state"] == "REVIEW" and task.get("validation") and all(v["ok"] for v in task["validation"]), "Evidence has not passed validation")
        task["state"] = "ACCEPTED"
        note(task, f'Accepted. Payment of ${task["fee"]} released (simulated)')
        project = next((p for p in sim.state["projects"] if p["id"] == task["projectId"]), None)
        if project:
            log(project, f'Human task accepted: {task["title"]}', "human")
            check = next((c for c in project["checks"] if c["id"] == task.get("gateCheckId")), None)
            if check: check.update(state="PASS", detail=f'Resolved by human evidence ({task["platform"]})')
    elif action == "requestRevision":
        require(task["state"] == "REVIEW", "Task is not ready for review")
        task.update(state="REVISION_REQUESTED", revisionCount=task["revisionCount"] + 1)
        task.pop("validation", None)
        note(task, "Revision requested: missing required evidence")
        def resume():
            task["state"] = "IN_PROGRESS"
            note(task, "Worker resumed")
        sim.enqueue(key, [(900, resume)])
    else:
        raise ValueError(f"Unsupported command: {kind}")
