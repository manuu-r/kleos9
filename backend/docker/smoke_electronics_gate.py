"""Actual KiCad negative fixture: clean ERC/DRC must not accept an empty circuit."""
import base64
import hashlib
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "/opt/kleos")
from kleos.native_runner import run_command, run_job  # noqa: E402

BOARD = """
import pcbnew
board = pcbnew.BOARD()
for a, b in [((0,0),(40,0)),((40,0),(40,30)),((40,30),(0,30)),((0,30),(0,0))]:
    line = pcbnew.PCB_SHAPE()
    line.SetShape(pcbnew.SHAPE_T_SEGMENT)
    line.SetStart(pcbnew.VECTOR2I(pcbnew.FromMM(a[0]), pcbnew.FromMM(a[1])))
    line.SetEnd(pcbnew.VECTOR2I(pcbnew.FromMM(b[0]), pcbnew.FromMM(b[1])))
    line.SetWidth(pcbnew.FromMM(0.05))
    line.SetLayer(pcbnew.Edge_Cuts)
    board.Add(line)
pcbnew.SaveBoard('empty.kicad_pcb', board)
"""
with tempfile.TemporaryDirectory(prefix="electronics-gate-") as temporary:
    root = Path(temporary)
    root.chmod(0o755)
    source = root / "fixture"
    source.mkdir()
    code, logs = run_command(["/usr/bin/python3", "-c", BOARD], source, 60)
    if code:
        raise RuntimeError(logs)
    (source / "empty.kicad_sch").write_text(
        '(kicad_sch (version 20250114) (generator "eeschema") '
        '(uuid "160a9019-117f-4fe3-9850-b7a67e84ce85") (paper "A4") (lib_symbols))')
    artifacts = []
    for path in source.glob("*.kicad_*"):
        content = path.read_bytes()
        artifacts.append({"id": path.name, "name": "native/" + path.name, "lane": "electronics",
                          "sha256": hashlib.sha256(content).hexdigest(),
                          "contentBase64": base64.b64encode(content).decode(),
                          "provenance": {"nativeJobId": "trusted-negative-fixture"}})
    validation = root / "validation"
    validation.mkdir()
    result = run_job({"schemaVersion": 1, "purpose": "validate", "inputArtifacts": artifacts}, validation)
    electrical = next(check for check in result.checks if check.name == "electronics")
    print(json.dumps({"status": result.status, "electronics": electrical.model_dump(),
                      "logs": result.logs[-4000:]}))
    assert electrical.status == "FAIL"
    assert "Empty schematic" in electrical.details["reason"], "Fixture did not reach the empty-netlist gate"
