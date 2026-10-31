"""Actual compiler -> router -> DRC -> independent correspondence, offline."""
import base64
import hashlib
import json
import tempfile
from pathlib import Path

from kleos.engineering_tools import BUILTINS
from kleos.native_runner import run_job

interfaces = {'power': {'loads': [{'id': 'r1'}, {'id': 'r2'}]},
    'electronics': {'boardMm': {'x': 40, 'y': 30}}, 'circuit': {
    'schemaVersion': 'circuit-netlist-v1', 'components': [
        {'id': ident, 'reference': ref, 'symbol': 'Device:R', 'footprint': 'Resistor_SMD:R_0603_1608Metric',
         'value': '1k', 'loadIds': [ident], 'pins': {'1': 'VCC', '2': 'GND'}}
        for ident, ref in [('r1', 'R1'), ('r2', 'R2')]]}}
profile = next(p for p in BUILTINS if p.id == 'atopile-kicad' and p.version == '2')
with tempfile.TemporaryDirectory(prefix='circuit-compiler-proof-') as folder:
    directory = Path(folder)
    directory.chmod(0o755)
    build = run_job({'schemaVersion': 1, 'purpose': 'build', 'lane': 'electronics', 'interfaces': interfaces,
                     'inputArtifacts': [], 'engineeringTool': profile.model_dump(mode='json', by_alias=True)}, directory)
    if build.status != 'succeeded':
        for report in directory.rglob('*rc.json'):
            print(report.read_text(), flush=True)
        raise RuntimeError(json.dumps(build.model_dump(mode='json', exclude={'files'})))
    inputs = []
    for f in build.files:
        if f.path.endswith(('.kicad_sch', '.kicad_pcb')):
            inputs.append({'id': f.path, 'name': f.path, 'lane': 'electronics', 'sha256': hashlib.sha256(base64.b64decode(f.content)).hexdigest(),
                          'contentBase64': f.content, 'provenance': {'nativeJobId': 'compiled-circuit-proof'}})
    validation = directory / 'validator'
    validation.mkdir()
    checked = run_job({'schemaVersion': 1, 'purpose': 'validate', 'interfaces': interfaces, 'inputArtifacts': inputs}, validation)
    checks = {c.name: c.status for c in checked.checks}
    assert all(checks.get(name) == 'PASS' for name in ['electronics_erc_drc', 'electronics_correspondence', 'electronics']), checked.model_dump(exclude={'files'})
    print(json.dumps({'status': 'passed', 'modelCalls': 0, 'checks': checks,
        'outputs': [f.path for f in build.files], 'freeroutingVersion': '2.1.0',
        'freeroutingSha256': hashlib.sha256(Path('/opt/freerouting/freerouting.jar').read_bytes()).hexdigest(),
        'partsManifestSha256': hashlib.sha256(Path('/opt/kleos-parts/manifest.json').read_bytes()).hexdigest(),
        'sourceHashes': {name: hashlib.sha256((Path('/usr/local/lib/python3.12/site-packages/kleos') / name).read_bytes()).hexdigest()
                         for name in ('native_runner.py', 'pcb_compiler.py', 'circuit_validation.py')}}))
