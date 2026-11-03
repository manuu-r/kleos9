# Workbench requirement edits

`project.swapBattery` consumes the production workbench's explicit `capacityMah`
and `dimensionsMm: {x, y, z}` values. Values must be finite and positive; input
bounds are 1,000,000 mAh and 10,000 mm per axis. These are request size/range
bounds, not a declaration that a battery is safe, compatible or manufacturable.
The backend never guesses a larger capacity from the simulation's old shortcut.
Chemistry, voltage, charging, sourcing and fit remain unspecified unless separate
evidence establishes them. Compatibility is recorded as `UNKNOWN`.

`project.toggleFeature` uses an exact, unique requirement name from the current
capture candidate. An explicitly excluded name remains restorable from its
server-recorded edit; arbitrary new names are rejected. The legacy feature
checkbox lives in the simulation screen, whereas the production battery form
already submits the implemented payload. No new frontend controls are introduced.

Both commands require current write authority and the visible project revision.
Command receipts make replay idempotent. One transaction records the user's
requirement, cancels old active objectives, advances engineering context and queues
a new capture through the existing design service. Old publication attempts are
fenced immediately; native termination and usage reconciliation remain separate.

Current concept approval, interface acceptance and release pointers are cleared.
Their complete prior records remain in bounded edit/design histories; release rows,
source manifests, artifact IDs, hashes and blob bytes are unchanged. Historical
release IDs are retained in `supersededReleaseIds`. Old files are not automatically
accepted for the revised requirements. A new concept still needs explicit user
confirmation, interface review and separate engineering checks.

Canonical `requirements.userEdits` enters the model packet with exact required
and excluded requirement strings. Publication and confirmation check those
strings, and confirmation preserves the structured user edits. This prevents the
model from overwriting explicit structured values. It is **not** a semantic
contradiction detector or geometry/compatibility validation; freeform claims and
physical behavior still require review and independent evidence.

Focused verification: `backend/.venv/bin/pytest -q backend/tests/test_workbench_edits.py`
from the repository root. Tests cover actual HTTP/runtime/worker capture with
mocked model responses, revision and authority fences, history/source preservation,
idempotency, exact requirement propagation and invalid input. They run no live
inference, native builds, purchasing or physical actions.
