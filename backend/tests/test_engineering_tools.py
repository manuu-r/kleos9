import base64
import json
from copy import deepcopy

import pytest
from pydantic import ValidationError
from test_control_plane import command, create, engine
from test_execution import execution  # noqa: F401

from kleos.auth import Principal
from kleos.config import Settings
from kleos.contracts import DomainError, EngineeringToolDefinition
from kleos.db import Attempt, NativeJob, Project, Task
from kleos.engineering_tools import DEFAULT_TOOL_POLICY, EngineeringToolRegistry
from kleos.native_runner import run_job
from kleos.storage import digest


def cad_tool(**changes):
    return EngineeringToolDefinition.model_validate({
        "id": "example-cad", "version": "1", "name": "Example CAD adapter", "lane": "mechanics",
        "runner": "plugin", "sourceContract": "Produce design.json for the installed CAD adapter; dimensions are in mm.",
        "outputFiles": ["outputs/model.step", "outputs/model.stl"], "qualified": True,
        "qualificationReference": "fixture:independent-contract-report", **changes})


def enable(platform, **changes):
    platform.settings.engineering_tools = [cad_tool(**changes)]
    platform.settings.execution_backend = "ecs"
    platform.settings.ecs_task_definitions = {"example-cad@1": "tool:example-cad:4"}
    platform.control.tool_registry = EngineeringToolRegistry(platform.settings)


def custom_policy():
    return {**deepcopy(DEFAULT_TOOL_POLICY), "mechanics": {"id": "example-cad", "version": "1"}}


def test_catalog_keeps_current_defaults_and_hides_other_tenants():
    settings = Settings(_env_file=None, engineering_tools=[cad_tool(tenantIds=["private-org"])])
    registry = EngineeringToolRegistry(settings)
    catalog = registry.catalog("other-org")
    assert catalog["defaultPolicy"] == DEFAULT_TOOL_POLICY
    assert {tool["id"] for tool in catalog["tools"]} == {"build123d", "atopile-kicad"}
    assert all("tenantIds" not in tool for tool in registry.catalog("private-org")["tools"])
    with pytest.raises(DomainError) as exc:
        registry.validate_policy(custom_policy(), "other-org")
    assert exc.value.status == 404


def test_catalog_is_exposed_through_authenticated_typed_api(platform):
    response = platform.client.get("/api/v1/engineering-tools")
    assert response.status_code == 200
    assert response.json()["defaultPolicy"] == DEFAULT_TOOL_POLICY
    assert response.json()["tools"][0]["sourceContract"]


@pytest.mark.parametrize("change", [
    {"qualified": True, "qualificationReference": None},
    {"outputFiles": ["../leak.step"]},
    {"outputFiles": ["outputs/*"]},
    {"runner": "kicad"},
])
def test_plugin_manifest_rejects_unsafe_or_incompatible_contracts(change):
    with pytest.raises(ValidationError):
        cad_tool(**change)


def test_server_profiles_cannot_replace_builtins_or_duplicate_versions():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, engineering_tools=[cad_tool(id="build123d")])
    with pytest.raises(ValidationError):
        Settings(_env_file=None, engineering_tools=[cad_tool(), cad_tool()])


@pytest.mark.parametrize("change,code", [
    ({"qualified": False}, "tool_unqualified"),
    ({"tenantIds": ["another-org"]}, "tool_unavailable"),
])
def test_unavailable_plugins_cannot_be_selected(platform, change, code):
    enable(platform, **change)
    ident = create(platform)["projectId"]
    with pytest.raises(DomainError) as exc:
        command(platform, ident, "project.setToolPolicy", {"toolPolicy": custom_policy()})
    assert exc.value.code == code


def test_policy_selection_checks_installation_lane_and_authority(platform):
    enable(platform)
    ident = create(platform)["projectId"]
    platform.settings.ecs_task_definitions = {}
    with pytest.raises(DomainError) as exc:
        command(platform, ident, "project.setToolPolicy", {"toolPolicy": custom_policy()})
    assert exc.value.code == "tool_unconfigured"
    platform.settings.ecs_task_definitions = {"example-cad@1": "tool:example-cad:4"}
    wrong = {**custom_policy(), "electronics": {"id": "example-cad", "version": "1"}}
    with pytest.raises(DomainError) as exc:
        command(platform, ident, "project.setToolPolicy", {"toolPolicy": wrong})
    assert exc.value.code == "invalid_tool_lane"
    principal = platform.principal
    platform.principal = Principal(principal.subject, principal.tenant_id, "viewer")
    with pytest.raises(DomainError) as exc:
        command(platform, ident, "project.setToolPolicy", {"toolPolicy": custom_policy()})
    assert exc.value.status == 403


def test_running_attempt_is_pinned_and_next_attempt_gets_new_tool(platform):
    enable(platform)
    ident = create(platform)["projectId"]
    with platform.db.transaction() as session:
        project = session.get(Project, ident)
        project.state = {**project.state, "confirmed": True}
        project.interfaces = {"mechanical": {"units": "mm"}}
    receipt = command(platform, ident, "project.generate", {"lane": "mechanics"})
    worker = engine(platform)
    task_id = worker.admit_run(receipt["runId"])[0]
    first = worker.begin_attempt(task_id)
    before = worker.attempt_input(first)[0]["engineeringTool"]
    command(platform, ident, "project.setToolPolicy", {"toolPolicy": custom_policy()})
    assert worker.attempt_input(first)[0]["engineeringTool"] == before
    with platform.db.transaction() as session:
        session.get(Attempt, first).status = "failed"
        session.get(Task, task_id).status = "queued"
    second = worker.begin_attempt(task_id)
    context = worker.attempt_input(second)[0]
    assert context["engineeringTool"]["id"] == "example-cad"
    assert context["engineeringTool"]["policyVersion"] == 2
    assert context["manifest"]["engineeringTool"]["definitionHash"]
    command(platform, ident, "project.swapBattery", {"capacityMah": 150,
        "dimensionsMm": {"x": 20, "y": 20, "z": 5}})
    project = platform.control.workspace(platform.principal, ident)["project"]
    assert project["state"]["toolPolicy"] == custom_policy()


def pin_custom(executor):
    executor.settings.engineering_tools = [cad_tool()]
    executor.settings.ecs_task_definitions["example-cad@1"] = "tool:example-cad:4"
    registry = EngineeringToolRegistry(executor.settings)
    with executor.db.transaction() as session:
        attempt = session.get(Attempt, "attempt")
        attempt.context = {"engineeringTool": registry.describe(cad_tool(), 4)}


def test_native_job_uses_pinned_adapter_and_rejects_mutated_definition(execution):  # noqa: F811
    pin_custom(execution)
    ident = execution.submit("attempt", "build")
    with execution.db.transaction() as session:
        request = session.get(NativeJob, ident).request
        assert request["toolVersion"] == "tool:example-cad:4"
        assert request["engineeringTool"]["id"] == "example-cad"
    # Revoking/changing the adapter before dispatch cannot silently run a different contract.
    execution.settings.engineering_tools = [cad_tool(sourceContract="A changed contract requiring another source format.")]
    execution.dispatch_pending()
    assert not execution.ecs.calls
    with execution.db.transaction() as session:
        job = session.get(NativeJob, ident)
        assert job.status == "cancelled" and job.request["reservation"]["settled"]


def test_plugin_selection_never_replaces_independent_validator(execution):  # noqa: F811
    pin_custom(execution)
    execution.settings.ecs_task_definitions["validate"] = "validator:independent:3"
    ident = execution.submit("attempt", "validate")
    with execution.db.transaction() as session:
        request = session.get(NativeJob, ident).request
    assert request["toolVersion"] == "validator:independent:3"
    assert "engineeringTool" not in request


def test_ecs_tools_require_explicit_revision(execution):  # noqa: F811
    execution.settings.ecs_task_definitions["mechanics"] = "mutable-family"
    with pytest.raises(DomainError, match="revision"):
        execution.submit("attempt", "build")


def test_default_attempt_pins_worker_before_generation(execution):  # noqa: F811
    registry = EngineeringToolRegistry(execution.settings)
    profile = registry.get(DEFAULT_TOOL_POLICY["mechanics"], "org")
    pinned = execution.pin_engineering_tool(registry.describe(profile), "org", "mechanics")
    with execution.db.transaction() as session:
        session.get(Attempt, "attempt").context = {"engineeringTool": pinned}
    execution.settings.ecs_task_definitions["mechanics"] = "task:mechanics:18"
    ident = execution.submit("attempt", "build")
    with execution.db.transaction() as session:
        assert session.get(NativeJob, ident).request["toolVersion"] == "task:mechanics:17"


def test_custom_installation_cannot_drift_without_new_qualified_version(execution):  # noqa: F811
    pin_custom(execution)
    execution.settings.ecs_task_definitions["example-cad@1"] = "tool:example-cad:5"
    with pytest.raises(DomainError) as exc:
        execution.submit("attempt", "build")
    assert exc.value.code == "tool_changed"


def test_custom_docker_tools_require_immutable_image_references():
    settings = Settings(_env_file=None, environment="test", session_key="fixture-key", execution_backend="docker",
                        engineering_tools=[cad_tool()], docker_native_images={"example-cad@1": "example:latest"})
    with pytest.raises(DomainError) as exc:
        EngineeringToolRegistry(settings).validate_policy(custom_policy(), "org")
    assert exc.value.code == "tool_unconfigured"
    settings.docker_native_images["example-cad@1"] = "sha256:" + "a" * 64
    assert EngineeringToolRegistry(settings).validate_policy(custom_policy(), "org") == custom_policy()


def plugin_request(lane="mechanics", **changes):
    content = b'{"width": 40}'
    return {"schemaVersion": 1, "purpose": "build", "lane": lane, "timeoutSeconds": 20,
        "requirements": {"units": "mm"}, "interfaces": {}, "dispatch": {"callbackBaseUrl": "private"},
        "engineeringTool": EngineeringToolRegistry(Settings(_env_file=None)).describe(cad_tool()),
        "inputArtifacts": [{"id": "source", "lane": lane, "name": "design.json", "sha256": digest(content),
                            "contentBase64": base64.b64encode(content).decode()}], **changes}


def test_plugin_runner_has_scoped_input_and_cannot_self_approve(tmp_path):
    calls = []

    def execute(argv, work, timeout):
        calls.append(argv)
        assert argv[0] == "/opt/kleos-tools/example-cad/run" and timeout == 20
        data = json.loads((work / ".kleos-tool-request.json").read_text())
        assert "dispatch" not in data and data["inputs"][0]["name"] == "design.json"
        (work / "outputs/model.step").write_bytes(b"fixture-step")
        (work / "outputs/model.stl").write_bytes(b"fixture-stl")
        (work / "outputs/checks.json").write_text('{"status":"PASS"}')
        return 0, "generator claims PASS; this is only a log"

    result = run_job(plugin_request(), tmp_path, execute)
    assert result.status == "succeeded" and not result.checks and len(calls) == 1
    assert {f.path for f in result.files} == {"native/outputs/model.step", "native/outputs/model.stl"}
    assert all("checks.json" not in f.path for f in result.files)


def test_plugin_output_symlink_or_missing_exports_cannot_publish(tmp_path):
    secret = tmp_path / "outside"
    secret.write_text("outside")

    def execute(argv, work, timeout):
        (work / "outputs/model.step").symlink_to(secret)
        (work / "outputs/model.stl").write_bytes(b"stl")
        return 0, ""

    result = run_job(plugin_request(), tmp_path / "job", execute)
    assert result.status == "failed" and not result.files


def test_plugin_cannot_relabel_supplied_outputs_as_native_generation(tmp_path):
    request = plugin_request()
    request["inputArtifacts"][0]["name"] = "outputs/model.step"
    called = []
    result = run_job(request, tmp_path, lambda *args: called.append(args) or (0, ""))
    assert result.status == "failed" and not result.files and not called
    assert "reserved" in result.limitations[0]


def test_direct_kicad_tool_does_not_require_atopile(tmp_path, monkeypatch):
    monkeypatch.setattr('kleos.circuit_validation.prepare_kicad_libraries', lambda _: {'source': 'test_fixture'})
    from kleos.contracts import EngineeringToolDefinition

    definition = EngineeringToolDefinition(
        id="direct-kicad", version="1", name="Native KiCad", lane="electronics", runner="kicad",
        sourceContract="Produce matching board.kicad_pcb and board.kicad_sch sources directly.",
        outputFiles=["*.kicad_pcb", "*.kicad_sch"], qualified=True,
        qualificationReference="fixture:kicad-native")
    request = {"schemaVersion": 1, "purpose": "build", "lane": "electronics", "timeoutSeconds": 10,
               "engineeringTool": definition.model_dump(mode="json", by_alias=True), "inputArtifacts": []}
    for path in ["board.kicad_pcb", "board.kicad_sch"]:
        content = b"fixture-only"
        request["inputArtifacts"].append({"id": path, "lane": "electronics", "name": path,
            "sha256": digest(content), "contentBase64": base64.b64encode(content).decode()})
    calls = []

    def execute(argv, work, timeout):
        calls.append(argv)
        return 0, "fixture CLI result; not circuit qualification"

    result = run_job(request, tmp_path, execute)
    assert result.status == "succeeded"
    assert calls and all(argv[0] == "kicad-cli" for argv in calls)
    assert len(calls) == 4 and not result.checks
