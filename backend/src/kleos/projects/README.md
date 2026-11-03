# Project authority

This package owns durable application facts and command receipts. LangGraph owns
workflow execution/checkpoints; it does not overwrite these records from memory.
Keep module changes scoped to their files and tests. Shared contracts, migrations
and runtime composition require integrator coordination.

| Module | Responsibility |
|---|---|
| `store.py` | Membership checks, revisions, transactional commands/events, objectives and context |
| `graph_repository.py` | Scoped worker authority, exact task inputs and durable effect receipts |
| `interfaces.py`, `interface_runner.py` | Candidate configuration proposals and separate user acceptance |
| `prerequisites.py` | Quiescent same-run interface review, explicit approval and revision-linked continuation |
| `engineering.py` | Generation, scoped repair, cancellation and bounded existing-run retry |
| `rebuild.py` | Exact-source native-only rebuild admission without model/retrieval calls |
| `verification.py` | Independent checking of completed objective candidates without new generation |
| `views.py` | Existing workbench projections from actual authoritative records |
| `consent.py` | Explicit recorded training preference, without training or export dispatch |
| `lifecycle.py` | Owner-authorized project tombstones, immediate publication fencing and retained recovery history |
| `workbench_progress.py` | Print preferences and explicitly simulated assembly navigation without physical acceptance |

Commands authorize membership and writes, compare the expected UI revision, and
persist a durable idempotency receipt with their state changes and events. Context
changes advance both context and engineering revisions. Presentation-only changes
must preserve engineering authority. Idempotent replay must reauthorize the actor.

`project.setTrainingConsent` records an explicit boolean through the existing
workbench control. It advances the visible revision while preserving engineering
context, emits an auditable preference event and supports revocation. Recording
consent creates no training job, export, model request or physical action.

Interface proposals pin their source context, candidate hash and original run.
Only an exact explicit user approval accepts configuration; generators never
write accepted circuit/firmware facts. Approved concept peripheral IDs map uniquely
to actual circuit references. A relative concept illustration is not dimensioned
manufacturing geometry. Imported firmware-only work can propose an explicit board.
Imported PCB work can explicitly propose a no-concept identity binding; it is
never inferred from an absent concept or silently represented by an empty list.

Verification uses a separate bounded scope tied to the completed run and exact
candidate/source closure. It cannot reactivate generation or refresh provider
spend. Checker selection is server-owned; acceptance remains in `validation` as
a separate authorized operation. Cancellation fences publication immediately;
uncertain compute is reconciled separately and retains its capacity reservation.

Never infer successful engineering from a model summary, process exit, illustration
or unavailable check. `PASS`, `FAIL`, `UNKNOWN`, `ERROR` and `NOT_APPLICABLE` retain
their distinct meanings in all views and release gates.

Project deletion is a revision-checked tombstone, not a data purge. It excludes the
project from ordinary reads and listings and cancels outstanding work while retaining
artifacts, receipts and uncertain compute reservations for reconciliation. External
job rows are locked before the project row, matching observer lock order; a changed
job set aborts the command before mutation. See
[project deletion](../../../docs/project-deletion.md) for the API and recovery scope.

## Interface review through the existing workbench

`project.solveInterfaces` creates a bounded proposal objective, never automatic
agreement. The full candidate is saved in `state.interfaceProposal` and the
existing conversation. Publication advances the UI revision while preserving
the active graph context; its display stage returns to the conversation. The user
can reply exactly `approve interfaces` with the current expected revision, or use
`project.approveInterfaces` with the exact `proposalId` and `proposalHash`.
Approval requires unchanged context and a successfully completed, uncancelled
proposal run. It preserves unrelated interfaces and sources and records history.
Other messages revise the proposal; generic yes/confirm never approve it.

Default generation waits for pending interface review or missing inputs. Explicit
scoped objectives remain available, including independent mechanical work.
Confirming a concept with electronic peripherals queues interface review as its
specific circuit prerequisite and invalidates the old current circuit while
retaining approval history. Pure mechanical concepts retain their dynamic
engineering objective. Both retain an explicitly visual-only
`interfaces.designIntent` reference, with relative illustration units and no
manufacturing or validation claim.

Imported text context is bounded and authorized; it is never executed. Images
require the authorized loader and a configured vision model. Native dispatch
separately checks a firmware board's qualified tool installation. Model IO has
a durable receipt: successful inner calls replay without another model call;
uncertain effects remain blocked. See `tests/test_interfaces.py` and
`tests/test_interface_runtime.py` for scoped mocked-provider/HTTP/recovery checks;
these do not claim live-provider, PostgreSQL or native-device qualification.

## Imported PCB without a visual concept

An explicit `project.solveInterfaces` objective may propose
`circuitIdentity: {mode: "imported_no_concept", sourceArtifactIds: [...]}` alongside
a complete circuit contract whose `conceptReferences` is empty. The selected
identities must include exactly one current user-uploaded `.kicad_pcb` source.
The server checks its content hash and KiCad source header; this only identifies
the source format, not PCB validity, routing success or manufacturing suitability.

The full current source-manifest closure, exact artifact hashes, requirements and
brief are bound to the candidate. The existing conversation shows the resolved
source references and hashes and explicitly describes the no-concept choice.
Only the existing explicit approval command/message creates
`project.data.acceptedCircuitIdentity`, with the proposal, user, revision and
matching approval-history record. Approval does not create or alter a visual
concept or accept generated artifacts. Any existing approved design, including
one with zero peripherals, prevents switching to this no-concept mode.

Native admission calls `accepted_circuit_identity(session, project)` inside its
authorized transaction. Imported identities require the exact approved source
closure and circuit; changing sources, circuit values, requirements or concept
invalidates the binding. Firmware-only approval preserves an unchanged circuit
binding. Starting a new generation or changing model policy does not rewrite it.
The native adapter must consume the bound original sources and separately enforce
its qualified path/library/placement contracts; identity approval itself does not
establish that an arbitrary imported project is supported by a native image.

Ordinary messages on imported-source projects now remain dynamic objectives
instead of being forced into concept capture. Explicit capture and selected-part
redo are unchanged. For generic objectives with pinned native PCB/firmware tools,
the orchestrator now detects missing circuit or board interfaces before preparing
native task inputs. It proposes only those prerequisites and pauses the same
LangGraph run for the existing conversation's exact `approve interfaces` reply.
No keyword-routing branch or automatic no-concept approval is involved.

This first continuation phase requires a quiescent boundary before any native
job or candidate publication. Completed read-only research history may remain;
prepared native inputs, pending effects and external work prevent amendment.
A bounded clarification round asks the original pinned model again; successful
model receipts replay and uncertain calls remain blocked. Reference images require
the configured vision model. Exact source/image references, metadata and verified
blob bytes are bound at proposal, reply and continuation.

Any currently authorized project writer may approve; the original execution
actor must also retain write access. The approval records the actual approver,
updates canonical interfaces and advances context revision. A server-recorded
amendment changes only the run's current revision; its initial scope, original
actor, model/tool pins, deadline, cumulative active-compute budget and spend
history remain unchanged. Waiting closes the active compute grant and does not
reset its allowance. Subsequent objectives and validation/release evidence use
the canonical accepted interfaces, not a private run-only override.

Only one such amendment is supported in this first phase. Continuation after
native work or across multiple engineering revision phases remains unsupported.
See the [continuation design](../../../docs/prerequisite-continuation-plan.md)
for the broader intended behavior and verification boundaries. Focused service,
API, worker and LangGraph tests use mocked model responses; they do not qualify
arbitrary imported layouts or device behavior.

`tests/test_imported_interfaces.py` uses the fresh native PCB fixture source data
without executing it. `tests/test_imported_interface_runtime.py` exercises actual
HTTP, typed model gateway, worker, source review and approval with mocked provider
responses. Live imported-board routing qualification belongs to the native module.
