import copy
import math
from types import SimpleNamespace

import pytest

from kleos.contracts import ConceptScene
from kleos.design_intent import bind_interfaces, derive_intent, scene_hash
from kleos.design_validation import inside_profile, validate_design_binding, validate_pcb_layout


def design_fixture(shape="cylinder", sensor=False, board_shape="box", board_width=20):
    parts = [
        {"id": "shell", "label": "Enclosure", "role": "shell", "shape": shape,
         "size": {"x": 2, "y": 2 if shape == "cylinder" else 1.5, "z": .5}, "position": {"x": 0, "y": 0, "z": 0}},
        {"id": "board", "label": "Board", "role": "board", "shape": board_shape,
         "size": {"x": 1, "y": 1, "z": .05}, "position": {"x": 0, "y": 0, "z": .025}},
        {"id": "battery", "label": "Battery", "role": "battery", "shape": "box",
         "size": {"x": .8, "y": .7, "z": .1}, "position": {"x": 0, "y": 0, "z": -.1}},
    ]
    if sensor:
        parts.append({"id": "sensor", "label": "Front sensor", "role": "sensor", "shape": "box",
                      "size": {"x": .1, "y": .1, "z": .04}, "position": {"x": 0, "y": .7, "z": .075}})
    scene = ConceptScene.model_validate({"schemaVersion": "concept-scene-v1", "title": "Approved fixture",
        "summary": "Public deterministic fixture", "deviceKind": "monitor", "units": "relative", "parts": parts}).model_dump(
            mode="json", by_alias=True)
    approval = {"schemaVersion": "approved-design-v1", "version": 1, "sceneVersion": 1,
                "sceneHash": scene_hash(scene), "scene": scene}
    requirements = {"designIntent": derive_intent(approval)}
    project = SimpleNamespace(requirements=requirements, state={"approvedDesign": approval,
                                                               "confirmed": True, "approvedDesignStatus": "approved"})
    outer = dict(x=40, y=40 if shape == "cylinder" else 30, z=10)
    inner = {axis: number - 2 for axis, number in outer.items()}
    components = [
        {"id": "pcb", "conceptPartId": "board", "sizeMm": dict(x=board_width, y=board_width, z=1),
         "positionMm": dict(x=(inner["x"] - board_width) / 2, y=(inner["y"] - board_width) / 2, z=4)},
        {"id": "battery", "conceptPartId": "battery", "sizeMm": dict(x=12, y=10, z=2),
         "positionMm": dict(x=(inner["x"] - 12) / 2, y=(inner["y"] - 10) / 2, z=1)},
    ]
    if sensor:
        components.append({"id": "sensor", "conceptPartId": "sensor", "sizeMm": dict(x=1, y=1, z=1),
            "positionMm": dict(x=inner["x"] / 2 - .5, y=inner["y"] / 2 + 14 - .5, z=5)})
    proposed = {"mechanical": {"externalMm": outer, "internalMm": inner, "minimumWallMm": 1, "components": components},
                "electronics": {"componentReferences": [{"conceptPartId": "sensor", "reference": "U1"}] if sensor else []},
                "circuit": {"components": [{"reference": "U1"}]} if sensor else {"components": []},
                "power": {"supplyVoltageV": 3.3, "maxCurrentMa": 100,
                          "loads": [{"id": "mcu", "minVoltageV": 3, "maxVoltageV": 3.6, "currentMa": 10}]}}
    interfaces, _ = bind_interfaces(project, proposed)
    return requirements, interfaces


def pcb_snapshot(size=20, circle=False):
    points = ([[math.cos(index * math.tau / 128) * size / 2, math.sin(index * math.tau / 128) * size / 2]
               for index in range(128)] if circle else [[-size / 2, -size / 2], [size / 2, -size / 2],
                                                       [size / 2, size / 2], [-size / 2, size / 2]])
    return {"schemaVersion": "pcb-layout-v1", "kicadVersion": "9.0.8", "pcbSha256": "a" * 64,
            "outlines": [points], "components": [], "tracks": [], "unsupported": []}


@pytest.mark.parametrize("shape", ["cylinder", "box", "rounded_box"])
def test_server_bound_approved_profile_is_required_and_cannot_be_changed(shape):
    requirements, interfaces = design_fixture(shape)
    assert validate_design_binding(requirements, interfaces).status == "PASS"
    interfaces["mechanical"]["designIntent"]["enclosure"]["profile"] = "rectangle" if shape != "box" else "circle"
    assert validate_design_binding(requirements, interfaces).status == "FAIL"


def test_equal_circle_bbox_does_not_make_square_corners_fit():
    assert not inside_profile(20, 20, dict(x=40, y=40, z=10), "circle")
    assert inside_profile(20, 0, dict(x=40, y=40, z=10), "circle")
    assert inside_profile(20, 20, dict(x=40, y=40, z=10), "rectangle")


def test_rectangular_pcb_can_fit_circular_case_and_actual_exterior_copper_cannot():
    requirements, interfaces = design_fixture()
    snapshot = pcb_snapshot()
    assert validate_pcb_layout(requirements, interfaces, snapshot, "a" * 64).status == "PASS"
    snapshot["tracks"] = [{"points": [[18, 10], [19, 10]], "radius": .2}]
    assert validate_pcb_layout(requirements, interfaces, snapshot, "a" * 64).status == "FAIL"


def test_actual_circular_pcb_is_not_rejected_using_its_nonexistent_bbox_corners():
    requirements, interfaces = design_fixture(board_shape="cylinder", board_width=36)
    assert validate_design_binding(requirements, interfaces).status == "PASS"
    assert validate_pcb_layout(requirements, interfaces, pcb_snapshot(36, circle=True), "a" * 64).status == "PASS"
    assert validate_pcb_layout(requirements, interfaces, pcb_snapshot(36), "a" * 64).status == "FAIL"


def test_actual_sensor_position_uses_y_up_reference_and_wrong_region_fails():
    requirements, interfaces = design_fixture(sensor=True)
    snapshot = pcb_snapshot()
    sensor = {"reference": "U1", "extentSource": "fabrication_body_and_pads", "center": [0, -8],
              "points": [[-.5, -8.5], [.5, -8.5], [.5, -7.5], [-.5, -7.5]]}
    snapshot["components"] = [sensor]
    result = validate_pcb_layout(requirements, interfaces, snapshot, "a" * 64)
    assert result.status == "PASS", result.details
    sensor["center"] = [0, 8]
    assert validate_pcb_layout(requirements, interfaces, snapshot, "a" * 64).status == "FAIL"
    interfaces["electronics"]["designIntent"]["componentReferences"] = []
    assert validate_pcb_layout(requirements, interfaces, snapshot, "a" * 64).status == "UNKNOWN"


def test_circular_internal_component_corners_and_aspect_conflicts_block_before_native_work():
    requirements, interfaces = design_fixture(board_width=30)
    assert validate_design_binding(requirements, interfaces).status == "FAIL"
    requirements, interfaces = design_fixture()
    interfaces["mechanical"]["externalMm"]["z"] = 30
    interfaces["mechanical"]["designIntent"]["enclosure"]["externalMm"]["z"] = 30
    assert validate_design_binding(requirements, interfaces).status == "FAIL"


def test_frozen_region_and_stacking_are_not_candidate_expected_truth():
    requirements, interfaces = design_fixture()
    tampered = copy.deepcopy(interfaces)
    tampered["mechanical"]["designIntent"]["components"][0]["region"]["center"]["x"] = .4
    assert validate_design_binding(requirements, tampered).status == "FAIL"
    moved = copy.deepcopy(interfaces)
    moved["mechanical"]["components"][0]["positionMm"]["z"] = 0
    moved["mechanical"]["designIntent"]["components"][0]["positionMm"]["z"] = 0
    assert validate_design_binding(requirements, moved).status == "FAIL"


def test_legacy_missing_approval_is_explicitly_excluded_not_an_invented_pass():
    result = validate_design_binding({}, {})
    assert result.status == "UNKNOWN" and result.details["required"] is False and result.details["scopeExcluded"]


def test_approved_off_center_internal_part_cannot_move_to_the_center():
    requirements, interfaces = design_fixture()
    frozen = next(item for item in requirements["designIntent"]["components"] if item["conceptPartId"] == "battery")
    frozen["region"]["center"]["x"] = -.2
    mapped = next(item for item in interfaces["mechanical"]["designIntent"]["components"] if item["conceptPartId"] == "battery")
    mapped["region"] = copy.deepcopy(frozen["region"])
    # A .2 displacement would fit the broad .22 region tolerance, but erase left intent.
    assert validate_design_binding(requirements, interfaces).status == "FAIL"
    raw = next(item for item in interfaces["mechanical"]["components"] if item["id"] == "battery")
    raw["positionMm"]["x"] -= 6
    mapped["positionMm"] = copy.deepcopy(raw["positionMm"])
    assert validate_design_binding(requirements, interfaces).status == "PASS"


def test_front_peripheral_cannot_lose_its_side_within_broad_position_tolerance():
    requirements, interfaces = design_fixture(sensor=True)
    source = next(item for item in requirements["designIntent"]["components"] if item["conceptPartId"] == "sensor")
    source["region"]["center"]["y"] = .2
    interfaces["mechanical"]["designIntent"]["components"][-1]["region"] = copy.deepcopy(source["region"])
    interfaces["electronics"]["designIntent"]["componentReferences"][0]["region"] = copy.deepcopy(source["region"])
    snapshot = pcb_snapshot()
    snapshot["components"] = [{"reference": "U1", "extentSource": "fabrication_body_and_pads", "center": [0, 0],
                                "points": [[-.5, -.5], [.5, -.5], [.5, .5], [-.5, .5]]}]
    assert validate_pcb_layout(requirements, interfaces, snapshot, "a" * 64).status == "FAIL"
