"""Generate a real KiCad board from the accepted circuit contract. Runs only inside the isolated PCB image.

Footprints come from KiCad's own library (`/usr/share/kicad/footprints`), loaded through pcbnew, so the board carries
real pad geometry, silkscreen and courtyards. A part whose library or footprint is missing, or whose real footprint
lacks a pad the contract names, keeps the generic starter footprint seeded by the server and is reported as a finding.
Parts are placed by connectivity (connectors first, then outward), inside an outline sized to what was placed.

Usage: python3 -I pcb_generate.py <contract.json> <project directory> <output directory>
Writes board.kicad_pcb, footprint files under <output>/files and findings.json. Everything is a candidate.
"""
import json
import math
import re
import sys
from pathlib import Path

import pcbnew

LIBRARY_ROOT = Path('/usr/share/kicad/footprints')
POWER = re.compile(r'^(GND|AGND|DGND|VSS|VBUS|VCC|VDD|V\d+V?\d*|\+?\d+V\d*|3V3|5V|VIN|VOUT)$', re.I)
MARGIN, GAP_START, GAP_MIN, EDGE = 2.5, 3.0, 1.0, 0.1


def mm(value):
    return pcbnew.FromMM(value)


def snap(value):
    """Integer nanometres on a 0.05 mm grid, exact through the router's micrometre interchange."""
    return round(value / 0.05) * 50000


def point(x, y):
    return pcbnew.VECTOR2I(snap(x), snap(y))


def natural(reference):
    return [int(p) if p.isdigit() else p for p in re.split(r'(\d+)', reference)]


def load(path, name):
    try:
        return pcbnew.FootprintLoad(str(path), name)
    except Exception:
        return None


def box_mm(footprint):
    """Courtyard box if the footprint has one, otherwise the bounding box without text, in mm."""
    courtyard = footprint.GetCourtyard(pcbnew.F_CrtYd)
    if courtyard is not None and courtyard.OutlineCount():
        box = courtyard.BBox()
    else:
        box = footprint.GetBoundingBox(False)
    return (pcbnew.ToMM(box.GetX()), pcbnew.ToMM(box.GetY()), pcbnew.ToMM(box.GetWidth()), pcbnew.ToMM(box.GetHeight()))


def edge_line(footprint):
    """Local y of the footprint's horizontal "PCB Edge" marker (drawing layer), or None when it has none."""
    for item in footprint.GraphicalItems():
        if item.GetLayer() == pcbnew.Dwgs_User and item.GetShape() == pcbnew.SHAPE_T_SEGMENT:
            a, b = item.GetStart(), item.GetEnd()
            if a.y == b.y and abs(pcbnew.ToMM(a.x - b.x)) >= 4:
                return pcbnew.ToMM(a.y)
    return None


def choose(part, project):
    """The footprint to use for a part, its source ('library' or 'generic') and any finding."""
    nick, name = part['footprint'].split(':', 1)
    pins = set(part['pins'])
    library = LIBRARY_ROOT / f'{nick}.pretty'
    if library.is_dir():
        real = load(library, name)
        if real is not None:
            numbers = {pad.GetNumber() for pad in real.Pads()}
            missing = sorted(pins - numbers)
            if not missing:
                return real, 'library', None
            note = f"{part['footprint']} has no pad {', '.join(missing)}; generic pads were used"
        else:
            note = f"{part['footprint']} is not in the KiCad library; generic pads were used"
    else:
        note = f"footprint library {nick} is not installed; generic pads were used"
    generic = load(project / f'{nick}.pretty', name)
    if generic is None:
        raise SystemExit(f"No footprint could be loaded for {part['reference']} ({part['footprint']})")
    return generic, 'generic', {'severity': 'warning', 'kind': 'footprint_fallback', 'reference': part['reference'],
                                'message': note}


def levels(parts):
    """Connectivity depth from the first connector (or the busiest part) over non-power nets."""
    nets = {}
    for part in parts:
        for net in part['pins'].values():
            if not POWER.match(net):
                nets.setdefault(net, set()).add(part['reference'])
    neighbours = {part['reference']: set() for part in parts}
    for members in nets.values():
        for a in members:
            neighbours[a] |= members - {a}
    connectors = [p['reference'] for p in parts if re.match(r'^(J|P|CN|USB|X)\d', p['reference'])]
    start = connectors[0] if connectors else max(neighbours, key=lambda r: (len(neighbours[r]), r))
    depth, queue = {start: 0}, [start]
    for ref in queue:
        for other in sorted(neighbours[ref], key=natural):
            if other not in depth:
                depth[other] = depth[ref] + 1
                queue.append(other)
    rest = max(depth.values(), default=0) + 1
    for part in parts:
        depth.setdefault(part['reference'], rest)  # parts only on power nets sit in the last column
    return depth


def place(parts, footprints, limits):
    """Fill columns top to bottom in connectivity order, wrapping to a new column when the height is used up.

    A part that marks its board edge (a USB receptacle, a barrel jack) is aligned to the bottom edge with its body
    overhanging it, in the first column; other parts stack above it while they fit.
    """
    boxes = {ref: box_mm(fp) for ref, fp in footprints.items()}
    edges = {ref: edge_line(fp) for ref, fp in footprints.items()}
    depth = levels(parts)
    order = [p['reference'] for p in sorted(parts, key=lambda p: (depth[p['reference']], natural(p['reference'])))]
    anchored = [r for r in order if edges[r] is not None][:1]
    order = anchored + [r for r in order if r not in anchored]
    inside = {r: (edges[r] - boxes[r][1]) if r in anchored else boxes[r][3] for r in order}  # height on the board
    max_w, max_h = limits['maxWidthMm'] - 2 * EDGE, limits['maxHeightMm'] - 2 * EDGE
    gap = GAP_START
    while True:
        usable, columns, current, used = max_h - 2 * MARGIN, [], [], 0.0
        for ref in order:
            need = inside[ref] + (gap if current else 0)
            if current and used + need > usable:
                columns.append(current)
                current, used, need = [], 0.0, inside[ref]
            current.append(ref)
            used += need
        columns.append(current)
        widths = [max(boxes[r][2] for r in refs) for refs in columns]
        total_w = sum(widths) + gap * (len(columns) - 1) + 2 * MARGIN
        tallest = max(inside.values())
        if (total_w <= max_w and tallest + 2 * MARGIN <= max_h) or gap <= GAP_MIN:
            break
        gap = max(GAP_MIN, gap - .5)
    heights = [sum(inside[r] for r in refs) + gap * (len(refs) - 1) for refs in columns]
    total_h = min(max_h, max(heights) + 2 * MARGIN)
    if total_w > max_w or tallest + 2 * MARGIN > max_h:
        raise SystemExit(f'The accepted components need {total_w:.1f} x {max(heights) + 2 * MARGIN:.1f} mm but the board '
                         f'is limited to {max_w:.1f} x {max_h:.1f} mm')
    positions, x = {}, MARGIN
    for refs, width, height in zip(columns, widths, heights, strict=True):
        refs = [r for r in refs if r not in anchored] + [r for r in refs if r in anchored]  # edge part last
        y = MARGIN if anchored and anchored[0] in refs else (total_h - height) / 2
        for ref in refs:
            bx, by, bw, bh = boxes[ref]
            cx = x - bx + (width - bw) / 2
            if ref in anchored:
                positions[ref] = (cx, total_h - edges[ref])  # the edge marker lies on the board edge
            else:
                positions[ref] = (cx, y - by)
            y += inside[ref] + gap
        x += width + gap
    return positions, math.ceil(total_w / .05 - 1e-9) * .05, math.ceil(total_h / .05 - 1e-9) * .05


def outline(board, width, height, radius=1.5):
    layer, w = pcbnew.Edge_Cuts, mm(.05)
    def line(a, b):
        s = pcbnew.PCB_SHAPE(board)
        s.SetShape(pcbnew.SHAPE_T_SEGMENT)
        s.SetLayer(layer)
        s.SetWidth(w)
        s.SetStart(point(*a))
        s.SetEnd(point(*b))
        board.Add(s)
    def arc(centre, start, end):
        s = pcbnew.PCB_SHAPE(board)
        s.SetShape(pcbnew.SHAPE_T_ARC)
        s.SetLayer(layer)
        s.SetWidth(w)
        mid_angle = math.atan2((start[1] + end[1]) / 2 - centre[1], (start[0] + end[0]) / 2 - centre[0])
        mid = (centre[0] + radius * math.cos(mid_angle), centre[1] + radius * math.sin(mid_angle))
        s.SetArcGeometry(point(*start), point(*mid), point(*end))
        board.Add(s)
    r = radius
    for a, b in [((r, 0), (width - r, 0)), ((width, r), (width, height - r)),
                 ((width - r, height), (r, height)), ((0, height - r), (0, r))]:
        line(a, b)
    for corner in [((r, r), (0, r), (r, 0)), ((width - r, r), (width - r, 0), (width, r)),
                   ((width - r, height - r), (width, height - r), (width - r, height)),
                   ((r, height - r), (r, height), (0, height - r))]:
        arc(*corner)


def main(contract_path, project, out):
    contract = json.loads(Path(contract_path).read_text())
    project, out = Path(project), Path(out)
    parts = sorted(contract['references'], key=lambda p: natural(p['reference']))
    board = pcbnew.BOARD()
    board.SetCopperLayerCount(2)
    limits = contract['constraints']
    settings = board.GetDesignSettings()
    settings.m_MinClearance = mm(limits['minClearanceMm'])
    settings.m_TrackMinWidth = mm(limits['minTrackWidthMm'])
    nets = {}
    for net in sorted({n for p in parts for n in p['pins'].values()}):
        item = pcbnew.NETINFO_ITEM(board, net)
        board.Add(item)
        nets[net] = item
    findings, footprints, sources = [], {}, {}
    for part in parts:
        footprint, origin, note = choose(part, project)
        if note:
            findings.append(note)
        nick, name = part['footprint'].split(':', 1)
        library_file = (LIBRARY_ROOT / f'{nick}.pretty' / f'{name}.kicad_mod') if origin == 'library' else None
        if library_file is not None:
            sources[f'{nick}.pretty/{name}.kicad_mod'] = library_file.read_text()
        footprint.SetFPID(pcbnew.LIB_ID(nick, name))  # the board names its footprint exactly as the contract does
        footprint.SetReference(part['reference'])
        footprint.SetValue(part['value'])
        for pad in footprint.Pads():
            if pad.GetNumber() in part['pins']:
                pad.SetNet(nets[part['pins'][pad.GetNumber()]])
            else:
                pad.SetNetCode(0)  # unlisted mechanical or unused pads stay netless
        footprints[part['reference']] = footprint
    positions, width, height = place(parts, footprints, limits)
    for ref, footprint in footprints.items():
        footprint.SetPosition(point(*positions[ref]))
        board.Add(footprint)
    outline(board, width, height)
    out.mkdir(parents=True, exist_ok=True)
    if not pcbnew.SaveBoard(str(out / 'board.kicad_pcb'), board):
        raise SystemExit('KiCad could not save the generated board')
    files = out / 'files'
    for relative, text in sources.items():
        target = files / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    (out / 'generated.json').write_text(json.dumps({
        'version': 'pcb-kicad-library-v1', 'widthMm': round(width, 3), 'heightMm': round(height, 3),
        'library': sorted(sources), 'findings': findings}))
    print(json.dumps({'parts': len(parts), 'widthMm': round(width, 2), 'heightMm': round(height, 2), 'findings': len(findings)}))


if __name__ == '__main__':
    if len(sys.argv) != 4:
        raise SystemExit('usage: pcb_generate.py <contract.json> <project directory> <output directory>')
    main(*sys.argv[1:])
