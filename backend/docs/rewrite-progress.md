# Backend rewrite progress

## Authorized objective

Complete fresh backend: task-oriented LangGraph orchestrator, LangMem shared
memory, modular specialists, isolated engineering tools, external procurement/
human-work lifecycles, existing Flutter contract. Reuse only Bambu connection
logic and public certificates; do not copy the old backend implementation.
Do not declare completion based on fixtures or code-size reduction alone.

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

Old root repository documentation/infra still reference removed implementation;
update them before completion. Old native images contain archived runtime code;
new native runtime must not silently keep using that backend implementation.

## Foundation checkpoint

Actual implementation now includes:
- SQLAlchemy ProjectStore with per-project memberships, transactional events,
  idempotent commands, revisions and free-form objectives (no stage prerequisites).
- GraphRepository with durable external/model operation claims/receipts, uncertain
  effect blocking, cancellation fences and candidate observation publication.
- Immutable local blob storage and scoped hash-checked source-manifest changes.
- KLEOS2 settings namespace; old .env/database cannot silently become the new store.
- Injected FastAPI adapter registers all59 frontend route obligations; route
  coverage is NOT completed service functionality. A production facade is pending.
- LangGraph ObjectiveEngine and LangMem Knowledge with injected model tests and
  persistent PostgreSQL factory. PostgreSQL restart qualification remains pending.
- Bambu connections newly implemented with offline tests; physical dispatch absent.

Combined foundation suite passed60tests before5new settings tests were added;
settings5pass separately. No new HTTP server has been started. Old services remain
stopped; data/volumes are preserved. No paid inference/physical job was started.

Next root work: finish shared schema/migration; integrate access+API facade with
projects/artifacts/objectives; production model adapters and durable worker;
new isolated execution/capabilities; complete frontend responses and scenarios.
Active agent ownership: rewrite_contracts now access/auth; rewrite_graph_design
now dynamic task supersession/model harness; rewrite_bambu finishing connections.
Dependency deprecation warning in LangMem's trustcall dependency (Send import)
observed; functionality tests passed. No claim of full production qualification.

Latest combined foundation run:66tests passed; Ruff clean (one dependency deprecation warning).

## Modular implementation checkpoint — 2 November 2026

The earlier counts above record the foundation at that time. Current additions:

- AccessService with persisted accounts, organization membership, hashed sessions,
  browser CSRF protection and login throttling; scoped FastAPI service facade.
- Idempotent private uploads and metadata-stripped image references. Planner
  receives authorized inline image bytes, fails when no image loader exists,
  and includes explicit part, interface, dimension and geometry constraints.
- Native candidate publication receipts survive restart without duplicating
  files/events. Source closures preserve unchanged source IDs and hashes.
  Task results cannot invent artifact references, hashes or cross-project inputs.
- Objective dispatcher uses PostgreSQL advisory locks and fenced worker identity.
  Exact interruption IDs bind saved answers. LangGraph owns checkpoints and
  resumption; the dispatcher does not create a second workflow engine.
- Recovery of the known specialist coordinator reuses nested model/tool receipts;
  genuinely uncertain external effects remain blocked. Task context and tool
  inputs are frozen before execution; dependency artifacts are resolved through
  authoritative storage. Validated plans are published as project events.
- Explicit model catalog with image capability checks, pinned protocols/policies,
  durable usage/cost reservations and only explicitly configured Modal429 fallback.
- Qualified capability registry, fixed native tool schemas and fresh isolated
  execution supervisor. Actual native-image qualification is a separate gate.
- Scoped sourcing/human-job lifecycle, read-only DigiKey/RentAHuman adapters and
  independently reviewed human evidence. Physical dispatch remains policy-blocked.
- Bambu HTTP facade checks actual local transport, origin and administrator status.
- Independent validation policies and immutable accepted release manifests;
  generator observations cannot satisfy independent acceptance. Concrete checker
  adapters still require qualification before registration.
- Explicit fresh-schema Alembic baseline for24application tables. Migration refuses
  unrelated existing application tables, and leaves LangGraph-owned tables alone.

Real PostgreSQL verification ran against a separately created disposable local
database. Three tests passed: migration upgrade/schema check, competing run locks,
duplicate-effect exclusion/receipt replay, and graph plus memory reconstruction
after all persistence connections closed. These checks used fixture models/tools,
not paid inference. Old databases and volumes remained untouched.

Remaining integration work includes complete capture/concept and frontend command
parity, concrete registered engineering and independent-check capabilities,
PCB routing/firmware/app qualifications, external API integration, runtime launch
and full end-to-end scenarios. The frozen OpenAPI file is still a target, not a
claim that every route has a functional service implementation.

### Verified runtime and native CAD checkpoint

- Latest combined test run at this checkpoint:279passed,4explicit PostgreSQL/process
  opt-in tests skipped, one upstream LangMem/Trustcall deprecation warning. The
  three PostgreSQL tests and one real CLI/HTTP startup test passed separately.
- CLI migration now owns its PostgreSQL transaction: a real process-start check
  exposed a rolled-back schema despite a successful migration exit; the explicit
  transaction fix passed the repeat process/HTTP test. Temporary server stopped
  after qualification; this was not a frontend cutover.
- Fresh exact CAD image qualified locally:
  `sha256:62280256393a1f822fa1a256eb2991220bb2ec225f7e17894dbb52ec7a02d918`.
  Evidence: `src/kleos/execution/qualification/cad-exact-image.json`.
  Two real parts passed patch/build/analyze/render/finish, STEP/STL export,
  measurement/hash checks and idempotent publication. Qualification covers this
  fixture on explicit Docker Desktop daemon-seccomp mode, not all designs/ECS.
- Docker Desktop startup/socket and host-bind permission differences were found
  using actual containers. Inputs are read-only; trusted results live in private
  Linux tmpfs and transfer through a fixed trusted reader. Generated code remains
  unprivileged, without credentials, and cannot forge trusted result files.
- Successful native execution remains UNKNOWN as engineering evidence; explicit
  trusted candidate-execution markers allow the generator task to finish once
  actual artifacts exist. Independent validation and acceptance remain separate.
- LangMem retrieval now retains historical preferences/lessons across objectives,
  marked advisory with current/historical source status. Scoped optional extraction
  uses durable model receipts and does not invalidate produced candidates on a
  separate memory failure. No paid extraction was run.
- Model/tool catalogs reflect actual operator configuration. Capture defaults
  only advertise explicitly configured available vision models. Catalog tests
  separately verify frozen response fields and absence of credentials/default inventions.
- Migration0002adds saved external draft presentation data; the already-tested
  baseline was not rewritten after the schema addition.

Current disjoint assignments: root integrates API/state/schema; graph agent owns
capture/concept behavior; execution agent owns real PCB routing qualification;
Bambu agent owns isolated Orca preparation. Remaining frontend operations and
all other native/physical qualifications remain incomplete and explicit.

### Integrated modules and revision/rebuild checkpoint

- Root integrated the separate fabrication worker, independent checker, release
  facade, external installation composition and dynamic engineering command adapters.
  HTTP startup still reports partial readiness; no complete frontend cutover is claimed.
- Independent CAD image
  `sha256:e3c5823091b7dde83d1043bd455ef96567939424ab447a3efed1149dc1030e69`
  passed six actual isolated cases, including wrong dimensions, changed digests,
  unsupported requirements, wrong units and mesh mismatch. The named policy is
  `cad.export-integrity`; it does not certify arbitrary engineering requirements.
- Exact firmware image
  `sha256:1ea0aa4e85133be3a85fd7399d53c94cb2d1dc6d426b603235fcf44beb93e3bc`
  compiled the explicit `nrf52840dk/nrf52840` fixture to ELF/BIN. No board execution
  or flashing occurred. Missing/unqualified board selections fail closed.
- Isolated Orca image
  `sha256:e283c0bede34777412d02443889d075b5f0a60f6c9cc50e2524fd89340bde40e`
  passed A1 mini/A1/P1S offline fixture slicing and open-mesh/modified-preset rejection.
  Runtime registration checks exact image, source, profile and isolation evidence.
  Separate worker admission has global capacity control and reconciles before claiming.
- Actual companion web compilation passed on
  `sha256:7c5aedd853f6be70c0d629a90134297fdf94716213cf32b3e2a743339776ea8d`
  with hard 4 GiB private tmpfs and 6 GiB memory. Final evidence audit is in progress;
  this is not APK, browser behavior, BLE or device qualification.
- Release downloads are deterministic private archives of the exact accepted
  source closure/output references. Tests reject cross-tenant access, altered
  manifests, missing/corrupt blobs and changed provenance; replay reauthorizes.
- Workspace projections now show actual objectives/tasks, independent checks and
  external jobs. Accepted current-release checks stay current across provider-only
  settings changes. Native run views expose saved jobs and structured admin-only
  action history without raw source/patch/output content.
- Engineering/context revisions now advance on content changes. Provider-only
  changes preserve the engineering revision. A regression proves stale upload
  rejection and idempotent replay without overwriting a newer source.
- Repairs snapshot exact previous candidate inputs without accepting them. The
  planner is fenced to authorized edit paths. Generic completion clears queued
  planning state without creating an accepted release.
- Native-only rebuild uses a deterministic LangGraph planner/specialist, original
  source hashes/image/resources/network pins, bounded linked grants and zero
  model/retrieval calls. Production-gateway mocked integration covers restart after
  a committed action and changed operator settings. Real-image rebuild qualification
  remains outstanding. Confirmed native termination is required before admission.
- Independent external review found account identity/pin isolation gaps despite
  passing fixture suites. Corrections and re-review are required before creating
  a reviewed external qualification record. No live provider actions ran.

Verification at this checkpoint: the latest completed broad backend suite passed
449 tests with four explicit opt-in skips; later edits have focused verification
and require another combined run. PostgreSQL/recovery and temporary CLI/HTTP startup
passed four separately opted-in tests on the disposable local database. Flutter
analysis and generated-transport checks passed. The unchanged Flutter suite passed
402 tests with four skips after restoring three shared JSON fixtures removed by
cleanup. The retained files contain test data only, not legacy backend code:
`tests/fixtures/concept-geometry-v2.json`, `verification/ngspice-power-on-sample.json`
and `kleos/bambu-models.json`. Capture now preserves the shared scene's explicit
relative units; physical dimensions remain separately typed constraints.

Raw runtime Python newline comparison at one intermediate checkpoint was 29,557
previous versus 11,859 replacement lines (59.9% reduction), excluding tests and
replacement qualification scripts. The replacement is incomplete and this is not
a final reduction or functional-equivalence claim. Do not trim necessary behavior
or count fixtures as implementation to manufacture a target percentage.

Still required: complete frontend command/query behavior, post-run verification,
standing external event/timer resumption, usable accepted interface binding,
simulation/preview integration, plugin deployment/qualification, APK scope,
replacement packaging and local full-workflow verification. No live physical gate
is unlocked, no deployment was performed, and old data/volumes remain preserved.

Follow-up evidence: the final web audit is now in
`src/kleos/execution/qualification/web-exact-image.json` and `WEB.md`.
Runtime qualification verifies the web-only target, 6 GiB/2 CPU/256 PID bounds,
hard 4 GiB executable private tmpfs and absence of writable host mounts.
The exact-source CAD rebuild also passed with actual Docker through the production
gateway and deterministic graph; see `rebuild-attempt-1.json`. It preserved the
original image/resource/network/source contracts despite changed operator settings,
required confirmed termination of its parent, produced eight candidate artifacts
and replayed finish without a model or retrieval client. This closes the earlier
real-image rebuild fixture gap, not broad native/device qualification.

External integration remains under independent review. The initial review in
`external-readonly-review.md` found five issues, including account binding changes,
unbounded decimal formatting, unsuccessful status promotion and an owner-role race.
Fixes require exact-source re-review before any qualification report is accepted.

Final independent external re-review passed all five reproduced regressions and
104 focused tests on the exact corrected source hashes. Source-bound records now
exist at `src/kleos/capabilities/qualification/digikey-readonly.json` and
`rentahuman-readonly.json`; the real registration gate accepted both records
without constructing clients or contacting providers. Scope remains mocked HTTP,
read-only adapters, with `liveAccessVerified: false`. No accounts are configured
by this change. Root runtime filters installed external tools per actor, pins the
opaque account installation identity and fences changed-account recovery before
model work. Unrelated account additions preserve existing pins.

The audit also found native reconciliation was not reachable from any worker.
A bounded independent maintenance task now runs alongside objective work, plus
an explicit `native-maintenance` CLI for cleanup without generation/model calls.
Unknown cleanup retains durable records for retry. Final combined verification
is required after this maintenance wiring and concurrent receipt hardening.

## 3 November 2026 — interfaces, verification and shared compute admission

This phase was implemented by separate interface, verification/fabrication and
native-capacity owners, with root integration and cross-module review. The whole
rewrite remains incomplete; this checkpoint is not frontend cutover approval.

- Interface proposals now use the actual model gateway, bounded imported context
  and authorized vision inputs. They remain candidates until explicit approval
  through the existing conversation or exact proposal ID/hash command. User,
  revision, concept identity and source-baseline checks fence approval. Electronic
  concept confirmation requests its required circuit review first; mechanical
  work keeps its direct dynamic objective. Approved visual references remain
  explicitly relative illustrations, not manufacturing dimensions. The unchanged
  message payload and display stages are exercised through mocked-provider HTTP
  flows. Default generation waits for pending interface review; explicit scoped
  work remains available.
- `project.verify` now queues independent checks against the completed objective's
  exact source closure and candidates. It makes no new model/retrieval call,
  does not reactivate generation and leaves accepted sources unchanged. Current
  policy covers CAD export integrity only. Existing Cancel and separate release
  acceptance paths are wired. Permission/context/policy changes during checking
  prevent completion or acceptance. Only a trusted pre-dispatch capacity deferral
  may requeue the same check/job; original scope/deadline remain pinned and actual
  running, failed or uncertain checks are not silently repeated.
- Native generation, slicing and checking share PostgreSQL-backed aggregate
  reservations, with nonblocking cross-process admission. Default pool limits are
  four CPUs, 6 GiB RAM, 5 GiB total scratch and two jobs. Native control scratch and
  slicer control scratch are included without changing original container limits.
  Older unreserved work must be reconciled before new admissions. Pool changes
  cannot silently discard reservations.
- Independent review reproduced cleanup and admission defects despite passing
  unit fixtures: lost create acknowledgements, deletion by mutable name, name-only
  absence checks and capacity timeout becoming permanent ERROR. Corrected paths
  persist observed immutable container IDs, require ID/name removal evidence,
  retain uncertain reservations and defer only before any dispatch. Renamed
  containers, cancellation races and same-job capacity retry have regressions.
- Workbench model spending now includes historical attempts and retains uncertain
  charges as reservations. The private invocation endpoint exposes only bounded
  operational metadata after project and organization-admin authorization. Task
  attribution is async-local and isolated across concurrent specialists; raw
  prompts, provider reasoning, credentials and endpoint configuration are excluded.
- Migration `0004_capacity_verification` adds capacity and verification records.
  A separate `verification-worker` and the ordinary objective worker dispatch
  these checks; no model call is made by the verification loop. The fresh test
  schema helper now explicitly registers all modules, eliminating import-order
  dependence discovered by standalone native qualification.

Verification on the final combined code: **574 backend tests passed, five explicit
opt-in skips; Ruff passed.** A separate disposable PostgreSQL run passed **15 tests**,
including the five opt-in recovery, real CLI/HTTP startup and cross-process capacity
checks. Flutter analysis and generated transport verification passed; the unchanged
Flutter suite passed **402 tests with four skips** (excluding hidden suite-loading
events). Runtime OpenAPI exported separately; the frozen frontend target and
generated Dart files were not changed in this phase. `git diff --check` passed.
The existing upstream Trustcall `Send` deprecation warning remains.

Real isolated native evidence is in
`src/kleos/execution/qualification/rebuild-capacity-2026-11-03-attempt-2.json`:
the existing exact CAD image rebuilt the fixed two-part source, preserved original
image/resources/source hashes despite changed operator settings, produced eight
candidate artifacts and replayed its trusted finish. Both parent and rebuild held
capacity until reconciliation; final reservations were zero. Docker inspection
confirmed no native containers remained. No source override, model/retrieval call,
live provider, printer or physical operation ran. The initial failed qualification
record is preserved at `rebuild-capacity-2026-11-03.json`; it failed before dispatch
because the standalone fixture had not registered the slicer table.

Remaining priorities: explicit imported-PCB identity approval without requiring a
visual concept, external event/timer-to-LangGraph resumption, previews/simulation,
archive import and remaining workbench commands, broader independent policies,
backend plugin deployment, packaging and full frontend cutover. Missing container
acknowledgements remain conservatively held where absence cannot be proven; an
operator recovery path is still needed for those unresolved cases. No full-device,
live-provider or physical qualification is claimed, and no final code-reduction
percentage is claimed for this incomplete replacement.

## Archive import, requirement edits and Bambu test preparation — 3 November 2026

- Explicit `project.importArchive` imports a bounded ZIP as an atomic add-only
  source closure. It preserves exact bytes, hashes, existing sources and provenance;
  rejects traversal, links, duplicate/ambiguous paths and expansion limits; and
  advances engineering context once. Ordinary ZIP uploads remain opaque. The
  command is reachable through the existing command envelope; no new UI picker
  or remote repository checkout was added.
- `project.swapBattery` accepts the existing explicit capacity/dimensions payload;
  `project.toggleFeature` changes an exact candidate requirement. Both preserve
  existing source/artifact/release history, invalidate current approvals and old
  work, and queue recapture through the existing workflow. Explicit user edits
  survive capture and confirmation. Exact structured requirement checks do not
  prove semantic agreement, physical fit, electrical safety or compatibility.
- Existing Bambu test-print create/status/source/plate routes now use a fixed
  10×10×2 mm tile and the existing durable isolated slicer queue. A private fixture
  project is excluded from ordinary listing and authorization. Tests cover replay,
  worker reconciliation, restart, private downloads and concurrent first requests.
  Downloads are bound to actual stored hashes; no printer upload/send is enabled.
  Slicer installation remains explicit, and fake-driver evidence is not a native
  image or physical print qualification.
- Root integration and a separate agent review checked archive bounds, source
  history, authority invalidation and composed HTTP paths. The independent review
  passed 96 scoped tests. The combined checkpoint passed **878 backend tests with
  seven opt-in skips**, plus Ruff and diff checks. A separate disposable local
  PostgreSQL recovery/runtime run passed **17 tests**. Generated Flutter transport
  still matches the frozen contract. No Flutter UI files, paid inference, cloud
  resources, live printer operations or generated host-code execution changed.

Companion web preview hosting is the next active module. Its new configuration
and API service seam are preparatory; the separate server, runtime wiring and
browser behavior are not qualified by the checkpoint above. Independent release
coverage beyond CAD, repository import, remaining workbench controls, plugin
installation, deployment packaging and full frontend cutover remain incomplete.

## Companion static preview hosting — 3 November 2026

The existing companion-preview action and engineering-preview metadata now connect
to a separate `preview-serve` process. It is disabled by default, has no API/auth
routes, uses three distinct canonical origins, disables token-bearing access logs
and bounds concurrent responses. Encrypted short-lived leases bind the requesting
actor, project context, artifact hash and native publication. Every file request
rechecks account/organization/project membership, publication and current context.
Files are parsed from bounded ZIPs without extraction or server-side execution.

Independent review found and corrected organization revocation, semaphore/row-lock
ordering and equivalent-origin handling. Final preview/runtime/settings/CLI checks
passed **96 tests**; scoped Ruff and diff checks passed. The broader backend
snapshot subsequently passed **960 tests with seven opt-in skips**, with Ruff
clean. That snapshot includes the developing PCB-policy work; it does not qualify
an independent PCB image or certify that unfinished module.

An actual in-app browser rendered the trusted static fixture through the unchanged
outer iframe restrictions and the new nested wrapper. Relative assets and the tiny
WASM fixture loaded, the visible origin was opaque, storage/service workers were
blocked, and keyboard activation incremented the counter. Temporary servers and
browser tab were stopped. See [companion preview](companion-preview.md) for exact
scope: this is not generated Flutter/CanvasKit or all-browser isolation evidence.
The previous qualified native web ZIP bytes were not found in the accessible
preserved fixture locations. A fresh exact-image reproduction and stronger browser
checks are being prepared; neither is claimed complete here.

Independent digital policy coverage and a fresh PCB checker are being implemented
and reviewed separately. Generator ERC/DRC/build reports remain candidates; absent
qualified checker installations must block release. Remaining full-rewrite gates
include broader release coverage, actual generated-app browser qualification,
repository imports, remaining workbench controls, plugin deployment, packaging
and full unchanged-frontend cutover. The active goal is not complete.

## Combined backend and deletion checkpoint — 3 November 2026

The unchanged project DELETE route now records an owner-authorized, revision-fenced
tombstone and returns HTTP 202. Ordinary project access/listing stops immediately;
artifact bytes, release history, command receipts and uncertain compute reservations
are retained. Existing reconcilers own compute termination. No user project was
deleted during verification. See [project deletion](project-deletion.md).

After integrating the preview asset queue, default-font packaging and lifecycle
changes, Ruff passed and the complete backend suite passed **1,012 tests with eight
explicit opt-in skips**. A separate run against the existing disposable local
PostgreSQL database passed **27 tests**, covering lifecycle deletion and observer
lock ordering, external waits, runtime recovery/launch and capacity admission.
`git diff --check` passed. The upstream Trustcall/LangGraph deprecated-import
warning remains. No paid inference, cloud mutation or physical operation ran.

This checkpoint establishes only the checked implementation scope. Independent
PCB image review and compiled companion browser evidence are recorded separately;
firmware/application release validation, repository import, remaining workbench
controls, plugin deployment, packaging and complete frontend cutover remain open.

### Actual companion Flutter browser qualification — 2026-11-03

A fresh exact-image run of the original trusted Flutter fixture exposed failures
that HTTP fixtures had missed: forbidden service-worker startup, concurrent asset
requests rejected by the two-processor gate, and an absent local default text font.
The fixes preserve the unchanged workbench iframe restrictions: versioned and
hash-declared preview-only bootstrap/HTML views; bounded pre-transaction asset
admission while metadata remains nonblocking; and a native output-only step that
packages pinned SDK Roboto fonts, complete license and provenance into the ZIP.

New image `sha256:572fd5f8d64fd6d7a1bed7c955ee33c8223529a43fc0dedd8e53dcc81328e315`
compiled the unchanged fixture without network or source override. Source hashes,
all12font hashes, license, manifest and capacity1→0 were verified. ZIP SHA-256 is
`556f0b9a9fb3c12d9a51ffb30909d02709c0db46062558810db2312710072e2f`.
The root integrator then observed the actual app in IAB: title and count rendered,
and keyboard interaction changed the visible sample count0→1. Browser error/warning
logs were empty afterward. Temporary preview servers were stopped.

See [exact scoped evidence](companion-native-font-browser.json) and
[derivation, failed attempts and limitations](companion-preview-derivation.md).
This is narrow application-fixture/browser evidence, not independent engineering
acceptance, device/BLE behavior, arbitrary generated-app qualification or an APK
claim. Older image/report/artifact evidence remains intact; no paid model/provider
calls or physical actions were used.

## Workbench preferences and CI repair — 3 November 2026

The existing `project.selectPrint` and `project.assemblyStep` commands now reach
`WorkbenchProgress` through production runtime composition. Print selection saves
`home` or `order` as a preference without printer dispatch or purchasing. Assembly
navigation records bounded adjacent steps as synthetic progress, with completion
`UNKNOWN` and physical acceptance false. Both enforce write authorization, current
UI revision and durable replay, while preserving active engineering context,
source hashes and release authority. The unchanged workspace projection exposes
the saved state. See [workbench progress](workbench-progress.md).

Forty focused module/runtime tests passed, including composed HTTP command/replay
and workspace checks with a model transport that fails if called. Ruff and diff
checks passed. This is not a complete simulated fulfillment journey: these
commands do not advance shipping, activation or acceptance stages.

CI now installs the existing `uv.lock` with pinned uv 0.8.11, uses the fresh
`KLEOS2_DATABASE_URL`, migrates business and graph schemas through the current CLI,
and exports implemented HTTP schemas separately from the frozen UI target. The
simulation tests use the installed backend environment. Local verification passed
the offline locked dependency dry run, runtime contract export, YAML parsing,
15 simulation tests, and migration plus `alembic check` against the disposable
PostgreSQL database. The initial restricted macOS uv invocation crashed in system
configuration; the identical offline dry run outside that restriction passed
without modifying dependencies. GitHub Actions itself was not run. Cloud deploy
and Terraform still require fresh runtime packaging and adapter work.

## Reviewed independent PCB checker — 3 November 2026

The fresh checker image passed 15 isolated qualification cases and an independent
agent reviewed the exact image, four implementation hashes, all input/request
bindings, container restrictions and released capacity reservations. The reviewed
report is [pcb-check-exact-image.json](../src/kleos/validation/qualification/pcb-check-exact-image.json);
raw attempt 8 and earlier failed attempts remain unchanged. The installation
validator accepted the reviewed report in a temporary registry; no operator
configuration or running service was changed.

The supported scope is the recorded local, simple two-layer digital identity and
fresh ERC/DRC checks. The watchdog evidence uses a trusted sleep workload under the
fixed timeout wrapper, not a claim about malformed-board performance. Arbitrary
circuits, manufacturing-export correspondence, electrical behavior and physical
acceptance remain unqualified. Firmware/application release checking is still
separate work.

The root verification of PCB/policy/font/schema regressions passed **64 tests**.
The CI service database was also renamed `kleos_test_ci` so its explicit opt-in
recovery tests satisfy the disposable-database guard. This is a configuration
correction; GitHub-hosted execution remains unobserved.

## Bounded repository import and cancellation — 3 November 2026

`project.importRepository` now imports a public GitHub repository at an explicit
full commit SHA through the existing command envelope. A bounded HTTPS downloader
uses fixed provider hosts, validated public IPs and original-host TLS verification;
redirects cannot change the selected repository/commit. It receives no provider
keys or ambient proxy credentials. Raw archive and individual source hashes remain
immutable, with original paths and wrapper removal recorded in provenance.

New migration `0006_repository_imports` stores download receipts. Admission and
publication reauthorize the actor and exact project context; completed command
replay does not download again. A downloaded receipt survives a process restart.
Uncertain downloads are not automatically repeated. The explicit
`project.cancelRepositoryImport` command uses the original import key and fences
publication immediately without claiming transport termination or deleting files.
Account and organization-membership locks serialize publication with revocation.

The root composed HTTP tests exercise import, replay, workspace projection and
cancellation of an in-flight download. The import/schema/archive checkpoint passed
98 tests before the additional cancellation/locking tests; the subsequent focused
import run passed 23 tests including real PostgreSQL revocation interleavings.
Migration plus `alembic check` passed against the disposable database, and a
regression verifies upgrading an existing rewrite project preserves its receipt
and data. A separate settings/runtime/PCB/policy/packaging run passed 75 tests.
Ruff and diff checks passed. No live repository fetch, paid model or physical work
was used for these checks.

This initial path supports bounded source-only archives. Ordinary hidden repository
metadata, private repositories, LFS content and submodule resolution are not yet
supported; unsupported content fails explicitly. See
[repository import](repository-import.md) for the exact API and limitations.
No frontend picker or other UI change was introduced.

## Combined integration checkpoint — 3 November 2026

The complete backend suite passed **1,149 tests with ten opt-in skips**, and Ruff
passed, after repository import/cancellation, new checker composition hooks and
local packaging landed. This checkpoint predates subsequent independent-review
fixes to the new application export parser. Review found ZIP filename/prefix
aliases and duplicate HTML-attribute interpretation differences; native checker
qualification remains held until those corrections are reviewed. No unsupported
checker was installed by passing the fixture suite.

The root setup guide now uses the replacement's actual lockfile, CLI, settings,
native-image contracts and source-layout documentation. Deleted old setup commands
and unsupported APK/cloud/runtime claims were removed from that guide. Its local
Markdown targets and `git diff --check` pass. Cloud deployment and full frontend
cutover remain incomplete.

## Native scratch accounting correction — 3 November 2026

An exact historical firmware-image fixture run failed when ccache removed a
temporary stderr file between the supervisor's existence and size checks. The
current supervisor source now uses one non-following stat per entry and tolerates
only files that disappeared; other stat errors still fail the operation. The
container's hard scratch allocation remains the independent resource bound.
No generated program was executed on the host to test this correction.

Three focused filesystem regressions cover regular-file accounting, deletion
during inspection and permission-error propagation. The combined execution,
runtime, project-context and import-HTTP run passed **83 tests**. This source fix
is not present in historical pinned images; new exact-image qualification remains
required before using it in native deployments. The failed firmware preparation
report is retained separately by the qualification phase.

## Authority and plugin persistence checkpoint — 3 November 2026

Runtime project access now requires a current account and organization membership,
including already-admitted graph work. Organization viewer roles cannot continue
writes. Listing, creation and deletion use the same authority boundary; private
read failures retain non-disclosing not-found responses. Existing completed effect
receipts remain intact after access is revoked. Slicing independently checks its
durable creator before admission and candidate publication, including the internal
Bambu test preparation's administrator requirement. Cleanup remains independent of
user authority and frees capacity only after verified removal.

New migration `0007_mcp_sessions` adds immutable dispatch identities and cleanup
records for the isolated MCP runner under development. The `mcp` reservation
namespace shares the same aggregate CPU, memory, scratch and job bounds as native,
slicer and checker work. Schema registration and fresh/existing-database migration
checks pass. This does not enable an MCP installation: independent runner review,
actual image qualification and runtime composition are still pending.

The combined backend checkpoint passed **1,241 tests with ten opt-in skips**.
A separate disposable PostgreSQL run passed **14 tests**, including migration
consistency, competing process admission and account/organization revocation
interleavings during repository publication. The focused schema/capacity/authority/
slicing suite passed **55 tests with one opt-in skip**. Scoped Ruff passed.
The full-suite snapshot predates ongoing MCP runner corrections; it is not proof
of that runner's container qualification or full workbench cutover. No paid model,
physical action or cloud operation ran in this checkpoint.

Independent repository-metadata review also passed 101 focused import/archive/
runtime checks and scoped Ruff. It verified immutable archive/commit/path provenance,
transactional rollback, current authority, bounded quoted excerpts and exclusion of
metadata from native source inputs. This supersedes the earlier source-only import
limitation for the explicitly supported metadata paths; private repositories, LFS
and submodules remain unsupported. The review performed no live repository fetch.

## Imported-project single-part journeys — 3 November 2026

Four composed Runtime/API/worker regressions cover creating a missing bracket and
redoing an existing bracket, each with a negative case for unauthorized changes to
the neighboring enclosure. Project inputs are uploaded through the artifact
service, without seeding approvals. Real planning, specialist, native gateway and
publication boundaries execute with mocked model HTTP and a fixed non-executing
native writer. The specialist receives an actual source-read observation. Both
positive paths keep existing source IDs, hashes and bytes, publish only scoped
candidates, and require neither concept capture nor unrelated engineering lanes.
Replaying the command makes no additional model or tool calls.

The author checkpoint passed 24 journey/engineering/publication tests; the root
combined journey/orchestration/repair/validation-policy run passed **41 tests**.
These fixtures prove coordination and source authority, not actual bracket geometry
or physical fit. Multi-step source dependency precedence and final composed-source
rebuilds remain separate outstanding work; no release closure check was weakened.

## Explicit MCP runtime composition — 3 November 2026

The runtime now registers only explicitly configured, independently reviewed MCP
installations. Configuration/report loading is bounded and validates every entry
before lazy Docker transport construction; startup performs no daemon IO. Worker
and maintenance entry points include MCP cleanup, including persisted sessions
whose installation has since been removed. No installation is enabled by default.

Independent review found that the runtime wrapper initially hid the runner's image
preflight. The wrapper now forwards that check before the tool effect is claimed.
A composed registered-tool regression confirms repeated unavailable-image attempts
create no effects, sessions or capacity reservations. Runtime/maintenance checks
passed **28 tests**, with scoped Ruff and diff checks clean. Actual isolated-image
qualification, the runner's original-daemon/lifetime cleanup checks and the full
backend combination after those changes remain outstanding. These lifecycle
fixtures are not evidence that a desktop plugin is safe to install in production.

The subsequent combined checkpoint passed **1,295 backend tests with ten opt-in
skips**, plus full source/test/migration Ruff and diff checks. The focused MCP,
runtime and shared-capacity set passed **68 tests with one opt-in skip**. Review
also corrected duplicate installation IDs across versions, which otherwise made
the model-facing tool catalog ambiguous. Independent static lifecycle review
passed 60 checks, while the actual isolated MM-core image build began separately.
This snapshot includes initial dependency-source selection tests but predates its
repair-snapshot extension, final composed-source compilation and actual MCP image
qualification. The broader rewrite remains incomplete.

## Dependency handoff and actual plugin evidence — 3 November 2026

Dependency input selection now requires an exact published native successor before
replacing an older source reference. It preserves editable paths and accepted
source identities, rejects competing edits without declared ancestry, and removes
shadowed versions from the checkpointed task context. Prior-run repair inputs must
be the exact pinned ancestor of their replacement. The root combined handoff,
orchestration, repository, repair and single-part journey suite passed **57 tests**;
scoped Ruff passed. Subsequent review identified a superseded-dependency ancestry
edge case, which remains under correction rather than being treated as covered.

The first real MM-core image attempt stopped before parser execution because Docker
added pinned image provenance labels to the expected session labels. The ownership
comparison was corrected without dropping exact image/session identity checks;
the unstarted container was removed and its reservation settled. Attempt 2 used
exact image `sha256:c48f10ec68186f6e2425577e19bbd8cf8e352c1bd76926305920bbf82abdae93`
and the real upstream image reader, with no source override. PNG inspection and
receipt replay passed; malformed and excessive-pixel inputs were rejected. After
controller loss, the image-owned watchdog exited with code 137 after approximately
30.12 seconds. Capacity remained reserved until verified removal, then returned to
zero. Root review verified current source hashes against the retained raw evidence.

That evidence remains unqualified pending the independent review and an additional
actual JPEG case. It proves no semantic image understanding, engineering acceptance,
FreeCAD/Blender capability or production deployment. The installation remains off
by default, and the failed first attempt is preserved.

## Frozen-source output publication — 3 November 2026

The new composition grant service freezes exact authoring sources, prior repair
inputs, producer contracts and bounded history without modifying accepted sources.
Its independent review passed 79 combined grant/dependency/validation checks.
Unsupported overlapping authors, ambiguous standard export targets, derived-output
dependencies and ID-less historical rebuild inputs remain explicit blockers.

A separate output-only publication path now revalidates that grant in the same
project transaction. It binds the compiled job, original image/resource contract,
exact source bytes and IDs, output scope and immutable admission deadline. It
publishes candidate exports with the original source closure and no new source
artifacts. Existing publication receipts provide atomic replay; cancellation and
changed scope still fence replay. No generic release closure guard was relaxed.

Independent review reproduced two initial gaps: terminal compiled jobs could
publish, and a long objective could exceed the target execution duration. The
publisher now locks the compiled job and requires an exact in-flight finish for a
first publication; a persisted server admission binds the start and bounded
deadline. Exact already-committed replay remains supported after job completion.
The final combined publisher/native-publication/grant/validation suite passed
**75 tests**, with scoped Ruff and diff checks clean. The reviewer separately
passed 59 focused checks after the corrections. These are mocked execution/data
boundary checks; deterministic execution wiring and full combined releases are
still under implementation.

## Final compilation and canonical consumers — 3 November 2026

The objective graph now runs a deterministic composition node before ready
validation or completion when native outputs do not cover the final source set.
It uses pinned grants and fixed native actions, records only an all-target
completed handoff, and retains original authoring results and source identities.
Multiple standard targets in one lane remain blocked pending qualified entrypoint
selection; this checkpoint does not claim arbitrary multi-target builds.

Planning receives a read-only projection of verified compiled references. Explicit
superseded authoring output IDs are rejected rather than silently retargeted.
A matching completed grant is fully verified before skipping composition; corrupt
receipts fail instead of triggering an automatic rebuild. Independent engine
regressions cover the actual planner packet, immutable history, receipt replay,
changed checkpoint results and stale explicit input selection.

Engineering and companion preview selection now uses completed compiled exports.
Validation carry-forward cannot restore replaced outputs. Accepted release
selection and companion hosting code remain unchanged; no Flutter edits were
made for this phase. Retained repair sources receive exact provenance checks in
independent verification, without becoming newly generated sources or accepted
state.

The integrator ran **184 combined tests** across compilation, handoff, publication,
previews, companion selection, runtime and validation policies; an additional
**85-test** graph/dependency/dynamic-journey suite passed. Independent planner
review passed four new focused regressions and a related 38-test run. The
integrator reran all nine planner/finalizer tests successfully. Scoped Ruff and
diff checks passed. These use inert native responses and mocked model calls,
not actual combined native image execution, browser qualification or paid models.

Historical compiled inputs and overlapping source ownership for a later native
repair within the same objective remain under implementation. Current consumers
fail closed on stale grants. Full combined native qualification, multi-target
selection and the remaining rewrite audit gates are still open.

## Same-objective repair provenance — 3 November 2026

Later native repairs can now read verified historical compiled artifacts while
current validation remains strict. Immutable grants bind the original authoring
plan, all producers, exact source-successor chains and final compile targets.
A successor must consume the exact predecessor source ID/hash through a declared
same-lane dependency. Conflicting writers, changed provenance and partial target
ownership cannot silently replace an earlier target. New compilation uses final
leaf sources, preserves old grants/results, and produces only new candidate outputs.

The planner receives explicit historical applicability metadata. Automatic native
dependency staging includes source files; exports remain read context unless
explicitly requested as inputs. Echoed old outputs cannot restore stale references.
The integrated repair fixture exercises graph input preparation, native gateway
publication, a new grant, deterministic compilation, all-target handoff and exact
independent-validation source closure. Its native responses are inert fixtures.

Independent review found and reproduced a retained-source checker identity gap:
a prior generator could be omitted from the candidate's producer exclusion set.
The validator now includes every retained source's generator, even if its source
ID is omitted from the explicit selection. Regression tests reject that generator
as independent evidence and prevent acceptance without changing the baseline.

The first full run passed 1,433 tests with ten opt-in skips and one failure caused
by a missing checkpointer argument in the new test fixture. After correction,
the final full backend suite passed **1,454 tests with ten skips**. Full Ruff and
diff checks passed; the existing dependency deprecation warning remains. Separate
independent reviews passed 139 provenance/integration checks and 57 planner/input
checks. No paid provider call, real native build, physical operation or UI change
was performed in this phase.

Distinct same-lane build entrypoints, derived-export compilation contracts, real
composed native release qualification and broader rewrite gates remain open.
The next planned phase is canonical interface prerequisite approval within the
same LangGraph objective, preserving budget and tool identities through explicit
revision-linked continuation rather than restarting the objective.

## Canonical interface prerequisites in the same objective — 3 November 2026

Normal native objectives now detect missing planned PCB/firmware interfaces before
native input preparation. Separate LangGraph proposal and wait nodes preserve
model receipts across interruptions. The existing chat message approves the exact
visible proposal or supplies bounded refinement; generic objective answers cannot
bypass the proposal-bound service. No new UI control, endpoint or DTO was added.

Approval writes canonical interfaces and immutable approval history, advances only
the authorized engineering revision, and resumes the same objective/thread. The
original actor, deadline, active-work allowance, model budget and native/tool pins
remain unchanged. Both the approver and original execution actor must still be
project writers. Exact source/image metadata and verified bytes bind the reviewed
context through proposal, reply and resume. This first phase permits completed
read-only research but requires a quiescent boundary before native/output work,
allows one revision amendment, and does not re-stamp earlier generated artifacts.
Mixed-revision continuation after native work remains a separate open gate.

New validation subjects and release manifests include the exact canonical
interface/approval snapshot. Same-revision authority drift and snapshot removal
are rejected; legacy subjects retain their historical format. This records
configuration authority and does not turn interface approval into engineering
acceptance or device evidence.

Actual API/worker/ModelGateway fixtures exercise firmware planning, text or image
proposal, explicit approval, native protocol publication and final planning in one
run with the original budget/pins. Native bytes and provider responses are inert
fixtures. A reused engine regression exposed an old bound planner scope after
amendment; a per-call resolver now binds the current scope while retaining the
original provider policy. Both text/image tests fail without that resolver and
pass with it. Recovery tests also cover process loss after the scope-update
checkpoint and reject changed interfaces before downstream dispatch.

Independent review found a two-account lock-order inversion for opposite
approvers across projects. The service now locks Project first and authorizes
both account identities in a deterministic order. A real disposable PostgreSQL
test confirmed concurrent opposite approvals, receipt replay and retained original
execution identities. Its temporary schema was removed; no production database,
provider inference, generated host execution or physical operation was used.

The initial full backend checkpoint passed 1,517 tests with ten opt-in skips.
After the final context/lock hardening, the integrator passed 105 focused
prerequisite/runtime/validation checks; independent groups passed 154 service/API/
interface checks and 235 validation/checker/release checks. The PostgreSQL
concurrency regression passed separately. Final combined verification follows
below; counts are scoped checkpoints, not universal installation qualification.

Final combined prerequisite-phase verification: **1,561 backend tests passed,
11 opt-in tests skipped**; the new PostgreSQL concurrency test is one of those
opt-ins and passed in its separate disposable-database run. Full Ruff and diff
checks passed. The existing `trustcall` LangGraph deprecation warning remains.
No live model or native-image qualification was performed in this phase.
