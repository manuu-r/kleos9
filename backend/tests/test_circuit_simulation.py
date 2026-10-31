"""Pure compiler tests; the small independent interpreters below are not ngspice."""

import copy
import hashlib
import math
import re

import pytest

from kleos.circuit_simulation import MODELS, parse_value, plan_simulation


def component(ident, symbol, value, pins, loads=()):
    return {"id": ident, "reference": ident, "symbol": symbol, "value": value,
            "footprint": "Fixture:Package", "pins": pins, "loadIds": list(loads)}


def example(kind="divider"):
    parts = [component("J1", "Connector_Generic:Conn_01x02", "Input", {"1": "VIN", "2": "GND"}),
             component("R1", "Device:R", "1k", {"1": "VIN", "2": "OUT"})]
    if kind == "divider":
        parts.append(component("R2", "Device:R", "2k", {"1": "OUT", "2": "GND"}, ["load"]))
    elif kind == "rc":
        parts.append(component("C1", "Device:C", "1u", {"1": "OUT", "2": "GND"}, ["load"]))
    else:
        parts.append(component("D1", "Device:LED", "kleos-red-led-v1",
                               {"1": "GND", "2": "OUT"}, ["load"]))
    return {"circuit": {"schemaVersion": "circuit-netlist-v1", "components": parts},
            "power": {"supplyVoltageV": 3.3, "maxCurrentMa": 20,
                      "loads": [{"id": "load", "minVoltageV": 1.8, "maxVoltageV": 2.4, "currentMa": 2}],
                      "simulation": {"source": {"componentId": "J1", "positivePin": "1", "negativePin": "2"},
                                     "loads": {"load": {"componentId": parts[-1]["id"],
                                                        "positivePin": "2" if kind == "led" else "1",
                                                        "negativePin": "1" if kind == "led" else "2"}}}}}


def elements(deck, kind):
    return [line.split() for line in deck.splitlines() if re.match(kind + r"[0-9]+ ", line)]


def resistive_solution(deck):
    """Test-only nodal analysis of the emitted resistor network, with pivoting."""
    supply = elements(deck, "V")[0]
    fixed = {0: 0.0, int(supply[1]): float(supply[4])}
    resistors = [(int(row[1]), int(row[2]), float(row[3])) for row in elements(deck, "R")]
    unknown = sorted({n for a, b, _ in resistors for n in (a, b)} - fixed.keys())
    rows = []
    for node in unknown:
        conductance = {n: 0.0 for n in unknown}
        rhs = 0.0
        for a, b, resistance in resistors:
            if node not in (a, b):
                continue
            other = b if a == node else a
            conductance[node] += 1 / resistance
            if other in fixed:
                rhs += fixed[other] / resistance
            else:
                conductance[other] -= 1 / resistance
        rows.append([conductance[n] for n in unknown] + [rhs])
    for i in range(len(rows)):
        pivot = max(range(i, len(rows)), key=lambda j: abs(rows[j][i]))
        rows[i], rows[pivot] = rows[pivot], rows[i]
        divisor = rows[i][i]
        rows[i] = [v / divisor for v in rows[i]]
        for j in range(len(rows)):
            if i != j:
                factor = rows[j][i]
                rows[j] = [a - factor * b for a, b in zip(rows[j], rows[i], strict=True)]
    return {**fixed, **{n: rows[i][-1] for i, n in enumerate(unknown)}}


def test_divider_deck_matches_independent_nodal_and_analytic_answers():
    plan = plan_simulation(example())
    assert plan.status == "READY"
    for deck in plan.decks:
        voltages = resistive_solution(deck.source)
        probe = plan.metadata["loads"]["load"]
        measured = voltages[probe["positiveNode"]] - voltages[probe["negativeNode"]]
        # Component IDs sort R1 before R2; nominal then exhaustive endpoint corners.
        r1, r2 = [base * (1 + 0.05 * sign) for base, sign in zip((1000, 2000), deck.corner, strict=True)]
        assert measured == pytest.approx(3.3 * r2 / (r1 + r2), abs=1e-10)
    assert resistive_solution(plan.decks[0].source)[plan.metadata["nodes"]["OUT"]] == pytest.approx(2.2)
    endpoints = [resistive_solution(d.source)[plan.metadata["nodes"]["OUT"]] for d in plan.decks[1:]]
    assert min(endpoints) < 2.2 < max(endpoints)


def test_rc_emitted_values_match_step_analytic_reference_with_fixed_ramp():
    plan = plan_simulation(example("rc"))
    assert plan.status == "READY"
    for deck in plan.decks:
        r = float(elements(deck.source, "R")[0][3])
        c = float(elements(deck.source, "C")[0][3])
        resistor = elements(deck.source, "R")[0]
        capacitor = elements(deck.source, "C")[0]
        assert list(map(int, resistor[1:3])) == [plan.metadata["nodes"]["VIN"], plan.metadata["nodes"]["OUT"]]
        assert list(map(int, capacitor[1:3])) == [plan.metadata["nodes"]["OUT"], 0]
        assert "IC=0" in deck.source and "uic" in deck.source
        assert "PWL(0 0 1e-6 3.3)" in deck.source
        # Independent forward integration of the generated circuit's KCL.
        dt, voltage = 1e-7, 0.0
        for step in range(10000):
            supply = 3.3 * min(step * dt / 1e-6, 1)
            voltage += dt * (supply - voltage) / (r * c)
        ideal_step = 3.3 * (1 - math.exp(-0.001 / (r * c)))
        ramp_end = 3.3 * (1 - r * c / 1e-6 * (1 - math.exp(-1e-6 / (r * c))))
        ramp_reference = 3.3 + (ramp_end - 3.3) * math.exp(-(0.001 - 1e-6) / (r * c))
        assert voltage == pytest.approx(ramp_reference, abs=0.00015)
        assert voltage == pytest.approx(ideal_step, abs=0.001)
    assert plan.metadata["passiveIds"] == ["C1", "R1"]
    assert len(plan.decks) == 5


def test_led_orientation_sense_source_and_shockley_resistor_reference():
    plan = plan_simulation(example("led"))
    assert plan.status == "READY"
    deck = plan.decks[0].source
    diode = elements(deck, "D")[0]
    sense = elements(deck, "V")[1]
    assert int(sense[1]) == plan.metadata["nodes"]["OUT"]
    assert sense[2] == diode[1] and diode[2] == "0"
    assert "i(V2)" in plan.metadata["vectors"]
    spec = MODELS["Device:LED", "kleos-red-led-v1"]
    assert ".model D1 D(IS=1e-20 N=2 RS=0)" in deck
    # Solve V_supply = I*R + N*Vt*ln(1+I/Is) independently by bisection.
    thermal_voltage = 1.380649e-23 * 300.15 / 1.602176634e-19
    resistance = float(elements(deck, "R")[0][3])
    low, high = 0.0, 3.3 / resistance
    for _ in range(100):
        current = (low + high) / 2
        drop = spec["n"] * thermal_voltage * math.log1p(current / spec["is"])
        if current * resistance + drop > 3.3:
            high = current
        else:
            low = current
    current = (low + high) / 2
    assert 1.25 < current * 1000 < 1.27
    assert current * resistance == pytest.approx(3.3 - drop, abs=1e-10)
    assert current * 1000 < spec["safeCurrentMa"]


def test_deterministic_ordering_hashes_and_exhaustive_corners():
    interfaces = example()
    plan = plan_simulation(interfaces)
    changed = copy.deepcopy(interfaces)
    changed["circuit"]["components"].reverse()
    for part in changed["circuit"]["components"]:
        part["pins"] = dict(reversed(list(part["pins"].items())))
    reordered = plan_simulation(changed)
    assert plan.decks == reordered.decks
    assert len({deck.sha256 for deck in plan.decks}) == 5
    assert {d.corner for d in plan.decks} == {(0, 0), (-1, -1), (-1, 1), (1, -1), (1, 1)}
    for deck in plan.decks:
        assert hashlib.sha256(deck.source.encode()).hexdigest() == deck.sha256
    changed["power"]["maxCurrentMa"] = 1
    assert plan_simulation(changed).metadata["powerSha256"] != plan.metadata["powerSha256"]


@pytest.mark.parametrize("value,kind,expected", [
    ("1k", "R", 1000), ("2.2meg", "R", 2.2e6), ("1M", "R", 1e6), ("330R", "R", 330),
    ("1e3", "R", 1000), ("100n", "C", 1e-7), ("1u", "C", 1e-6), ("1e-6", "C", 1e-6),
])
def test_strict_values(value, kind, expected):
    assert parse_value(value, kind) == pytest.approx(expected)


@pytest.mark.parametrize("value", ["nan", "inf", "-1", "0", "1e999", "1e-999", "1k\n.end",
    "1k;quit", "{V(1)}", "1k tc=1", "1m", "1K", " 1k", "1k ", "1 k", "4k7", "1Ω", "1e+2.include"])
def test_untrusted_values_never_reach_decks(value):
    interfaces = example()
    interfaces["circuit"]["components"][1]["value"] = value
    plan = plan_simulation(interfaces)
    assert plan.status == "UNKNOWN" and not plan.decks


@pytest.mark.parametrize("mutation", ["binding", "unknown", "generic_led", "pin", "coverage",
    "wrong_load", "same_net", "disconnected", "source", "nonfinite", "reversed_limits"])
def test_unsupported_scope_has_no_partial_simulation(mutation):
    interfaces = example("led")
    power = interfaces["power"]
    parts = interfaces["circuit"]["components"]
    if mutation == "binding":
        power.pop("simulation")
    elif mutation == "unknown":
        parts[1]["symbol"] = "Unknown:Regulator"
    elif mutation == "generic_led":
        parts[-1]["value"] = "Red"
    elif mutation == "pin":
        parts[1]["pins"]["3"] = None
    elif mutation == "coverage":
        power["simulation"]["loads"] = {}
    elif mutation == "wrong_load":
        power["simulation"]["loads"]["load"]["componentId"] = "R1"
    elif mutation == "same_net":
        parts[0]["pins"]["2"] = "VIN"
    elif mutation == "disconnected":
        parts.append(component("R3", "Device:R", "1k", {"1": "FLOAT_A", "2": "FLOAT_B"}))
    elif mutation == "source":
        power["simulation"]["source"]["componentId"] = "R1"
    elif mutation == "nonfinite":
        power["supplyVoltageV"] = float("nan")
    else:
        power["loads"][0]["minVoltageV"] = 4
    plan = plan_simulation(interfaces)
    assert plan.status == "UNKNOWN" and plan.reasons and not plan.decks
    assert plan.metadata["physicalAcceptance"] is False


def test_labels_and_references_cannot_inject_spice():
    interfaces = example()
    for part in interfaces["circuit"]["components"]:
        part["pins"] = {pin: "v(1); .include evil" if net == "OUT" else net for pin, net in part["pins"].items()}
        part["footprint"] = "Untrusted:$(shell)"
    plan = plan_simulation(interfaces)
    assert plan.status == "READY"
    assert "v(1); .include evil" in plan.metadata["nodes"]
    for deck in plan.decks:
        assert "evil" not in deck.source and "Untrusted" not in deck.source
        assert not any(token in deck.source for token in ("shell", ".include", ".lib", "VIN", "OUT", "GND"))


def test_corner_ceiling():
    interfaces = example()
    for i in range(3, 6):
        interfaces["circuit"]["components"].append(
            component(f"R{i}", "Device:R", "1k", {"1": "VIN", "2": "GND"}))
    assert plan_simulation(interfaces).status == "UNKNOWN"
    interfaces["circuit"]["components"].pop()
    plan = plan_simulation(interfaces)
    assert plan.status == "READY" and len(plan.decks) == 17
    assert plan.metadata["bounds"]["maxDecks"] == 17
    assert all(len(d.source.encode()) < 10000 for d in plan.decks)


def test_node_and_component_ceilings():
    interfaces = example()
    for i in range(2, 10):
        interfaces["circuit"]["components"].append(component(f"J{i}", "Connector_Generic:Conn_01x04", "Port",
            {str(pin): f"net_{i}_{pin}" for pin in range(1, 5)}))
    assert "node ceiling" in plan_simulation(interfaces).reasons[0]
    interfaces = example()
    for i in range(2, 16):
        interfaces["circuit"]["components"].append(component(f"J{i}", "Connector_Generic:Conn_01x02", "Port",
            {"1": "VIN", "2": "GND"}))
    assert "component ceiling" in plan_simulation(interfaces).reasons[0]


@pytest.mark.parametrize("value", [True, False, "3.3", float("inf"), -1, 0, 51])
def test_power_requires_bounded_numbers(value):
    interfaces = example()
    interfaces["power"]["supplyVoltageV"] = value
    assert plan_simulation(interfaces).status == "UNKNOWN"


def test_dc_capacitive_island_is_not_stabilized_with_invented_resistors():
    interfaces = example("rc")
    interfaces["circuit"]["components"][1]["symbol"] = "Device:C"
    interfaces["circuit"]["components"][1]["value"] = "1u"
    assert "DC-floating" in plan_simulation(interfaces).reasons[0]


def test_diode_model_has_no_invented_current_rating():
    interfaces = example("led")
    part = interfaces["circuit"]["components"][-1]
    part["symbol"], part["value"] = "Device:D", "kleos-silicon-diode-v1"
    plan = plan_simulation(interfaces)
    assert plan.status == "READY"
    assert plan.metadata["devices"][0]["safeCurrentMa"] is None
    assert ".model D2 D(IS=1e-14 N=1 RS=0)" in plan.decks[0].source


def test_compilation_does_not_mutate_accepted_interfaces():
    interfaces = example()
    before = copy.deepcopy(interfaces)
    plan_simulation(interfaces)
    assert interfaces == before


def test_metadata_identifies_each_corner_current_measurement():
    plan = plan_simulation(example("led"))
    assert plan.metadata["sourceCurrent"] == {"vector": "i(V1)", "supplyDrawMultiplier": -1}
    passive = plan.metadata["passives"][0]
    assert passive == {"componentId": "R1", "elementName": "R1", "kind": "R",
                       "positiveNode": plan.metadata["nodes"]["VIN"],
                       "negativeNode": plan.metadata["nodes"]["OUT"],
                       "nominalValue": 1000, "toleranceFraction": 0.05}
    assert plan.metadata["devices"][0]["currentVector"] == "i(V2)"


@pytest.mark.parametrize("interfaces", [None, [], {}, {"circuit": None}, {"power": []}])
def test_missing_contract_is_unknown(interfaces):
    assert plan_simulation(interfaces).status == "UNKNOWN"
