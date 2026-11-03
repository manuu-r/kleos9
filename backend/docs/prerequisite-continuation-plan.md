# Dynamic prerequisite approval and continuation

Status: accepted implementation plan, **not implemented or qualified behavior**.
The first phase below covers approval before native execution begins. It does not
complete the broader mixed-revision, multi-agent continuation goal.

## Intended behavior

A user can submit a specific objective against an existing project—for example,
route an imported PCB or build firmware for a selected board. The orchestrator
plans only the required work. When a planned operation lacks an accepted circuit
contract or firmware target, the host identifies that concrete prerequisite,
prepares a reviewable proposal, and asks the user through the existing workbench
conversation. An explicit approval saves canonical project authority and resumes
the same objective with its remaining budget. There is no mandatory concept,
whole-device pipeline, provider switch, new objective, or automatic approval.

The broader goal includes discovering prerequisites after earlier work, revising
them explicitly, and continuing without losing independent work. That requires
additional rules for evidence and sources spanning revisions. The initial phase
must expose that limitation rather than re-stamp old artifacts or weaken checks.

## Current implementation and the gap

The supported explicit path is
[`InterfaceFacade`](../src/kleos/api/interfaces.py) →
[`InterfaceService`](../src/kleos/projects/interfaces.py) →
[`InterfaceRunner`](../src/kleos/projects/interface_runner.py).
`project.solveInterfaces` creates a separate objective; approval requires its
successful completion and advances the project's engineering context revision.
Generic objectives currently have no automatic continuation through that path.

[`ObjectiveEngine.resume`](../src/kleos/orchestration/engine.py) requires the
checkpointed scope to equal the current scope. The
[`worker`](../src/kleos/orchestration/worker.py) and
[`GraphRepository`](../src/kleos/projects/graph_repository.py) fence a run when its
revision differs from the current project. Calling the existing approval command
and then blindly resuming a generic objective would therefore be incorrect.

The existing Flutter
[`PlanningConversation` callers](../../kleos-ui/lib/ui/features/project/production_stages.dart)
send `project.message` with text, client message identity, optional images and the
current expected revision. They do not supply the graph's run/interrupt identity.
The backend must resolve a uniquely visible pending prerequisite; it cannot rely
on a new frontend selector or infer approval from ordinary prose.

## Chosen authority model

Use an explicit revision-linked approval phase inside the same LangGraph thread.
Preserve the initial scope as immutable history and append a server-owned receipt
for each permitted transition. The current execution scope moves to the approved
project revision, while the objective ID, actor, deadline, task/planning limits,
compute allowance, model spending, effect history and installation pins remain
unchanged. Changing the execution revision is recorded authority, not a hidden
exception to the stale-context fence.

Do not solve this by writing canonical interfaces with `context=False`, or by
providing a private run-only interface overlay to native tools. The former hides
a real authority change; the latter leaves generation, independent checking,
release records and the next objective with different interface authority.
Canonical `interfaces`, accepted circuit identity and approval history must be
written together under the project lock, using the existing typed validation.

## First phase: quiescent approval before native dispatch

1. After a planner produces its bounded task graph, inspect the **planned native
   capabilities** before preparing task inputs or dispatching native work.
   Inspect all currently planned lanes, including dependent tasks, so a later PCB
   task does not unnecessarily allow an earlier CAD task to start before review.
   Use server rules for accepted circuit identity, explicit firmware board and
   the original pinned qualified installations. Do not use message keywords.
   Existing valid interfaces require no approval detour. An unavailable tool is
   an explicit capability blocker, not something user approval can qualify.
2. Admit a bounded prerequisite record tied to the run, original scope, exact
   source references/hashes, requirements, approved concept, requested missing
   interface keys and runtime pins. The initial phase permits no native job,
   native candidate/publication, unresolved external effect, active external wait
   or prepared downstream task input. Completed preparatory model/read receipts
   stay immutable. Their absence of side effects must be established from durable
   records, not a model claim. Discovery outside this boundary blocks explicitly.
3. Generate only a typed candidate for the missing interfaces, using the existing
   `InterfaceProposal` schema and reusable source/image preparation. Use the
   objective's pinned coordinator/vision model and existing model ledger. Each
   proposal round has a stable receipt identity and an explicit bounded count;
   neither clarification nor retry resets that count or the overall budget.
   Missing critical facts produce questions, not partial contracts for approval.
4. Save the complete candidate, exact hash, source bindings, limitations and clear
   approval instruction in the existing conversation. Advancing only the visible
   UI revision is appropriate for this candidate publication. It grants no
   circuit, firmware, artifact or release authority. Persist the LangGraph
   interrupt and enter `waiting`; hold no native slot or active-work lease.
5. Route the exact reply `approve interfaces` only to the uniquely visible pending
   prerequisite, using current membership and expected UI revision. An additive
   explicit command may also carry proposal ID/hash. Generic `yes`, `confirm`,
   tool output or model text cannot approve. Other bounded text requests another
   proposal round; images require a newly reviewed context. A message received
   before the exact interrupt is durable must fail without changing authority.
6. In one authorized command transaction, revalidate the proposal, quiescence,
   original source/requirements binding, qualified target constraints, scope,
   interrupt and all budget/tool pins. Write canonical accepted interfaces and
   history, advance the project context revision, update the objective's current
   revision/scope, append the transition receipt, and enqueue its exact
   server-owned resume value. Preserve the original scope and previous receipts.
   The receipt binds old/new scope hashes, old/new context revisions, proposal
   hash, user, timestamp, interrupt ID and approval command identity. Only the
   revision field may differ between old and new execution scopes.
7. Resume the same checkpoint using LangGraph `Command` with the verified scope
   update and interrupt receipt. A scope mismatch is permitted **only** for this
   exact persisted transition at its designated waiting node; every other
   mismatch remains an error. Do not implement a parallel workflow engine or
   directly rewrite checkpoint storage. Recreate model adapters with the current
   execution scope and original provider pins. Prepare previously deferred task
   inputs against the new context without rewriting task identities or previous
   model decisions. Then continue the existing graph.

An approval transaction can commit before the worker updates the checkpoint.
Reentry must consume that same transition idempotently, without approving again,
generating another proposal or extending any deadline. Cancellation, membership
removal, unrelated context changes, mismatched hashes or uncertain effects must
fence both publication and continuation. The first phase does not automatically
carry earlier native outputs into the new revision.

## Time, spending and interruption

Follow the existing [external wait](../src/kleos/orchestration/external-waits.md)
and [active-work accounting](../src/kleos/projects/compute.py) boundaries.
The immutable lifetime deadline is distinct from cumulative active compute.
Proposal generation and graph work consume existing grants; a saved wait closes
the active grant, and approval requires that the prior worker has relinquished
it. Waiting consumes no active grant, but does not extend the lifetime deadline.
On resumption the worker reserves from the remaining allowance, retaining any
unsettled reservation from an abandoned owner.

Do not use the entire lifetime as the model request timeout or grant a fresh
compute budget after approval. Model requests retain provider request bounds,
remaining lifetime and active-worker enforcement. Existing model budgets and
attempts remain keyed to the same run, including uncertain usage. The current
worker token and revision checks fence old owners; command approval itself is
not permission for an old worker to continue.

## Canonical authority through checking and future work

After approval, native admission reads the same canonical circuit/firmware facts
shown by the workbench. Imported PCB approval binds the original source closure;
it cannot silently invent a visual concept or downgrade an approved concept.
Approved peripheral IDs retain exact, injective mappings to real references.
Board choice cannot override the qualified board set or imply device behavior.

All subsequent generated candidates and independent checks use the new context
revision. Validation subjects and release provenance should additionally bind the
accepted interface/proposal identity hashes, preserving an auditable account of
the approval used for generation. The release path still requires independent
evidence and separate acceptance; prerequisite approval cannot satisfy it. The
next objective reads canonical accepted interfaces normally, rather than losing
an overlay when its predecessor ends. Ordinary source/context changes retain
their existing stale-binding behavior and may require renewed review.

## Proposed ownership and implementation sequence

These new paths are planned ownership, not existing implementation links.

| Owner/module | Work |
|---|---|
| Graph-design agent: `projects/prerequisites.py`, `tests/test_prerequisites.py` | Durable bounded prerequisite/transition records, quiescent admission, command authority, exact hashes and idempotency. |
| Orchestration agent: `orchestration/prerequisites.py`, `tests/test_prerequisite_graph.py` | Typed proposal preparation, graph interruption/continuation, receipt replay and bounded rounds. |
| Assigned API owner: `api/prerequisites.py`, `tests/test_prerequisite_runtime.py` | Existing conversation/command routing and real HTTP → worker → approval → continuation fixtures. |
| Integrator: [engine](../src/kleos/orchestration/engine.py), [worker](../src/kleos/orchestration/worker.py), [runtime](../src/kleos/infrastructure/runtime.py), [services](../src/kleos/api/services.py) | Minimal shared node, resume, provider and facade hooks; preserve ordinary scope checks. |
| Coordinated interface owner: [interfaces service](../src/kleos/projects/interfaces.py), [runner](../src/kleos/projects/interface_runner.py) | Extract reusable typed proposal/approval logic while preserving the explicit standalone solve path. |
| Integrator/validation owner: [validation service](../src/kleos/validation/service.py), [project views](../src/kleos/projects/views.py) | Approval provenance through candidate/release evidence and existing workbench projections. |

Implement admission and transition semantics first, review them, then connect
graph interruption, existing UI commands and runtime. Use bounded objective JSON
records if sufficient; any new tables require a fresh Alembic migration. Export
OpenAPI/regenerate transport together if a typed public contract changes. Do not
modify Flutter UI or silently enable unqualified integrations.

## Required verification and remaining phases

- Generic imported PCB and firmware objectives reach proposal review, restart,
  explicit existing-UI approval and same-run continuation with mocked providers.
  Pure mechanical and already-configured objectives take no prerequisite detour.
- Compare before/after run identity, initial scope, current scope, provider/tool
  pins, deadline, planning/task counters, model usage and active-compute ledger.
  Waiting and delayed approval must not refresh any allowance.
- Exercise stale revisions, changed sources/requirements, wrong proposal hashes,
  duplicate approvals, ambiguous pending proposals, revoked authority, missing
  vision, unqualified boards and attempted no-concept downgrades.
- Crash after proposal model receipt, after candidate publication, after interrupt
  persistence, after approval commit and before checkpoint update. Verify exact
  replay, old-owner fencing and no repeated model or native effect.
- Reject transition after native work, unknown effects or an unreleased active
  grant. Test simultaneous approval/cancellation and unrelated project changes.
- Verify the next objective sees canonical accepted interfaces and candidate/
  release provenance identifies the exact approval. No generated file, process
  exit or proposal summary may become independent acceptance evidence.
- Use real local PostgreSQL/checkpointer restart tests after mocked service/graph
  tests pass. Live provider, device and physical qualification remain separate.

Later phases must define safe prerequisite changes after independent work has
already completed, revision-linked evidence carry-forward, and multiple distinct
approval boundaries without resetting the original objective. Those are part of
the intended system; the first quiescent phase must not be described as completing
them.
