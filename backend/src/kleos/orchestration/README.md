# Objective graph

`ObjectiveEngine` runs an actual LangGraph StateGraph. Planner decisions determine
which scoped specialist tasks exist; no product stage or fixed lane pipeline is
required. Independent ready tasks execute concurrently with a bounded semaphore.
Dependencies must exist and be acyclic. Plans and task results are checkpointed as
plain data; artifacts remain references. A successful objective is candidate work,
not physical or independent engineering acceptance.

The injected Repository is an authoritative transactional adapter. It must:
- authorize actor/project/run and fence revision/cancellation in `assert_active`;
- return only authorized current `ProjectContext` in `context`;
- implement `run_once(scope,key,operation)` using durable operation identity,
  cached committed receipts, atomic claim and bounded reservations. A previously
  claimed operation without a receipt is uncertain and must not blindly rerun;
- make `publish` idempotent and recheck cancellation/revision inside its commit.

The graph checks authority before/after IO, but cannot replace these transactions.
The planner/model and specialist are injected; tests use fakes, production must
supply configured server-owned providers and budgeted tool executors. No provider
is chosen implicitly. `postgres_runtime` creates real AsyncPostgresSaver and
AsyncPostgresStore; setup is an explicit startup/schema task. InMemorySaver is
only a test dependency. `resume` validates the full saved scope and derives its
thread identity server-side. User input is collected through LangGraph interrupt.

Repairs use `PlanDecision.supersedes` mapping a failed/blocked task ID to a new
unexecuted task. The old spec/result remains in history. Pending dependencies
resolve to the replacement, whose capability/edit scope/acceptance criteria may
not broaden/weaken the original. Supersession cannot rewrite old relationships.
Planner context includes the already-authorized supersession map.

`ModelPlanner` and `ModelSpecialist` accept injected LangChain chat models, bind
bounded output tokens and deadlines, and use typed outputs. The specialist sees
only registered tool descriptions and has no raw shell/provider capability. Each
registered tool requires an authorization callback and an isolated/qualified host
implementation. Model/tool calls have separate durable receipt keys. Tool results
supply actual artifacts/checks; model finish prose cannot invent them. Failed or
unknown observed checks prevent success; even success remains a candidate.

## Configured model gateway

`ModelGateway` takes immutable operator-owned `ModelConfiguration` records,
`ModelPolicy`, `ModelLedger(store)` and server-side provider credentials. The
gateway has no built-in model defaults. Each supplied entry pins provider, model identifier,
configuration version, endpoint, qualified vision/tool support, price and limits.
`gateway.chat_model(scope, images=True)` explicitly selects the configured vision
entry; images sent to a text-only entry fail before a request. Pass the returned
LangChain model directly to `ModelPlanner`, `ModelSpecialist` or LangMem.
Image bytes must come from the authorized artifact service. Only bounded inline
PNG/JPEG/WebP user content is accepted; the gateway never fetches supplied URLs.

Requests use OpenAI-compatible chat completions and tool schemas. The adapter
disables redirects and has no implicit retries. Only a Modal HTTP 429 can use the
single explicitly configured fallback key; it must satisfy the same vision/tool
requirements. Every HTTP attempt consumes the same run allowance and has its own
ledger record, including the parent attempt of a fallback. Changing configuration
or budget after a run begins is rejected; a restarted worker must restore the
original policy, not reinterpret it with current operator defaults.

Runtime [provider settings](../../../docs/provider-wiring.md) accept the preserved
Meta and Modal credential names and supply documented Muse installations when an
explicit catalog is absent. The gateway itself still receives only pinned records.

The shared SQLAlchemy Base adds `kleos2_model_budgets` and
`kleos2_model_attempts`; production schema setup must include these tables.
Before IO, a transaction authorizes the project/run/revision, locks the budget,
reserves the configured maximum input and requested output cost, and increments
the request count. Actual token usage settles that reservation. Timeout, malformed
response, provider error or missing usage retains the conservative cost for
reconciliation. A rate-limit rejection releases cost but retains the request
count. Cancellation fences output while observed usage still settles. These are
estimates from pinned operator prices, not provider invoices. Per-model context
and image-token limits require provider qualification. No exactly-once inference
claim is made; outer operation receipts prevent blind replay after uncertainty.

Ledger records contain provider/model pins, request hashes, IDs, usage, cost and
status. They contain neither prompts, completions, private reasoning nor keys.
External tracing must separately use the application's redacted tracing policy.
All gateway tests use HTTPX mock transports and make no paid requests. PostgreSQL
concurrent reservation qualification remains separate from SQLite unit checks.

Protocol references: [Meta chat API](https://dev.meta.ai/docs/api-reference),
[Meta image inputs](https://dev.meta.ai/docs/cookbook/vision-input),
[Modal shared endpoints](https://modal.com/docs/guide/shared-endpoints),
[LangChain chat models](https://reference.langchain.com/python/langchain-core/language_models/chat_models/BaseChatModel).

## Recovery and dependency inputs

Graph invocation uses synchronous LangGraph checkpoint durability. Each accepted
plan publishes its task view transactionally before dispatch and checkpoints each
ready task's context and authorized input references. Successful prerequisite
artifacts become read inputs only after authoritative project/run lookup and
exact hash/version comparison. They never enlarge a task's allowed edit paths.

`ModelSpecialist` is a replayable local coordinator: every model/tool call has its
own durable receipt. It does not receive an additional outer task effect claim,
which would incorrectly block recovery after an already-recorded inner action.
On graph reentry it reconstructs local history from those exact receipts. An inner
claim without a committed response still blocks for reconciliation, and arbitrary
third-party specialist implementations keep the conservative outer task claim.
The production worker's PostgreSQL run lock and execution token fence concurrent
coordinator reentry. No custom workflow checkpoint or second workflow scheduler
is introduced.

External source observations use the [external-wait contract](external-waits.md).
New runtime objectives pin a seven-day lifetime and a separate cumulative active
allowance (30 minutes by default). Waiting checkpoints release their active grant;
cleanly stopped dispatches return unused time, while process loss conservatively
retains its outstanding grant. Restarts cannot reset allowances or extend pinned
deadlines. Historical scopes retain their original deadline-only behavior.

## Final compilation and later repairs

The graph can insert a deterministic `compose` node after authoring and before
validation or completion. It freezes the exact current source set and original
native contracts, invokes fixed build/render/finish actions, and publishes only
replacement candidate exports. A completed all-target handoff changes read
selection without rewriting the original task results or accepted source manifest.
The planner receives verified references and an explicit `artifactContext`
applicability label. Historical compiled outputs can inform a later repair but
cannot satisfy current verification or release coverage.

A later native repair inherits declared dependency sources as build inputs.
Dependency exports remain in specialist read context; they are staged as native
inputs only when explicitly requested. Such derived-input execution needs its own
qualified composition contract. Input selection never enlarges editable paths.
Source replacement requires one exact declared same-lane read-before-write chain;
competing writers do not merge by ordering. A predecessor build target can retire
only when one proven successor owns all its generated sources and covered exports.
Original grants, results and source identities remain immutable history.

Current integration tests use inert native responses. Multiple distinct standard
entrypoints in the same lane and actual composed native release qualification
remain open gates; passing the graph tests does not establish either.

## Interface prerequisites within an objective

Normal native objectives inspect their planned PCB/firmware requirements before
preparing native inputs. If a required canonical interface is missing, separate
LangGraph proposal and wait nodes produce a bounded typed candidate and pause for
an explicit project-writer reply. The existing chat command can refine the
candidate or approve the exact visible proposal; generic objective answers cannot
substitute for that approval. Proposal inference retains its original model,
image support, durable receipt and budget accounting.

Approval writes canonical project interfaces and an append-only revision
amendment. The same objective and thread resume with only the engineering revision
changed; actor, deadline, active-work allowance, model budget and installation pins
remain bound. Both the approver and original run actor must still have write
access. The original scope and approved interface identity remain recorded;
independent validation and release manifests bind the configuration they checked.

This first continuation phase requires a quiescent boundary before native jobs,
publications or unresolved external work. Completed read-only research may
precede the proposal. It does not re-stamp prior generated artifacts or support
arbitrary mixed-revision continuation after native work. Existing standalone
interface planning and capture remain separate supported entry points; a focused
CAD task does not acquire a mandatory interface-planning stage.
