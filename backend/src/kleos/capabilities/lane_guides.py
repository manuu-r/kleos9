"""Source contracts for the native lanes, given to a specialist once per turn as part of its instructions.

The native runner accepts only exact file names and rejects everything else, so a model that is not told the
contract spends its allowance reading files that do not exist. The worked examples are the fixtures that passed
the real toolchains during qualification, loaded verbatim so they cannot drift from what actually builds.
"""
import json
from functools import cache
from pathlib import Path

QUALIFICATION = Path(__file__).resolve().parents[1] / 'execution' / 'qualification'

WORKFLOW = '''Workspace and workflow: the workspace starts empty. Create each source with one `patch` whose
expectedSha256 is null (omit it for a new file); to change an existing file pass that file's current sha256 from
a `read` result. Paths are exact file paths, never directories or globs, and must be listed in the task's
allowedPaths. One call per turn; the lane's `build` must succeed before its `analyze`, `render` or `finish`.
Read the real error text from a failed build, repair only that cause, and rebuild. A passing build that
meets the brief is done: do not keep inspecting or re-reading files you already read (their content stays in
your history), and read a large file only in small windows around the part you will change. The lane's own `finish` tool
(for example `cad__finish`) is what publishes the built files as candidates, so call it after a successful build
and only then end the task with `complete_task`; ending without it leaves no deliverable.'''

CAD = WORKFLOW + '''

CAD source contract: exactly one file, `model.py`. It imports build123d and defines `parts`, a dict of 1-24 stable
part ids (letters, digits, `_`, `-`) to build123d solids in their real assembled coordinates, in millimetres.
Never bake exploded-view offsets into geometry. No network, no other files, only build123d and the standard
library. Model every independently manufactured part separately (for example case, lens, rear cover, button).
Each part must be a valid closed solid. Worked example that builds:

```python
from build123d import Box, Cylinder, Pos
base = Box(24, 16, 4) - Cylinder(2, 10)
lid = Pos(0, 0, 7) * Box(24, 16, 2)
parts = {"base": base, "lid": lid}
```
After a successful build, `analyze` reports each part's volume, size and validity, and `render` accepts the views
front, top or isometric. Record any assumed dimension as a comment and report it as UNKNOWN in the finish summary.'''


def _web():
    return json.loads((QUALIFICATION / 'web-fixture-sources.json').read_text())


def _files(sources, names):
    return '\n\n'.join(f'--- {name}\n{sources[name]}' for name in names)


@cache
def application():
    try:
        example = _files(_web(), ['pubspec.yaml', 'pubspec.lock', 'web/index.html', 'lib/main.dart'])
    except (OSError, ValueError, KeyError):
        example = '(example unavailable)'
    return WORKFLOW + '''

Application source contract (Flutter web, built with no network): the build needs exactly these tracked files
- `pubspec.yaml`, `pubspec.lock`, `web/index.html` and `lib/main.dart` - plus any further `lib/*.dart` files you
add. Dependencies resolve offline against a fixed cache, so the app may depend on `flutter` alone: no other pub
package, no plugins, no network or Bluetooth access. The lock file must match the pubspec and that cache
exactly, and a build that rewrites it fails, so patch `pubspec.yaml`, `pubspec.lock` and `web/index.html` with
the verbatim example files below (change only the package name and description) and author only `lib/main.dart`
and extra Dart files. Build a companion that is honest about what it can do: it cannot talk to the device, so show the
device's intended screens, controls and a local simulation of its state, labelled as such. Verbatim example:

''' + example


PCB = WORKFLOW + '''

PCB source contract (KiCad 9, two copper layers). The accepted circuit contract in the task context lists every
reference with its Value, Footprint and a net for each pin; it is authoritative and is checked exactly. The server
has already turned that connection table into a starter project in the workspace, so do not write KiCad from
scratch: `circuit.kicad_sch` (each reference once, pins numbered by the contract's pin keys, every net a global
label), `board.kicad_pcb`, and local libraries (`sym-lib-table`, `fp-lib-table`, `project.kicad_sym`,
`<Lib>.pretty/*.kicad_mod`). While `board.kicad_pcb` is still the starter, the first `build` replaces it with a real
board: footprints from KiCad's own library (real pads, silkscreen, courtyards; a footprint the library lacks keeps
the generic starter pads and is reported), placed by connectivity with connectors at the edge, inside a rounded
outline, all by script, so you never author placement for the sake of appearance.

Your work, in this order: (1) `build`, which compares the netlist and board to the contract, runs ERC, routes with
the offline router, removes dangling stubs, runs DRC, exports Gerbers and renders top and bottom previews. The result
lists `findings` (ERC and DRC violations, unconnected nets, footprint fallbacks). An unchanged project gives the same
result, so do not rebuild it: edit, or finish. (2) Change the placement only for a concrete requirement the brief
states that a generator cannot know (for example a button, port or antenna that must sit at an edge or corner). To
move a footprint, `read` just its block in a small window (a footprint block starts at its `(footprint` line),
`patch` `board.kicad_pcb` with its current sha256, and keep every footprint inside the outline. (3) Repair an
observed cause only when a small `patch` clearly fixes it, then rebuild once. When findings remain, still call
`finish`: the board, its previews and the findings are published so the user can see exactly what is wrong and ask
for a fix of just that; never hide a finding and never edit a reference, value, footprint, pad number, net or net
name, add or remove components, or exceed the contract's maximum size. Library files are inputs and are not
editable. Report assumptions you could not check, such as the footprints' electrical suitability, as UNKNOWN in the
finish summary.'''


GUIDES = {'cad': CAD}


def lane_guide(capability):
    """Instruction text for a lane, or an empty string for a lane without a contract guide."""
    if capability == 'application':
        return application()
    if capability == 'pcb':
        return PCB
    return GUIDES.get(capability, '')
