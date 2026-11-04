"""Opt-in: the real-footprint board generator inside the PCB image (KLEOS_TEST_PCB_IMAGE=<image tag or id>).

Build the image as in src/kleos/execution/README.md. The generator imports pcbnew, which only the image provides.
"""
import copy
import json
import os
import subprocess

import pytest

from kleos.execution.pcb_scaffold import scaffold

IMAGE = os.environ.get('KLEOS_TEST_PCB_IMAGE')
pytestmark = pytest.mark.skipif(not IMAGE, reason='Set KLEOS_TEST_PCB_IMAGE to the built PCB image')

USB = 'Connector_USB:USB_C_Receptacle_GCT_USB4105-xx-A_16P_TopMnt_Horizontal'
CONTRACT = {'references': [
    {'reference': 'J1', 'value': 'USB-C', 'footprint': USB,
     'pins': {'A1': 'GND', 'A4': 'VBUS', 'A5': 'CC1', 'A9': 'VBUS', 'A12': 'GND', 'B1': 'GND', 'B5': 'CC2', 'B12': 'GND', 'S1': 'GND'}},
    {'reference': 'R1', 'value': '5.1k', 'footprint': 'Resistor_SMD:R_0603_1608Metric', 'pins': {'1': 'CC1', '2': 'GND'}},
    {'reference': 'R2', 'value': '5.1k', 'footprint': 'Resistor_SMD:R_0603_1608Metric', 'pins': {'1': 'CC2', '2': 'GND'}},
], 'conceptReferences': {'usb-input': 'J1'}, 'constraints': {
    'maxWidthMm': 40, 'maxHeightMm': 24, 'minTrackWidthMm': .25, 'minClearanceMm': .2, 'copperLayers': ['F.Cu', 'B.Cu']}}


def generate(tmp_path, contract, starter=None):
    project = tmp_path / 'project'
    for path, text in scaffold(starter or contract).items():
        (project / path).parent.mkdir(parents=True, exist_ok=True)
        (project / path).write_text(text)
    (tmp_path / 'contract.json').write_text(json.dumps(contract))
    (tmp_path / 'out').mkdir()
    for path in [tmp_path, project, tmp_path / 'out']:
        path.chmod(0o777)
    run = subprocess.run(['docker', 'run', '--rm', '--network', 'none', '-v', f'{tmp_path}:/w', '-w', '/w', '--entrypoint',
                          '/usr/bin/python3', IMAGE, '-I', '/opt/kleos-native/pcb_generate.py', 'contract.json', 'project', 'out'],
                         capture_output=True, text=True, check=False)
    return run, tmp_path / 'out'


def test_a_real_board_uses_library_footprints_inside_the_contract_limits(tmp_path):
    run, out = generate(tmp_path, CONTRACT)
    assert run.returncode == 0, run.stderr
    meta = json.loads((out / 'generated.json').read_text())
    assert meta['findings'] == [] and meta['version'] == 'pcb-kicad-library-v1'
    assert 0 < meta['widthMm'] <= 40 and 0 < meta['heightMm'] <= 24
    board = (out / 'board.kicad_pcb').read_text()
    assert f'"{USB}"' in board and board.count('(footprint "') == 3
    assert '(gr_arc' in board  # rounded outline
    assert sorted(meta['library']) == ['Connector_USB.pretty/USB_C_Receptacle_GCT_USB4105-xx-A_16P_TopMnt_Horizontal.kicad_mod',
                                       'Resistor_SMD.pretty/R_0603_1608Metric.kicad_mod']
    assert not (out / 'files' / 'Resistor_SMD.pretty/R_0603_1608Metric.kicad_mod').is_symlink()


def test_a_footprint_without_a_contract_pad_falls_back_and_is_reported(tmp_path):
    contract = copy.deepcopy(CONTRACT)
    contract['references'][1]['pins']['3'] = 'GND'  # the real 0603 resistor has two pads only
    run, out = generate(tmp_path, contract)
    assert run.returncode == 0, run.stderr
    findings = json.loads((out / 'generated.json').read_text())['findings']
    assert [(item['kind'], item['reference'], item['severity']) for item in findings] == [('footprint_fallback', 'R1', 'warning')]


def test_parts_that_cannot_fit_the_limits_are_refused_with_their_size(tmp_path):
    contract = copy.deepcopy(CONTRACT)
    contract['constraints'].update(maxWidthMm=12, maxHeightMm=8)
    run, _ = generate(tmp_path, contract, CONTRACT)
    assert run.returncode != 0 and 'limited to' in run.stderr
