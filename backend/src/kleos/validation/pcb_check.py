"""Standalone trusted PCB reader. Candidate Python, project rules and reports are never run."""
import hashlib
import json
import math
import os
import re
import socket
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path, PurePosixPath

PROTOCOL = 'kleos-pcb-check-v1'
MAX_FILE = 32*1024**2
MAX_TOTAL = 128*1024**2


class InvalidBoard(ValueError):
    pass


class UnsupportedBoard(ValueError):
    pass


def safe_path(value):
    if (not isinstance(value, str) or len(value) > 240 or '\\' in value or '\x00' in value
            or any(ord(char) < 32 for char in value) or PurePosixPath(value).is_absolute() or any(part in {'', '.', '..'} for part in value.split('/'))):
        raise InvalidBoard('Unsafe PCB input path')
    return value


def source_supported(path):
    return path in {'board.kicad_pcb', 'circuit.kicad_sch', 'fp-lib-table', 'sym-lib-table'} or path.endswith(('.kicad_mod', '.kicad_sym'))


def stage_inputs(request, directory, stage):
    files = request.get('files')
    if request.get('protocol') != PROTOCOL or not isinstance(files, list) or not 3 <= len(files) <= 256:
        raise InvalidBoard('Unsupported PCB checker input')
    total, seen, bindings = 0, set(), []
    for item in files:
        name = safe_path(item['path'])
        if name in seen or not (source_supported(name) or name == 'outputs/routed.kicad_pcb'):
            raise InvalidBoard('Unsupported or duplicate PCB input')
        seen.add(name)
        checksum = item['sha256']
        if not isinstance(checksum, str) or not re.fullmatch('[a-f0-9]{64}', checksum):
            raise InvalidBoard('Invalid PCB digest')
        source = directory/checksum
        if source.is_symlink() or not source.is_file():
            raise InvalidBoard('Missing PCB input')
        with source.open('rb') as stream:
            data = stream.read(MAX_FILE+1)
        total += len(data)
        if (len(data) > MAX_FILE or total > MAX_TOTAL or len(data) != item['size']
                or hashlib.sha256(data).hexdigest() != checksum):
            raise InvalidBoard('PCB byte binding failed')
        target = stage/('routed.kicad_pcb' if name == 'outputs/routed.kicad_pcb' else name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        bindings.append({'path': name, 'sha256': checksum})
    if not {'board.kicad_pcb', 'circuit.kicad_sch', 'outputs/routed.kicad_pcb'} <= seen:
        raise InvalidBoard('PCB sources and routed board are required')
    # Local library tables may only refer to exact staged source files/directories.
    for name in ('sym-lib-table', 'fp-lib-table'):
        if name not in seen:
            continue
        text = (stage/name).read_text()
        uris = re.findall(r'\(uri\s+"([^"\n]+)"\s*\)', text)
        if text.count('(uri') != len(uris):
            raise UnsupportedBoard('Unsupported library URI syntax')
        for uri in uris:
            if not uri.startswith('${KIPRJMOD}/'):
                raise UnsupportedBoard('Only project-local PCB libraries are supported')
            relative = safe_path(uri[len('${KIPRJMOD}/'):])
            if not any(path == relative or path.startswith(relative+'/') for path in seen):
                raise InvalidBoard('PCB library closure is incomplete')
    return bindings


def netlist_parts(data):
    if len(data) > MAX_FILE or b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
        raise InvalidBoard('Unsupported schematic netlist')
    tree = ET.fromstring(data)
    result = {}
    for node in tree.findall('./components/comp'):
        ref = node.attrib['ref']
        if ref in result or len(result) >= 128:
            raise InvalidBoard('Duplicate or excessive schematic references')
        result[ref] = {'reference': ref, 'value': node.findtext('value'),
                       'footprint': node.findtext('footprint'), 'pins': {}}
    for node in tree.findall('./nets/net'):
        for pin in node.findall('node'):
            ref, number = pin.attrib['ref'], pin.attrib['pin']
            if ref not in result or number in result[ref]['pins']:
                raise InvalidBoard('Invalid schematic pin identity')
            result[ref]['pins'][number] = node.attrib['name']
    return result


def board_measurements(path):
    # Imported only inside the checker container, never in host tests/control code.
    import pcbnew
    board = pcbnew.LoadBoard(str(path))
    if board.GetAreaCount():
        raise UnsupportedBoard('Copper zones are outside the checker contract')
    parts, positions, pad_geometry = {}, {}, {}
    for footprint in board.GetFootprints():
        ref = footprint.GetReference()
        if ref in parts or len(parts) >= 128:
            raise InvalidBoard('Duplicate or excessive board references')
        pins, geometry = {}, {}
        for pad in footprint.Pads():
            number = pad.GetNumber()
            if not number or number in pins:
                raise UnsupportedBoard('Unnumbered or repeated physical pads are unsupported')
            pins[number] = pad.GetNetname()
            if pad.GetShape() not in (pcbnew.PAD_SHAPE_CIRCLE, pcbnew.PAD_SHAPE_RECT, pcbnew.PAD_SHAPE_OVAL):
                raise UnsupportedBoard('Complex pad geometry requires separate qualification')
            xy, size, drill, offset = pad.GetPosition(), pad.GetSize(), pad.GetDrillSize(), pad.GetOffset()
            geometry[number] = {'position': [xy.x, xy.y], 'size': [size.x, size.y],
                'shape': int(pad.GetShape()), 'orientation': pad.GetOrientationDegrees(),
                'layers': sorted(int(layer) for layer in pad.GetLayerSet().Seq()),
                'drillSize': [drill.x, drill.y], 'drillShape': int(pad.GetDrillShape()),
                'offset': [offset.x, offset.y], 'attribute': int(pad.GetAttribute())}
        pad_geometry[ref] = geometry
        ident = footprint.GetFPID()
        parts[ref] = {'reference': ref, 'value': footprint.GetValue(),
                      'footprint': str(ident.GetLibNickname())+':'+str(ident.GetLibItemName()), 'pins': pins}
        position = footprint.GetPosition()
        positions[ref] = [position.x, position.y, footprint.GetOrientationDegrees(), footprint.GetLayer()]
    edges = []
    for edge in board.GetDrawings():
        if edge.GetLayer() != pcbnew.Edge_Cuts:
            continue
        if edge.GetShape() != pcbnew.SHAPE_T_SEGMENT:
            raise UnsupportedBoard('Only straight-segment board outlines are qualified')
        a, b = edge.GetStart(), edge.GetEnd()
        edges.append(sorted([[a.x, a.y], [b.x, b.y]]))
    if not 4 <= len(edges) <= 256:
        raise UnsupportedBoard('Unsupported board outline segment count')
    box = board.GetBoardEdgesBoundingBox()
    tracks = []
    for track in board.GetTracks():
        if isinstance(track, pcbnew.PCB_VIA) or track.GetLayer() not in (pcbnew.F_Cu, pcbnew.B_Cu):
            raise UnsupportedBoard('Vias or unsupported copper layers need separate qualification')
        tracks.append({'widthMm': pcbnew.ToMM(track.GetWidth()), 'net': track.GetNetname()})
        if len(tracks) > 100000:
            raise InvalidBoard('Excessive track count')
    return {'references': parts, 'positions': positions, 'pads': pad_geometry, 'outline': sorted(edges),
            'widthMm': pcbnew.ToMM(box.GetWidth()), 'heightMm': pcbnew.ToMM(box.GetHeight()),
            'copperLayers': board.GetCopperLayerCount(), 'tracks': tracks}


def check_correspondence(contract, schematic, source, routed):
    expected = {part['reference']: part for part in contract['references']}
    if len(expected) != len(contract['references']) or not expected:
        raise InvalidBoard('Invalid accepted references')
    if schematic != expected or source['references'] != expected or routed['references'] != expected:
        raise InvalidBoard('Schematic or board differs from accepted references, values, footprints or pin nets')
    if (source['positions'] != routed['positions'] or source['outline'] != routed['outline']
            or source['pads'] != routed['pads']):
        raise InvalidBoard('Routing changed source component/pad geometry or outline')
    limits = contract['constraints']
    for key, maximum in [('widthMm', 'maxWidthMm'), ('heightMm', 'maxHeightMm')]:
        if not math.isfinite(routed[key]) or not 0 < routed[key] <= limits[maximum]:
            raise InvalidBoard('Board dimensions exceed accepted constraints')
    if routed['copperLayers'] != 2 or source['copperLayers'] != 2:
        raise UnsupportedBoard('Only two copper layers are supported')
    if not routed['tracks']:
        raise InvalidBoard('Routed board has no tracks')
    nets = {net for part in expected.values() for net in part['pins'].values()}
    for track in routed['tracks']:
        if (not math.isfinite(track['widthMm']) or track['widthMm'] < limits['minTrackWidthMm']
                or track['net'] not in nets):
            raise InvalidBoard('Routed copper violates accepted widths or net identities')
    return {'referenceCount': len(expected), 'trackCount': len(routed['tracks']),
            'widthMm': routed['widthMm'], 'heightMm': routed['heightMm'], 'sourcePlacementPreserved': True}


def clean_rules(stage, limits):
    rules = {'meta': {'version': 3}, 'board': {'design_settings': {'meta': {'version': 2},
        'rule_severities': {name: 'error' for name in ('clearance', 'shorting_items', 'unconnected_items',
            'tracks_crossing', 'track_width', 'invalid_outline', 'item_on_disabled_layer', 'padstack')},
        'rules': {'min_clearance': limits['minClearanceMm'], 'min_track_width': limits['minTrackWidthMm']}}},
        'net_settings': {'meta': {'version': 4}, 'classes': [
        {'name': 'Default', 'clearance': limits['minClearanceMm'], 'track_width': limits['minTrackWidthMm']}]}}
    # Candidate .kicad_pro/.kicad_dru files are not staged. Rules/exemptions are server-owned.
    for stem in ('board', 'circuit', 'routed'):
        (stage/(stem+'.kicad_pro')).write_text(json.dumps(rules))
    (stage/'routed.kicad_dru').write_text('(version 1)\n'
        f'(rule "Accepted clearance" (constraint clearance (min {limits["minClearanceMm"]}mm)))\n'
        f'(rule "Accepted track width" (constraint track_width (min {limits["minTrackWidthMm"]}mm)))\n')


def command(arguments, stage):
    # Fixed caller-owned arguments only, bounded log files; no shell or source scripts.
    with (stage/'last-tool.log').open('wb') as log:
        result = subprocess.run(arguments, cwd=stage, stdin=subprocess.DEVNULL, stdout=log,
            stderr=log, timeout=60, env={'PATH': '/usr/bin:/bin', 'HOME': '/tmp',
                                      'LANG': 'C.UTF-8', 'KIPRJMOD': str(stage)}, check=False)
    return result.returncode


def inspect_request(request, directory, stage, *, execute=command, read_board=board_measurements):
    bindings = stage_inputs(request, directory, stage)
    contract = request['circuitContract']
    if hashlib.sha256(json.dumps(contract, sort_keys=True, separators=(',', ':')).encode()).hexdigest() != request['circuitContractHash']:
        raise InvalidBoard('Accepted circuit digest mismatch')
    clean_rules(stage, contract['constraints'])
    if execute(['kicad-cli', 'sch', 'export', 'netlist', '--format', 'kicadxml',
                '--output', 'netlist.xml', 'circuit.kicad_sch'], stage) != 0:
        raise InvalidBoard('Independent schematic netlist export failed')
    netlist_path = stage/'netlist.xml'
    if netlist_path.is_symlink() or not netlist_path.is_file() or netlist_path.stat().st_size > MAX_FILE:
        raise InvalidBoard('Independent netlist missing or excessive')
    raw = netlist_path.read_bytes()
    measurements = check_correspondence(contract, netlist_parts(raw), read_board(stage/'board.kicad_pcb'),
                                         read_board(stage/'routed.kicad_pcb'))
    counts, tool_details = {}, {}
    for kind, path in [('erc', 'circuit.kicad_sch'), ('drc', 'routed.kicad_pcb')]:
        report = kind+'.json'
        code = execute(['kicad-cli', 'sch' if kind == 'erc' else 'pcb', kind,
                        '--exit-code-violations', '--format', 'json', '--output', report, path], stage)
        report_path = stage/report
        if report_path.is_symlink() or not report_path.is_file() or report_path.stat().st_size > MAX_FILE:
            raise InvalidBoard('Independent rule report missing or excessive')
        payload = json.loads(report_path.read_bytes())
        if kind == 'erc':
            sheets = payload.get('sheets')
            if not isinstance(sheets, list) or any(not isinstance(sheet.get('violations'), list) for sheet in sheets):
                raise UnsupportedBoard('Unsupported ERC report schema')
            count = sum(len(sheet['violations']) for sheet in sheets)
        else:
            if any(not isinstance(payload.get(key), list) for key in ('violations', 'unconnected_items')):
                raise UnsupportedBoard('Unsupported DRC report schema')
            count = len(payload['violations'])+len(payload['unconnected_items'])
        counts[kind] = count
        log_path = stage/'last-tool.log'
        tool_details[kind] = {'reportVersion': payload.get('kicad_version'), 'reportSource': payload.get('source'),
            'messages': log_path.read_bytes()[:4000].decode(errors='replace') if log_path.exists() else ''}
        if code != 0 or count:
            violations = payload.get('violations', [])+payload.get('unconnected_items', [])
            if kind == 'erc':
                violations = [value for sheet in payload['sheets'] for value in sheet['violations']]
            details = [(item.get('type'), item.get('description')) for item in violations[:5]]
            raise InvalidBoard(('Independent '+kind.upper()+' did not pass: '+json.dumps(details))[:2000])
    return {'status': 'PASS', 'measurements': {**measurements, 'ruleViolationCounts': counts,
        'inputBindings': bindings, 'toolDetails': tool_details, 'physicalAcceptance': False},
        'reason': 'Independent two-layer board identity and ERC/DRC checks; no functional or manufacturing acceptance'}


def main():
    request = json.loads(Path('/input/request.json').read_text())
    saved, sink = os.dup(1), os.open(os.devnull, os.O_WRONLY)
    os.dup2(sink, 1)
    os.dup2(sink, 2)
    isolation = {}
    try:
        status = Path('/proc/self/status').read_text()
        if (os.getuid() != 10001 or os.getgid() != 10001 or 'NoNewPrivs:\t1' not in status
                or 'Seccomp:\t2' not in status or Path('/opt/kleos-native').exists()
                or int(next(line.split()[1] for line in status.splitlines() if line.startswith('CapEff:')), 16)):
            raise RuntimeError('Checker isolation mismatch')
        try:
            connection = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        except PermissionError:
            pass
        else:
            connection.close()
            raise RuntimeError('Internet socket was permitted')
        isolation = {'uid': os.getuid(), 'gid': os.getgid(), 'noNewPrivileges': True, 'seccomp': True,
            'capabilities': 0, 'internetSocketDenied': True, 'generatorCodeAbsent': True,
            'credentialEnvironmentEmpty': all(not value for key, value in os.environ.items()
                                              if key not in {'PATH', 'LANG', 'LC_CTYPE', 'HOME', 'HOSTNAME'})
                and os.environ.get('HOME') == '/tmp' and os.environ.get('HOSTNAME') == 'kleos-pcb-checker',
            'toolVersion': subprocess.run(['kicad-cli', 'version'], capture_output=True, timeout=10, check=True).stdout.decode().strip()}
        if not isolation['credentialEnvironmentEmpty']:
            raise RuntimeError('Unexpected checker environment')
        with tempfile.TemporaryDirectory(prefix='pcb-check-') as folder:
            result = inspect_request(request, Path('/input'), Path(folder))
    except UnsupportedBoard as error:
        result = {'status': 'UNKNOWN', 'reason': str(error), 'measurements': {}}
    except InvalidBoard as error:
        result = {'status': 'FAIL', 'reason': str(error), 'measurements': {}}
    except Exception as error:
        result = {'status': 'ERROR', 'reason': 'Independent PCB checker failed', 'measurements': {},
                  'diagnostic': type(error).__name__+': '+str(error)[:1000]}
    os.dup2(saved, 1)
    os.close(saved)
    os.close(sink)
    result.update(isolation=isolation, protocol=PROTOCOL, executionId=request['executionId'], subjectHash=request['subjectHash'],
                  checkerSourceSha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    print(json.dumps(result, allow_nan=False, separators=(',', ':')), flush=True)


if __name__ == '__main__':
    main()
