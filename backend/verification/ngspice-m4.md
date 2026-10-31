# M4: authorized evidence access

1 November 2026. `GET /api/v1/projects/{project_id}/circuit-simulation` returns
the current independently produced envelope or an explicit unavailable response.
It performs no simulation/model work. Existing artifact download/content routes
also expose the immutable artifact to authorized project members.

Every read rechecks tenant and project membership, engineering revision, accepted
contract, latest native-rebuild boundary, exact candidate closure, evidence role,
four authoritative Validation bindings, artifact SHA-256 and canonical result
hash. Stale, candidate-uploaded, corrupt or unbound files are not substituted for
current evidence. Missing evidence is not simulated with browser fixtures.

The new typed response envelope is exported in `contracts/openapi.json`.
`kleos-ui/tool/generate_transport.py` includes it as a root and regenerated the
Dart DTO; the production API helper validates that DTO before use. The bounded
versioned result body remains a JSON data object; the viewer validates waveform
dimensions and values separately. No generated DTO was hand-edited.

Actual checks: 9 new route/service tests passed; Ruff passed. Existing security
review tests were also run, including authorization and validation isolation.
Contract export, Dart transport regeneration and `--check` passed (SDK formatter
needed cache access). Tests used fixture solver output and local temporary
SQLite/artifact storage, not Docker, paid inference, AWS or physical hardware.
