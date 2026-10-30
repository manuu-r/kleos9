# Approved concept shape and engineering layout

Capture and feasibility scenes are working drafts. The existing explicit
`project.confirm` action freezes the saved, current structured `ConceptScene` as
`state.approvedDesign`, with its complete scene, canonical SHA-256, scene/approval
versions, actor, timestamp and source run. A provisional seed, stale scene, model
summary or generated CAD cannot create approval authority.

The server derives `requirements.designIntent` from that snapshot. Every engineering
attempt, including provider switches and repairs, receives the frozen scene and
context-manifest reference. A later unapproved draft cannot replace it. Major
appearance changes follow the existing review and explicit confirmation flow;
superseded approvals cannot start new engineering, validation or release acceptance.
Reconfirmation invalidates dependent work when the contract changes. Historical
accepted releases remain available with their own original reference.

## Mapping into engineering

Concept units are relative. Exact millimetre dimensions come from accepted
requirements, interfaces and component sources. The server preserves the body's
profile, coarse proportions, critical component identities, centre regions,
rotations and meaningful stacking order. The outer X/Y aspect and height-to-width
ratios allow 25% relative deviation; material changes require concept review.
Coarse positions allow 0.22 of body size per axis. Clearly sided features
(normalised centre magnitude at least 0.15) must remain on that side, beyond a
0.025 centre dead zone. These are versioned qualification tolerances, not fabrication
or assembly tolerances.

Coordination supplies `mechanical.components[].conceptPartId` links to the
approved board, battery and internal sensors, plus actual mm sizes and positions.
Display/control/connector volumes may be explicitly mapped internally when
appropriate; structural covers and external straps remain appearance references.
`electronics.componentReferences` maps approved sensors, controls and connectors
to exact accepted circuit references. The server derives their expected regions;
workers cannot supply replacement expected positions or expected shapes.

A rectangular internal board may fit inside a circular housing. A circular PCB is
required only when the approved board itself is circular. Additional circuit
components still come from the accepted circuit contract.

## Deterministic checks

The independent native validator emits three additional required checks:

- `approved_design_contract`: frozen intent, accepted dimensions and identity/pose
  mappings agree; components fit the actual circular/rounded internal profile.
- `approved_enclosure_form`: re-imported STEP bounds, volume outside the approved
  profile and perimeter coverage at three heights preserve the housing form.
  A same-bounds square, or a round lid hiding a square body, fails.
- `approved_pcb_layout`: actual KiCad Edge.Cuts polygons, package/pad extents,
  copper extents and mapped peripheral positions fit the approved physical layout.
  Checks account for KiCad's downward Y axis, board placement and rotation.

Built-in generation runs provisional guards in a fresh trusted process after
generated execution ends. A mismatch enters the existing bounded targeted repair
path; provisional checks grant no release authority. Alternative tool adapters
retain their files and expose unsupported guard coverage explicitly until
qualified. Final independent checks remain required regardless of the generator.

Current qualification covers a single unrotated Z-axis straight circular,
rectangular or rounded rectangular body. Ellipses, spheres, rotated/compound bodies
and missing peripheral mappings remain UNKNOWN and cannot silently gain release
approval. This is coarse appearance/layout preservation, not complete visual
equivalence, mechanical fit qualification or validation of every surface detail.
Physical operation, materials and production remain separate gates.
See the [native qualification scope](approved-design-validation.md) for exact
geometric checks and limitations.

## UI and manufacturing lineage

Interfaces and generation display the exact frozen reference in an expandable
card beside the existing generated previews. Missing legacy references are shown
honestly; current drafts do not stand in for them. Viewing the reference, compiled
app or exploded CAD uses no inference.

An accepted release includes its full approved snapshot. Its deterministic download
contains `approved-design-reference.json` and a hashed `designReference` entry in
`manufacturing-manifest.json`. The relative appearance reference stays separate
from unchanged assembled mm STEP/STL files and physical part identities. Later
draft edits cannot rewrite an accepted package's reference.

Run `python tools/check_approved_design_runtime.py` after building the final native
images for actual offline OCCT/KiCad positive and negative fixtures. The report in
`verification/approved-design-runtime.json` records image/source/fixture hashes
and zero hosted model calls.
