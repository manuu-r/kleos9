"""Pure compiler: accepted circuit + simulation bindings -> READY decks, or UNKNOWN with reasons.

READY is not a validation PASS. Unknown or unsupported parts invalidate the whole plan; they are
never omitted so an affected check could pass. The output contains no candidate text: every deck
line is generated here from validated structured data.
"""
import hashlib
import itertools
from dataclasses import dataclass, field

from pydantic import ValidationError

from kleos.projects.store import fingerprint
from kleos.validation.simulation.contract import (
    DIODE_VALUE,
    LED_VALUE,
    SimulationContract,
    parse_value,
)

POLICY_VERSION = 'ngspice-ideal-v1'
RESISTOR_TOLERANCE, CAPACITOR_TOLERANCE = .05, .20
RAMP_SECONDS, STEP_SECONDS, WINDOW_SECONDS = 1e-6, 100e-6, .1
MAX_DECKS, MAX_NETS, MAX_RC_PARTS = 17, 32, 4
LED_SAFE_MA = 5.0
MODELS = {'LED': ('kleos_red_led_v1', 'D(IS=1e-20 N=2 RS=0)'),
          'DIODE': ('kleos_silicon_diode_v1', 'D(IS=1e-14 N=1 RS=0)')}
LIMITATIONS = ['Simulated, not measured; digital evidence only, never physical acceptance.',
    'Ideal lumped parts at 27 C: no layout, thermal, RF, parasitic or manufacturing effects.',
    'Resistor +/-5% and capacitor +/-20% endpoint combinations only; interior values are not proven.',
    'LED and diode models are illustrative image-owned selections, not manufacturer models.',
    'Reverse-voltage and surge safety are not evaluated.']


@dataclass(frozen=True)
class Deck:
    corner: tuple
    text: str
    sha256: str


@dataclass(frozen=True)
class Plan:
    status: str  # READY | UNKNOWN
    reasons: tuple = ()
    decks: tuple = ()
    metadata: dict = field(default_factory=dict)

    @property
    def ready(self):
        return self.status == 'READY'


def _unknown(*reasons):
    return Plan('UNKNOWN', tuple(reasons))


def plan_simulation(circuit, simulation) -> Plan:
    """`circuit` is the accepted circuit contract dict; `simulation` the accepted bindings dict."""
    try:
        contract = SimulationContract.model_validate(simulation)
    except ValidationError:
        return _unknown('Simulation bindings are missing or invalid.')
    references = circuit.get('references') if isinstance(circuit, dict) else None
    if not isinstance(references, list) or not references:
        return _unknown('No accepted circuit is available.')
    pins = {item['reference']: item for item in references}
    if len(pins) != len(references) or len(pins) > 16:
        return _unknown('The circuit is too large or ambiguous for the simulation envelope (16 components).')
    classified = {item.component_id: item for item in contract.components}
    for reference in pins:
        if reference != contract.source.component_id and reference not in classified:
            return _unknown(f'Component {reference} has no explicit simulation binding.')
    for reference in classified:
        if reference not in pins:
            return _unknown(f'Bound component {reference} is not in the accepted circuit.')
    if contract.source.component_id not in pins:
        return _unknown('The supply connector is not in the accepted circuit.')

    def net(reference, pin):
        found = pins.get(reference, {}).get('pins', {}).get(pin)
        if found is None:
            raise KeyError(f'{reference} has no pin {pin}')
        return found

    try:
        supply_net, ground_net = net(contract.source.component_id, contract.source.positive_pin), net(
            contract.source.component_id, contract.source.negative_pin)
        if supply_net == ground_net:
            return _unknown('The supply terminals are shorted together.')
        elements, rc_parts, diodes = [], [], []
        for item in sorted(contract.components, key=lambda x: x.component_id):
            if item.kind == 'OPEN':
                continue
            a, b = net(item.component_id, item.pins[0]), net(item.component_id, item.pins[1])
            value_text = pins[item.component_id]['value']
            if item.kind in ('R', 'C'):
                elements.append((item.kind, item.component_id, a, b, parse_value(value_text, item.kind)))
                rc_parts.append(len(elements) - 1)
            else:
                expected = LED_VALUE if item.kind == 'LED' else DIODE_VALUE
                if value_text != expected:
                    return _unknown(f'{item.component_id} has no trusted {item.kind} model (value must be {expected}).')
                elements.append((item.kind, item.component_id, a, b, None))
                diodes.append(item)
        probes = {}
        for load in contract.loads:
            if load.probe.component_id not in classified or classified[load.probe.component_id].kind == 'OPEN':
                return _unknown(f'Load {load.id} is probed on a component that is not simulated.')
            probes[load.id] = (net(load.probe.component_id, load.probe.positive_pin),
                               net(load.probe.component_id, load.probe.negative_pin))
    except KeyError as error:
        return _unknown(str(error.args[0]))
    except ValueError as error:
        return _unknown(str(error))
    if len(rc_parts) > MAX_RC_PARTS:
        return _unknown(f'More than {MAX_RC_PARTS} resistor/capacitor parts exceed the corner envelope.')

    nets = sorted({supply_net, ground_net, *[n for e in elements for n in e[2:4]],
                   *[n for pair in probes.values() for n in pair]} - {ground_net})
    if len(nets) > MAX_NETS:
        return _unknown(f'More than {MAX_NETS} external nets exceed the simulation envelope.')
    node = {ground_net: 0, **{name: index + 1 for index, name in enumerate(nets)}}
    # Every simulated net needs a DC path to ground (through a source, resistor or diode).
    parent = {name: name for name in node}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    parent[find(supply_net)] = find(ground_net)
    for kind, _, a, b, _value in elements:
        if kind != 'C':
            parent[find(a)] = find(b)
    floating = sorted(name for name in node if find(name) != find(ground_net))
    if floating:
        return _unknown('No DC path to ground for net(s): ' + ', '.join(floating) + '.')

    vectors = [f'v({node[name]})' for name in nets] + ['i(v1)'] + [f'i(vs_{d.component_id.lower()})' for d in diodes]
    corners = [(0,) * len(rc_parts)] + [c for c in itertools.product((-1, 1), repeat=len(rc_parts))]
    decks = tuple(_deck(corner, elements, rc_parts, diodes, node, supply_net, contract, vectors) for corner in corners)
    metadata = {
        'policyVersion': POLICY_VERSION,
        'circuitSha256': fingerprint(circuit), 'simulationSha256': fingerprint(simulation),
        'nodes': {'GND': 0, **{name: node[name] for name in nets}},
        'vectors': vectors, 'sourceCurrent': {'vector': 'i(v1)', 'supplyDrawMultiplier': -1},
        'supplyVoltageV': contract.supply_voltage_v, 'maxCurrentMa': contract.max_current_ma,
        'loads': {load.id: {'positiveNode': node[probes[load.id][0]], 'negativeNode': node[probes[load.id][1]],
                            'minVoltageV': load.min_voltage_v, 'maxVoltageV': load.max_voltage_v,
                            'declaredCurrentMa': load.current_ma} for load in contract.loads},
        'passiveIds': [elements[i][1] for i in rc_parts],
        'passives': [{'componentId': elements[i][1], 'elementName': f'{elements[i][0]}_{elements[i][1]}',
                      'kind': elements[i][0], 'positiveNode': node[elements[i][2]], 'negativeNode': node[elements[i][3]],
                      'nominalValue': elements[i][4],
                      'toleranceFraction': RESISTOR_TOLERANCE if elements[i][0] == 'R' else CAPACITOR_TOLERANCE}
                     for i in rc_parts],
        'devices': [{'id': d.component_id, 'componentId': d.component_id, 'anodeNode': node[net(d.component_id, d.pins[0])],
                     'cathodeNode': node[net(d.component_id, d.pins[1])],
                     'currentVector': f'i(vs_{d.component_id.lower()})',
                     'safeCurrentMa': LED_SAFE_MA if d.kind == 'LED' else None,
                     'assumption': ('Illustrative red LED, Shockley Is=1e-20 A, N=2 at 27 C; advisory '
                                    f'{LED_SAFE_MA:g} mA limit.') if d.kind == 'LED' else
                                   'Illustrative silicon diode, Is=1e-14 A, N=1 at 27 C; no qualified current rating.'}
                    for d in diodes],
        'bounds': {'maxDecks': MAX_DECKS, 'maxRawSamples': 5000, 'maxOutputBytesPerFile': 2_000_000,
                   'timeoutSecondsPerDeck': 15, 'maxTotalSeconds': 255},
        'sampleIntervalSeconds': STEP_SECONDS, 'limitations': list(LIMITATIONS)}
    return Plan('READY', (), decks, metadata)


def _deck(corner, elements, rc_parts, diodes, node, supply_net, contract, vectors):
    scale = {}
    for slot, index in enumerate(rc_parts):
        tolerance = RESISTOR_TOLERANCE if elements[index][0] == 'R' else CAPACITOR_TOLERANCE
        scale[index] = 1 + corner[slot] * tolerance
    lines = [f'* kleos circuit-simulation deck v1 corner={list(corner)}',
             f'V1 {node[supply_net]} 0 DC {contract.supply_voltage_v:.9g} PWL(0 0 {RAMP_SECONDS:g} {contract.supply_voltage_v:.9g})']
    for index, (kind, ref, a, b, value) in enumerate(elements):
        if kind == 'R':
            lines.append(f'R_{ref} {node[a]} {node[b]} {value * scale[index]:.9g}')
        elif kind == 'C':
            lines.append(f'C_{ref} {node[a]} {node[b]} {value * scale[index]:.9g} ic=0')
        else:
            model = MODELS[kind][0]
            lines += [f'VS_{ref} {node[a]} s_{ref.lower()} 0', f'D_{ref} s_{ref.lower()} {node[b]} {model}']
    for model, parameters in {MODELS[d.kind][0]: MODELS[d.kind][1] for d in diodes}.items():
        lines.append(f'.model {model} {parameters}')
    names = ' '.join(vectors)
    lines += ['.temp 27', '.control', 'set filetype=ascii', 'set wr_singlescale', 'set wr_vecnames', 'op',
              f'wrdata op.txt {names}', f'tran {STEP_SECONDS:g} {WINDOW_SECONDS:g} uic', f'linearize {names}',
              f'wrdata tran.txt {names}', 'quit', '.endc', '.end']
    text = '\n'.join(lines) + '\n'
    return Deck(corner, text, hashlib.sha256(text.encode()).hexdigest())
