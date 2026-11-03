"""A starter KiCad project derived mechanically from the accepted circuit contract.

The contract already names every component, its value and footprint, and the net on every pin, so the schematic is
not something a model should have to reason out. This module writes that connection table as a KiCad 9 project:
local symbol and footprint libraries, a schematic whose nets are global labels (so exported net names match the
contract exactly, with no sheet prefix), and a board that places every footprint inside the outline with its pads
assigned to the contract's nets. Pin keys are used as pin and pad numbers, which is what the checker compares.

Everything produced is a candidate: the footprints are plain through-hole pads on a coarse grid, not vendor
geometry, and the independent checks and routing still decide whether the result is acceptable.
"""
import json
import re
import uuid
from itertools import count
from pathlib import Path

QUALIFICATION = Path(__file__).resolve().parent / 'qualification'

VERSION = 'pcb-contract-scaffold-v1'
LIBRARY = 'Project'
NAMESPACE = uuid.UUID('3f0d9a5e-6b6c-4d5b-9f5e-8f8a7d2c1b00')
PIN_PITCH = 2.54
PAD_PITCH = 2.0
PAD_SIZE, PAD_DRILL = 1.2, 0.6
GAP = 2.5
MARGIN = 2.0
FONT = '(effects (font (size 1.27 1.27)))'


def _id(*parts):
    return str(uuid.uuid5(NAMESPACE, '/'.join(parts)))


def _natural(reference):
    return [int(piece) if piece.isdigit() else piece for piece in re.split(r'(\d+)', reference)]


def _number(value):
    return f'{value:.4f}'.rstrip('0').rstrip('.')


def _quote(text):
    return '"' + text.replace('\\', '\\\\').replace('"', '\\"') + '"'


def _parts(contract):
    return sorted(contract['references'], key=lambda item: _natural(item['reference']))


def connection_table(contract):
    """Net name to the sorted `REF.PIN` members, the connections the schematic has to realize."""
    nets = {}
    for part in contract['references']:
        for pin, net in part['pins'].items():
            nets.setdefault(net, []).append(f"{part['reference']}.{pin}")
    return {net: sorted(members, key=lambda text: (_natural(text.split('.')[0]), text)) for net, members in sorted(nets.items())}


def _symbol(name, part, *, library):
    pins = list(part['pins'])
    height = PIN_PITCH * len(pins)
    top = f'    (symbol {_quote((library + ":" if library else "") + name)} (pin_names (offset 0)) (in_bom yes) (on_board yes)\n'
    props = (f'      (property "Reference" {_quote(re.match(r"[A-Z]+", part["reference"]).group())} (at 0 {_number(PIN_PITCH + 1.27)} 0) {FONT})\n'
             f'      (property "Value" {_quote(part["value"])} (at 0 {_number(-height - 1.27)} 0) {FONT})\n'
             f'      (property "Footprint" {_quote(part["footprint"])} (at 0 0 0) {FONT})\n'
             f'      (property "Datasheet" "" (at 0 0 0) {FONT})\n')
    body = (f'      (symbol {_quote(name + "_0_1")} (rectangle (start -2.54 {_number(PIN_PITCH)}) (end 2.54 {_number(-height)})\n'
            '        (stroke (width 0.254) (type default)) (fill (type background))))\n'
            f'      (symbol {_quote(name + "_1_1")}\n')
    for index, pin in enumerate(pins):
        body += (f'        (pin passive line (at -5.08 {_number(-PIN_PITCH * index)} 0) (length 2.54) '
                 f'(name {_quote(pin)} {FONT}) (number {_quote(pin)} {FONT}))\n')
    return top + props + body.rstrip('\n') + '))\n'


def _symbols_library(contract):
    parts = _parts(contract)
    return ('(kicad_symbol_lib (version 20241209) (generator "kicad_symbol_editor")\n'
            + ''.join(_symbol(part['reference'], part, library='').replace('    (symbol', '  (symbol', 1) for part in parts) + ')\n')


def _schematic(contract):
    root = _id('schematic')
    out = [f'(kicad_sch (version 20250114) (generator "eeschema") (uuid {_quote(root)})\n  (paper "A3") (lib_symbols\n']
    parts = _parts(contract)
    out += [_symbol(part['reference'], part, library=LIBRARY) for part in parts]
    out.append('  )\n')
    # Every coordinate is a multiple of the 2.54 mm grid, or ERC warns that pins are off the connection grid.
    column, y_cursor, top = 0, 16 * PIN_PITCH, 16 * PIN_PITCH
    for part in parts:
        height = PIN_PITCH * len(part['pins'])
        x = 20 * PIN_PITCH + column * 30 * PIN_PITCH
        y = y_cursor + 3 * PIN_PITCH
        ref = part['reference']
        out.append(
            f'  (symbol (lib_id {_quote(LIBRARY + ":" + ref)}) (at {_number(x)} {_number(y)} 0) (unit 1)\n'
            f'    (in_bom yes) (on_board yes) (dnp no) (uuid {_quote(_id("symbol", ref))})\n'
            f'    (property "Reference" {_quote(ref)} (at {_number(x)} {_number(y - PIN_PITCH - 1.27)} 0) {FONT})\n'
            f'    (property "Value" {_quote(part["value"])} (at {_number(x)} {_number(y + height + 1.27)} 0) {FONT})\n'
            f'    (property "Footprint" {_quote(part["footprint"])} (at {_number(x)} {_number(y)} 0) (effects (font (size 1.27 1.27)) hide))\n')
        for pin in part['pins']:
            out.append(f'    (pin {_quote(pin)} (uuid {_quote(_id("pin", ref, pin))}))\n')
        out.append(f'    (instances (project "circuit" (path "/{root}" (reference {_quote(ref)}) (unit 1)))))\n')
        for index, (pin, net) in enumerate(part['pins'].items()):
            out.append(
                f'  (global_label {_quote(net)} (shape passive) (at {_number(x - 5.08)} {_number(y + PIN_PITCH * index)} 180) '
                f'(effects (font (size 1.27 1.27)) (justify right)) (uuid {_quote(_id("label", ref, pin))})\n'
                f'    (property "Intersheetrefs" "${{INTERSHEET_REFS}}" (at 0 0 0) (effects (font (size 1.27 1.27)) (justify left) (hide yes))))\n')
        y_cursor += height + 4 * PIN_PITCH
        if y_cursor > 100 * PIN_PITCH:
            column, y_cursor = column + 1, top
    out.append('  (sheet_instances (path "/" (page "1"))) (embedded_fonts no))\n')
    return ''.join(out)


def _pad_positions(count_of_pads):
    """Pad centres relative to the footprint origin, on a grid that grows with the pin count.

    Up to four pads sit in one column; more use two columns, and parts beyond sixteen pads add columns so that
    no footprint is taller than eight rows. The pad numbers, not the geometry, are what the contract constrains.
    """
    if count_of_pads <= 4:
        return [(0.0, PAD_PITCH * index) for index in range(count_of_pads)]
    columns = max(2, -(-count_of_pads // 8))
    rows = -(-count_of_pads // columns)
    return [((index // rows) * 4.0, PAD_PITCH * (index % rows)) for index in range(count_of_pads)]


def _extent(pads):
    return (max(x for x, _ in pads) + PAD_SIZE, max(y for _, y in pads) + PAD_SIZE)


def _property(name, value, layer, hide, x, y, ident):
    return (f'\t\t(property {_quote(name)} {_quote(value)}\n\t\t\t(at {x} {y} 0)\n\t\t\t(layer {_quote(layer)})\n'
            + ('\t\t\t(hide yes)\n' if hide else '') + f'\t\t\t(uuid {_quote(ident)})\n'
            '\t\t\t(effects\n\t\t\t\t(font\n\t\t\t\t\t(size 1 1)\n\t\t\t\t\t(thickness 0.15)\n\t\t\t\t)\n\t\t\t)\n\t\t)\n')


def _pad(number, x, y, net_index, net_name, ident):
    net = f'\t\t\t(net {net_index} {_quote(net_name)})\n' if net_index else ''
    return (f'\t\t(pad {_quote(number)} thru_hole circle\n\t\t\t(at {_number(x)} {_number(y)})\n'
            f'\t\t\t(size {PAD_SIZE} {PAD_SIZE})\n\t\t\t(drill {PAD_DRILL})\n\t\t\t(layers "*.Cu" "*.Mask")\n'
            f'\t\t\t(remove_unused_layers no)\n{net}\t\t\t(uuid {_quote(ident)})\n\t\t)\n')


def _footprint_block(part, x, y, net_ids):
    pads = _pad_positions(len(part['pins']))
    ref = part['reference']
    out = (f'\t(footprint {_quote(part["footprint"])}\n\t\t(layer "F.Cu")\n\t\t(uuid {_quote(_id("footprint", ref))})\n'
           f'\t\t(at {_number(x)} {_number(y)})\n')
    out += _property('Reference', ref, 'F.SilkS', False, 0, -3, _id('ref', ref))
    out += _property('Value', part['value'], 'F.Fab', True, 0, 0, _id('value', ref))
    out += _property('Datasheet', '', 'F.Fab', True, 0, 0, _id('datasheet', ref))
    out += _property('Description', '', 'F.Fab', True, 0, 0, _id('description', ref))
    out += '\t\t(attr through_hole)\n'
    for (px, py), (pin, net) in zip(pads, part['pins'].items(), strict=True):
        out += _pad(pin, px, py, net_ids[net], net, _id('pad', ref, pin))
    return out + '\t\t(embedded_fonts no)\n\t)\n'


def _shelves(boxes, width, height, gap):
    placed, x, y, shelf = {}, MARGIN + PAD_SIZE / 2, MARGIN + 4.0, 0.0
    for part, w, h in boxes:
        if x + w > width - MARGIN:
            x, y, shelf = MARGIN + PAD_SIZE / 2, y + shelf + gap + 3.0, 0.0
        if y + h > height - MARGIN:
            return None
        placed[part['reference']] = (x, y)
        x, shelf = x + w + gap, max(shelf, h)
    return placed


def _layout(parts, width, height):
    """Shelf-pack the footprints inside the outline, tallest first, with the widest gaps that still fit.

    Spreading parts over the whole board, not packing them into one corner, leaves the router room to reach every
    pad of a busy net such as ground; a part that cannot fit even at the tightest gap is refused, not squeezed.
    """
    boxes = []
    for part in parts:
        w, h = _extent(_pad_positions(len(part['pins'])))
        boxes.append((part, w, h))
    boxes.sort(key=lambda item: (-item[2], _natural(item[0]['reference'])))
    for gap in (9.0, 7.0, 6.0, 5.0, 4.0, 3.0, GAP):
        placed = _shelves(boxes, width, height, gap)
        if placed is not None:
            return placed
    raise ValueError('The accepted components do not fit the board outline at the scaffold grid')


def _board(contract):
    limits = contract['constraints']
    width, height = limits['maxWidthMm'] - 0.2, limits['maxHeightMm'] - 0.2
    parts = _parts(contract)
    nets = sorted({net for part in parts for net in part['pins'].values()})
    net_ids = {net: index for index, net in enumerate(nets, start=1)}
    placed = _layout(parts, width, height)
    head = open_template()
    body = '\t(net 0 "")\n' + ''.join(f'\t(net {index} {_quote(net)})\n' for net, index in net_ids.items())
    body += ''.join(_footprint_block(part, *placed[part['reference']], net_ids) for part in parts)
    edges = [((0, 0), (width, 0)), ((0, height), (0, 0)), ((width, 0), (width, height)), ((width, height), (0, height))]
    counter = count()
    for start, end in edges:
        body += (f'\t(gr_line\n\t\t(start {_number(start[0])} {_number(start[1])})\n\t\t(end {_number(end[0])} {_number(end[1])})\n'
                 f'\t\t(stroke\n\t\t\t(width 0.05)\n\t\t\t(type default)\n\t\t)\n\t\t(layer "Edge.Cuts")\n'
                 f'\t\t(uuid {_quote(_id("edge", str(next(counter))))})\n\t)\n')
    return head + body + '\t(embedded_fonts no)\n)\n'


def open_template():
    """The board header (layers, setup) of the fixture the real toolchain accepted."""
    board = json.loads((QUALIFICATION / 'pcb-fixture-sources.json').read_text())['board.kicad_pcb']
    return board[:board.index('\t(net 0 "")')]


def _footprint_library(contract):
    """`Nick.pretty/Name.kicad_mod` files: plain numbered pads, one per distinct footprint name."""
    files, seen = {}, {}
    for part in _parts(contract):
        nick, name = part['footprint'].split(':', 1)
        pins = list(part['pins'])
        if (nick, name) in seen and seen[(nick, name)] != pins:
            pins = list(dict.fromkeys([*seen[(nick, name)], *pins]))
        seen[(nick, name)] = pins
    for (nick, name), pins in seen.items():
        pads = _pad_positions(len(pins))
        text = (f'(footprint {_quote(name)}\n\t(version 20241229)\n\t(generator "pcbnew")\n\t(generator_version "9.0")\n'
                '\t(layer "F.Cu")\n' + _property('Reference', 'REF**', 'F.SilkS', False, 0, -3, _id('lib-ref', nick, name))
                .replace('\t\t', '\t').replace('\t\t\t', '\t\t')
                + '\t(attr through_hole)\n')
        for (x, y), pin in zip(pads, pins, strict=True):
            text += (f'\t(pad {_quote(pin)} thru_hole circle\n\t\t(at {_number(x)} {_number(y)})\n\t\t(size {PAD_SIZE} {PAD_SIZE})\n'
                     f'\t\t(drill {PAD_DRILL})\n\t\t(layers "*.Cu" "*.Mask")\n\t\t(remove_unused_layers no)\n'
                     f'\t\t(uuid {_quote(_id("lib-pad", nick, name, pin))})\n\t)\n')
        files[f'{nick}.pretty/{name}.kicad_mod'] = text + '\t(embedded_fonts no)\n)\n'
    return files


def scaffold(contract):
    """Source files (path to text) for a starter project that realizes the accepted circuit contract."""
    files = {
        'sym-lib-table': '(sym_lib_table (version 7) (lib (name "Project") (type "KiCad") (uri "${KIPRJMOD}/project.kicad_sym") '
                         '(options "") (descr "")))\n',
        'project.kicad_sym': _symbols_library(contract),
        'circuit.kicad_sch': _schematic(contract),
        'board.kicad_pcb': _board(contract),
    }
    library = _footprint_library(contract)
    nicks = sorted({path.split('.pretty/')[0] for path in library})
    files['fp-lib-table'] = ('(fp_lib_table (version 7)' + ''.join(
        f' (lib (name {_quote(nick)}) (type "KiCad") (uri "${{KIPRJMOD}}/{nick}.pretty") (options "") (descr ""))' for nick in nicks) + ')\n')
    files.update(library)
    return files
