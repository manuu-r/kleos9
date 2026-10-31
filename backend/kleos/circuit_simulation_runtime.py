"""Independent bounded ngspice evaluation; execution is injected by the sandbox."""

import hashlib
import json
import math
import time

from .circuit_simulation import (
    DURATION_SECONDS,
    MAX_OUTPUT_BYTES,
    MAX_RAW_SAMPLES,
    TIMEOUT_SECONDS,
    canonical_sha256,
    plan_simulation,
)
from .contracts import CheckResult

WAVEFORM_NAME = "circuit-waveform.json"
MAX_WAVEFORM_BYTES = 4_000_000
MAX_VIEW_SAMPLES = 501
CHECK_NAMES = ("circuit_dc_rails", "circuit_startup", "circuit_led_current", "circuit_total_current")


def advisory(name, status, **details):
    return CheckResult(name=name, status=status, evidence_kind="digital",
                       details={**details, "required": False, "scopeExcluded": True,
                                "advisory": True, "physicalAcceptance": False})


def parse_table(path, vectors, *, transient):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_OUTPUT_BYTES:
        raise ValueError("ngspice output is absent, symlinked or oversized")
    raw = path.read_bytes()
    lines = raw.decode("ascii").splitlines()
    if not 2 <= len(lines) <= MAX_RAW_SAMPLES + 1:
        raise ValueError("ngspice sample ceiling exceeded or output is empty")
    # wrdata headers use v1#branch rather than i(V1) on some supported versions.
    expected = [v.lower().replace("i(", "").replace(")", "#branch") if v.startswith("i(") else v
                for v in vectors]
    header = lines[0].lower().split()
    if (header[1:] not in (expected, [v.lower() for v in vectors])
            or len(header) != len(vectors) + 1):
        raise ValueError("Unexpected ngspice vector header")
    if transient and header[0] != "time":
        raise ValueError("Transient scale is not time")
    rows = []
    for line in lines[1:]:
        row = [float(v) for v in line.split()]
        if len(row) != len(vectors) + 1 or any(not math.isfinite(v) or abs(v) > 1e12 for v in row):
            raise ValueError("Invalid or nonfinite ngspice measurement")
        if transient and (not 0 <= row[0] <= DURATION_SECONDS + 1e-8
                          or rows and row[0] <= rows[-1][0]):
            raise ValueError("Transient timestamps are not strictly ordered and bounded")
        rows.append(row)
    if transient and (len(rows) < 3 or rows[0][0] > 1e-5 or abs(rows[-1][0] - DURATION_SECONDS) > 1e-8):
        raise ValueError("Transient output does not cover the observation window")
    if not transient and len(rows) != 1:
        raise ValueError("Operating point must have exactly one row")
    return rows, hashlib.sha256(raw).hexdigest()


def _evaluate(metadata, runs):
    vectors = metadata["vectors"]

    def voltage(row, node):
        return 0 if node == 0 else row[1 + vectors.index(f"v({node})")]

    rails, startup, currents = [], [], []
    peaks = []
    for run in runs:
        op, raw = run["op"][0], run["raw"]
        peaks.append(max([-1000 * row[1 + vectors.index("i(V1)")] for row in [op, *raw]]))
        for ident, probe in metadata["loads"].items():
            def measured(row):
                return voltage(row, probe["positiveNode"]) - voltage(row, probe["negativeNode"])
            dc = measured(op)
            rails.append({"loadId": ident, "corner": run["corner"], "voltageV": dc,
                          "minVoltageV": probe["minVoltageV"], "maxVoltageV": probe["maxVoltageV"]})
            tolerance = max(abs(dc) * 0.02, 0.001)
            settled = all(abs(measured(row) - dc) <= tolerance for row in raw if row[0] >= 0.095)
            rise = next((row[0] for row in raw if dc > 0 and measured(row) >= dc * 0.9), None)
            startup.append({"loadId": ident, "corner": run["corner"], "settled": settled,
                            "rise90Seconds": rise, "finalVoltageV": measured(raw[-1])})
        for device in metadata["devices"]:
            if device["safeCurrentMa"] is None:
                continue
            column = 1 + vectors.index(device["currentVector"])
            values = [row[column] * 1000 for row in [op, *raw]]
            currents.append({"componentId": device["componentId"], "corner": run["corner"],
                             "peakCurrentMa": max(values), "minCurrentMa": min(values),
                             "limitMa": device["safeCurrentMa"]})
    return [
        advisory("circuit_dc_rails", "PASS" if all(r["minVoltageV"] <= r["voltageV"] <= r["maxVoltageV"]
                                                     for r in rails) else "FAIL", measurements=rails),
        advisory("circuit_startup", "PASS" if all(s["settled"] and s["rise90Seconds"] is not None
                                                   for s in startup) else "UNKNOWN",
                 measurements=startup, reason="90% rise and final 5 ms within 2%/1 mV of DC; unresolved windows are UNKNOWN"),
        advisory("circuit_led_current", "NOT_APPLICABLE" if not currents else
                 "PASS" if all(-0.001 <= c["minCurrentMa"] and c["peakCurrentMa"] <= c["limitMa"]
                               for c in currents) else "FAIL", measurements=currents,
                 reason="Illustrative LED forward-current policy only; reverse-voltage safety is unqualified"),
        advisory("circuit_total_current", "PASS" if max(peaks) <= metadata["maxCurrentMa"] else "FAIL",
                 peakCurrentMa=max(peaks), limitMa=metadata["maxCurrentMa"],
                 reason="Maximum DC and raw transient supply draw across enumerated corners"),
    ]


def _waveforms(metadata, runs):
    base = runs[0]["linear"]
    if len(base) != 1001 or any(len(r["linear"]) != len(base) for r in runs):
        raise ValueError("Unexpected linearized sample grid")
    if any(abs(row[0] - i * 0.0001) > 1e-8 for run in runs for i, row in enumerate(run["linear"])):
        raise ValueError("Tolerance runs have incompatible time grids")
    indices = list(range(0, len(base), 2))
    times = [base[i][0] for i in indices]
    series = []
    for name, probe in metadata["loads"].items():
        series.append((name, "V", probe["positiveNode"], probe["negativeNode"], None,
                       {"min": probe["minVoltageV"], "max": probe["maxVoltageV"]}))
    # Supply current and LED sense currents can be selected alongside rails.
    series.append(("Supply current", "mA", None, None, ("i(V1)", -1000),
                   {"max": metadata["maxCurrentMa"]}))
    for device in metadata["devices"]:
        series.append((device["componentId"] + " current", "mA", None, None,
                       (device["currentVector"], 1000), {"max": device["safeCurrentMa"]}))
    # Also expose every named net, independent of whether a load is bound there.
    for name, node in metadata["nodes"].items():
        if node:
            series.append(("Net " + name, "V", node, 0, None, {}))
    result = []
    for name, unit, positive, negative, current, limits in series:
        def value(row):
            if current:
                return row[1 + metadata["vectors"].index(current[0])] * current[1]
            def at(node):
                return 0 if node == 0 else row[1 + metadata["vectors"].index(f"v({node})")]
            return at(positive) - at(negative)
        low = [min(value(run["linear"][i]) for run in runs) for i in indices]
        high = [max(value(run["linear"][i]) for run in runs) for i in indices]
        # Bins preserve raw peaks as well as all endpoint-corner linear samples.
        for run in runs:
            for row in run["raw"]:
                bucket = max(0, min(len(indices) - 1, int(row[0] / 0.0002 + 0.5)))
                measured = value(row)
                low[bucket] = min(low[bucket], measured)
                high[bucket] = max(high[bucket], measured)
        result.append({"name": name, "unit": unit, "values": [value(base[i]) for i in indices],
                       "low": low, "high": high, "limits": {k: v for k, v in limits.items() if v is not None}})
    return {"timeSeconds": times, "signals": result, "downsampling": "200 us bins preserving raw/corner extrema"}


def simulate_circuit(interfaces, directory, execute):
    from .native_runner import UnsupportedTool

    plan = plan_simulation(interfaces)
    payload = {"schemaVersion": "circuit-waveform-v1", "metadata": plan.metadata,
               "evidenceKind": "digital", "physicalAcceptance": False, "decks": [], "toolVersion": None,
               "waveform": {"timeSeconds": [], "signals": []}}
    checks = [advisory(name, "UNKNOWN", reason="; ".join(plan.reasons)) for name in CHECK_NAMES]
    if plan.status == "READY":
        try:
            directory.mkdir(mode=0o755, parents=True, exist_ok=False)
            started = time.monotonic()
            code, version = execute(["ngspice", "--version"], directory, TIMEOUT_SECONDS)
            if code:
                raise RuntimeError("ngspice version command failed")
            if not ("ngspice-39" in version or "ngspice-47" in version):
                raise UnsupportedTool("ngspice version is outside the tested 39/47 parser policy")
            payload["toolVersion"] = next(line.strip() for line in version.splitlines() if "ngspice-" in line)
            runs = []
            for index, deck in enumerate(plan.decks):
                if time.monotonic() - started + TIMEOUT_SECONDS > plan.metadata["bounds"]["maxTotalSeconds"]:
                    raise TimeoutError("Circuit scenario deadline exceeded")
                work = directory / str(index)
                work.mkdir(mode=0o755)
                (work / "circuit.cir").write_text(deck.source, encoding="ascii")
                code, logs = execute(["ngspice", "-n", "-b", "circuit.cir"], work, TIMEOUT_SECONDS)
                if code or "error" in logs.lower() or "failed" in logs.lower():
                    raise RuntimeError("ngspice failed; no partial results are authoritative")
                tables, hashes = {}, {}
                for key, filename in (("op", "operating.dat"), ("raw", "transient-raw.dat"),
                                      ("linear", "transient.dat")):
                    tables[key], hashes[key] = parse_table(work / filename, plan.metadata["vectors"], transient=key != "op")
                runs.append({**tables, "corner": list(deck.corner)})
                payload["decks"].append({"corner": list(deck.corner), "sha256": deck.sha256, "outputSha256": hashes})
            checks = _evaluate(plan.metadata, runs)
            payload["waveform"] = _waveforms(plan.metadata, runs)
        except (UnsupportedTool, FileNotFoundError) as exc:
            checks = [advisory(name, "UNKNOWN", reason=str(exc)[:1000]) for name in CHECK_NAMES]
        except (OSError, ValueError, RuntimeError, TimeoutError, StopIteration) as exc:
            checks = [advisory(name, "ERROR", reason=str(exc)[:1000]) for name in CHECK_NAMES]
    for check in checks:
        check.details.update({"limitations": plan.metadata["limitations"],
                              "policyVersion": plan.metadata["policyVersion"]})
    payload["checks"] = [c.model_dump(mode="json", by_alias=True) for c in checks]
    payload["resultsSha256"] = canonical_sha256(payload)
    for check in checks:
        check.details["resultsSha256"] = payload["resultsSha256"]
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if len(body.encode()) > MAX_WAVEFORM_BYTES:
        raise ValueError("Waveform artifact exceeds its storage ceiling")
    return checks, body
