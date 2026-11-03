# Engineering preview data

`kleos.api.previews.EngineeringPreviews(projects, artifacts).query(actor, operation,
params)` serves the existing workbench `engineering_preview` and
`circuit_simulation` operations. Runtime composition supplies this facade; it does
not start native work or call a model. No Flutter changes or generated DTO edits
are needed because the engineering preview response is an existing free-form
JSON contract.

Current published, nonsuperseded task artifacts are selected from active/completed
objectives at the current engineering context revision. Their immutable native
publication receipt must contain the exact references. A current accepted release
can provide its exact file closure after acceptance advances the revision. Tenant,
project, revision, receipt, artifact size and SHA-256 checks precede parsing. Source
text is bounded and displayed as text; it is never imported or evaluated.

CAD previews parse per-part STL data and require corresponding STEP identities
from the same native job. The optional native part manifest must match the files.
Vertices remain in manufacturing coordinates and transforms are identity maps.
The current Flutter assembly viewer already computes display-only exploded
translations. Additional `displayExplodeMm` metadata is advisory display data and
never modifies source/manufacturing bytes. Sampling is explicit and each part
receives its own triangle allowance; one detailed part cannot suppress another.

Bounds: 256 selected artifacts / 64 MiB total bytes, 24 CAD parts, 16 MiB binary
STL (2 MiB ASCII), 250,000 input triangles per part, 1,800 displayed triangles per
part and 12,000 total. Coordinates must be finite and bounded. STL syntax is read
with Python standard-library data operations, not a CAD kernel. Real existing
fixture STL parsing is recorded in `qualification/preview-data-2026-11-03.json`;
this is not another native-image or physical qualification.

The PCB reader uses the documented
[KiCad board S-expression format](https://dev-docs.kicad.org/en/file-formats/sexpr-pcb/)
and [common coordinates](https://dev-docs.kicad.org/en/file-formats/sexpr-intro/).
It reads only the exact published `outputs/routed.kicad_pcb`, without loading any
referenced model, library or script. The unchanged viewer represents rectangular
boards, component rectangles and straight front/back copper segments. Supported
previews therefore require a single rectangular Edge.Cuts outline and unrotated
footprints/pads. Component rectangles show actual pad extents, not inferred body
meshes. Origins are normalized for display and the original origin is included.
Vias, copper arcs and zones are explicitly listed as not drawn. Unsupported
outlines/orientations, duplicate references and excessive geometry produce an
unavailable/UNKNOWN preview, never substitute concept geometry. Parser bounds
are 8 MiB, 400,000 tokens, depth 64, 128 footprints, 512 pads per footprint and
256 straight tracks.

Firmware/app sources can be read as bounded text. Compiled app hosting remains
unavailable until a separate preview-origin service is connected. The circuit
simulation query returns `UNKNOWN` with no invented waveform or result. A visual
preview and successful native execution do not grant validation or acceptance.

Focused verification:

```bash
backend/.venv/bin/pytest backend/tests/test_previews.py -q
```
