"""Trusted fixture creator. Run only inside the fresh PCB image, never on the host."""
import json
import tempfile
from pathlib import Path
from uuid import uuid4

import pcbnew


def uuid():
    return str(uuid4())


def effects():
    return '(effects (font (size 1.27 1.27)))'


def library_symbol(name):
    return f'''(symbol "{name}" (pin_names (offset 0)) (in_bom yes) (on_board yes)
      (property "Reference" "J" (at 0 3.81 0) {effects()})
      (property "Value" "TwoPin" (at 0 -8.89 0) {effects()})
      (property "Footprint" "" (at 0 0 0) {effects()})
      (property "Datasheet" "" (at 0 0 0) {effects()})
      (symbol "TwoPin_0_1" (rectangle (start -2.54 2.54) (end 2.54 -7.62)
        (stroke (width 0.254) (type default)) (fill (type background))))
      (symbol "TwoPin_1_1"
        (pin passive line (at -5.08 0 0) (length 2.54) (name "1" {effects()}) (number "1" {effects()}))
        (pin passive line (at -5.08 -5.08 0) (length 2.54) (name "2" {effects()}) (number "2" {effects()}))))'''


def fixture():
    root = Path(tempfile.mkdtemp(prefix='pcb-fixture-', dir='/tmp'))
    board = pcbnew.BOARD()
    board.SetCopperLayerCount(2)
    nets = {}
    for number, name in enumerate(('/NET_A', '/NET_B'), 1):
        nets[name] = pcbnew.NETINFO_ITEM(board, name, number)
        board.Add(nets[name])
    library = root / 'Fixture.pretty'
    library.mkdir()
    for reference, value, x in [('J1', 'Connector', 10), ('R1', '1k', 30)]:
        part = pcbnew.FOOTPRINT(board)
        part.SetReference(reference)
        part.SetValue(value)
        part.SetFPID(pcbnew.LIB_ID('Fixture', 'TwoPin'))
        part.SetAttributes(pcbnew.FP_THROUGH_HOLE)
        part.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(x), pcbnew.FromMM(12)))
        for number, net in enumerate(nets.values(), 1):
            pad = pcbnew.PAD(part)
            pad.SetNumber(str(number))
            pad.SetAttribute(pcbnew.PAD_ATTRIB_PTH)
            pad.SetShape(pcbnew.PAD_SHAPE_CIRCLE)
            pad.SetSize(pcbnew.VECTOR2I(pcbnew.FromMM(2), pcbnew.FromMM(2)))
            pad.SetDrillSize(pcbnew.VECTOR2I(pcbnew.FromMM(1), pcbnew.FromMM(1)))
            pad.SetLayerSet(pcbnew.PAD.PTHMask())
            pad.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(x), pcbnew.FromMM(12 + (number - 1) * 5.08)))
            pad.SetNet(net)
            part.Add(pad)
        part.Reference().SetTextSize(pcbnew.VECTOR2I(pcbnew.FromMM(1), pcbnew.FromMM(1)))
        part.Reference().SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(x), pcbnew.FromMM(9)))
        part.Value().SetVisible(False)
        board.Add(part)
        if reference == 'J1':
            pcbnew.PCB_IO_KICAD_SEXPR().FootprintSave(str(library), part)
    points = [(0, 0), (40, 0), (40, 30), (0, 30), (0, 0)]
    for first, second in zip(points, points[1:]):
        line = pcbnew.PCB_SHAPE(board)
        line.SetShape(pcbnew.SHAPE_T_SEGMENT)
        line.SetStart(pcbnew.VECTOR2I(pcbnew.FromMM(first[0]), pcbnew.FromMM(first[1])))
        line.SetEnd(pcbnew.VECTOR2I(pcbnew.FromMM(second[0]), pcbnew.FromMM(second[1])))
        line.SetLayer(pcbnew.Edge_Cuts)
        line.SetWidth(pcbnew.FromMM(.05))
        board.Add(line)
    pcbnew.SaveBoard(str(root / 'board.kicad_pcb'), board)
    sheet_id = uuid()
    instances = []
    for reference, value, x in [('J1', 'Connector', 40.64), ('R1', '1k', 81.28)]:
        instances.append(f'''(symbol (lib_id "Fixture:TwoPin") (at {x} 50.8 0) (unit 1)
          (in_bom yes) (on_board yes) (dnp no) (uuid "{uuid()}")
          (property "Reference" "{reference}" (at {x} 44.92 0) {effects()})
          (property "Value" "{value}" (at {x} 60.16 0) {effects()})
          (property "Footprint" "Fixture:TwoPin" (at {x} 50.8 0) (effects (font (size 1.27 1.27)) hide))
          (pin "1" (uuid "{uuid()}")) (pin "2" (uuid "{uuid()}"))
          (instances (project "circuit" (path "/{sheet_id}" (reference "{reference}") (unit 1)))))''')
        for name, y in [('NET_A', 50.8), ('NET_B', 55.88)]:
            instances.append(f'(label "{name}" (at {x - 5.08:.2f} {y} 0) {effects()} (uuid "{uuid()}"))')
    schematic = f'''(kicad_sch (version 20250114) (generator "eeschema") (uuid "{sheet_id}")
      (paper "A4") (lib_symbols {library_symbol('Fixture:TwoPin')})
      {' '.join(instances)} (sheet_instances (path "/" (page "1"))) (embedded_fonts no))'''
    (root / 'circuit.kicad_sch').write_text(schematic)
    (root / 'fixture.kicad_sym').write_text('(kicad_symbol_lib (version 20241209) (generator "kicad_symbol_editor") '
                                          + library_symbol('TwoPin') + ')')
    (root / 'fp-lib-table').write_text('(fp_lib_table (version 7) (lib (name "Fixture") (type "KiCad") '
                                      '(uri "${KIPRJMOD}/Fixture.pretty") (options "") (descr "")))')
    (root / 'sym-lib-table').write_text('(sym_lib_table (version 7) (lib (name "Fixture") (type "KiCad") '
                                       '(uri "${KIPRJMOD}/fixture.kicad_sym") (options "") (descr "")))')
    return {str(path.relative_to(root)): path.read_text() for path in root.rglob('*') if path.is_file()}


if __name__ == '__main__':
    print(json.dumps(fixture()))
