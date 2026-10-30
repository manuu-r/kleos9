"""Real KiCad positive/negative correspondence fixture; no hosted models or network.

This runs real all-severity ERC/DRC and the full electronics validation branch;
other engineering lanes and physical operation are outside this fixture.
The fixture's expectations are authored independently below.
"""
import base64
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, "/opt/kleos")
from kleos.circuit_validation import (  # noqa: E402
    PCB_CONNECTIVITY_SCRIPT,
    prepare_kicad_libraries,
    validate_circuit_correspondence,
)
from kleos.native_runner import run_command, run_job  # noqa: E402

PCB_BUILD = r'''
import pcbnew, sys
board=pcbnew.BOARD()
nets={}
for name in ('/VCC','/GND'):
    nets[name]=pcbnew.NETINFO_ITEM(board,name)
    board.Add(nets[name])
parts={}
for ref,value,y in [('R1','1k',20),('R2','2k',30)]:
    fp=pcbnew.FootprintLoad('/usr/share/kicad/footprints/Resistor_SMD.pretty','R_0603_1608Metric')
    fp.SetReference(ref); fp.SetValue(value); fp.SetFPIDAsString('Resistor_SMD:R_0603_1608Metric')
    fp.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(20),pcbnew.FromMM(y)))
    for pad in fp.Pads(): pad.SetNet(nets['/VCC' if pad.GetNumber()=='1' else '/GND'])
    parts[ref]={pad.GetNumber():pad for pad in fp.Pads()}
    board.Add(fp)
for number,name in (('1','/VCC'),('2','/GND')):
    track=pcbnew.PCB_TRACK(board)
    track.SetStart(parts['R1'][number].GetPosition())
    track.SetEnd(parts['R2'][number].GetPosition())
    track.SetWidth(pcbnew.FromMM(.25)); track.SetLayer(pcbnew.F_Cu); track.SetNet(nets[name]); board.Add(track)
for a,b in [((15,15),(25,15)),((25,15),(25,35)),((25,35),(15,35)),((15,35),(15,15))]:
    edge=pcbnew.PCB_SHAPE(); edge.SetShape(pcbnew.SHAPE_T_SEGMENT)
    edge.SetStart(pcbnew.VECTOR2I(*[pcbnew.FromMM(v) for v in a]))
    edge.SetEnd(pcbnew.VECTOR2I(*[pcbnew.FromMM(v) for v in b]))
    edge.SetWidth(pcbnew.FromMM(.05)); edge.SetLayer(pcbnew.Edge_Cuts); board.Add(edge)
pcbnew.SaveBoard(sys.argv[1],board)
'''


def schematic():
    root = str(uuid4())
    source = Path('/usr/share/kicad/symbols/Device.kicad_sym').read_text()
    start = source.index('(symbol "R"')
    level, quoted, escaped = 0, False, False
    for end in range(start, len(source)):
        char = source[end]
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char == '(':
            level += 1
        elif char == ')':
            level -= 1
            if level == 0:
                break
    symbol = source[start:end + 1].replace('(symbol "R"', '(symbol "Device:R"', 1)
    document = f'''(kicad_sch (version 20250114) (generator "eeschema") (uuid {root}) (paper "A4")
      (lib_symbols {symbol})'''
    for ref, value, x in (("R1", "1k", 76.2), ("R2", "2k", 101.6)):
        document += f'''(symbol (lib_id "Device:R") (at {x} 76.2 0) (unit 1) (in_bom yes) (on_board yes) (dnp no)
          (uuid {uuid4()})
          (property "Reference" "{ref}" (at {x + 2} 76.2 90) (effects (font (size 1.27 1.27))))
          (property "Value" "{value}" (at {x} 76.2 90) (effects (font (size 1.27 1.27))))
          (property "Footprint" "Resistor_SMD:R_0603_1608Metric" (at {x} 76.2 0) (effects (font (size 1.27 1.27)) (hide yes)))
          (pin "1" (uuid {uuid4()})) (pin "2" (uuid {uuid4()}))
          (instances (project "circuit" (path "/{root}" (reference "{ref}") (unit 1)))))'''
        for name, y in (("VCC", 72.39), ("GND", 80.01)):
            document += f'''(label "{name}" (at {x} {y} 0)
              (effects (font (size 1.27 1.27)) (justify left bottom)) (uuid {uuid4()}))'''
    return document + ")"


with tempfile.TemporaryDirectory(prefix="kleos-circuit-smoke-") as temporary:
    work = Path(temporary)
    work.chmod(0o755)
    prepare_kicad_libraries(work)
    sch, pcb, netlist = work / "circuit.kicad_sch", work / "circuit.kicad_pcb", work / "netlist.xml"
    sch.write_text(schematic())
    fixture_env = {"HOME": str(work), "PATH": "/usr/local/bin:/usr/bin:/bin"}
    subprocess.run(["/usr/bin/python3", "-I", "-c", PCB_BUILD, str(pcb)], check=True, timeout=60, env=fixture_env)
    code, output = run_command(["kicad-cli", "sch", "export", "netlist", "--format", "kicadxml",
                                "--output", str(netlist), str(sch)], work, 60)
    if code:
        raise RuntimeError(output)
    for verb, source in ((["sch", "erc"], sch), (["pcb", "drc"], pcb)):
        report = work / f"{verb[1]}.json"
        status, output = run_command(["kicad-cli", *verb, "--severity-all", "--exit-code-violations",
                                      "--format", "json", "--output", str(report), str(source)], work, 60)
        assert status == 0, {"ruleCheck": verb[1], "report": report.read_text() if report.is_file() else output}
    interfaces = {"power": {"loads": [{"id": "r1"}, {"id": "r2"}]}, "circuit": {
        "schemaVersion": "circuit-netlist-v1", "components": [
            {"id": ident, "reference": ref, "symbol": "Device:R", "value": value,
             "footprint": "Resistor_SMD:R_0603_1608Metric", "loadIds": [ident], "pins": {"1": "/VCC", "2": "/GND"}}
            for ident, ref, value in (("r1", "R1", "1k"), ("r2", "R2", "2k"))]}}
    def inspect():
        code, output = run_command(["/usr/bin/python3", "-I", "-c", PCB_CONNECTIVITY_SCRIPT, str(pcb)], work, 60)
        if code:
            raise RuntimeError(output)
        evidence = {"schematicSha256": hashlib.sha256(sch.read_bytes()).hexdigest(),
                    "pcbSha256": hashlib.sha256(pcb.read_bytes()).hexdigest()}
        return validate_circuit_correspondence(interfaces, netlist.read_bytes(), json.loads(output), {}, evidence)
    positive = inspect()
    assert positive.status == "PASS", positive.model_dump()
    validation = work / "full-validation"
    validation.mkdir()
    artifacts = []
    for path in (sch, pcb):
        raw = path.read_bytes()
        artifacts.append({"id": path.name, "lane": "electronics", "name": "native/" + path.name,
                          "sha256": hashlib.sha256(raw).hexdigest(), "contentBase64": base64.b64encode(raw).decode(),
                          "provenance": {"nativeJobId": "qualified-circuit-fixture"}})
    result = run_job({"schemaVersion": 1, "purpose": "validate", "interfaces": interfaces,
                      "inputArtifacts": artifacts}, validation)
    checked = {check.name: check for check in result.checks}
    for name in ("electronics_erc_drc", "electronics_correspondence", "electronics"):
        assert checked[name].status == "PASS", checked[name].model_dump()
    corrupt = '''import pcbnew,sys
b=pcbnew.LoadBoard(sys.argv[1]); ground=b.FindNet('/GND')
for fp in b.GetFootprints():
 if fp.GetReference()=='R1':
  for pad in fp.Pads():
   if pad.GetNumber()=='1': pad.SetNet(ground)
pcbnew.SaveBoard(sys.argv[1],b)
'''
    subprocess.run(["/usr/bin/python3", "-I", "-c", corrupt, str(pcb)], check=True, timeout=60, env=fixture_env)
    negative = inspect()
    assert negative.status == "FAIL", negative.model_dump()
    print(json.dumps({"positive": positive.status, "wrongPadNet": negative.status,
                      "fullElectronicsPipeline": checked["electronics"].status,
                      "ercDrc": checked["electronics_erc_drc"].status,
                      "checks": {name: checked[name].status for name in (
                          "electronics_erc_drc", "electronics_correspondence", "electronics")},
                      "libraries": checked["electronics_erc_drc"].details["libraries"],
                      "sourceHashes": {name: hashlib.sha256((Path("/opt/kleos/kleos") / name).read_bytes()).hexdigest()
                                       for name in ("circuit_validation.py", "native_runner.py", "contracts.py")},
                      "fixtureScriptSha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                      "packageVersions": subprocess.check_output(["dpkg-query", "-W", "-f=${Package}=${Version}\n",
                          "kicad", "kicad-symbols", "kicad-footprints"], text=True).strip().splitlines(),
                      "modelCalls": 0,
                      "acceptedContractHash": positive.details["acceptedContractHash"],
                      "evidence": positive.details["evidence"], "netlistSha256": positive.details["netlistSha256"],
                      "scope": "Actual ERC/DRC and full electronics validation; other engineering lanes are excluded"}))
