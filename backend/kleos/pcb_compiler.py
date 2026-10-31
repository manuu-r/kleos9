"""Deterministic circuit-netlist-v1 -> KiCad sources. Only image-owned parts.

No model-written schematic, executable circuit code, downloaded library or
candidate-written rules supply expectations. Native validation stays independent.
"""
import hashlib
import json
import math
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from .circuit_validation import CircuitContract, UnavailableCircuitData, validate_circuit_contract

CATALOG = {
    'Device:R': ['Resistor_SMD:R_0603_1608Metric', 'Resistor_SMD:R_0805_2012Metric'],
    'Device:C': ['Capacitor_SMD:C_0603_1608Metric', 'Capacitor_SMD:C_0805_2012Metric'],
    'Device:LED': ['LED_SMD:LED_0603_1608Metric', 'LED_SMD:LED_0805_2012Metric'],
    'Connector_Generic:Conn_01x02': ['Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical'],
    'Connector_Generic:Conn_01x04': ['Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical'],
}
CATALOG_VERSION = 'passives-connectors-kicad-9.0.8-v1'


def _quote(value):
    return json.dumps(str(value), ensure_ascii=False)


def _block(source, start):
    depth, quoted, escaped = 0, False, False
    for end in range(start, len(source)):
        c = source[end]
        if quoted:
            if escaped:
                escaped = False
            elif c == '\\':
                escaped = True
            elif c == '"':
                quoted = False
        elif c == '"':
            quoted = True
        elif c == '(':
            depth += 1
        elif c == ')':
            depth -= 1
            if not depth:
                return source[start:end + 1]
    raise UnavailableCircuitData('Incomplete image-owned symbol')


def load_symbol(identity, root):
    library, name = identity.split(':')
    source = (root / 'symbols' / (library + '.kicad_sym')).read_text()
    marker = '(symbol ' + _quote(name)
    start = source.find(marker + '\n')
    if start < 0:
        start = source.find(marker + ' ')
    if start < 0:
        raise UnavailableCircuitData('Qualified symbol is missing: ' + identity)
    symbol = _block(source, start)
    if '(extends ' in symbol:
        raise UnavailableCircuitData('Inherited symbols require catalog expansion qualification')
    # Pin blocks have a nested name/number and a library-local anchor.
    import re
    pins = {}
    for found in re.finditer(r'\(pin\s+(?:input|output|bidirectional|tri_state|passive|free|unspecified|power_in|power_out|open_collector|open_emitter|no_connect)\s', symbol):
        block = _block(symbol, found.start())
        number = re.search(r'\(number\s+"([^"]+)"', block)
        at = re.search(r'\(at\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\)', block)
        if not number or not at or number[1] in pins:
            raise UnavailableCircuitData('Ambiguous qualified symbol pins')
        pins[number[1]] = (float(at[1]), float(at[2]))
    if not pins:
        raise UnavailableCircuitData('No qualified symbol pins')
    return symbol.replace(marker, '(symbol ' + _quote(identity), 1), pins


def catalog_manifest(root=Path('/usr/share/kicad')):
    parts = []
    for symbol, footprints in sorted(CATALOG.items()):
        raw, pins = load_symbol(symbol, root)
        for footprint in footprints:
            library, name = footprint.split(':')
            path = root / 'footprints' / (library + '.pretty') / (name + '.kicad_mod')
            parts.append({'symbol': symbol, 'footprint': footprint, 'pins': sorted(pins),
                'symbolSha256': hashlib.sha256(raw.encode()).hexdigest(),
                'footprintSha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    return {'schemaVersion': 'offline-parts-v1', 'version': CATALOG_VERSION, 'parts': parts,
            'scope': 'Literal symbol/pin/package pairs; not ratings, component sourcing or physical performance'}


def compile_circuit(interfaces, directory, root=Path('/usr/share/kicad'), catalog=Path('/opt/kleos-parts/manifest.json')):
    check = validate_circuit_contract(interfaces)
    if check.status != 'PASS':
        raise UnavailableCircuitData('A complete accepted circuit and power load contract is required')
    contract = CircuitContract.model_validate(interfaces['circuit'])
    manifest = json.loads(catalog.read_text())
    if manifest != catalog_manifest(root):
        raise UnavailableCircuitData('Offline part library integrity/version mismatch')
    qualified = {(p['symbol'], p['footprint']) for p in manifest['parts']}
    mechanical = interfaces.get('mechanical', {})
    board_id = interfaces.get('electronics', {}).get('designIntent', {}).get('boardConceptPartId')
    board = next((p for p in mechanical.get('components', []) if p.get('id') in {'pcb', 'board'}
                  or board_id and p.get('conceptPartId') == board_id), None)
    dimensions = interfaces.get('electronics', {}).get('boardMm') or (board or {}).get('sizeMm')
    if not isinstance(dimensions, dict) or any(isinstance(dimensions.get(a), bool) or not isinstance(dimensions.get(a), (int, float))
            or not math.isfinite(dimensions[a]) or not 5 <= dimensions[a] <= 300 for a in 'xy'):
        raise UnavailableCircuitData('Accept an explicit 5–300mm board envelope in electronics.boardMm or the PCB clearance component')
    width, height = dimensions['x'], dimensions['y']
    key = hashlib.sha256(json.dumps(interfaces['circuit'], sort_keys=True).encode()).hexdigest()
    def uid(name):
        return str(uuid5(NAMESPACE_URL, key + ':' + name))
    embedded, symbols, labels = {}, [], []
    parts = []
    positions = interfaces.get('electronics', {}).get('placements', [])
    if not isinstance(positions, list) or any(not isinstance(p, dict) for p in positions):
        raise UnavailableCircuitData('PCB placements must be reference/xMm/yMm records')
    explicit = {p.get('reference'): p for p in positions}
    if len(explicit) != len(positions) or set(explicit) - {p.reference for p in contract.components}:
        raise UnavailableCircuitData('PCB placement identities are ambiguous')
    bound_board = next((p for p in mechanical.get('designIntent', {}).get('components', [])
                        if board_id and p.get('conceptPartId') == board_id), {})
    board_shape = 'circle' if bound_board.get('shape') == 'cylinder' else 'rectangle'
    if board_shape == 'circle' and not math.isclose(width, height, abs_tol=.001):
        raise UnavailableCircuitData('Approved circular PCB requires equal X/Y diameters')
    if board and bound_board:
        angle = math.radians(bound_board.get('rotation', {}).get('z', 0))
        cx, cy = (board['positionMm'][a] + dimensions[a]/2 - mechanical['internalMm'][a]/2 for a in 'xy')
        for mapping in interfaces.get('electronics', {}).get('designIntent', {}).get('componentReferences', []):
            if mapping['reference'] in explicit:
                continue
            region = mapping['region']['center']
            dx, dy = region['x']*mechanical['externalMm']['x']-cx, region['y']*mechanical['externalMm']['y']-cy
            explicit[mapping['reference']] = {'xMm': width/2 + dx*math.cos(angle)+dy*math.sin(angle),
                                             'yMm': height/2 + dx*math.sin(angle)-dy*math.cos(angle)}
    count = len(contract.components)
    columns = math.ceil(math.sqrt(count))
    rows = math.ceil(count / columns)
    for index, part in enumerate(sorted(contract.components, key=lambda c: c.reference)):
        if (part.symbol, part.footprint) not in qualified:
            raise UnavailableCircuitData(f'{part.reference}: symbol/footprint pair is outside the vetted offline catalog')
        symbol, pins = load_symbol(part.symbol, root)
        if set(pins) != set(part.pins):
            raise UnavailableCircuitData(f'{part.reference}: accepted pins differ from the qualified symbol')
        embedded[part.symbol] = symbol
        sx, sy = 76.2 + index % 5 * 25.4, 76.2 + index // 5 * 25.4
        fields = ''.join(f'(property {_quote(k)} {_quote(v)} (at {sx+4} {sy+offset} 0) '
            '(effects (font (size 1.27 1.27)))' + ('(hide yes)' if k == 'Footprint' else '') + ')'
            for k, v, offset in [('Reference', part.reference, -2), ('Value', part.value, 2), ('Footprint', part.footprint, 4)])
        pin_uuids = ''.join(f'(pin {_quote(p)} (uuid {uid(part.reference+":"+p)}))' for p in sorted(pins))
        symbols.append(f'(symbol (lib_id {_quote(part.symbol)}) (at {sx} {sy} 0) (unit 1) (in_bom yes) (on_board yes) (dnp no) '
            f'(uuid {uid(part.reference)}) {fields}{pin_uuids}(instances (project "circuit" '
            f'(path "/{uid("root")}" (reference {_quote(part.reference)}) (unit 1)))))')
        for pin, net in sorted(part.pins.items()):
            x, y = sx + pins[pin][0], sy - pins[pin][1]
            ident = uid(part.reference + ':net:' + pin)
            labels.append(f'(no_connect (at {x} {y})(uuid {ident}))' if net is None else
                f'(global_label {_quote(net)} (shape bidirectional) (at {x} {y} 0)(effects (font (size 1.27 1.27))'
                f'(justify left bottom))(uuid {ident}))')
        placement = explicit.get(part.reference, {})
        x = placement.get('xMm', width * (index % columns + 1) / (columns + 1))
        y = placement.get('yMm', height * (index // columns + 1) / (rows + 1))
        angle = placement.get('rotationDegrees', 0)
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in (x, y, angle)):
            raise UnavailableCircuitData('Placement coordinates must be finite millimetres')
        parts.append({**part.model_dump(mode='json', by_alias=True), 'xMm': x, 'yMm': y, 'rotationDegrees': angle})
    directory.mkdir(parents=True, exist_ok=True)
    schematic = f'(kicad_sch (version 20250114)(generator "eeschema")(uuid {uid("root")})(paper "A3")' + \
        '(lib_symbols ' + ''.join(embedded[k] for k in sorted(embedded)) + ')' + ''.join(symbols+labels) + ')\n'
    (directory / 'circuit.kicad_sch').write_text(schematic)
    placement = {'schemaVersion': 'compiled-pcb-v1', 'contractSha256': key, 'catalogVersion': CATALOG_VERSION,
                 'widthMm': width, 'heightMm': height, 'shape': board_shape, 'parts': parts}
    (directory / 'placement.json').write_text(json.dumps(placement, sort_keys=True, indent=2))
    return placement


PCB_PLACE_SCRIPT = r'''
import json,sys,pcbnew
from pathlib import Path
p=Path(sys.argv[1]); data=json.loads((p/'placement.json').read_text()); b=pcbnew.BOARD(); b.SetCopperLayerCount(2)
def vec(x,y): return pcbnew.VECTOR2I(pcbnew.FromMM(x),pcbnew.FromMM(y))
nets={}
for name in sorted({n for c in data['parts'] for n in c['pins'].values() if n is not None}):
 net=pcbnew.NETINFO_ITEM(b,name);b.Add(net);nets[name]=net
bounds=[]
for part in data['parts']:
 lib,name=part['footprint'].split(':'); fp=pcbnew.FootprintLoad('/usr/share/kicad/footprints/'+lib+'.pretty',name)
 if fp is None: raise ValueError('Missing qualified footprint')
 fp.SetReference(part['reference']);fp.SetValue(part['value']);fp.SetFPIDAsString(part['footprint'])
 fp.SetPosition(vec(part['xMm'],part['yMm']));fp.SetOrientationDegrees(part['rotationDegrees'])
 pads={pad.GetNumber():pad for pad in fp.Pads()}
 if set(pads)!=set(part['pins']) or len(pads)!=len(list(fp.Pads())):raise ValueError('Package and accepted symbol pins differ')
 for number,net in part['pins'].items():
  if net is not None:pads[number].SetNet(nets[net])
 b.Add(fp)
 # Ignore reference/value text in physical placement; real DRC remains mandatory.
 for text in (fp.Reference(),fp.Value()):text.SetVisible(False)
 box=fp.GetBoundingBox(False,False)
 x0,y0=pcbnew.ToMM(box.GetX()),pcbnew.ToMM(box.GetY());x1,y1=pcbnew.ToMM(box.GetRight()),pcbnew.ToMM(box.GetBottom())
 if x0<1 or y0<1 or x1>data['widthMm']-1 or y1>data['heightMm']-1:raise ValueError('Placed package exceeds accepted board envelope')
 for a,c,d,e in bounds:
  if not(x1+.5<a or x0-.5>d or y1+.5<c or y0-.5>e):raise ValueError('Placed package envelopes overlap')
 bounds.append((x0,y0,x1,y1))
w,h=data['widthMm'],data['heightMm']
for start,end in ([] if data['shape']=='circle' else [((0,0),(w,0)),((w,0),(w,h)),((w,h),(0,h)),((0,h),(0,0))]):
 edge=pcbnew.PCB_SHAPE();edge.SetShape(pcbnew.SHAPE_T_SEGMENT);edge.SetStart(vec(*start));edge.SetEnd(vec(*end));edge.SetWidth(pcbnew.FromMM(.05));edge.SetLayer(pcbnew.Edge_Cuts);b.Add(edge)
if data['shape']=='circle':
 edge=pcbnew.PCB_SHAPE();edge.SetShape(pcbnew.SHAPE_T_CIRCLE);edge.SetCenter(vec(w/2,h/2));edge.SetEnd(vec(w,h/2));edge.SetWidth(pcbnew.FromMM(.05));edge.SetLayer(pcbnew.Edge_Cuts);b.Add(edge)
pcbnew.SaveBoard(str(p/'circuit.kicad_pcb'),b)
if not pcbnew.ExportSpecctraDSN(b,str(p/'circuit.dsn')):raise ValueError('DSN export failed')
'''
PCB_IMPORT_SCRIPT = r'''
import pcbnew,sys
from pathlib import Path
p=Path(sys.argv[1]);b=pcbnew.LoadBoard(str(p/'circuit.kicad_pcb'))
if not pcbnew.ImportSpecctraSES(b,str(p/'circuit.ses')):raise ValueError('Autorouter session import failed')
pcbnew.SaveBoard(str(p/'circuit.kicad_pcb'),b)
'''
