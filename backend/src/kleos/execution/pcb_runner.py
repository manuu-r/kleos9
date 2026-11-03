"""Fixed KiCad data transforms. Executed only by the isolated supervisor's unprivileged child."""
import hashlib
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pcbnew

sys.path.insert(0, '/opt/kleos-native')
from kleos.execution.pcb_contract import circuit_contract  # noqa: E402

ROOT = Path('build/pcb')


def contract():
    source = json.loads(Path('build/pcb-contract.json').read_bytes())
    return circuit_contract(source['circuitContract'], source['peripheralConceptIds'])


def schematic(value):
    raw = (ROOT / 'netlist.xml').read_bytes()
    if b'<!DOCTYPE' in raw or b'<!ENTITY' in raw or len(raw) > 8 * 1024**2:
        raise ValueError('Unsafe or oversized exported netlist')
    tree = ET.fromstring(raw)
    parts = {}
    for component in tree.findall('./components/comp'):
        ref = component.attrib['ref']
        if ref in parts:
            raise ValueError('Schematic reference is duplicated')
        parts[ref] = {'reference': ref, 'value': component.findtext('value'),
                      'footprint': component.findtext('footprint'), 'pins': {}}
    for net in tree.findall('./nets/net'):
        name = net.attrib['name']
        for node in net.findall('node'):
            ref, pin = node.attrib['ref'], node.attrib['pin']
            if ref not in parts or pin in parts[ref]['pins']:
                raise ValueError('Schematic pin is duplicated or belongs to an unknown reference')
            parts[ref]['pins'][pin] = name
    expected = {item['reference']: item for item in value['references']}
    if parts != expected:
        raise ValueError('Schematic references, values, footprints or pin nets differ from accepted circuit: '
                         + json.dumps(parts, sort_keys=True)[:8000])


def board_check(board, value):
    if board.GetAreaCount():
        raise ValueError('Copper zones require a separately qualified fill and connectivity contract')
    parts = {}
    positions = {}
    for part in board.GetFootprints():
        ref = part.GetReference()
        if ref in parts:
            raise ValueError('PCB reference is duplicated')
        pins = {}
        for pad in part.Pads():
            pin, net = pad.GetNumber(), pad.GetNetname()
            if not pin:  # Mechanical-only holes/pads are not accepted by this first routing contract.
                raise ValueError('Unnumbered PCB pads are not supported by the accepted pin contract')
            if pin in pins:
                raise ValueError('Multiple physical pads for one pin require a separately qualified contract')
            pins[pin] = net
        parts[ref] = {'reference': ref, 'value': part.GetValue(),
                      'footprint': str(part.GetFPID().GetLibNickname()) + ':' + str(part.GetFPID().GetLibItemName()), 'pins': pins}
        positions[ref] = [part.GetPosition().x, part.GetPosition().y, part.GetOrientationDegrees(), part.GetLayer()]
    if parts != {item['reference']: item for item in value['references']}:
        raise ValueError('PCB references, values, footprints or pin nets differ from accepted circuit: '
                         + json.dumps(parts, sort_keys=True)[:8000])
    bounds = board.GetBoardEdgesBoundingBox()
    width, height = pcbnew.ToMM(bounds.GetWidth()), pcbnew.ToMM(bounds.GetHeight())
    limits = value['constraints']
    if not 0 < width <= limits['maxWidthMm'] or not 0 < height <= limits['maxHeightMm']:
        raise ValueError('PCB outline violates accepted dimensions')
    if board.GetCopperLayerCount() != 2:
        raise ValueError('PCB copper layers differ from accepted layer constraints')
    return {'references': parts, 'positions': positions, 'widthMm': width, 'heightMm': height}


def prepare():
    accepted = contract()
    schematic(accepted)
    board = pcbnew.LoadBoard(str(ROOT / 'board.kicad_pcb'))
    snapshot = board_check(board, accepted)
    settings = board.GetDesignSettings()
    limits = accepted['constraints']
    settings.m_MinClearance = pcbnew.FromMM(limits['minClearanceMm'])
    settings.m_TrackMinWidth = pcbnew.FromMM(limits['minTrackWidthMm'])
    default = settings.m_NetSettings.GetDefaultNetclass()
    default.SetClearance(pcbnew.FromMM(limits['minClearanceMm']))
    default.SetTrackWidth(pcbnew.FromMM(limits['minTrackWidthMm']))
    default.SetViaDiameter(pcbnew.FromMM(.8))
    default.SetViaDrill(pcbnew.FromMM(.4))
    if not pcbnew.ExportSpecctraDSN(board, str(ROOT / 'board.dsn')):
        raise ValueError('KiCad could not export the routing interchange')
    (ROOT / 'before-routing.json').write_text(json.dumps(snapshot))
    print(json.dumps({'referencesChecked': sorted(snapshot['references']), 'routed': False}))


def finish():
    accepted = contract()
    board = pcbnew.LoadBoard(str(ROOT / 'board.kicad_pcb'))
    if not pcbnew.ImportSpecctraSES(board, str(ROOT / 'board.ses')):
        raise ValueError('KiCad could not import router output')
    snapshot = board_check(board, accepted)
    before = json.loads((ROOT / 'before-routing.json').read_bytes())
    if snapshot != before:
        raise ValueError('Routing changed accepted component placement, identities or outline')
    tracks = list(board.GetTracks())
    if not tracks:
        raise ValueError('Router produced no copper tracks')
    for track in tracks:
        if track.GetWidth() < pcbnew.FromMM(accepted['constraints']['minTrackWidthMm']):
            raise ValueError('Routed track violates accepted minimum width')
        if track.GetNetname() not in {net for ref in accepted['references'] for net in ref['pins'].values()}:
            raise ValueError('Routed copper references an unaccepted net')
    if not pcbnew.SaveBoard(str(ROOT / 'board.kicad_pcb'), board):
        raise ValueError('Cannot save routed PCB')
    report = {'references': sorted(snapshot['references']), 'conceptReferences': accepted['conceptReferences'],
              'constraints': accepted['constraints'], 'trackCount': len(tracks),
              'boardSha256': hashlib.sha256((ROOT / 'board.kicad_pcb').read_bytes()).hexdigest()}
    Path('outputs/routing.json').write_text(json.dumps(report))
    print(json.dumps(report))


if __name__ == '__main__':
    if sys.argv[1:] == ['prepare']:
        prepare()
    elif sys.argv[1:] == ['finish']:
        finish()
    else:
        raise SystemExit('Unsupported fixed PCB operation')
