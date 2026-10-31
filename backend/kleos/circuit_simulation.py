"""Deterministic, numeric-only ngspice plans from accepted interfaces.

This module does not execute a solver or produce validation evidence. Unsupported
scope emits no partial deck: omitting a part could flatter the remaining circuit.
"""

import hashlib
import itertools
import json
import math
import re
from dataclasses import dataclass
from typing import Literal

from pydantic import Field, model_validator

from .circuit_validation import CircuitContract, validate_circuit_contract
from .contracts import WireModel

POLICY_VERSION = "ngspice-ideal-v1"
MAX_NODES = 32
MAX_COMPONENTS = 16
MAX_PASSIVES = 4
SAMPLE_INTERVAL_SECONDS = 0.0001
DURATION_SECONDS = 0.1
MAX_RAW_SAMPLES = 5000
MAX_OUTPUT_BYTES = 2_000_000
TIMEOUT_SECONDS = 15
LIMITATIONS = (
    "Simulated, not measured; digital evidence only, never physical acceptance.",
    "Ideal lumped parts; no layout, thermal, RF, parasitic or manufacturing effects.",
    "Fixed 1 us supply ramp, initially discharged capacitors, 100 ms observation window.",
    "Tolerance envelope covers only the enumerated R/C endpoints, not all interior values.",
    "Declared currentMa is a budget, not an extra simulated load; loads must exist in the circuit.",
)

# Illustrative image-owned models, not manufacturer ratings or interchangeable LEDs.
MODELS = {
    ("Device:LED", "kleos-red-led-v1"): {
        "id": "D1", "is": 1e-20, "n": 2.0, "safeCurrentMa": 5.0,
        "assumption": "Illustrative red LED, Shockley Is=1e-20 A, N=2 at 27 C; advisory 5 mA limit.",
    },
    ("Device:D", "kleos-silicon-diode-v1"): {
        "id": "D2", "is": 1e-14, "n": 1.0, "safeCurrentMa": None,
        "assumption": "Illustrative silicon diode, Shockley Is=1e-14 A, N=1 at 27 C; no rated current.",
    },
}


class Terminals(WireModel):
    component_id: str = Field(min_length=1, max_length=80)
    positive_pin: str = Field(min_length=1, max_length=40)
    negative_pin: str = Field(min_length=1, max_length=40)


class SimulationBinding(WireModel):
    source: Terminals
    loads: dict[str, Terminals] = Field(min_length=1, max_length=128)


class PowerLoad(WireModel):
    id: str = Field(min_length=1, max_length=80)
    min_voltage_v: float = Field(ge=0, le=50, allow_inf_nan=False, strict=True)
    max_voltage_v: float = Field(gt=0, le=50, allow_inf_nan=False, strict=True)
    current_ma: float = Field(ge=0, le=10000, allow_inf_nan=False, strict=True)

    @model_validator(mode="after")
    def ordered_limits(self):
        if self.min_voltage_v > self.max_voltage_v:
            raise ValueError("Load voltage limits are reversed")
        return self


class SimulationPower(WireModel):
    supply_voltage_v: float = Field(gt=0, le=50, allow_inf_nan=False, strict=True)
    max_current_ma: float = Field(gt=0, le=10000, allow_inf_nan=False, strict=True)
    loads: list[PowerLoad] = Field(min_length=1, max_length=16)
    simulation: SimulationBinding


@dataclass(frozen=True)
class Deck:
    corner: tuple[int, ...]
    source: str
    sha256: str


@dataclass(frozen=True)
class SimulationPlan:
    status: Literal["READY", "UNKNOWN"]
    reasons: tuple[str, ...]
    decks: tuple[Deck, ...]
    metadata: dict


def canonical_sha256(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def parse_value(value, kind):
    """Accept decimal/scientific SI literals only, never SPICE expressions/options."""
    if kind not in {"R", "C"} or not isinstance(value, str):
        raise ValueError("Unsupported passive value")
    suffixes = {"R": {"": 1, "R": 1, "k": 1e3, "M": 1e6, "meg": 1e6},
                "C": {"": 1, "p": 1e-12, "n": 1e-9, "u": 1e-6, "m": 1e-3}}
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]{1,3})?)(meg|[RkMpnum])?", value)
    if not match or (match[2] or "") not in suffixes[kind]:
        raise ValueError("Passive value must use a qualified numeric SI literal")
    number = float(match[1]) * suffixes[kind][match[2] or ""]
    low, high = (0.01, 1e9) if kind == "R" else (1e-12, 0.1)
    if not math.isfinite(number) or not low <= number <= high:
        raise ValueError("Passive value exceeds the qualified range")
    return number


def _number(value):
    if not math.isfinite(value):
        raise ValueError("Nonfinite deck number")
    return format(value, ".12g")


def plan_simulation(interfaces):
    """No caller text is interpolated into source, element names or control code."""
    try:
        return _plan(interfaces)
    except (ValueError, TypeError, KeyError) as exc:
        return SimulationPlan("UNKNOWN", (str(exc)[:2000],), (), {"policyVersion": POLICY_VERSION,
            "limitations": list(LIMITATIONS), "physicalAcceptance": False})


def _plan(interfaces):
    structural = validate_circuit_contract(interfaces)
    if structural.status != "PASS":
        raise ValueError("Complete accepted circuit and load coverage are required")
    circuit = CircuitContract.model_validate(interfaces["circuit"])
    power = SimulationPower.model_validate(interfaces["power"])
    parts = sorted(circuit.components, key=lambda part: part.id)
    by_id = {part.id: part for part in parts}
    if len(parts) > MAX_COMPONENTS:
        raise ValueError("Simulation component ceiling exceeded")
    nets = sorted({net for part in parts for net in part.pins.values() if net is not None})
    if len(nets) > MAX_NODES:
        raise ValueError("Simulation node ceiling exceeded")

    def terminals(binding):
        part = by_id[binding.component_id]
        a, b = part.pins[binding.positive_pin], part.pins[binding.negative_pin]
        if a is None or b is None or a == b:
            raise ValueError("Simulation terminals must be distinct connected nets")
        return a, b

    binding = power.simulation
    source = by_id[binding.source.component_id]
    if source.symbol != "Connector_Generic:Conn_01x02" or set(source.pins) != {"1", "2"}:
        raise ValueError("Supply must bind an explicit two-pin connector")
    positive, ground = terminals(binding.source)
    nodes = {ground: 0, **{net: i + 1 for i, net in enumerate(n for n in nets if n != ground)}}
    if set(binding.loads) != {load.id for load in power.loads}:
        raise ValueError("Every power load needs explicit simulation terminals")
    load_probes = {}
    for load in sorted(power.loads, key=lambda load: load.id):
        probe = binding.loads[load.id]
        if load.id not in by_id[probe.component_id].load_ids:
            raise ValueError("Load probe must bind a component carrying that loadId")
        a, b = terminals(probe)
        load_probes[load.id] = {"positiveNode": nodes[a], "negativeNode": nodes[b],
            "minVoltageV": load.min_voltage_v, "maxVoltageV": load.max_voltage_v,
            "declaredCurrentMa": load.current_ma}

    passives, devices, graph = [], [], {net: set() for net in nets}
    for part in parts:
        if part.id == source.id:
            continue
        if part.symbol in {"Connector_Generic:Conn_01x02", "Connector_Generic:Conn_01x04"}:
            expected = {str(i) for i in range(1, 3 if part.symbol.endswith("02") else 5)}
            if set(part.pins) != expected:
                raise ValueError("Unsupported connector pin map")
            continue
        if set(part.pins) != {"1", "2"} or None in part.pins.values():
            raise ValueError("Only connected two-pin devices are qualified")
        a, b = part.pins["1"], part.pins["2"]
        if a == b:
            raise ValueError("Shorted device terminals are unsupported")
        # Capacitors are open at DC; reject islands rather than add fake leakage.
        if part.symbol != "Device:C":
            graph[a].add(b)
            graph[b].add(a)
        if part.symbol in {"Device:R", "Device:C"}:
            kind = part.symbol[-1]
            passives.append((part.id, kind, nodes[a], nodes[b], parse_value(part.value, kind)))
        elif (part.symbol, part.value) in MODELS:
            # KiCad LED/diode pin 1 is cathode, pin 2 is anode.
            devices.append((part.id, nodes[b], nodes[a], MODELS[part.symbol, part.value]))
        else:
            raise ValueError("A circuit part has no trusted simulation model")
    if not passives or len(passives) > MAX_PASSIVES:
        raise ValueError("Simulation requires one to four R/C parts for exhaustive tolerance corners")
    graph[positive].add(ground)
    graph[ground].add(positive)
    seen, pending = set(), [ground]
    while pending:
        net = pending.pop()
        if net not in seen:
            seen.add(net)
            pending.extend(graph[net] - seen)
    if seen != set(nets):
        raise ValueError("Disconnected or DC-floating nets are unsupported; no artificial leakage is added")

    vectors = [f"v({i})" for i in range(1, len(nodes))] + ["i(V1)"]
    vectors += [f"i(V{i + 2})" for i in range(len(devices))]
    output_vectors = " ".join(vectors)
    decks = []
    corners = [(0,) * len(passives), *itertools.product((-1, 1), repeat=len(passives))]
    for corner in corners:
        lines = ["* Kleos numeric circuit simulation v1", ".temp 27",
                 ".options numdgt=12 plotwinsize=0",
                 f"V1 {nodes[positive]} 0 DC {_number(power.supply_voltage_v)} "
                 f"PWL(0 0 1e-6 {_number(power.supply_voltage_v)})"]
        for i, (_, kind, a, b, value) in enumerate(passives):
            varied = value * (1 + corner[i] * (0.05 if kind == "R" else 0.2))
            lines.append(f"{kind}{i + 1} {a} {b} {_number(varied)}" + (" IC=0" if kind == "C" else ""))
        for i, (_, anode, cathode, model) in enumerate(devices):
            sense = len(nodes) + i
            lines += [f"V{i + 2} {anode} {sense} 0", f"D{i + 1} {sense} {cathode} {model['id']}"]
        for model in sorted({model["id"] for _, _, _, model in devices}):
            spec = next(item for item in MODELS.values() if item["id"] == model)
            lines.append(f".model {model} D(IS={_number(spec['is'])} N={_number(spec['n'])} RS=0)")
        lines += [".control", "set wr_vecnames", "set wr_singlescale", "op",
                  f"wrdata operating.dat {output_vectors}",
                  "tran 0.0001 0.1 0 0.0001 uic", f"wrdata transient-raw.dat {output_vectors}",
                  "linearize " + output_vectors,
                  f"wrdata transient.dat {output_vectors}", "quit", ".endc", ".end", ""]
        deck = "\n".join(lines)
        decks.append(Deck(corner, deck, hashlib.sha256(deck.encode()).hexdigest()))
    return SimulationPlan("READY", (), tuple(decks), {"policyVersion": POLICY_VERSION,
        "circuitSha256": canonical_sha256(interfaces["circuit"]),
        "powerSha256": canonical_sha256(interfaces["power"]), "nodes": nodes,
        "loads": load_probes, "maxCurrentMa": power.max_current_ma,
        "vectors": vectors, "passiveIds": [part[0] for part in passives],
        "sourceCurrent": {"vector": "i(V1)", "supplyDrawMultiplier": -1},
        "passives": [{"componentId": ident, "elementName": f"{kind}{i + 1}", "kind": kind,
                      "positiveNode": a, "negativeNode": b, "nominalValue": value,
                      "toleranceFraction": 0.05 if kind == "R" else 0.2}
                     for i, (ident, kind, a, b, value) in enumerate(passives)],
        "devices": [{"componentId": ident, "anodeNode": anode, "cathodeNode": cathode,
                     "currentVector": f"i(V{i + 2})", **model}
                    for i, (ident, anode, cathode, model) in enumerate(devices)],
        "limitations": list(LIMITATIONS), "physicalAcceptance": False,
        "bounds": {"timeoutSecondsPerDeck": TIMEOUT_SECONDS, "maxRawSamples": MAX_RAW_SAMPLES,
                   "maxOutputBytesPerFile": MAX_OUTPUT_BYTES, "maxDecks": 17,
                   "maxTotalSeconds": 17 * TIMEOUT_SECONDS},
        "sampleIntervalSeconds": SAMPLE_INTERVAL_SECONDS, "durationSeconds": DURATION_SECONDS})
