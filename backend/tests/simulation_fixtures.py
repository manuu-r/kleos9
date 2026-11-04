"""Shared simulation fixtures: an accepted divider circuit and closed-form solver output."""
import copy
import math

from kleos.validation.simulation.plan import RESISTOR_TOLERANCE, STEP_SECONDS, plan_simulation
from kleos.validation.simulation.results import SAMPLES, DeckOutput

CIRCUIT = {'references': [
    {'reference': 'J1', 'value': 'Supply', 'footprint': 'Fixture:Conn', 'pins': {'1': 'VIN', '2': 'GND'}},
    {'reference': 'R1', 'value': '1k', 'footprint': 'Fixture:R', 'pins': {'1': 'VIN', '2': 'OUT'}},
    {'reference': 'R2', 'value': '2k', 'footprint': 'Fixture:R', 'pins': {'1': 'OUT', '2': 'GND'}},
    {'reference': 'C1', 'value': '100n', 'footprint': 'Fixture:C', 'pins': {'1': 'OUT', '2': 'GND'}},
    {'reference': 'D1', 'value': 'kleos-red-led-v1', 'footprint': 'Fixture:LED', 'pins': {'1': 'OUT', '2': 'GND'}}]}
SIMULATION = {'supplyVoltageV': 3.3, 'maxCurrentMa': 50,
    'source': {'componentId': 'J1', 'positivePin': '1', 'negativePin': '2'},
    'loads': [{'id': 'load', 'minVoltageV': 1.8, 'maxVoltageV': 3.4, 'currentMa': 2,
               'probe': {'componentId': 'R2', 'positivePin': '1', 'negativePin': '2'}}],
    'components': [{'componentId': 'R1', 'kind': 'R', 'pins': ['1', '2']},
                   {'componentId': 'R2', 'kind': 'R', 'pins': ['1', '2']},
                   {'componentId': 'C1', 'kind': 'C', 'pins': ['1', '2']},
                   {'componentId': 'D1', 'kind': 'LED', 'pins': ['1', '2']}]}
# Divider without the LED: closed-form reference for logic tests (no solver involved).
RC_CIRCUIT = {'references': [r for r in CIRCUIT['references'] if r['reference'] != 'D1']}
RC_SIMULATION = {**SIMULATION, 'components': [c for c in SIMULATION['components'] if c['componentId'] != 'D1']}


def variant(circuit=None, simulation=None):
    return copy.deepcopy(circuit or CIRCUIT), copy.deepcopy(simulation or SIMULATION)


def analytic_output(plan, index, *, led_ma=1.0):
    """Closed-form RC divider response for one corner, in the exact wrdata text layout."""
    meta, corner = plan.metadata, plan.decks[index].corner
    by_id = {p['componentId']: p for p in meta['passives']}
    scale = {pid: 1 + corner[slot] * by_id[pid]['toleranceFraction'] for slot, pid in enumerate(meta['passiveIds'])}
    r1, r2, c1 = (by_id[k]['nominalValue'] * scale[k] for k in ('R1', 'R2', 'C1'))
    vs, out_node, vin_node = 3.3, meta['nodes']['OUT'], meta['nodes']['VIN']
    final, tau = vs * r2 / (r1 + r2), (r1 * r2 / (r1 + r2)) * c1
    names, led = meta['vectors'], [d for d in meta['devices']]

    def row(t, op=False):
        v_out = final if op else final * (1 - math.exp(-t / tau))
        values = {f'v({out_node})': v_out, f'v({vin_node})': vs, 'i(v1)': -(vs - v_out) / r1}
        for device in led:
            values[device['currentVector']] = led_ma * 1e-3
        return [values[name] for name in names]
    header = ' '.join(names)
    op = f' V(x) {header}\n' + ' '.join(['0'] + [f'{v:.8e}' for v in row(0, True)]) + '\n'
    lines = [' time ' + header]
    for i in range(SAMPLES):
        t = i * STEP_SECONDS
        lines.append(' '.join([f'{t:.8e}'] + [f'{v:.8e}' for v in row(t)]))
    return DeckOutput(op, '\n'.join(lines) + '\n'), (final, tau, scale)


def ready_plan(circuit=None, simulation=None):
    plan = plan_simulation(*variant(circuit, simulation))
    assert plan.ready, plan.reasons
    return plan


__all__ = ['CIRCUIT', 'SIMULATION', 'RC_CIRCUIT', 'RC_SIMULATION', 'RESISTOR_TOLERANCE', 'analytic_output',
           'ready_plan', 'variant']
