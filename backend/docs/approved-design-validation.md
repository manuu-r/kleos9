# Approved device reference and actual engineering geometry

An explicitly confirmed capture scene freezes the enclosure family, coarse outer
proportions and component regions in `requirements.designIntent`. Millimetres
still come from accepted requirements and engineering interfaces. An illustration
does not establish component dimensions, printability or physical performance.

The independent native validator requires three checks for projects with that
frozen reference: `approved_design_contract`, `approved_enclosure_form` and
`approved_pcb_layout`. These checks bind their evidence to the current approved
scene hash, approval version and actual candidate file hashes. A missing check,
`UNKNOWN` or `FAIL` cannot authorize release. Older projects without an approved
reference explicitly exclude these checks rather than inventing an approval.

## Qualified scope

The initial supported body is one unrotated Z-axis circle, rectangular box or
rounded rectangular prism. The rounded radius follows the pinned capture
renderer ratio, 0.13 times the smaller outer XY dimension; inner radius subtracts
the accepted minimum-wall allowance. Rotated bodies, ellipses, spherical bodies
and multiple primary shells remain `UNKNOWN` pending additional qualification.

The server binds required board, battery and internal-sensor identity mappings
from the frozen reference. Surface displays, controls, connectors, lids and
straps are kept as separate feature references; they do not create fictional
empty volumes inside an enclosure. A mapped display/control/connector may declare
an internal mount. Relative component sizes remain illustrative. Accepted actual
dimensions and component clearance positions must fit the approved interior.

Coarse component centers retain a 0.22 normalized tolerance. A frozen center at
least 0.15 from the body center on an axis must remain on that side and at least
0.025 from the center. This applies to internal XYZ centers and PCB peripheral XY
centers, preventing a distinctly left battery or front port from disappearing
into the center within the broader tolerance. Frozen vertical stacking order is
also retained. Outer XY and height/width proportions allow at most 25% relative
variation from the approved shape; larger changes require a revised concept and
explicit confirmation.

## Actual CAD inspection

A fresh trusted process imports the exported STEP after the generated process
has terminated. It measures solid validity, dimensions and actual intersections
with profile-aware component/inner-clearance volumes. Circular and rounded
interiors are never checked as empty rectangular corners.

Body form inspection measures actual STEP material outside the approved profile
with a 0.1 mm geometric tolerance. It also measures perimeter material in three
bands centered at -25%, 0% and +25% of body height. Each band requires at least
85% coverage, allowing bounded port openings while rejecting a round lid used to
hide a square body. This is a qualified straight-body appearance check, not proof
of continuous walls, assembly insertion, manufacturing tolerance or safety.

The built-in mechanical builder runs the same fixed inspection provisionally
before reporting successful generation. Wrong shapes trigger the existing
bounded engineering repair path. Unsupported profiles preserve candidate files
with a visible capability blocker. This provisional guard creates no independent
release validation and does not allow a generator to approve itself.
Operator-installed tool adapters without a qualified generation-time guard retain
their actual outputs with a provisional `UNKNOWN` capability result. Their
selected adapter is preserved and mandatory formal checks can inspect those files.

## Actual PCB inspection

KiCad 9 independently extracts the closed `Edge.Cuts` outline, package/pad
extents, straight copper segments and vias. Physical package bounds use
fabrication-layer shapes plus pads, with a conservative courtyard fallback;
reference text does not become the package. Missing physical body data is
`UNKNOWN`. Filled zones remain subject to the separate all-severity DRC.

The validator converts KiCad Y-down coordinates to approved Y-up coordinates,
then applies accepted board rotation and placement. Actual outline/package/copper
points must fit the circle/box/rounded enclosure interior. A rectangular PCB is
allowed inside a circular enclosure if all actual extents fit. An explicitly
approved circular PCB also requires actual radial outline containment and
polygon area within 2% of its circle; bounding-box corners are not used as real
circular-board corners.

Accepted sensor/control/connector identities are linked to actual PCB reference
designators. Their measured physical footprint centers must retain the frozen
coarse XY regions and sides. Missing mappings remain `UNKNOWN`, and absent or
misplaced actual footprints fail. This does not verify sensor performance,
display modules, footprint height, peripheral orientation, straps or physical
operation. Circuit pin/net correspondence and ERC/DRC remain separate required
checks.

## Offline qualification

`tools/check_approved_design_runtime.py` runs public synthetic fixtures inside the
existing isolated validator image without network or model calls. It verifies
circular and rounded body positives, same-bounding-box square rejection,
round-cover/square-body rejection, generation-time rejection, actual circular PCB
outlines, exterior copper rejection and actual front/back footprint placement.
The report records actual image identity, source/fixture hashes, numerical
measurements and scope in `verification/approved-design-runtime.json`.

Use `--source-override` only for a development qualification before image refresh;
the report records that the read-only current source replaced the image source.
Accepted artifact files, physical-part manifests and manufacturing coordinates
remain unchanged by these visual form/layout checks.
