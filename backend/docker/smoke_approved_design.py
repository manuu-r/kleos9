"""Offline OCCT/KiCad proof using public frozen references, never hosted inference."""
import base64
import copy
import hashlib
import importlib.metadata
import json
import subprocess
import tempfile
from pathlib import Path

from kleos.design_validation import PCB_LAYOUT_SCRIPT, validate_pcb_layout
from kleos.native_runner import run_command, run_job

CASES = json.loads(Path('/approved_design_validation.json').read_text())
CIRCLE = b'''from build123d import Cylinder, Compound
case=Cylinder(20,9).translate((0,0,-.5))-Cylinder(19,8)
cover=Cylinder(20,1).translate((0,0,4.5))
assembly=Compound(children=[case,cover])
parts={'case':{'shape':case,'label':'Circular case','quantity':1},
       'cover':{'shape':cover,'label':'Circular cover','quantity':1}}
'''
SQUARE = b'from build123d import Box\nassembly = Box(40,40,10) - Box(38,38,8)\n'
LID = b'''from build123d import Box,Cylinder
body=Box(28,28,10)-Box(26,26,8)
cover=Cylinder(20,1).translate((0,0,4.5))
assembly=body+cover
'''
ROUNDED = b'''from kleos.design_validation import _profile_solid
outer={'x':40,'y':30,'z':10}; inner={'x':38,'y':28,'z':8}
assembly=_profile_solid('rounded_rectangle',outer,3.9)-_profile_solid('rounded_rectangle',inner,2.9)
'''


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def artifact(name, raw, lane='mechanics'):
    return {'id': name, 'lane': lane, 'name': name, 'sha256': sha(raw),
            'contentBase64': base64.b64encode(raw).decode(), 'provenance': {'nativeJobId': 'approved-design-qualification'}}


def build(source, case=None):
    with tempfile.TemporaryDirectory(prefix='approved-design-build-') as temporary:
        work = Path(temporary)
        work.chmod(0o755)
        request = {'schemaVersion': 1, 'purpose': 'build', 'lane': 'mechanics',
                   'inputArtifacts': [artifact('enclosure.py', source)]}
        if case:
            request.update(copy.deepcopy(CASES[case]))
        return run_job(request, work)


def validate(result, case):
    with tempfile.TemporaryDirectory(prefix='approved-design-check-') as temporary:
        work = Path(temporary)
        work.chmod(0o755)
        request = {'schemaVersion': 1, 'purpose': 'validate', **copy.deepcopy(CASES[case]), 'inputArtifacts': [
            artifact(item.path, base64.b64decode(item.content)) for item in result.files]}
        checked = run_job(request, work)
        return {check.name: check for check in checked.checks}


positive = build(CIRCLE, 'circle')
assert positive.status == 'succeeded', positive.model_dump()
checked = validate(positive, 'circle')
for name in ('interfaces', 'mechanics', 'mechanical_parts', 'approved_design_contract', 'approved_enclosure_form'):
    assert checked[name].status == 'PASS', checked[name].model_dump()
assert checked['mechanics'].details['measurements']['internalObstructionMm3'] < 1e-4
negative = build(SQUARE, 'circle')
assert negative.status == 'failed' and negative.measurements.get('failureKind') == 'engineering', negative.model_dump()
bad = build(SQUARE)
assert bad.status == 'succeeded', bad.model_dump()
bad_checks = validate(bad, 'circle')
assert bad_checks['approved_enclosure_form'].status == 'FAIL', bad_checks['approved_enclosure_form'].model_dump()
lid = build(LID)
assert lid.status == 'succeeded', lid.model_dump()
lid_checks = validate(lid, 'circle')
assert lid_checks['approved_enclosure_form'].status == 'FAIL', lid_checks['approved_enclosure_form'].model_dump()
assert lid_checks['approved_enclosure_form'].details['boundaryGuardCoverage'] < .85
rounded = build(ROUNDED, 'rounded')
assert rounded.status == 'succeeded', rounded.model_dump()
rounded_checks = validate(rounded, 'rounded')
assert rounded_checks['approved_enclosure_form'].status == 'PASS', rounded_checks['approved_enclosure_form'].model_dump()
sharp = build(b'from build123d import Box\nassembly=Box(40,30,10)-Box(38,28,8)\n', 'rounded')
assert sharp.status == 'failed', sharp.model_dump()

PCB_BUILD = r'''
import pcbnew,sys
b=pcbnew.BOARD(); mode=sys.argv[2]
def vec(x,y): return pcbnew.VECTOR2I(pcbnew.FromMM(x),pcbnew.FromMM(y))
if mode=='circle':
 edge=pcbnew.PCB_SHAPE(); edge.SetShape(pcbnew.SHAPE_T_CIRCLE); edge.SetCenter(vec(20,20)); edge.SetEnd(vec(38,20))
 edge.SetWidth(pcbnew.FromMM(.05)); edge.SetLayer(pcbnew.Edge_Cuts); b.Add(edge)
else:
 for a,c in [((10,10),(30,10)),((30,10),(30,30)),((30,30),(10,30)),((10,30),(10,10))]:
  edge=pcbnew.PCB_SHAPE(); edge.SetShape(pcbnew.SHAPE_T_SEGMENT); edge.SetStart(vec(*a)); edge.SetEnd(vec(*c))
  edge.SetWidth(pcbnew.FromMM(.05)); edge.SetLayer(pcbnew.Edge_Cuts); b.Add(edge)
if mode in ('sensor','wrong_sensor'):
 fp=pcbnew.FootprintLoad('/usr/share/kicad/footprints/Resistor_SMD.pretty','R_0603_1608Metric')
 fp.SetReference('U1'); fp.SetValue('sensor-proxy'); fp.SetPosition(vec(20,12 if mode=='sensor' else 28)); b.Add(fp)
if mode=='outside':
 track=pcbnew.PCB_TRACK(b); track.SetStart(vec(38,10)); track.SetEnd(vec(39,10)); track.SetWidth(pcbnew.FromMM(.25))
 track.SetLayer(pcbnew.F_Cu); b.Add(track)
pcbnew.SaveBoard(sys.argv[1],b)
'''
pcb_results = {}
with tempfile.TemporaryDirectory(prefix='approved-pcb-check-') as temporary:
    work = Path(temporary)
    work.chmod(0o755)
    pcb = work / 'board.kicad_pcb'
    for mode, case, expected in [('rectangle', 'circle', 'PASS'), ('circle', 'round_board', 'PASS'),
                                  ('sensor', 'sensor', 'PASS'), ('wrong_sensor', 'sensor', 'FAIL'),
                                  ('outside', 'circle', 'FAIL')]:
        subprocess.run(['/usr/bin/python3', '-I', '-c', PCB_BUILD, str(pcb), mode], check=True, timeout=60)
        code, output = run_command(['/usr/bin/python3', '-I', '-c', PCB_LAYOUT_SCRIPT, str(pcb)], work, 60)
        assert not code, output
        snapshot = json.loads(output.strip().splitlines()[-1])
        reference = CASES[case]
        check = validate_pcb_layout(reference['requirements'], reference['interfaces'], snapshot, sha(pcb.read_bytes()))
        assert check.status == expected, (mode, check.model_dump())
        pcb_results[mode] = check.model_dump(mode='json', by_alias=True)

print(json.dumps({'schemaVersion': 'approved-design-runtime-v1', 'modelCalls': 0,
    'mechanics': {name: checked[name].status for name in ('interfaces', 'mechanics', 'mechanical_parts', 'approved_design_contract', 'approved_enclosure_form')},
    'generationSquareRejected': negative.status == 'failed', 'sameBboxSquare': bad_checks['approved_enclosure_form'].model_dump(),
    'roundCoverSquareBody': lid_checks['approved_enclosure_form'].model_dump(),
    'roundedEnclosure': rounded_checks['approved_enclosure_form'].model_dump(), 'sharpRoundedSubstituteRejected': sharp.status == 'failed',
    'circleMeasurements': checked['mechanics'].details['measurements'], 'pcb': pcb_results,
    'build123dVersion': importlib.metadata.version('build123d'),
    'sourceHashes': {name: sha((Path('/usr/local/lib/python3.12/site-packages/kleos') / name).read_bytes())
                     for name in ('design_validation.py', 'cad_parts.py', 'native_runner.py', 'contracts.py')},
    'fixtureSha256': sha(Path(__file__).read_bytes()), 'frozenReferencesSha256': sha(Path('/approved_design_validation.json').read_bytes()),
    'scope': 'Actual STEP body form/clearance and KiCad Edge.Cuts/package/copper extents, not complete manufacturing or physical qualification'}))
