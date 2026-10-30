"""Bounded KiCad 9 circuit correspondence against an accepted interface contract.

Expectations come only from interfaces.circuit, never from generated artifacts.
This checks declared component identity and electrical topology, not analog/RF
performance, datasheet correctness, footprint geometry, or physical operation.
"""

import hashlib
import json
import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError, field_validator, model_validator

from .contracts import CheckResult, WireModel

MAX_COMPONENTS = 128
MAX_PINS = 2048
MAX_EXPORT_BYTES = 1024 * 1024


def prepare_kicad_libraries(directory, library_root=Path("/usr/share/kicad"), *, interfaces=None):
    """Seed job-local tables solely from operator-installed immutable libraries.

    Never copy generated fp/sym-lib-table files or allow candidate paths into
    these tables. clean_environment(directory) uses this same per-job HOME.
    """
    installed = {"symbol": sorted((library_root / "symbols").glob("*.kicad_sym")),
                 "footprint": sorted((library_root / "footprints").glob("*.pretty"))}
    if not installed["symbol"] or not installed["footprint"]:
        raise UnavailableCircuitData("Qualified KiCad symbol/footprint packages are unavailable in this image")
    if interfaces is not None and validate_circuit_contract(interfaces).status == "PASS":
        namespaces = {kind: {path.stem: path for path in paths} for kind, paths in installed.items()}
        for component in CircuitContract.model_validate(interfaces["circuit"]).components:
            symbol_library = component.symbol.split(":", 1)[0]
            footprint_library, item = component.footprint.split(":", 1)
            if symbol_library not in namespaces["symbol"] or footprint_library not in namespaces["footprint"]:
                raise UnavailableCircuitData(f"Accepted {component.reference} requires a symbol/footprint library "
                                             "not qualified in this image")
            base = namespaces["footprint"][footprint_library].resolve()
            footprint = (base / (item + ".kicad_mod")).resolve()
            if not footprint.is_relative_to(base) or not footprint.is_file():
                raise UnavailableCircuitData(f"Accepted {component.reference} footprint is not installed in this image")
    config = directory / ".home/.config/kicad/9.0"
    config.mkdir(parents=True, exist_ok=True)
    hashes = {}
    for kind, paths in installed.items():
        if len(paths) > 2000:
            raise UnavailableCircuitData("Installed library table exceeds its qualified bound")
        table = "sym_lib_table" if kind == "symbol" else "fp_lib_table"
        rows = []
        for path in paths:
            rows.append(f"(lib (name {json.dumps(path.stem)})(type KiCad)"
                        f"(uri {json.dumps(str(path))})(options \"\")(descr \"Operator-installed KiCad library\"))")
        content = f"({table}\n" + "\n".join(rows) + "\n)\n"
        destination = config / ("sym-lib-table" if kind == "symbol" else "fp-lib-table")
        destination.write_text(content)
        hashes[kind + "TableSha256"] = hashlib.sha256(content.encode()).hexdigest()
    return {**hashes, "source": "immutable_image", "symbolLibraries": len(installed["symbol"]),
            "footprintLibraries": len(installed["footprint"])}


class CircuitComponent(WireModel):
    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
    reference: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,30}[0-9]$")
    symbol: str = Field(min_length=3, max_length=200)
    value: str = Field(min_length=1, max_length=200)
    footprint: str = Field(min_length=3, max_length=200)
    load_ids: list[str] = Field(default_factory=list, max_length=128)
    pins: dict[str, str | None] = Field(min_length=1, max_length=256)

    @field_validator("symbol", "value", "footprint")
    @classmethod
    def literal_identity(cls, value):
        if value != value.strip() or any(ord(char) < 32 for char in value):
            raise ValueError("Component identity must be a nonblank literal without control characters")
        return value

    @model_validator(mode="after")
    def valid_pins(self):
        if ":" not in self.symbol or ":" not in self.footprint:
            raise ValueError("Symbol and footprint require full library:item identifiers")
        if len(set(self.load_ids)) != len(self.load_ids):
            raise ValueError("Load bindings must be unique")
        for pin, net in self.pins.items():
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+-]{0,39}", pin):
                raise ValueError("Pin numbers must be bounded literal identifiers")
            if net is not None and (not net or len(net) > 200 or net != net.strip()
                                    or any(ord(char) < 32 for char in net) or net.startswith("unconnected-(")):
                raise ValueError("Use explicit bounded net labels; null denotes an isolated unused pin")
        return self


class CircuitContract(WireModel):
    schema_version: Literal["circuit-netlist-v1"]
    components: list[CircuitComponent] = Field(min_length=1, max_length=MAX_COMPONENTS)

    @model_validator(mode="after")
    def unambiguous_components(self):
        for key in ("id", "reference"):
            if len({getattr(component, key) for component in self.components}) != len(self.components):
                raise ValueError(f"Circuit component {key} values must be unique")
        if sum(len(component.pins) for component in self.components) > MAX_PINS:
            raise ValueError("Circuit exceeds the qualified pin ceiling")
        if not any(net is not None for component in self.components for net in component.pins.values()):
            raise ValueError("An entirely disconnected circuit is not a qualified design")
        return self


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def validate_circuit_contract(interfaces):
    """Structural/coverage check for coordination and interface acceptance callers."""
    details = {"required": True, "scopeExcluded": False, "qualification": "circuit-netlist-v1"}
    if not isinstance(interfaces, dict):
        return CheckResult(name="electronics_contract", status="UNKNOWN", details={**details,
            "reason": "Accepted interfaces must be an object with a complete circuit contract"})
    circuit = interfaces.get("circuit")
    if not isinstance(circuit, dict) or circuit.get("schemaVersion") != "circuit-netlist-v1":
        return CheckResult(name="electronics_contract", status="UNKNOWN", details={**details,
            "reason": "Accept a complete interfaces.circuit circuit-netlist-v1 pin/net contract before engineering"})
    try:
        contract = CircuitContract.model_validate(circuit)
        power = interfaces.get("power")
        if not isinstance(power, dict):
            raise ValueError("Accepted power interface must be an object with stable load IDs")
        loads = power.get("loads")
        if not isinstance(loads, list) or not loads or any(
                not isinstance(load, dict) or not isinstance(load.get("id"), str) or not load["id"] for load in loads):
            raise ValueError("Accepted power.loads need stable IDs for circuit coverage")
        load_ids = [load["id"] for load in loads]
        if len(set(load_ids)) != len(load_ids):
            raise ValueError("Accepted power load IDs are ambiguous")
        bindings = {load for component in contract.components for load in component.load_ids}
        if bindings != set(load_ids):
            raise ValueError(f"Circuit load coverage differs: missing={sorted(set(load_ids) - bindings)}, "
                             f"unknown={sorted(bindings - set(load_ids))}")
    except (ValidationError, ValueError, TypeError) as exc:
        return CheckResult(name="electronics_contract", status="FAIL", details={**details, "reason": str(exc)[:2000]})
    return CheckResult(name="electronics_contract", status="PASS", details={**details,
        "componentCount": len(contract.components), "pinCount": sum(len(c.pins) for c in contract.components),
        "coveredLoadIds": sorted(bindings), "circuitContractSha256": _hash(circuit)})


class UnavailableCircuitData(ValueError):
    pass


def _schematic_snapshot(raw):
    if (not isinstance(raw, bytes) or not raw or len(raw) > MAX_EXPORT_BYTES
            or any(marker in raw.upper() for marker in (b"<!DOCTYPE", b"<!ENTITY"))):
        raise ValueError("Independent schematic netlist is empty, oversized, or contains a document declaration")
    root = ET.fromstring(raw)
    if root.tag != "export" or root.get("version") not in {"D", "E"}:
        raise UnavailableCircuitData("Only KiCad XML netlist versions D/E are qualified")
    libraries = {}
    for part in root.findall("./libparts/libpart"):
        key = (part.get("lib"), part.get("part"))
        pins = [pin.get("num") for pin in part.findall("./pins/pin")]
        if key in libraries or not pins or None in pins or len(pins) != len(set(pins)):
            raise ValueError("Schematic library pin definitions are missing or ambiguous")
        libraries[key] = set(pins)
    components, virtual = {}, set()
    for item in root.findall("./components/comp"):
        ref = item.get("ref")
        source = item.find("libsource")
        if not ref or source is None:
            raise ValueError("Schematic component reference or library identity is absent")
        if ref.startswith("#") and source.get("lib") == "power" and not item.findtext("footprint"):
            virtual.add(ref)
            continue
        key = (source.get("lib"), source.get("part"))
        if key not in libraries:
            raise UnavailableCircuitData(f"Complete exported symbol pins are unavailable for {ref}")
        if ref in components or len(components) >= MAX_COMPONENTS:
            raise ValueError("Schematic references are duplicated or exceed the qualified ceiling")
        components[ref] = {"value": item.findtext("value", ""), "footprint": item.findtext("footprint", ""),
                           "symbol": ":".join(key), "pins": {pin: None for pin in libraries[key]}}
    seen, memberships = set(), defaultdict(set)
    for net in root.findall("./nets/net"):
        name = net.get("name", "")
        nodes = [(node.get("ref"), node.get("pin")) for node in net.findall("node")
                 if node.get("ref") not in virtual]
        if not nodes:
            continue
        isolated = name.startswith("unconnected-(") or not name
        if isolated and len(nodes) != 1:
            raise ValueError("An unnamed/unconnected schematic net joins multiple pins")
        for ref, pin in nodes:
            if ref not in components or pin not in components[ref]["pins"] or (ref, pin) in seen:
                raise ValueError("Schematic node is missing, duplicated, or bound to an unknown pin")
            seen.add((ref, pin))
            components[ref]["pins"][pin] = None if isolated else name
            if not isolated:
                memberships[name].add((ref, pin))
    if sum(len(component["pins"]) for component in components.values()) > MAX_PINS:
        raise ValueError("Schematic exceeds the qualified pin ceiling")
    return components, memberships


def _board_snapshot(snapshot):
    if not isinstance(snapshot, dict) or snapshot.get("schemaVersion") != "kicad-board-connectivity-v1":
        raise UnavailableCircuitData("Fresh independent PCB pad inspection is unavailable")
    if not str(snapshot.get("kicadVersion", "")).startswith("9.0."):
        raise UnavailableCircuitData("PCB pad inspection is qualified for KiCad 9.0.x only")
    items = snapshot.get("components")
    if not isinstance(items, list) or len(items) > MAX_COMPONENTS:
        raise ValueError("PCB component closure is invalid or exceeds the qualified ceiling")
    components, memberships, total = {}, defaultdict(set), 0
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("PCB inspection components must be objects")
        ref = item.get("reference")
        if item.get("netTie") or item.get("dnp"):
            raise UnavailableCircuitData("Net ties and do-not-populate footprints need a separate qualification")
        if not ref or ref in components or not isinstance(item.get("pads"), list):
            raise ValueError("PCB component references or pads are missing/ambiguous")
        pins = {}
        for pad in item["pads"]:
            if not isinstance(pad, dict):
                raise ValueError("PCB inspection pads must be objects")
            number, name = pad.get("number"), pad.get("net", "")
            if not isinstance(number, str) or not isinstance(name, str):
                raise ValueError("PCB pad binding must use literal pin/net names")
            # Numberless mechanical holes are allowed; conductive anonymous pads are not.
            if not number and pad.get("mechanical") is True and not name:
                continue
            if not number:
                raise ValueError("Conductive PCB pad has no physical pin number")
            if number in pins:
                if pins[number] != (name or None):
                    raise ValueError("Duplicated PCB pad number is connected to conflicting nets")
                raise UnavailableCircuitData("Stacked pads are outside v1 qualification")
            pins[number] = name or None
            if name:
                memberships[name].add((ref, number))
        total += len(pins)
        components[ref] = {"value": item.get("value"), "footprint": item.get("footprint"), "pins": pins}
    if total > MAX_PINS:
        raise ValueError("PCB exceeds the qualified pin ceiling")
    # Treat KiCad's isolated generated NC nets consistently, never collapse shorts.
    for name, members in list(memberships.items()):
        if name.startswith("unconnected-("):
            if len(members) != 1:
                raise ValueError("An unconnected PCB net shorts multiple pins")
            ref, pin = next(iter(members))
            components[ref]["pins"][pin] = None
            del memberships[name]
    return components, memberships


def validate_circuit_correspondence(interfaces, netlist_xml, board_snapshot, requirements=None, evidence=None):
    contract_check = validate_circuit_contract(interfaces)
    try:
        accepted_hash = _hash({"requirements": requirements or {}, "interfaces": interfaces})
    except (ValueError, TypeError, RecursionError):
        return CheckResult(name="electronics_correspondence", status="UNKNOWN", details={**contract_check.details,
            "reason": "Accepted contract must contain finite JSON-compatible values"})
    details = {**contract_check.details, "acceptedContractHash": accepted_hash, "evidence": evidence or {},
        "coverage": "Accepted component identities and complete symbol-pin/schematic-net/PCB-pad correspondence",
        "limitations": ["Not analog/RF/thermal/EMC simulation, footprint geometry qualification, or physical evidence",
                        "Virtual power flags are ignored; stacked pads, net ties and unlisted physical components are unsupported"]}
    if contract_check.status != "PASS":
        return CheckResult(name="electronics_correspondence", status=contract_check.status, details=details)
    if not isinstance(evidence, dict) or any(not re.fullmatch(r"[a-f0-9]{64}", str(evidence.get(key, "")))
                                            for key in ("schematicSha256", "pcbSha256")):
        return CheckResult(name="electronics_correspondence", status="UNKNOWN", details={**details,
            "reason": "Independent schematic/PCB content-hash evidence is required"})
    try:
        expected = {component.reference: component for component in CircuitContract.model_validate(
            interfaces["circuit"]).components}
        schematic, schematic_nets = _schematic_snapshot(netlist_xml)
        board, board_nets = _board_snapshot(board_snapshot)
        if board_snapshot.get("pcbSha256") != evidence["pcbSha256"]:
            raise ValueError("PCB inspection hash does not match the current candidate artifact")
        failures = []
        for label, actual in (("schematic", schematic), ("PCB", board)):
            if set(actual) != set(expected):
                failures.append(f"{label} component closure differs: missing={sorted(set(expected) - set(actual))}, "
                                f"unexpected={sorted(set(actual) - set(expected))}")
            for ref in sorted(set(expected) & set(actual)):
                part, measured = expected[ref], actual[ref]
                for key in (("symbol", "value", "footprint") if label == "schematic" else ("value", "footprint")):
                    if measured[key] != getattr(part, key):
                        failures.append(f"{label} {ref}: wrong {key}")
                if measured["pins"] != part.pins:
                    failures.append(f"{label} {ref}: missing, extra, shorted, or wrongly connected pins")
        if schematic_nets != board_nets:
            failures.append("Schematic and PCB net memberships differ")
        details.update(netlistSha256=hashlib.sha256(netlist_xml).hexdigest(),
                       boardConnectivitySha256=_hash(board_snapshot),
                       netCount=len(schematic_nets), failures=failures[:100])
        return CheckResult(name="electronics_correspondence", status="FAIL" if failures else "PASS", details=details)
    except UnavailableCircuitData as exc:
        return CheckResult(name="electronics_correspondence", status="UNKNOWN", details={**details, "reason": str(exc)})
    except (ET.ParseError, ValueError, KeyError, TypeError) as exc:
        return CheckResult(name="electronics_correspondence", status="FAIL", details={**details, "reason": str(exc)[:2000]})


# Fixed trusted code, run in the isolated validator's system Python, never supplied by a model.
PCB_CONNECTIVITY_SCRIPT = r'''
import hashlib, json, sys
import pcbnew
path = sys.argv[1]
board = pcbnew.LoadBoard(path)
parts, total = [], 0
for footprint in board.GetFootprints():
    pads = []
    for pad in footprint.Pads():
        pads.append({'number': str(pad.GetNumber()), 'net': str(pad.GetNetname()),
                     'mechanical': pad.GetAttribute() == pcbnew.PAD_ATTRIB_NPTH})
    total += len(pads)
    if len(parts) >= 128 or total > 2048:
        raise ValueError('PCB exceeds qualified component/pad limits')
    parts.append({'reference': str(footprint.GetReference()), 'value': str(footprint.GetValue()),
                  'footprint': str(footprint.GetFPIDAsString()), 'pads': pads,
                  'netTie':bool(footprint.IsNetTie()), 'dnp':bool(footprint.IsDNP())})
print(json.dumps({'schemaVersion':'kicad-board-connectivity-v1', 'kicadVersion':pcbnew.GetBuildVersion(),
                  'pcbSha256':hashlib.sha256(open(path,'rb').read()).hexdigest(), 'components':parts},
                 separators=(',',':')))
'''
