# External waits and bounded resumption

`ExternalWaits` correlates already-linked sourcing/human jobs and existing offline
print preparations with a LangGraph interrupt. It does not create a fulfillment
workflow, dispatch purchases, hire humans, send messages, upload printer files or
start printing. `ExternalWait` is an authorized correlation record;
`ExternalWaitEvent` is a deduplicated observation receipt. LangGraph retains the
checkpoint, task ordering, planner state and resume position.

The optional tools in `capabilities/external_wait_tools.py` accept only `jobId`
and a host-generated `actionId`. They return host-owned `wait_ids`, and the
specialist immediately returns WAITING without another model call. They cannot
supply an account, deadline, approval, qualification flag or provider status.
Existing source-bound provider adapters and their qualification records are
unchanged. The wait tool and each wait bind the hashes of both new source modules;
an implementation change fences existing bindings instead of silently executing
new behavior under an old installation.

## Runtime contract

Construct `ExternalWaits(projects, repository, external_jobs=..., preparations=...)`
and inject it into `ObjectiveEngine(external_waits=...)`.

- `await bind(scope, task, kind, job_id)` stores an idempotent project/context/run/
  task/job binding, current account installation, exact source/request identity,
  scope and runtime budget/tool pin hashes. It returns the wait ID.
- `await validate(scope, wait_ids)` checks the graph's published WAITING task
  bindings before `interrupt({kind: 'external_wait', waitIds, runId})`.
- `await poll_due(limit=16)` performs bounded read-only polling without holding a
  database transaction, run lock, native slot or model request across provider IO.
  Unchanged or intermediate progress observations do not wake the graph.
- `ingest(project_id, event_sequence)` reads an existing authoritative project
  event. Callers cannot supply a provider observation. Older/duplicate events do
  not replace a later receipt. Polling also refreshes sources, so lost delivery
  cannot strand a completed observation.
- `wake_ready()` writes the existing objective's `pendingAnswer`, bound to the
  exact persisted interrupt. Completion arriving before the worker saves its
  interrupt remains durable and is delivered on the next cycle. Each selected
  task needs all of its waits ready; other tasks may remain suspended.
- `await resolve(scope, wait_ids, answer)` accepts only bound server event IDs.
  It rechecks membership, context, source identity, preserved artifact hashes,
  account/tool installation, lifetime and the active worker's compute authority.
  It transactionally replaces only the selected WAITING result with BLOCKED plus
  UNKNOWN, non-independent observation evidence. Provider completion cannot
  approve a task or physical work. The existing planner may continue through its
  normal constrained supersession mechanism with the remaining allowances.

Resolve is idempotent across a crash after its database commit but before the
LangGraph checkpoint. Reentry returns the exact recorded result and does not emit
another task publication. User clarification answers cannot substitute for these
server events. Cancellation, removed membership, changed context/account/pins or
an expired objective lifetime block resumption before further provider/model work.
Timers expire the wait explicitly; no status observation becomes PASS merely
because the polling request succeeded.

External provider writers currently acquire their job row before the project
row. The bridge holds the project lock and performs a **nonlocking** committed
job read, avoiding the reverse lock order. It never calls the job-locking `_load`
inside that project transaction. The project lock still serializes the existing
writers' authoritative publication.

New runtime objectives use an immutable lifetime deadline distinct from the
worker's cumulative active-compute allowance. The worker reserves bounded time
grants, closes them when a checkpoint suspends, and never resets model spend,
call counts, task limits or retry history. Waiting holds no active grant. Historical
scopes without the new compute budget retain their original deadline contract.
These grants and all checkpoint integration are owned by the shared worker/runtime;
the bridge cannot extend them.

## Schema and qualification scope

`kleos2_external_waits` contains `id`, `project_id`, `run_id`, `task_id`, `job_id`,
`kind`, `status`, `deadline_at`, `next_poll_at`, `last_sequence`, and bounded JSON
`data`, with a unique `(run_id, task_id, kind, job_id)` binding.
`kleos2_external_wait_events` contains a deterministic 64-character `id`, `wait_id`,
source `sequence` and bounded JSON `data`. Production uses the integrator's new
Alembic migration; there is no implicit schema creation.

The host-tool qualification scope is bookkeeping and read-only observation of an
already-authorized job. Fixture tests exercise real LangGraph interrupts,
partial completion, fresh worker/bridge objects, no-op polls, duplicate/out-of-order
source events, timer expiry, changed authority/account/context/implementation,
forged answers, interrupted resolve, the pre-interrupt completion race, exact
artifact preservation, immediate specialist suspension and native capacity waits.
They make no commercial, printer or model API calls. In-memory checkpoint tests do
not establish PostgreSQL process recovery; the separate opt-in
`test_external_wait_postgres.py` covers real checkpoint reconnection. Independent
code review and current scoped results are required before the runtime marks these
new host tools qualified. This document itself is not a review approval or a live
provider/physical qualification.

Focused fixture checks:

```bash
.venv/bin/pytest tests/test_external_waits.py tests/test_external_wait_graph.py tests/test_external_wait_tools.py tests/test_external_wait_print.py tests/test_external_wait_runtime.py -q
```

The integration follows the official LangGraph
[interrupt/Command resume contract](https://reference.langchain.com/python/langgraph/types/interrupt):
resume uses the same thread and exact interruption, and code before an interrupt
can execute again. Durable binding and result receipts therefore precede any
repeat-sensitive operation; checkpointed waits never rely on an in-memory timer.
