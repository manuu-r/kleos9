# Printing preference and simulated assembly progress

The existing production Flutter workbench sends:

- `project.selectPrint`: `{projectId, mode: "home" | "order"}`. It reads
  `project.state.printMode`; `home` exposes the separate existing Bambu panel.
- `project.assemblyStep`: `{projectId, step: integer}`. The production button sends
  `(state.assemblyStep ?? -1) + 1`, beginning at 0. It is labelled simulated.

`projects.workbench_progress.WorkbenchProgress(projects).command(actor, body, key)`
implements these commands. Runtime/ApplicationServices inject the service and
route only its `COMMANDS`. The existing raw `state` workspace projection already
carries the required fields; no DTO, route, widget or mock-backend changes are
needed.

Print selection is a real user preference (`evidenceKind: user_preference`), not
printer readiness or fulfillment evidence. It writes `state.printMode` and
`state.printPreference`, with actor, time and revisions. Choosing home needs no
paired printer; discovering, pairing and preparing a print remain separate adapter
operations. Choosing order creates no quote, purchase, supplier job or receipt
claiming an order was placed. Both choices explicitly record no printing, no
orders and no physical acceptance.

Assembly records `state.assemblyStep` plus `state.assemblyProgress`, always
`evidenceKind: synthetic`, `status: simulated`, completion `UNKNOWN` and physical
acceptance false. The first step must be 0; subsequent requests may remain at the
current step or move one step forward/back. Values 0–255 are a defensive counter
bound, not a generated 256-step guide or a six-step completion promise. Boolean,
floating, string, negative and excessive values are rejected. The backend never
writes `assemblyDone`, `guideStep`, activation success or physical check PASS.
There is no current accepted guide whose completion could be established.

The legacy simulation screen uses a fixed six-step guide and different flattened
fields. Its separate mock backend retains that behavior. Production commands do
not copy simulated delivery, printer or device success into real project state.

Both commands require current write membership, a strict expected revision and
an idempotency key. Replay checks current write permission and returns the prior
receipt without reverting later choices. Agent-worker contexts cannot invoke
these user commands. State, audit event and command receipt commit atomically.
They advance only the visible UI revision; existing engineering context,
objectives, source hashes, approvals, releases, planning state and display stage
remain unchanged. Recording progress does not dispatch a workflow or external
effect and does not unlock any release or physical gate.

Focused tests cover exact UI fields, replay after another choice, stale/invalid
revisions, tenant/viewer denial including receipt replay, simulated-only evidence,
no completion escalation, strict step bounds/adjacency and a still-valid active
engineering objective after both commands. No model, native, printer, provider or
physical work is involved.
