"""Deterministic compilation of accepted circuits into bounded SPICE decks; no solver runs here."""
import copy

import pytest
from simulation_fixtures import CIRCUIT, SIMULATION, ready_plan, variant

from kleos.validation.simulation.contract import parse_value
from kleos.validation.simulation.plan import plan_simulation


def test_ready_plan_enumerates_nominal_and_every_endpoint_corner():
    plan = ready_plan()
    assert plan.status == 'READY' and len(plan.decks) == 1 + 2**3  # R1, R2, C1
    assert plan.decks[0].corner == (0, 0, 0) and len({d.sha256 for d in plan.decks}) == 9
    assert plan.metadata['nodes'] == {'GND': 0, 'OUT': 1, 'VIN': 2}
    assert plan.metadata['vectors'] == ['v(1)', 'v(2)', 'i(v1)', 'i(vs_d1)']
    deck = plan.decks[0].text
    assert 'V1 2 0 DC 3.3 PWL(0 0 1e-06 3.3)' in deck and 'R_R1 2 1 1000' in deck and 'ic=0' in deck
    assert 'VS_D1 1 s_d1 0' in deck and '.model kleos_red_led_v1 D(IS=1e-20 N=2 RS=0)' in deck


def test_plan_is_independent_of_input_ordering_and_binds_contract_hashes():
    circuit, simulation = variant()
    shuffled_circuit, shuffled = copy.deepcopy(circuit), copy.deepcopy(simulation)
    shuffled_circuit['references'].reverse()
    shuffled['components'].reverse()
    first, second = plan_simulation(circuit, simulation), plan_simulation(shuffled_circuit, shuffled)
    assert [d.sha256 for d in first.decks] == [d.sha256 for d in second.decks]
    assert first.metadata['circuitSha256'] != second.metadata['circuitSha256']  # exact accepted order is provenance


def test_corner_values_scale_only_tolerance_bearing_parts():
    plan = ready_plan()
    low, high = plan.decks[1].text, plan.decks[-1].text  # (-1,-1,-1) and (1,1,1)
    assert 'R_R1 2 1 950' in low and 'R_R1 2 1 1050' in high
    assert 'C_C1 1 0 8e-08' in low and 'C_C1 1 0 1.2e-07' in high


def _mutate(kind):
    circuit, simulation = variant()
    if kind == 'unbound_component':
        circuit['references'].append({'reference': 'U1', 'value': 'MCU', 'footprint': 'F:U', 'pins': {'1': 'OUT'}})
    elif kind == 'generic_led':
        next(r for r in circuit['references'] if r['reference'] == 'D1')['value'] = 'Red'
    elif kind == 'floating_net':
        circuit['references'].append({'reference': 'R3', 'value': '1k', 'footprint': 'F:R', 'pins': {'1': 'ISO', '2': 'ISO2'}})
        simulation['components'].append({'componentId': 'R3', 'kind': 'R', 'pins': ['1', '2']})
        simulation['components'] = [c for c in simulation['components'] if c['componentId'] != 'D1']
        circuit['references'] = [r for r in circuit['references'] if r['reference'] != 'D1']
    elif kind == 'capacitor_only_net':
        circuit['references'].append({'reference': 'C2', 'value': '1u', 'footprint': 'F:C', 'pins': {'1': 'CAPONLY', '2': 'GND'}})
        simulation['components'].append({'componentId': 'C2', 'kind': 'C', 'pins': ['1', '2']})
        simulation['components'] = [c for c in simulation['components'] if c['componentId'] != 'D1']
        circuit['references'] = [r for r in circuit['references'] if r['reference'] != 'D1']
    elif kind == 'too_many_rc':
        for index in (3, 4):
            circuit['references'].append({'reference': f'R{index + 3}', 'value': '1k', 'footprint': 'F:R',
                                          'pins': {'1': 'OUT', '2': 'GND'}})
            simulation['components'].append({'componentId': f'R{index + 3}', 'kind': 'R', 'pins': ['1', '2']})
    elif kind == 'spice_expression_value':
        next(r for r in circuit['references'] if r['reference'] == 'R1')['value'] = '{1k*2}'
    elif kind == 'unit_text_value':
        next(r for r in circuit['references'] if r['reference'] == 'R2')['value'] = '2 kOhm'
    elif kind == 'missing_pin':
        simulation['source']['negativePin'] = '9'
    elif kind == 'shorted_supply':
        next(r for r in circuit['references'] if r['reference'] == 'J1')['pins']['2'] = 'VIN'
    elif kind == 'probe_on_open':
        simulation['components'].append({'componentId': 'J2', 'kind': 'OPEN', 'pins': []})
        circuit['references'].append({'reference': 'J2', 'value': 'X', 'footprint': 'F:J', 'pins': {'1': 'OUT', '2': 'GND'}})
        simulation['loads'][0]['probe']['componentId'] = 'J2'
    elif kind == 'binding_for_missing_component':
        simulation['components'].append({'componentId': 'R9', 'kind': 'R', 'pins': ['1', '2']})
    elif kind == 'invalid_bindings':
        simulation['loads'] = []
    return circuit, simulation


@pytest.mark.parametrize('kind', ['unbound_component', 'generic_led', 'floating_net', 'capacitor_only_net', 'too_many_rc',
    'spice_expression_value', 'unit_text_value', 'missing_pin', 'shorted_supply', 'probe_on_open',
    'binding_for_missing_component', 'invalid_bindings'])
def test_unsupported_or_ambiguous_circuits_are_unknown_with_a_reason_and_no_decks(kind):
    plan = plan_simulation(*_mutate(kind))
    assert plan.status == 'UNKNOWN' and plan.reasons and not plan.decks


def test_unclassified_parts_are_never_omitted_to_let_a_check_pass():
    circuit, simulation = variant()
    circuit['references'].append({'reference': 'R7', 'value': '1', 'footprint': 'F:R', 'pins': {'1': 'OUT', '2': 'GND'}})
    plan = plan_simulation(circuit, simulation)
    assert plan.status == 'UNKNOWN' and 'R7' in plan.reasons[0]


@pytest.mark.parametrize('text,kind,expected', [('330', 'R', 330), ('1k', 'R', 1e3), ('2.2meg', 'R', 2.2e6),
    ('100n', 'C', 1e-7), ('1e-6', 'C', 1e-6), ('4.7u', 'C', 4.7e-6)])
def test_value_grammar_accepts_plain_numbers_with_one_suffix(text, kind, expected):
    assert parse_value(text, kind) == pytest.approx(expected)


@pytest.mark.parametrize('text,kind', [('4k7', 'R'), ('1M', 'R'), (' 1k', 'R'), ('1 k', 'R'), ('0', 'R'), ('-5', 'R'),
    ('nan', 'R'), ('1e400', 'R'), ('{x}', 'R'), ('1k;', 'R'), ('10F', 'C'), ('1e-15', 'C'), ('1', 'C'), ('2e9', 'R')])
def test_value_grammar_rejects_expressions_units_ambiguity_and_out_of_range(text, kind):
    with pytest.raises(ValueError):
        parse_value(text, kind)


def test_no_candidate_text_reaches_a_deck():
    circuit, simulation = variant()
    next(r for r in circuit['references'] if r['reference'] == 'J1')['value'] = '.include /etc/passwd'
    text = plan_simulation(circuit, simulation).decks[0].text
    assert '/etc/passwd' not in text and '.include' not in text and CIRCUIT is not None and SIMULATION is not None
