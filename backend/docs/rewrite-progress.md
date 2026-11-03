# Backend rewrite progress

## Authorized objective

Complete fresh backend: task-oriented LangGraph orchestrator, LangMem shared
memory, modular specialists, isolated engineering tools, external procurement/
human-work lifecycles, existing Flutter contract. Reuse only Bambu connection
logic and public certificates; do not copy the old backend implementation.
Do not declare completion based on fixtures or code-size reduction alone.

## Cleanup and gap-closing pass — 3 November 2026

- Removed documentation that described the retired DBOS/Pydantic AI/Qdrant backend
  (`docs/`), fixed `CLAUDE.md`, `AGENTS.md`, `README.md` and `kleos-ui/README.md`
  links and statements, and removed the broken `deploy.yml` (it called deleted scripts).
- Removed Conductor/Qdrant/DBOS Terraform, variables, secrets and alarms. `terraform fmt`
  and a reference scan passed; `terraform validate`, plan and apply were not run. The
  control task definitions still target the retired runtime (see `infra/README.md`).
- Added `tests/scenarios/test_workflows.py` (7 tests) for the Phase 4 workflows that had
  no composed coverage. Full backend suite after the change: 1,577 passed, 11 skipped;
  Ruff, mock-backend (15 passed), OpenAPI export and `git diff --check` passed. The
  Dart transport check and Flutter suites were not run (no Dart/Flutter SDK).
- Native toolchain parents for CAD, PCB, firmware and Flutter web were reconstructed in `execution/toolchains/`
  (the old recipes were deleted with `backend/docker`). CAD, PCB, firmware and Flutter web native images built
  from them passed strict Linux-engine qualification; defects found on the way were fixed (a supervisor
  IPv6 assumption, a missing per-job Zephyr cache directory, and settings tests that CI's exported
  `KLEOS2_DATABASE_URL` broke).
  With a local PostgreSQL 16 and the opt-in solver/container tests enabled, the full suite passed:
  1,675 passed, 0 failed, 0 skipped.
- Frontend check (3 November 2026): `kleos-ui` was not edited by the rewrite (the 61-file frontend diff in
  `b557441` is the repository owner's own feature work committed alongside it). With Flutter 3.47.8 the
  transport check, `flutter analyze` and `flutter test` (402 passed, 4 skipped) pass, and a temporary test using
  the app's own `ProductionApi` and generated DTOs passed against a live new backend (session, catalogs,
  connectors, project create/replay, workspace, circuit-simulation, preview, drafts, event stream). The full
  browser-driven `test_driver/production_live_test.dart` needs a billed model and has not been run.
- Composed real-native journeys (`tests/test_real_native_journeys.py`): the missing-bracket and imported-PCB
  routing journeys passed against the real CAD and PCB containers with a scripted model. A live-model pass is
  implemented and gated on credentials plus an explicit spend ceiling; it has not been run.
- Circuit simulation (this branch's ngspice work) was rebuilt as `validation/simulation`: a
  deterministic deck compiler, bounded parser/checks, an isolated one-deck-per-container
  executor and stored, hash-verified evidence behind the unchanged route. It is advisory and
  local-Docker only; see its README for the qualification record and limits.

## Latest integrated phase — 3 November 2026

This section supersedes earlier missing-capability statements only for the exact
scope described here. The complete backend rewrite and frontend cutover remain
unfinished; the current remaining-work audit is `rewrite-remaining-audit.md`.

- Imported PCB identity can be explicitly proposed and approved against exact
  source hashes without requiring a new visual concept. Existing accepted visual
  concepts cannot be downgraded. Routing admission preserves the approved full
  source closure and rejects patching those imported files. Ordinary imported
  chat remains a dynamic objective. Automatic interface-prerequisite requests
  from generic chat remain unfinished; explicit solve/review/approve is wired.
- Engineering previews are connected through production service composition.
  Actual current CAD files provide stable parts in manufacturing coordinates;
  exploded translations remain display-only. Supported KiCad geometry comes from
  actual routed artifacts. Unsupported geometry and unqualified simulation remain
  explicit limitations/UNKNOWN; companion hosting is still missing.
- Already-linked external jobs and existing print preparations can pause/resume
  through actual LangGraph interrupts. Unchanged polls consume no model calls or
  worker execution grant. Events remain scoped to their account/context/source,
  exact interrupt and pinned implementation. Provider completion becomes UNKNOWN
  evidence for continuation, never automatic acceptance. New migration
  `0005_external_waits` stores correlation/event receipts; `external-worker` is a
  thin observer, also composed alongside the ordinary worker.
- New objectives separate a pinned seven-day lifetime from cumulative active
  work (30 minutes by default). Small grants survive restart, abandoned grants
  remain charged, clean stops refund unused time, and clock rollback cannot
  manufacture refunds. Old deadline-only scopes remain unchanged. Model spending,
  call counts, task identities and source pins never reset on a wake-up.
- Independent review found and corrected a project/job lock inversion. The bridge
  now reads external jobs without acquiring their row locks while holding the
  project lock. Real PostgreSQL concurrency proves a waiting job mutator cannot
  deadlock that snapshot read. Crash-after-resolve, event-before-interrupt,
  partial waits, cancellation, changed accounts/files and deadline-first polling
  have focused regressions.
- The user's existing `KLEOS_META_API_KEY`, `KLEOS_MODAL_LLM_KEY` and
  `KLEOS_MODAL_LLM_URL` work unchanged. A narrow provider-only file source leaves
  old database/authentication/execution policy excluded. Documented Muse Spark
  1.3 and Modal Muse Spark Open profiles supply vision/function-tool support;
  explicit model catalogs and existing per-attempt pins retain precedence.
  Both real accounts returned HTTP 200 for read-only model listings and advertised
  the intended model IDs. No inference was performed. Mocked actual runtime/capture
  flows exercise images, typed output, bounded explicit429 fallback and redacted
  usage receipts. See `provider-wiring.md` for evidence and limits.

Final combined verification: **709 backend tests passed, seven explicit opt-in
skips; Ruff passed.** A separate disposable PostgreSQL run passed **17 tests**,
including all seven opt-in tests, migration/schema checks, real checkpoint
reconnection, concurrent lock ordering, runtime launch and capacity behavior.
Generated Flutter transport still matches the frozen OpenAPI target, and
`git diff --check` passed. No Flutter UI files changed in this phase; the full
Flutter suite was not rerun (the preceding phase's 402-pass result remains scoped
to that run). No native rebuild, paid inference, GPU deployment or physical work
ran in this phase. The upstream Trustcall deprecation warning remains.

Outstanding scope includes full independent electronic/firmware/application
verification, companion delivery/hosting, archive imports, remaining existing
workbench controls, deployed MCP/skill capabilities, packaging, recovery operator
paths and end-to-end frontend cutover. No completion or code-reduction claim is
made for the full rewrite.

## Completed foundation work

- Verified snapshot of 612 source/config/document files before deletion:
  `.rewrite-archive/backend-before-langgraph-2026-11-02.tar.gz` with SHA256 manifest.
  Local-only archive ignored by Git. Credentials/data remain at original paths.
- Stopped idle old API, engineering, connectors and preview containers.
- Deleted old active code, tests, migrations, tooling and documentation.
- Preserved exact frontend OpenAPI target:97 paths/108 operations/96 schemas.
- Created fresh src layout and shared camelCase contracts.
- Resolved84 dependencies including LangGraph1.2.19, LangMem0.0.30,
  PostgreSQL checkpointer3.2.0 and langchain-openai1.7.3.

## Ownership

- root: shared contracts, projects/state, artifacts, access, infrastructure/integration.
- rewrite_contracts: api package and API tests.
- rewrite_graph_design: orchestration, memory and respective tests.
- rewrite_bambu: capabilities/fabrication and Bambu tests.

## Required remaining gates

1. Persistent project/ownership/revision/idempotency and scoped artifact changes.
2. Actual durable graph: dynamic dependencies, parallel tasks, replan, cancellation,
   bounded usage, wait/resume, process restart without repeated side effects.
3. LangMem extraction and search with persistent tenant/project isolation.
4. Real isolated CAD/PCB/routing/firmware/application tool adapters and independent
   checks. Imported project edits preserve unrelated source hashes.
5. MCP/skill capability registry with qualified, credential-scoped workers.
6. Sourcing/human jobs: provider adapters, approval, dispatch, uncertain-effect
   reconciliation and independent evidence review. No invented successful jobs.
7. Bambu discovery/pair/telemetry/slicing authorization and A1mini compatibility.
8. Complete frontend contract parity: commands/events/auth/images/model settings,
   tools/connectors/runs/artifacts/previews/releases/admin and task projections.
9. Fresh database baseline, secure runtime packaging, local service launch.
10. End-to-end complete device, bracket-only, imported PCB routing, component
    replacement, human verification, restart/cancel and isolation scenarios.

Root documentation and the removed Conductor/Qdrant/DBOS infra were cleaned on
3 November 2026; the control-plane Terraform wiring remains open (see `infra/README.md`).
Old native images contain archived runtime code; the new native runtime must not
silently keep using that backend implementation.

## Earlier checkpoints

The dated checkpoint log (2–3 November 2026: test totals, per-feature increments,
code-size snapshots) was removed as superseded history. It remains in Git history
before this commit. Current status is the sections above and `rewrite-remaining-audit.md`;
exact qualification evidence lives beside each module (`*/qualification/`).
