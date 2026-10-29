import asyncio
from copy import deepcopy
import json
import pytest
from pydantic import ValidationError
from app.models import CommandRequest
from app.simulation import DATA, Simulation


async def send(sim, command_type, **payload):
    command = CommandRequest(type=command_type, payload=payload)
    response = await sim.execute(command.type, command.payload)
    await sim.drain()
    return response["result"]


async def digital(sim, *, with_app=True):
    result = await send(sim, "project.create", brief="A desk air quality monitor with CO2")
    ident = result["projectId"]
    for question in DATA["questions"]:
        values = [question["options"][0]]
        if question["key"] == "features": values = ["CO2 level", "Alerts on your phone"] if with_app else ["Temperature and humidity"]
        await send(sim, "project.answer", projectId=ident, questionKey=question["key"], values=values)
    for kind in ["research", "confirm", "solveInterfaces", "generate", "verify"]:
        await send(sim, f"project.{kind}", projectId=ident)
    project = sim.project(ident)
    assert any(c["state"] == "FAIL" for c in project["checks"])
    with pytest.raises(ValueError, match="blocking"):
        await send(sim, "project.acceptRelease", projectId=ident)
    await send(sim, "project.repair", projectId=ident)
    await send(sim, "project.swapBattery", projectId=ident)
    assert next(a for a in project["artifacts"] if a["id"] == "elec.sch")["status"] == "ready"
    assert any(a["status"] == "stale" for a in project["artifacts"])
    await send(sim, "project.repair", projectId=ident)
    await send(sim, "project.acceptRelease", projectId=ident)
    assert project["stage"] == 6
    return ident


async def accept_task(sim, ident, *, portal=True):
    task = sim.task(ident)
    if portal: await send(sim, "task.usePortal", taskId=ident)
    bad = next(a for a in task["applicants"] if not a["meets"])
    with pytest.raises(ValueError, match="requirements"):
        await send(sim, "task.assign", taskId=ident, applicantId=bad["id"])
    worker = next(a for a in task["applicants"] if a["meets"])
    await send(sim, "task.assign", taskId=ident, applicantId=worker["id"])
    assert task["filesShared"]
    await send(sim, "task.submitEvidence", taskId=ident)
    if not all(v["ok"] for v in task["validation"]):
        with pytest.raises(ValueError, match="validation"):
            await send(sim, "task.accept", taskId=ident)
        await send(sim, "task.requestRevision", taskId=ident)
        await send(sim, "task.submitEvidence", taskId=ident)
    await send(sim, "task.accept", taskId=ident)


@pytest.mark.parametrize("home", [False, True])
def test_all_ten_stages_order_and_home_print(home):
    async def scenario():
        sim = Simulation(time_scale=0)
        try:
            if home:
                await send(sim, "connector.discover", connectorId="octoprint", host="octopi.local")
                await send(sim, "connector.calibrate", connectorId="octoprint")
                await send(sim, "connector.savePrinter", connectorId="octoprint", host="octopi.local", confirmEach=True)
            ident = await digital(sim, with_app=not home)
            project = sim.project(ident)
            assert ("App" in sim.snapshot()["state"]["projects"][0]["generationLanes"]) is not home
            await send(sim, "project.requestQuotes", projectId=ident)
            await send(sim, "project.placeOrders", projectId=ident)
            assert project["procurement"] == "done"
            assert project["mandate"]["reserved"] == 0
            assert project["mandate"]["spent"] == sum(o["amount"] for o in project["orders"])
            assert all(o["state"] == "RECEIVED" for o in project["orders"])
            assert len({o["idemKey"] for o in project["orders"]}) == len(project["orders"])
            if home:
                assert project["homePrint"]["state"] == "done"
                assert all(not o["item"].startswith("Shells") for o in project["orders"])
            with pytest.raises(ValueError): await send(sim, "project.placeOrders", projectId=ident)
            await send(sim, "project.createPhysicalTasks", projectId=ident)
            with pytest.raises(ValueError): await send(sim, "project.acceptPhysical", projectId=ident)
            for task in project["physicalTaskIds"]: await accept_task(sim, task)
            await send(sim, "project.acceptPhysical", projectId=ident)
            await send(sim, "project.prepareKit", projectId=ident)
            for task in project["kitTaskIds"]: await accept_task(sim, task)
            await send(sim, "project.acceptKit", projectId=ident)
            await send(sim, "project.ship", projectId=ident)
            with pytest.raises(ValueError): await send(sim, "project.activate", projectId=ident)
            for step in range(1, 7): await send(sim, "project.assemblyStep", projectId=ident, step=step)
            await send(sim, "project.activate", projectId=ident)
            assert all(project["done"])
            assert project["activation"] == "done"
            assert all(t["state"] == "PASS" for t in project["selfTest"])
        finally: await sim.close()
    asyncio.run(scenario())


def test_connector_link_reposts_waiting_tasks_and_seeded_inspection_works():
    async def scenario():
        sim = Simulation(time_scale=0)
        try:
            ident = await digital(sim)
            await send(sim, "project.requestQuotes", projectId=ident)
            await send(sim, "project.placeOrders", projectId=ident)
            await send(sim, "project.createPhysicalTasks", projectId=ident)
            task_ids = sim.project(ident)["physicalTaskIds"]
            assert all(sim.task(i)["state"] == "WAITING_FOR_CONNECTOR" for i in task_ids)
            await send(sim, "connector.authorize", connectorId="rentahuman", apiKey="demo-only")
            await send(sim, "connector.test", connectorId="rentahuman")
            connector = next(c for c in sim.state["connectors"] if c["id"] == "rentahuman")
            await send(sim, "connector.save", connectorId="rentahuman", capabilities=connector["capabilities"], scope=60)
            for task in task_ids: await accept_task(sim, task, portal=False)
            await accept_task(sim, "ht-seed3", portal=False)
            assert sim.task("ht-seed3")["state"] == "ACCEPTED"
            assert "demo-only" not in json.dumps(sim.snapshot())
        finally: await sim.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["service", "tool", "printer"])
def test_all_developer_qualification_paths(kind):
    async def scenario():
        sim = Simulation(time_scale=0)
        try:
            await send(sim, "developer.configure", kind=kind)
            with pytest.raises(ValueError, match="preceding"): await send(sim, "developer.run", step="qualify")
            await send(sim, "developer.run", step="init")
            await send(sim, "developer.run", step="fixtures")
            assert sim.state["dev"]["steps"]["fixtures"] == "fail"
            await send(sim, "developer.applyFix")
            for step in ["fixtures", "conformance", "submit", "qualify"]: await send(sim, "developer.run", step=step)
            assert sim.state["dev"]["level"] == "Qualified"
            assert any(c["id"] == f"dev-{kind}" for c in sim.state["connectors"])
        finally: await sim.close()
    asyncio.run(scenario())


def test_provider_wins_losses_and_evidence():
    async def scenario():
        sim = Simulation(time_scale=0)
        try:
            jobs = [j for j in sim.state["providerJobs"] if j["state"] == "new"]
            with pytest.raises(ValueError): await send(sim, "provider.quote", jobId=jobs[0]["id"], price=-2, lead=3)
            await send(sim, "provider.quote", jobId=jobs[0]["id"], price=24, lead=3)
            assert jobs[0]["state"] == "in_progress"
            await send(sim, "provider.uploadEvidence", jobId=jobs[0]["id"])
            assert jobs[0]["state"] == "done"
            await send(sim, "provider.quote", jobId=jobs[1]["id"], price=100, lead=3)
            assert jobs[1]["state"] == "lost"
            assert "Price" in jobs[1]["lossReason"]
        finally: await sim.close()
    asyncio.run(scenario())


def test_missing_provider_blocks_partial_orders():
    async def scenario():
        sim = Simulation(time_scale=0)
        try:
            ident = await digital(sim)
            await send(sim, "connector.disconnect", connectorId="circuitworks")
            await send(sim, "project.requestQuotes", projectId=ident)
            with pytest.raises(ValueError, match="required parts"): await send(sim, "project.placeOrders", projectId=ident)
            assert not sim.project(ident)["orders"]
        finally: await sim.close()
    asyncio.run(scenario())


def test_reset_and_shutdown_cancel_jobs_and_snapshots_are_detached():
    async def scenario():
        sim = Simulation()
        snapshot = sim.snapshot()
        snapshot["state"]["projects"].append({"fake": True})
        assert not sim.state["projects"]
        await sim.execute("connector.test", {"connectorId": "autolayout"})
        assert sim.jobs
        await sim.reset()
        revision = sim.revision
        await asyncio.sleep(0)
        await sim.drain()
        assert sim.revision == revision
        assert not sim.connection_runs
        await sim.execute("connector.test", {"connectorId": "autolayout"})
        await sim.close()
        assert not sim.jobs
        with pytest.raises(ValueError, match="disposed"): await sim.execute("agent.nextMessage", {})
    asyncio.run(scenario())


def test_command_payload_validation_rejects_wrong_and_extra_fields():
    for command in [
        {"type": "task.assign", "payload": {"taskId": "x", "applicantId": 42}},
        {"type": "project.create", "payload": {"brief": "x", "real": True}},
        {"type": "project.answer", "payload": {"projectId": "x", "values": []}},
        {"type": "unknown.command", "payload": {}},
        {"type": "connector.setEnabled", "payload": {"connectorId": "x", "enabled": "yes"}},
    ]:
        with pytest.raises(ValidationError): CommandRequest.model_validate(command)
