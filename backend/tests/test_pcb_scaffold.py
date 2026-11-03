"""The starter KiCad project is derived from the accepted contract and must stay exact and deterministic."""
import re

import pytest

from kleos.execution.pcb_scaffold import connection_table, scaffold

CONTRACT = {'references': [
    {'reference': 'U1', 'value': 'MCU', 'footprint': 'RF:Module', 'pins': {'GPIO1': 'SIG', 'GND': 'GND', 'VCC': 'VDD'}},
    {'reference': 'J1', 'value': 'Header', 'footprint': 'Conn:Pad2', 'pins': {'1': 'VDD', '2': 'GND'}},
    {'reference': 'R1', 'value': '10k', 'footprint': 'Res:R0603', 'pins': {'1': 'SIG', '2': 'GND'}},
    {'reference': 'R2', 'value': '10k', 'footprint': 'Res:R0603', 'pins': {'1': 'VDD', '2': 'SIG'}},
], 'conceptReferences': {'header': 'J1'}, 'constraints': {
    'maxWidthMm': 34, 'maxHeightMm': 34, 'minTrackWidthMm': .15, 'minClearanceMm': .15, 'copperLayers': ['F.Cu', 'B.Cu']}}


def depth_is_balanced(text):
    depth, quoted = 0, False
    for char in text:
        if char == '"':
            quoted = not quoted
        elif not quoted:
            depth += (char == '(') - (char == ')')
            if depth < 0:
                return False
    return depth == 0


def test_connection_table_lists_every_net_with_its_pins_in_reference_order():
    assert connection_table(CONTRACT) == {
        'GND': ['J1.2', 'R1.2', 'U1.GND'], 'SIG': ['R1.1', 'R2.2', 'U1.GPIO1'], 'VDD': ['J1.1', 'R2.1', 'U1.VCC']}


def test_scaffold_is_deterministic_balanced_and_complete():
    files = scaffold(CONTRACT)
    assert files == scaffold(CONTRACT)
    assert {'circuit.kicad_sch', 'board.kicad_pcb', 'project.kicad_sym', 'sym-lib-table', 'fp-lib-table',
            'RF.pretty/Module.kicad_mod', 'Conn.pretty/Pad2.kicad_mod', 'Res.pretty/R0603.kicad_mod'} == set(files)
    assert all(depth_is_balanced(text) for text in files.values())


def test_schematic_uses_global_labels_so_net_names_match_the_contract_without_a_sheet_prefix():
    schematic = scaffold(CONTRACT)['circuit.kicad_sch']
    assert sorted(re.findall(r'\(global_label "([^"]+)"', schematic)) == sorted(
        net for part in CONTRACT['references'] for net in part['pins'].values())
    assert '(label ' not in schematic
    for part in CONTRACT['references']:
        assert f'(property "Reference" "{part["reference"]}"' in schematic
        assert f'(property "Footprint" "{part["footprint"]}"' in schematic
        for pin in part['pins']:
            assert f'(number "{pin}"' in schematic


def test_board_assigns_contract_nets_to_pads_numbered_by_pin_key_inside_the_outline():
    board = scaffold(CONTRACT)['board.kicad_pcb']
    assert {'(net 1 "GND")', '(net 2 "SIG")', '(net 3 "VDD")'} <= {item.strip() for item in board.splitlines()}
    for part in CONTRACT['references']:
        for pin, net in part['pins'].items():
            assert re.search(rf'\(pad "{re.escape(pin)}" thru_hole.*?\(net \d+ "{net}"\)', board, re.S)
    edges = [float(value) for value in re.findall(r'\(end (-?[\d.]+) (-?[\d.]+)\)', board)[0]]
    assert max(edges) <= CONTRACT['constraints']['maxWidthMm']
    assert 'GND' in board and board.count('(footprint "') == len(CONTRACT['references'])


def test_large_parts_get_more_pad_columns_instead_of_a_tall_footprint():
    from kleos.execution.pcb_scaffold import _pad_positions
    pads = _pad_positions(48)
    assert len(pads) == len(set(pads)) == 48 and max(y for _, y in pads) <= 7 * 2.0
    assert len({x for x, _ in pads}) == 6


def test_components_that_cannot_fit_the_outline_are_refused_not_squeezed():
    crowded = {**CONTRACT, 'references': [
        {'reference': f'U{index}', 'value': 'IC', 'footprint': 'RF:Big', 'pins': {str(pin): 'N' for pin in range(1, 21)}}
        for index in range(1, 12)]}
    with pytest.raises(ValueError, match='do not fit'):
        scaffold(crowded)
