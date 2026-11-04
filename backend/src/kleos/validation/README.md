# Independent digital acceptance

`ValidationService` separates native candidates from accepted releases. Generator
`TaskResult` observations and model claims are never imported as independent
evidence. A server-owned `CheckerRegistry` holds immutable, qualified isolated
checker adapters and versioned digital release policies. The registry starts
empty; absent or unsupported checks block acceptance. This module executes no
generated code, installs no tools and makes no physical acceptance claims.

`prepare(scope, artifact_ids, policy_key, request_key)` checks current project/run
authority, source versions and the persisted task edit scope. It builds a proposed
source manifest by replacing only explicitly selected allowed source paths and
retaining every other original source ID/hash. Missing files never imply deletion.
The proposal records exact source/output references, checker installation pins,
policy, project revision and a subject hash. Selected native outputs must have
been built against this exact source closure; outputs from inconsistent cross-lane
closures require rebuilding before qualification. No silent merge can certify a
file built against different sources.

`run(scope, candidate_id)` dispatches each pinned independent checker with a
`ValidationInput`. Callbacks must invoke qualified isolated execution, validate
blob hashes, and return bounded public measurements and `CheckReport` evidence
for that exact subject hash. Their execution identity must differ from all
generator jobs. Referenced evidence artifacts must belong to the same project/run
and checker execution. The service stamps independent authority; callers and
models cannot set it in a report. It preserves PASS, FAIL, UNKNOWN, ERROR and
NOT_APPLICABLE as distinct statuses. A crashed in-flight check remains uncertain
and cannot be blindly repeated. Exceptions become ERROR without retaining private
exception text. Cancellation retains observations but prevents publication.

`accept(actor, project_id, candidate_id, expected_revision, key)` is a separate
authorized command. Engineering worker context cannot invoke it. The objective
must have finished, current project revision and baseline must still match, and
every required check must be independent PASS for the exact proposal. Only an
explicit server policy can allow NOT_APPLICABLE for a specific check; it never
becomes PASS. Checker revocation invalidates acceptance even after prior results.
Acceptance atomically updates the source manifest and project revision and writes
an immutable release manifest containing all source/output hashes, checker pins,
evidence and the accepting actor. Command replay is idempotent. Private reads
authorize tenant/project membership.

Shared SQLAlchemy tables: `kleos2_validation_candidates`,
`kleos2_validation_checks`, `kleos2_releases`. Root schema migrations must import
these models. Tests use real scoped storage and database transitions with fake
isolated checker callbacks; they do not qualify CAD, PCB, firmware, application
or physical checks. Concrete independent tool qualification and release API
wiring remain integration responsibilities.

## Checking completed objectives

`projects.verification.VerificationService` admits `project.verify` against an
existing completed objective. The durable `kleos2_verification_jobs` row pins its
published, nonsuperseded native candidates, baseline, requesting actor and exact
checker policy. Admission does not advance the project/context revision, replace
sources, resume the generator, or call models/retrieval. The CAD export policy
selects CAD sources and STEP/STL outputs; SVG previews and ancillary files are
excluded from that candidate and cannot become accepted through its checks.

`VerificationRepository` grants checker-only access to the completed run using a
server worker context and persisted verification scope. Ordinary graph scopes
still require an active objective. Every check revalidates membership, context,
source baseline, exact published references and cancellation. Verification has its
own bounded deadline; an expired generation deadline is not silently extended.
`process_next()` recovers queued work and expired worker claims. Existing exact
subject candidates are reused, including their durable check claims; a running
uncertain check blocks recovery and is never repeated. Separate acceptance checks
that the verification job completed, and cannot accept a cancelled job's retained
observations. Completion/blocker events carry the exact candidate and individual
check statuses; completing observation does not turn FAIL/UNKNOWN into PASS.

The isolated CAD checker shares durable CPU, memory and scratch admission with
native generation and slicing. Busy admission waits within the checker/scope
bounds and checks current authority between polls. A reservation is released only
after identity-checked container removal and confirmed absence. An uncertain
create response followed by absence retains capacity for operator reconciliation;
a timer alone cannot prove that the create request will not complete later.

Temporary pool contention is an admission state, not ERROR evidence. The trusted
adapter can raise `CheckerAdmissionDeferred` only before attempting container
creation. Its check row returns to `queued`, and the verification worker retries
the same candidate/job with the original scope and deadline. The agent tool waits
within its existing effect instead of spending another model call. Running checks,
transport uncertainty and terminal ERROR results do not use this retry exception.
The existing workbench Cancel action cancels queued/running verification and
clears only its matching pending state; the completed generation run stays intact.
