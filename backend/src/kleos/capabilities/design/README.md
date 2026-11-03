# Capture and concept candidates

Capture is a specialized objective handled by the existing LangGraph worker. It
does not start a fixed device pipeline or require native CAD tools just to render
an editable illustration. `DesignPlanner` requests one bounded capture task;
`DesignRunner` calls the explicitly configured text/vision gateway and records its
typed response through a durable model receipt. `DesignRepository` publishes the
candidate separately and idempotently. A crash after that response or publication
replays the receipt without another model call. Unknown external outcomes still
block for reconciliation.

The schema follows the existing Flutter `concept-scene-v2` viewer: at most 24
stable physical parts, finite transforms, bounded relative dimensions, and
explicit extrusion, revolution, loft or sweep features. Profile intersections,
hole containment, loft correspondence, sweep collisions and triangle budgets are
checked before publication. This is illustration validation, not physical,
manufacturing or CAD validity. There is no generated code execution or placeholder
geometry. Actual authorized reference image bytes enter only a qualified vision
model; missing vision configuration blocks rather than choosing a text default.

A selected-part redo requires the current concept version and an existing part
ID. Its old geometry is removed from model input. The model sees the surrounding
assembly, the selected identity and mating constraints, then returns exactly one
replacement part with that same ID. The server merges only that part, preserving
every neighbor's identity, shape, transform and display offsets. Unrelated
requirements and constraints cannot be rewritten. `explode` is exclusively a
display translation; manufacturing positions and real dimensions never derive
from an exploded view.

Commands save bounded conversation messages, image references and hash-bound
question answers transactionally. New capture input cancels superseded capture
runs and changes the context revision, preventing stale model publication. The
existing workspace fields `concept`, `proposal`, `clarification`, `conversation`
and `planning` remain available without Flutter changes. Questions, assumptions
and critical unknowns remain explicit. Drafts do not overwrite accepted project
requirements or the prior approved design.

Only `project.confirm` in user command context creates `approved-design-v1` and
starts a general engineering objective. It freezes the scene hash, user identity,
relative units and `explodeAffectsManufacturing=false`. The canonical
`approvedDesign.peripheralConceptIds` list is derived server-side from accepted
parts tagged `circuitRole=peripheral`; boards are distinct. The circuit acceptance
path must map those IDs uniquely to actual accepted references. A concept
confirmation is never independent engineering validation or accepted fabrication.

Verification uses typed fake models, actual model gateway requests with HTTP
mocks, the real database/service projections and LangGraph restarts. No paid
model calls or physical work are part of these tests. The frontend viewer itself
is unchanged; the backend implements its public bounds rather than certifying a
part through rendering.
