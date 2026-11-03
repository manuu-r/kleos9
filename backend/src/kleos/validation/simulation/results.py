"""Bounded ngspice output parsing, advisory checks and the `circuit-waveform-v1` envelope.

Checks use raw output before downsampling. Malformed, partial, duplicated, non-finite or oversized
data never produces a PASS: it raises `SimulationDataError` and the caller records ERROR. Startup
that does not settle stays UNKNOWN because no startup deadline is defined by the contract.
"""
import hashlib
import math
from dataclasses import dataclass

from kleos.projects.store import fingerprint
from kleos.validation.simulation.plan import (
    POLICY_VERSION,
    RAMP_SECONDS,
    STEP_SECONDS,
    WINDOW_SECONDS,
    Plan,
)

MAX_FILE_BYTES = 2_000_000
SAMPLES = round(WINDOW_SECONDS / STEP_SECONDS) + 1
SETTLE_WINDOW_SECONDS, SETTLE_FRACTION, SETTLE_FLOOR_V, RISE_FRACTION = .005, .02, .001, .9
# Exact reported versions only. Debian bookworm's ngspice 39.3 reports itself as ngspice-39.
DEFAULT_VERSIONS = ('ngspice-39', 'ngspice-47')


class SimulationDataError(ValueError):
    """Solver output is unusable evidence (ERROR, never PASS)."""


@dataclass(frozen=True)
class DeckOutput:
    op_text: str
    tran_text: str


@dataclass(frozen=True)
class DeckData:
    corner: tuple
    op: dict
    time: list
    tran: dict


def tool_version(banner, allowed=DEFAULT_VERSIONS):
    """Return the exact reported version, or None when this solver build is not a trusted pin."""
    for line in str(banner).splitlines():
        if 'ngspice-' in line:
            version = line[line.index('ngspice-'):].split()[0]
            return version if version in tuple(allowed) else None
    return None


def _table(text, names, *, scale):
    if not isinstance(text, str) or len(text.encode()) > MAX_FILE_BYTES:
        raise SimulationDataError('Solver output is missing or exceeds its size bound')
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines or len(lines) > SAMPLES + 1:
        raise SimulationDataError('Solver output has an unexpected number of rows')
    header = lines[0].split()
    first = 'time' if scale else None
    if len(header) != len(names) + 1 or [name.lower() for name in header[1:]] != [n.lower() for n in names] or (
            scale and header[0].lower() != first):
        raise SimulationDataError('Solver output header does not match the requested vectors')
    rows = []
    for line in lines[1:]:
        parts = line.split()
        if len(parts) != len(header):
            raise SimulationDataError('Solver output row is malformed')
        try:
            values = [float(part) for part in parts]
        except ValueError:
            raise SimulationDataError('Solver output contains a non-numeric value') from None
        if any(not math.isfinite(v) or abs(v) > 1e12 for v in values):
            raise SimulationDataError('Solver output contains a non-finite or absurd value')
        rows.append(values)
    return rows


def parse_deck(corner, output: DeckOutput, vectors):
    op_rows = _table(output.op_text, vectors, scale=False)
    if len(op_rows) != 1:
        raise SimulationDataError('Operating point must contain exactly one row')
    rows = _table(output.tran_text, vectors, scale=True)
    if len(rows) != SAMPLES:
        raise SimulationDataError('Transient window is partial or has the wrong sample count')
    time = [row[0] for row in rows]
    if abs(time[0]) > 1e-12 or any(abs((b - a) - STEP_SECONDS) > STEP_SECONDS * 1e-3 for a, b in zip(time, time[1:])):
        raise SimulationDataError('Transient time axis is not the fixed linear grid')
    return DeckData(tuple(corner), dict(zip(vectors, op_rows[0][1:])),
                    time, {name: [row[i + 1] for row in rows] for i, name in enumerate(vectors)})


def _v(data, node, series):
    if node == 0:
        return 0.0 if series is None else [0.0] * len(data.time)
    key = f'v({node})'
    return data.op[key] if series is None else data.tran[key]


def _load_series(data, load):
    plus, minus = load['positiveNode'], load['negativeNode']
    return [a - b for a, b in zip(_v(data, plus, True), _v(data, minus, True))]


def _load_dc(data, load):
    return _v(data, load['positiveNode'], None) - _v(data, load['negativeNode'], None)


def _check(name, status, details, limitations):
    return {'name': name, 'status': status, 'evidenceKind': 'digital', 'details': {
        'advisory': True, 'required': False, 'scopeExcluded': True, 'physicalAcceptance': False,
        'policyVersion': POLICY_VERSION, 'limitations': limitations, **details}}


def evaluate(plan: Plan, runs):
    meta, limits = plan.metadata, plan.metadata['limitations']
    rails, startup = [], []
    for data in runs:
        for load_id, load in meta['loads'].items():
            volts = _load_dc(data, load)
            rails.append({'loadId': load_id, 'corner': list(data.corner), 'voltageV': volts,
                          'minVoltageV': load['minVoltageV'], 'maxVoltageV': load['maxVoltageV']})
            series, tolerance = _load_series(data, load), max(SETTLE_FRACTION * abs(volts), SETTLE_FLOOR_V)
            tail = [value for t, value in zip(data.time, series) if t >= WINDOW_SECONDS - SETTLE_WINDOW_SECONDS - 1e-12]
            settled = all(abs(value - volts) <= tolerance for value in tail)
            target = RISE_FRACTION * volts
            rise = None
            if abs(volts) >= SETTLE_FLOOR_V:
                rise = next((t for t, value in zip(data.time, series)
                             if (value >= target if volts > 0 else value <= target)), None)
            startup.append({'loadId': load_id, 'corner': list(data.corner), 'finalVoltageV': series[-1],
                            'rise90Seconds': rise, 'settled': settled})
    in_range = all(m['minVoltageV'] <= m['voltageV'] <= m['maxVoltageV'] for m in rails)
    checks = [_check('circuit_dc_rails', 'PASS' if in_range else 'FAIL', {'measurements': rails,
        'reason': 'DC operating point of every load across nominal and endpoint corners'}, limits)]
    resolved = all(m['settled'] and m['rise90Seconds'] is not None for m in startup)
    checks.append(_check('circuit_startup', 'PASS' if resolved else 'UNKNOWN', {'measurements': startup,
        'reason': '90% rise and final 5 ms within 2%/1 mV of DC; unresolved windows are UNKNOWN'}, limits))
    rated = [d for d in meta['devices'] if d['safeCurrentMa'] is not None]
    led = []
    for device in rated:
        for data in runs:
            samples = data.tran[device['currentVector']] + [data.op[device['currentVector']]]
            led.append({'componentId': device['componentId'], 'corner': list(data.corner),
                        'limitMa': device['safeCurrentMa'], 'peakCurrentMa': max(samples) * 1e3,
                        'minCurrentMa': min(samples) * 1e3})
    if led:
        status = 'PASS' if all(m['peakCurrentMa'] <= m['limitMa'] for m in led) else 'FAIL'
        reason = 'Illustrative LED forward-current policy only; reverse-voltage safety is unqualified'
    else:
        status, reason = 'NOT_APPLICABLE', 'No rated LED is bound in this circuit.'
    checks.append(_check('circuit_led_current', status, {'measurements': led, 'reason': reason}, limits))
    peak = max(max(-v for v in data.tran['i(v1)'] + [data.op['i(v1)']]) for data in runs) * 1e3
    limit = meta['maxCurrentMa']
    checks.append(_check('circuit_total_current', 'PASS' if peak <= limit else 'FAIL', {
        'peakCurrentMa': peak, 'limitMa': limit,
        'reason': 'Maximum DC and raw transient supply draw across enumerated corners'}, limits))
    return checks


def _bins(series_by_corner, nominal):
    """<=501 points: nominal value per 200 us bin plus raw/corner extrema within the bin."""
    count = (SAMPLES - 1) // 2 + 1
    values, low, high = [], [], []
    for k in range(count):
        start, stop = 2 * k, min(2 * k + 2, SAMPLES)
        window = [v for series in series_by_corner for v in series[start:stop]]
        values.append(nominal[start])
        low.append(min(window))
        high.append(max(window))
    return values, low, high


def build_result(plan: Plan, runs, outputs, version, duration_seconds):
    meta = plan.metadata
    signals = []

    def add(name, unit, per_corner, limits):
        values, low, high = _bins(per_corner, per_corner[0])
        signals.append({'name': name, 'unit': unit, 'values': values, 'low': low, 'high': high, 'limits': limits})

    for load_id, load in meta['loads'].items():
        add(load_id, 'V', [_load_series(d, load) for d in runs], {'min': load['minVoltageV'], 'max': load['maxVoltageV']})
    add('Supply current', 'mA', [[-v * 1e3 for v in d.tran['i(v1)']] for d in runs], {'max': meta['maxCurrentMa']})
    for device in meta['devices']:
        add(f"{device['componentId']} current", 'mA', [[v * 1e3 for v in d.tran[device['currentVector']]] for d in runs],
            {'max': device['safeCurrentMa']} if device['safeCurrentMa'] is not None else {})
    for name, node in meta['nodes'].items():
        if node:
            add(f'Net {name}', 'V', [d.tran[f'v({node})'] for d in runs], {})
    result = {
        'schemaVersion': 'circuit-waveform-v1', 'evidenceKind': 'digital', 'physicalAcceptance': False,
        'toolVersion': version, 'checks': evaluate(plan, runs),
        'decks': [{'corner': list(deck.corner), 'sha256': deck.sha256,
                   'outputSha256': {'op': hashlib.sha256(out.op_text.encode()).hexdigest(),
                                    'linear': hashlib.sha256(out.tran_text.encode()).hexdigest()}}
                  for deck, out in zip(plan.decks, outputs)],
        'metadata': {**meta, 'durationSeconds': duration_seconds, 'physicalAcceptance': False,
                     'evidenceKind': 'digital', 'rampSeconds': RAMP_SECONDS},
        'waveform': {'downsampling': '200 us bins preserving raw/corner extrema',
                     'timeSeconds': [round(2 * k * STEP_SECONDS, 9) for k in range(len(signals[0]['values']))],
                     'signals': signals}}
    result['resultsSha256'] = fingerprint({k: v for k, v in result.items() if k != 'resultsSha256'})
    return result
