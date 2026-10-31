import json
from copy import deepcopy

import pytest

from kleos import pcb_compiler as compiler
from kleos.circuit_validation import UnavailableCircuitData


@pytest.fixture
def parts(tmp_path, monkeypatch):
    root = tmp_path / 'libraries'
    (root / 'symbols').mkdir(parents=True)
    fp = root / 'footprints/Resistor_SMD.pretty'
    fp.mkdir(parents=True)
    (root / 'symbols/Device.kicad_sym').write_text('''(kicad_symbol_lib
      (symbol "R"
        (symbol "R_1_1"
          (pin passive line (at 0 3.81 270)(number "1")(name "~"))
          (pin passive line (at 0 -3.81 90)(number "2")(name "~")))))''')
    (fp / 'R_0603_1608Metric.kicad_mod').write_text('(footprint "R_0603_1608Metric")')
    monkeypatch.setattr(compiler, 'CATALOG', {'Device:R': ['Resistor_SMD:R_0603_1608Metric']})
    manifest = tmp_path / 'catalog.json'
    manifest.write_text(json.dumps(compiler.catalog_manifest(root)))
    interfaces = {'power': {'loads': [{'id': 'r1'}, {'id': 'r2'}]},
        'electronics': {'boardMm': {'x': 30, 'y': 30}}, 'circuit': {
            'schemaVersion': 'circuit-netlist-v1', 'components': [
                {'id': ident, 'reference': ref, 'symbol': 'Device:R', 'footprint': 'Resistor_SMD:R_0603_1608Metric',
                 'value': '1k', 'loadIds': [ident], 'pins': {'1': 'VCC', '2': 'GND'}}
                for ident, ref in [('r1', 'R1'), ('r2', 'R2')]]}}
    return root, manifest, interfaces


def test_compiler_is_deterministic_and_retains_accepted_pin_net_contract(parts, tmp_path):
    root, manifest, interfaces = parts
    a, b = tmp_path / 'a', tmp_path / 'b'
    compiler.compile_circuit(interfaces, a, root, manifest)
    reversed_contract = deepcopy(interfaces)
    reversed_contract['circuit']['components'].reverse()
    compiler.compile_circuit(interfaces, b, root, manifest)
    for file in ['circuit.kicad_sch', 'placement.json']:
        assert (a / file).read_bytes() == (b / file).read_bytes()
    placed = json.loads((a / 'placement.json').read_text())
    assert placed['parts'][0]['pins'] == {'1': 'VCC', '2': 'GND'}
    assert '(lib_id "Device:R")' in (a / 'circuit.kicad_sch').read_text()


@pytest.mark.parametrize('failure', ['catalog', 'pins', 'dimensions', 'parts'])
def test_unknown_or_modified_parts_cannot_silently_compile(parts, tmp_path, failure):
    root, manifest, interfaces = parts
    if failure == 'catalog':
        (root / 'footprints/Resistor_SMD.pretty/R_0603_1608Metric.kicad_mod').write_text('changed')
    elif failure == 'pins':
        interfaces['circuit']['components'][0]['pins']['3'] = 'VCC'
    elif failure == 'dimensions':
        interfaces.pop('electronics')
    else:
        interfaces['circuit']['components'][0]['symbol'] = 'Unknown:Invented'
    with pytest.raises(UnavailableCircuitData):
        compiler.compile_circuit(interfaces, tmp_path / 'out', root, manifest)
