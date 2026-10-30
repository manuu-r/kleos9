import base64
import copy
import hashlib
import json
from pathlib import Path

import pytest
from sqlalchemy import select
from test_control_plane import start_research

from kleos.circuit_validation import (
    UnavailableCircuitData,
    prepare_kicad_libraries,
    validate_circuit_contract,
    validate_circuit_correspondence,
)
from kleos.db import Task, Validation
from kleos.domain import contract_hash
from kleos.native_runner import run_job


def circuit_fixture():
    interfaces = {"power": {"loads": [{"id": "indicator"}]}, "circuit": {
        "schemaVersion": "circuit-netlist-v1", "components": [
            {"id": "input", "reference": "J1", "symbol": "Connector_Generic:Conn_01x02", "value": "Input",
             "footprint": "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical", "loadIds": [],
             "pins": {"1": "VCC", "2": "GND"}},
            {"id": "indicator", "reference": "R1", "symbol": "Device:R", "value": "1k",
             "footprint": "Resistor_SMD:R_0603_1608Metric", "loadIds": ["indicator"],
             "pins": {"1": "VCC", "2": "GND"}},
        ]}}
    xml = b'''<export version="E"><components>
      <comp ref="J1"><value>Input</value><footprint>Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical</footprint>
        <libsource lib="Connector_Generic" part="Conn_01x02"/></comp>
      <comp ref="R1"><value>1k</value><footprint>Resistor_SMD:R_0603_1608Metric</footprint>
        <libsource lib="Device" part="R"/></comp></components>
      <libparts><libpart lib="Connector_Generic" part="Conn_01x02"><pins><pin num="1"/><pin num="2"/></pins></libpart>
        <libpart lib="Device" part="R"><pins><pin num="1"/><pin num="2"/></pins></libpart></libparts>
      <nets><net code="1" name="VCC"><node ref="J1" pin="1"/><node ref="R1" pin="1"/></net>
        <net code="2" name="GND"><node ref="J1" pin="2"/><node ref="R1" pin="2"/></net></nets></export>'''
    evidence = {"schematicSha256": hashlib.sha256(b"schematic-source").hexdigest(),
                "pcbSha256": hashlib.sha256(b"pcb-source").hexdigest()}
    board = {"schemaVersion": "kicad-board-connectivity-v1", "kicadVersion": "9.0.8",
             "pcbSha256": evidence["pcbSha256"], "components": [
                 {"reference": part["reference"], "value": part["value"], "footprint": part["footprint"],
                  "pads": [{"number": pin, "net": net or "", "mechanical": False} for pin, net in part["pins"].items()]}
                 for part in interfaces["circuit"]["components"]]}
    return interfaces, xml, board, evidence


def check(interfaces, xml, board, evidence):
    return validate_circuit_correspondence(interfaces, xml, board, {"purpose": "fixture"}, evidence)


def test_matching_circuit_is_bound_to_accepted_contract_and_real_file_hashes():
    interfaces, xml, board, evidence = circuit_fixture()
    result = check(interfaces, xml, board, evidence)
    assert result.status == "PASS" and result.details["netCount"] == 2
    assert result.details["acceptedContractHash"] == contract_hash({"purpose": "fixture"}, interfaces)
    assert result.details["evidence"] == evidence
    assert result.details["netlistSha256"] == hashlib.sha256(xml).hexdigest()
    assert result.details["coveredLoadIds"] == ["indicator"]


@pytest.mark.parametrize("mutation", ["missing_component", "extra_component", "wrong_value", "wrong_footprint",
                                     "missing_pad", "wrong_pin", "short", "duplicate_pad", "stale_hash"])
def test_wrong_actual_pcb_never_passes_even_with_clean_rule_reports(mutation):
    interfaces, xml, board, evidence = circuit_fixture()
    part = board["components"][-1]
    if mutation == "missing_component":
        board["components"].pop()
    elif mutation == "extra_component":
        extra = copy.deepcopy(part)
        extra["reference"] = "R99"
        board["components"].append(extra)
    elif mutation == "wrong_value":
        part["value"] = "0R"
    elif mutation == "wrong_footprint":
        part["footprint"] = "Resistor_SMD:R_0402_1005Metric"
    elif mutation == "missing_pad":
        part["pads"].pop()
    elif mutation == "wrong_pin":
        part["pads"][0]["number"] = "3"
    elif mutation == "short":
        part["pads"][0]["net"] = "GND"
    elif mutation == "duplicate_pad":
        part["pads"].append(copy.deepcopy(part["pads"][0]))
    else:
        board["pcbSha256"] = "f" * 64
    assert check(interfaces, xml, board, evidence).status == ("UNKNOWN" if mutation == "duplicate_pad" else "FAIL")


@pytest.mark.parametrize("old,new", [(b'<value>1k</value>', b'<value>0R</value>'),
    (b'lib="Device" part="R"', b'lib="Device" part="C"'),
    (b'<node ref="R1" pin="1"/>', b''), (b'name="VCC"', b'name="GND"')])
def test_schematic_mismatch_is_independently_rejected(old, new):
    interfaces, xml, board, evidence = circuit_fixture()
    assert check(interfaces, xml.replace(old, new), board, evidence).status in {"FAIL", "UNKNOWN"}


def test_explicit_unused_pin_must_be_physically_present_and_isolated():
    interfaces, xml, board, evidence = circuit_fixture()
    interfaces["circuit"]["components"][0]["pins"]["3"] = None
    xml = xml.replace(b'<pin num="2"/></pins></libpart>', b'<pin num="2"/><pin num="3"/></pins></libpart>', 1)
    board["components"][0]["pads"].append({"number": "3", "net": "unconnected-(J1-Pin_3-Pad3)"})
    assert check(interfaces, xml, board, evidence).status == "PASS"
    board["components"][0]["pads"][-1]["net"] = "VCC"
    assert check(interfaces, xml, board, evidence).status == "FAIL"


@pytest.mark.parametrize("loads", [[], ["different-load"], ["indicator", "unknown"]])
def test_every_accepted_load_requires_an_explicit_component_binding(loads):
    interfaces, _, _, _ = circuit_fixture()
    interfaces["circuit"]["components"][1]["loadIds"] = loads
    assert validate_circuit_contract(interfaces).status == "FAIL"


def test_missing_expectations_or_independent_data_remain_unknown():
    interfaces, xml, board, evidence = circuit_fixture()
    assert check({}, xml, board, evidence).status == "UNKNOWN"
    assert check(interfaces, xml, None, evidence).status == "UNKNOWN"
    assert check(interfaces, xml, board, {}).status == "UNKNOWN"
    assert check(interfaces, xml.replace(b'<libparts>', b'<other>'), board, evidence).status == "FAIL"
    board["kicadVersion"] = "unqualified-99.0"
    assert check(interfaces, xml, board, evidence).status == "UNKNOWN"


@pytest.mark.parametrize("flag", ["netTie", "dnp"])
def test_unqualified_pcb_features_stay_unknown(flag):
    interfaces, xml, board, evidence = circuit_fixture()
    board["components"][0][flag] = True
    assert check(interfaces, xml, board, evidence).status == "UNKNOWN"


@pytest.mark.parametrize("interfaces", [None, [], "legacy", {}, {"circuit": None}, {"circuit": []}])
def test_malformed_or_legacy_interface_shapes_are_safe_unknowns(interfaces):
    assert validate_circuit_contract(interfaces).status == "UNKNOWN"


@pytest.mark.parametrize("power", [None, [], "legacy", 1, {"loads": None}, {"loads": [None]},
                                   {"loads": [{"id": []}]}])
def test_malformed_nested_power_shape_is_a_failed_contract_not_an_exception(power):
    interfaces, _, _, _ = circuit_fixture()
    interfaces["power"] = power
    assert validate_circuit_contract(interfaces).status == "FAIL"


@pytest.mark.parametrize("shape", [None, [], "legacy", {}, {"pads": [None]}, {"reference": "R1", "pads": [None]}])
def test_malformed_board_shapes_never_escape_as_exceptions(shape):
    interfaces, xml, board, evidence = circuit_fixture()
    board["components"] = [shape]
    assert check(interfaces, xml, board, evidence).status == "FAIL"


def test_nonfinite_contract_or_malformed_evidence_cannot_pass():
    interfaces, xml, board, evidence = circuit_fixture()
    assert check(interfaces, xml, board, []).status == "UNKNOWN"
    interfaces["legacyProviderField"] = float("nan")
    assert check(interfaces, xml, board, evidence).status == "UNKNOWN"


@pytest.mark.parametrize("complete", [False, True])
def test_interface_stage_exposes_contract_diagnostic_without_release_authority(platform, complete):
    interfaces, _, _, _ = circuit_fixture()
    if not complete:
        interfaces.pop("circuit")
    project_id, _, task_id, worker = start_research(platform)
    with platform.db.transaction() as session:
        session.get(Task, task_id).kind = "project.solveInterfaces"
    attempt = worker.begin_attempt(task_id)
    worker.finish_attempt(attempt, {"summary": "Fixture interfaces", "interfaces": interfaces})
    workspace = platform.control.workspace(platform.principal, project_id)["project"]
    diagnostic = workspace["state"]["circuitContractCheck"]
    assert diagnostic["status"] == ("PASS" if complete else "UNKNOWN")
    assert diagnostic["details"]["engineeringRevision"] == 1
    assert diagnostic["details"]["contractHash"] == contract_hash(workspace["requirements"], interfaces)
    with platform.db.transaction() as session:
        assert session.get(Task, task_id).result["circuitContractCheck"] == diagnostic
        assert not session.scalars(select(Validation)).all()


@pytest.mark.parametrize("wrong_pcb", [False, True])
def test_native_validation_runs_fresh_checks_and_ignores_generated_pass_reports(tmp_path, wrong_pcb, monkeypatch):
    monkeypatch.setattr("kleos.circuit_validation.prepare_kicad_libraries", lambda _, **kwargs: {"source": "test_fixture"})
    interfaces, xml, board, _ = circuit_fixture()
    artifacts = []
    for name in ("circuit.kicad_sch", "circuit.kicad_pcb", "circuit.kicad_pro", "pass.json"):
        content = b'{"status":"PASS","all_checks_disabled":true}'
        artifacts.append({"id": name, "lane": "electronics", "name": "native/" + name,
                          "sha256": hashlib.sha256(content).hexdigest(), "contentBase64": base64.b64encode(content).decode(),
                          "provenance": {"nativeJobId": "trusted-build"}})
    if wrong_pcb:
        board["components"][1]["pads"][0]["net"] = "GND"
    def tools(args, work, timeout):
        assert not (work / "circuit.kicad_pro").exists() and not (work / "pass.json").exists()
        if args[0] == "kicad-cli":
            if args[1:4] == ["sch", "export", "netlist"]:
                Path(args[args.index("--output") + 1]).write_bytes(xml)
            else:
                assert "--severity-all" in args
            return 0, "Independent tool completed"
        board["pcbSha256"] = hashlib.sha256(Path(args[-1]).read_bytes()).hexdigest()
        return 0, json.dumps(board)
    result = run_job({"schemaVersion": 1, "purpose": "validate", "interfaces": interfaces,
                      "inputArtifacts": artifacts}, tmp_path, tools)
    checks = {item.name: item for item in result.checks}
    expected = "FAIL" if wrong_pcb else "PASS"
    assert checks["electronics_correspondence"].status == expected
    assert checks["electronics"].status == expected
    assert checks["electronics_erc_drc"].status == "PASS"


def test_native_rejects_xml_entities_before_any_xml_parser_call(tmp_path, monkeypatch):
    monkeypatch.setattr("kleos.circuit_validation.prepare_kicad_libraries", lambda _, **kwargs: {"source": "test_fixture"})
    interfaces, xml, _, _ = circuit_fixture()
    content = b"fixture candidate"
    artifacts = [{"id": suffix, "lane": "electronics", "name": "native/circuit." + suffix,
                  "sha256": hashlib.sha256(content).hexdigest(), "contentBase64": base64.b64encode(content).decode(),
                  "provenance": {"nativeJobId": "trusted-build"}} for suffix in ("kicad_sch", "kicad_pcb")]
    def tools(args, work, timeout):
        if args[1:4] == ["sch", "export", "netlist"]:
            Path(args[args.index("--output") + 1]).write_bytes(b'<!DOCTYPE export [<!ENTITY test "unsafe">]>' + xml)
        return 0, "Independent export"
    def must_not_parse(*args, **kwargs):
        raise AssertionError("DTD/entity declarations must be rejected before XML parsing")
    monkeypatch.setattr("kleos.native_runner.ET.fromstring", must_not_parse)
    result = run_job({"schemaVersion": 1, "purpose": "validate", "interfaces": interfaces,
                      "inputArtifacts": artifacts}, tmp_path, tools)
    electronics = next(item for item in result.checks if item.name == "electronics")
    assert electronics.status == "FAIL" and "DTD/entity" in electronics.details["reason"]


def test_trusted_library_tables_use_only_installed_operator_paths(tmp_path):
    installed, job = tmp_path / "installed", tmp_path / "job"
    (installed / "symbols").mkdir(parents=True)
    (installed / "footprints/Resistor_SMD.pretty").mkdir(parents=True)
    (installed / "symbols/Device.kicad_sym").write_text("reviewed library")
    job.mkdir()
    (job / "fp-lib-table").write_text('untrusted generated paths')
    evidence = prepare_kicad_libraries(job, installed)
    assert evidence["source"] == "immutable_image"
    assert evidence["symbolLibraries"] == evidence["footprintLibraries"] == 1
    table = (job / ".home/.config/kicad/9.0/fp-lib-table").read_text()
    assert str(installed / "footprints/Resistor_SMD.pretty") in table
    assert "untrusted" not in table
    with pytest.raises(UnavailableCircuitData):
        prepare_kicad_libraries(job, tmp_path / "missing")


def test_missing_operator_libraries_are_unknown_before_any_engineering_tool_runs(tmp_path, monkeypatch):
    def missing(_directory, **kwargs):
        raise UnavailableCircuitData("Qualified KiCad libraries unavailable")
    monkeypatch.setattr("kleos.circuit_validation.prepare_kicad_libraries", missing)
    raw = b"fixture source"
    artifacts = [{"id": suffix, "lane": "electronics", "name": "native/circuit." + suffix,
                  "sha256": hashlib.sha256(raw).hexdigest(), "contentBase64": base64.b64encode(raw).decode(),
                  "provenance": {"nativeJobId": "trusted-build"}} for suffix in ("kicad_sch", "kicad_pcb")]
    def must_not_execute(*args):
        raise AssertionError("Missing operator capability must not invoke an engineering repair/check")
    result = run_job({"schemaVersion": 1, "purpose": "validate", "inputArtifacts": artifacts}, tmp_path, must_not_execute)
    electronics = next(check for check in result.checks if check.name == "electronics")
    assert electronics.status == "UNKNOWN" and "libraries unavailable" in electronics.details["reason"]


def test_missing_accepted_custom_library_is_an_operator_capability_limit(tmp_path):
    interfaces, _, _, _ = circuit_fixture()
    root = tmp_path / "installed"
    (root / "symbols").mkdir(parents=True)
    (root / "symbols/Device.kicad_sym").write_text("reviewed symbols")
    (root / "footprints/Resistor_SMD.pretty").mkdir(parents=True)
    (root / "footprints/Resistor_SMD.pretty/R_0603_1608Metric.kicad_mod").write_text("reviewed footprint")
    job = tmp_path / "job"
    job.mkdir()
    with pytest.raises(UnavailableCircuitData, match="not qualified"):
        prepare_kicad_libraries(job, root, interfaces=interfaces)
    interfaces["circuit"]["components"] = [interfaces["circuit"]["components"][1]]
    assert prepare_kicad_libraries(job, root, interfaces=interfaces)["source"] == "immutable_image"
    interfaces["circuit"]["components"][0]["footprint"] = "Resistor_SMD:Missing_Operator_Part"
    with pytest.raises(UnavailableCircuitData, match="not installed"):
        prepare_kicad_libraries(job, root, interfaces=interfaces)
