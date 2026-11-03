# Remaining backend work — current audit, 3 November 2026

This top section is the current source/evidence audit after independent firmware
and application export-checker review. Everything below **Historical snapshots**
is retained investigation history, not the current missing-feature list. In
particular, earlier claims that non-CAD validation, companion hosting, imports,
connectors, interface planning or workbench commands are absent are superseded.
Active parallel work can change these findings. This snapshot includes the reviewed
local MCP image reader and deterministic composition consumer integration. A new
native image containing the scratch-accounting fix remains unqualified.

The governing objective is the user's later **fresh backend rewrite using
LangGraph and LangMem**, preserving the existing Flutter workbench and supporting
focused existing-project work as well as coordinated hardware products. The older
attached DBOS refactor objective and its migration package map are historical;
they do not override the subsequently approved rewrite. This audit retains the
whole requested scope, including CAD, PCB/routing, firmware, companion apps,
sourcing, human work, printing connections, plugins and shared project knowledge.

## Current priority gaps

| Priority | Remaining behavior | Concrete source/evidence and next gate |
|---|---|---|
| P0 | **Real composed multi-lane releases and subsequent repairs.** Deterministic final compilation and canonical consumers now exist, but combined native execution and complete release journeys remain unqualified. | `orchestration/composition.py`, `execution/composition.py` and `projects/composition_results.py` freeze exact source IDs, execute pinned fixed actions and atomically hand off all-target results. Planning, validation and previews select verified compiled outputs; stale explicit output IDs are rejected. The 184-test integration checkpoint uses inert native responses. Later same-run repairs now use verified historical read context and exact source-successor chains; the full backend checkpoint passed 1,454 tests with ten opt-in skips. Run actual combined native generation, independent checking and acceptance; qualify multiple same-lane targets and derived-output inputs without weakening closure checks. |
| P0 | **Full unchanged-workbench cutover and real composed journeys.** Route/command coverage and fixture suites do not establish the user's complete workflow. | Run the existing Flutter app against the fresh configured backend and workers for capture with images/part redo, approve interfaces, generate, preview, verify and accept; also imported missing-part/routing work, focused repair, source-only native rebuild, external waits, reconnect/restart, cancellation and deletion. Confirm unchanged widget/golden/provider/transport behavior and projections. A real companion sample rendered and incremented in IAB, but this is not the full workbench journey. Composed imported/missing-part tests are active work, not completed evidence in this snapshot. |
| P1 | **Prerequisite continuation after native work and broader mixed-revision objectives.** Initial missing PCB/firmware interfaces now have a same-run host-owned proposal/approval continuation. | `projects/prerequisites.py` and `orchestration/prerequisites.py` retain the original scope, budget and pins through explicit canonical revision amendment. Existing chat approval resumes the same LangGraph thread; text/vision API-worker fixtures pass. The first phase requires no native jobs/outputs or unresolved external work and one amendment; later interface changes after generation need explicit revision/source carry-forward handling. This remains an open scope item rather than restarting objectives or re-stamping artifacts. |
| P1 | **Broader backend MCP/MM-plugin capabilities.** The local MM-core PNG/JPEG reader is reviewed; CAD/Blender and broader plugin installation remain incomplete. | `capabilities/mcp_bridge/qualification/REVIEW.md` binds actual PNG/JPEG fixtures, watchdog/cleanup evidence and independent review to an exact image. `infrastructure/mcp.py` explicitly registers reviewed installations; none is enabled by default. This proves bounded decoding/normalization, not image understanding. FreeCAD/Blender generation, desktop-loopback adapters, candidate publication and broad plugin discovery/install lifecycle remain separate work. |
| P1 | **Installed, qualified live read-only research and service coverage.** Provider protocol fixtures do not establish live accounts, inventory or fees. | `infrastructure/external.py` enables exact source-bound reviewed DigiKey/RentAHuman read-only adapters with explicitly false `liveAccessVerified`; accounts/secrets are operator-scoped. SearXNG evidence is test-only, with no default real search installation. Qualify and configure actual permitted read-only services, then exercise sourced findings, pricing, owned human-job observations and long waits through the composed agent flow. Local human previews are not remote fee/dry-run responses. No paid/live call was made by this audit. |
| P1 | **Current native images and broader engineering scope.** Qualified fixtures are intentionally narrow, and the newest native scratch-scan fix is source-only. | Existing pinned CAD, two-layer PCB/routing, NRF52840 and Flutter web images have scoped evidence. `execution/native_runner.py` now tolerates disappearing temporary files during accounting, but historical images do not contain that fix. Build/review/qualify new images before claiming it there. Other boards, Android/iOS delivery, arbitrary imported PCB layouts, zones/vias/differential pairs/controlled impedance, arbitrary CAD fits/loads, circuit simulation and functional device behavior remain unsupported/unqualified. |
| P1 | **Fresh deployment and operating integration.** Local control packaging does not supply hosted native execution or cloud adapters. | `backend/Dockerfile`, `compose.yaml` and `docs/local-runtime-qualification.json` provide scoped API/setup/worker/preview packaging smoke evidence. Default compose has no native Docker socket, tool/checker/slicer installation or commercial provider. `.github` cloud deployment and Terraform still require replacement-runtime wiring, ECS/native dispatch, object-store/shared artifact adapters, secrets and operational recovery/retention qualification. No cloud deployment has been performed. |
| P2 | **Semantic shared memory and complete imported-project experience.** Advisory LangMem persistence exists; universal semantic/document retrieval and repository intake are not complete. | `memory/README.md` and Runtime use LangGraph PostgreSQL storage with recent-project-record retrieval unless an explicit embedding index is configured. Qualify an approved embedding/index configuration and relevant retrieval/citation/staleness scenarios. Public GitHub full-commit imports and bounded ZIP commands exist; private repositories, LFS/submodules and arbitrary hidden/private metadata are excluded. Existing UI upload/import behavior still needs composed verification without new UI controls. |
| P2 | **Existing client selection/flow constraints and truthful simulation parity.** Saving a backend policy is not proof every unchanged client path can use it. | `engineering_tools.dart` requires usable CAD and PCB selections before Save, so CAD-only deployments cannot save there despite API support. Print preference and assembly navigation now persist, but do not advance a simulated shipping/activation journey. Verify existing simulation remains separate and intact; list unavoidable UI gaps instead of silently changing widgets or inventing success. |

## Implemented boundaries, with their actual limits

The composition grant freezes immutable source identities and original execution
contracts. Deterministic final compilation publishes outputs only, followed by an
atomic all-target handoff. Current planning, independent verification and preview
consumers verify that handoff without rewriting authoring history. The reviewed
integration uses inert native responses and does not qualify a composed native
release. Same-run source-successor handling has focused and independently reviewed fixture
evidence, including historical read isolation and exact final source selection. The fixed CAD runner executes `model.py`: separate task labels or edit
paths do not establish distinct build targets. Multiple same-lane entrypoints and
derived-output inputs still need qualified contracts. Validation continues to
require exact complete source closures.

| Area | Current implementation/evidence | What that does not establish |
|---|---|---|
| State, orchestration and recovery | PostgreSQL ownership/revisions/receipts/outbox; dynamic LangGraph DAG, dependency inputs, bounded model/tool attempts, cancellation, persisted interrupts and seven-day objective lifetime with cumulative active-work accounting. Existing external jobs/print preparations can wait and resume without model calls on unchanged polls. Shared durable native/slicer/checker capacity and exact cleanup proofs exist. | A completed all-domain user journey, arbitrary deployment scale or automatic prerequisite planning. Cleanup after an uncertain dispatch remains conservative rather than guessing termination. |
| Capture and project edits | Reference images/vision model routing, candidate design parts, selected-part replacement, explicit approval, feature/battery edits that recapture and preserve history, exact circuit peripheral mapping, imported PCB identity approval and explicit firmware target. | Reliable design of every complex device, inferred photo dimensions, automatic compatibility or model-created approval. |
| Engineering and repair | Fixed isolated native actions, actual per-part CAD STEP/STL, supported KiCad/Freerouting exports, NRF ELF/BIN, Flutter web ZIP, exact-source bounded native rebuild, ancestry-checked dependency source handoffs, pinned tools/models and explicit Modal 429 fallback. | Other boards/mobile builds, physical function or a universal build/repair loop; composed source-closure gaps above remain real. |
| Independent releases | Reviewed CAD checker; reviewed PCB identity/ERC/DRC checker with 15 actual cases; new reviewed firmware checker with 10 actual cases and application checker with 16. Fifteen nonempty lane policy combinations register only with qualified constituent installations; ELF and BIN are required together. Prepare/accept prevent omitted lanes and mismatched source closures. | Generator self-acceptance, all-format engineering validation, Gerber/drill manufacturing correspondence, source-build reproduction, firmware/application behavior, security or physical acceptance. Reviewed reports are not default Runtime installation. |
| Previews | Bound/hash/current-revision CAD meshes and display-only exploded vectors, supported actual routed-board geometry, separate-origin ticketed companion static hosting, and real fixed-font Flutter sample browser evidence. | Unsupported simulation, arbitrary browser/app security, BLE/device integration, or full Flutter workbench cutover. |
| Workbench API | Existing capture/review/confirm/interfaces/generate/repair/retry/native-retry/verify/release paths; model/tool policies, research, connectors, training-consent preference, feature/battery edits, print selection, assembly navigation and project deletion have service composition. Human local preview and Bambu test-preparation/download routes are wired. | That every route always works without required configured installations, that schema presence proves parity, or that consent starts training. Generic unavailable fallbacks remain truthful when a module/tool is absent. |
| Existing-project intake | Atomic bounded ZIP import and public GitHub full-commit download receipts/cancellation; preserved source/archive hashes. Allowed `.github/**` and root ignore/editor metadata have a separate immutable advisory closure and research reader, excluded from native source inputs. | Arbitrary repository support, private credentials, LFS/submodule fetch, repository instructions gaining authority, or a new frontend repository picker. |
| Sourcing and human work | Account-scoped discovery/nonbinding quote/local draft/owned observation tools; durable external receipts, waits and evidence review; local provider preview disclosure. | Real purchases, hiring, payment, remote fee previews or automatic physical acceptance. |
| Bambu and print preparation | Reused read-only connection semantics, explicit model/firmware pairing information, scoped printer observations, bounded offline repair/slicing and test preparation using reviewed profiles. | Upload/start, hardware cancellation, automatic printer discovery success on the user's LAN, all models/firmware or physical acceptance. |
| Packaging and access | Fresh migrations, current account/organization authorization, local persistent control container and separate preview process; CI lockfile/schema commands corrected. | GitHub-hosted CI execution, production deployment, hosted native adapters or automatic migration of the preserved legacy database. |

## Explicit policy exclusions, not missing permission checks to remove

Physical fabrication, ordering, paid human hiring, printer upload/start, shipping,
activation and physical acceptance remain simulated or blocked by server policy.
`ExternalFacade.BLOCKED_COMMANDS` and fabrication dispatch enforce this. Digital
kit preparation, print preference and synthetic assembly progress never make
these actions happen. Any future live version requires its own authorized product
scope, qualified integration and evidence; this rewrite must not quietly bypass
that boundary to make the workbench appear complete.

The original request to use Bambu is retained in scope through discovery/pairing,
read-only observation and offline preparation. It is not reported as fulfilled
live printing. Similarly, human-job lookup/drafts are not reported as having hired
someone. Server `PASS`, `FAIL`, `UNKNOWN`, `ERROR` and `NOT_APPLICABLE` retain distinct
meanings, and retrieval/preview success is not engineering acceptance.

## Evidence and verification checkpoints

The frozen frontend inventory records 97 paths/108 operations/96 schemas, including
59 directly referenced HTTP operations and 32 literal project commands. Those are
baseline obligations, not a present pass count. Actual `ApplicationServices`
delegates the formerly missing command families; full behavior must be verified
with configured service composition. A fallback 501 in that file alone does not
mean its intercepted Runtime route is missing. Simulation/developer legacy
commands must be assessed against their actual mock transport separately.

Relevant current evidence:

- [Rewrite progress](rewrite-progress.md) records dated combined test snapshots.
  The latest recorded full-suite checkpoint is 1,561 passed with eleven opt-in skips
  after canonical prerequisite continuation; the new PostgreSQL concurrency opt-in
  passed separately against a disposable database. This checkpoint is not a universal
  qualification. Earlier counts below are historical.
- [Independent export checking](independent-export-checkers.md) and
  [actual reviewed qualification](../src/kleos/validation/qualification/EXPORTS.md)
  document 153 focused tests,10 firmware/16 application actual cases, preserved
  failures and separate review. Existing [PCB review](../src/kleos/validation/qualification/PCB.md)
  and [native fixture scopes](../src/kleos/execution/README.md) remain narrow.
- [Companion evidence](companion-native-font-browser.json) records exact native
  image/output hashes and the observed sample interaction; [local packaging](local-runtime.md)
  records its separate scope.
- Metadata/import review in this audit independently ran 101 tests across repository
  metadata/context/runtime/import and archive suites, with scoped Ruff passing.
  It inspected account/member publication-lock tests but did not rerun PostgreSQL,
  download a live repository, execute a provider, build an image or change code.

Next completion checks should join the existing focused fixtures into the real
journeys listed above, then run the repository's current backend/mock suites,
transport generation check, unchanged Flutter analysis/tests, migrations and
explicit disposable PostgreSQL recovery checks. Native qualification is opt-in,
serialized through shared capacity and exact images; paid inference or physical
work is not a routine test step. This document-only refresh used source reads,
read-only size measurements and link/diff checks, not another native run.

## Code-size target: current measurement, not the earlier snapshot

Read directly from the preserved archive and current Python tree on 3 November 2026,
counting physical lines including blanks/comments, with tests shown separately:

| Comparable scope | Files | Bytes | Physical lines |
|---|---:|---:|---:|
| Archived `backend/kleos/**/*.py` |149|1,666,304|29,557|
| Current runtime excluding `qualification/` |142|1,299,144|22,790|
| Current runtime including qualification helpers |154|1,390,435|24,367|
| Current tests, separate from runtime |127|1,195,658|22,765|

Current runtime line reduction is about 22.89% excluding qualification helpers and
17.56% including them. The user's **more-than-60% reduction target is not met** by
this comparable line metric. The older 55–59% figures below must not be quoted as
current. Active parallel implementation can change these counts. Do not delete
required authority, isolation, recovery or validation behavior, or compress
formatting, to claim a percentage. Finish the real scope, then assess justified
consolidation with preserved semantics and tests. Existing user edits remain
unattributed and must be preserved.

## Historical snapshots — retained, not current status

The sections below were written during earlier phases on 2–3 November 2026. Their
missing-service lists, test totals, code-size counts and qualification statements
are historical evidence only. The current top section supersedes conflicting
status claims; newer exact reports retain their own limited scopes.

## Latest verified increment — 3 November 2026

Explicit bounded ZIP import, feature/battery requirement edits and the four Bambu
test-print preparation/download routes now supersede their missing-service entries
below. ZIP import is an explicit command, not a new UI control or network repository
checkout. Requirement changes retain historical files and approvals, invalidate
current authority and request recapture; compatibility remains UNKNOWN. Bambu
test preparation uses the existing slicer queue and never sends to a printer.
The combined checkpoint passed 878 backend tests with seven opt-in skips, 17
separate PostgreSQL checks and the unchanged generated transport check. See
[rewrite progress](rewrite-progress.md) for the verification scope. Companion web
hosting is under active implementation, not included in this completion evidence.

## Subsequent workbench integration — 3 November 2026

The following implementation supersedes only the corresponding gaps below:

- `GET /connectors` now projects installed tools and the caller's configured
  external accounts. Cached Bambu display status requires the existing local
  administrator boundary. Listing starts no discovery/provider request and
  exposes no credentials, endpoint URLs or printer addresses. Read-only adapters
  are disclosed as such; configured does not mean live access has been qualified.
- `project.setTrainingConsent` records and revokes an explicit project preference,
  with command replay and visible revision checks. It preserves engineering
  context and starts no training, export or other objective. The existing
  workspace field now reflects the stored preference.
- Human-task previews are connected as **local** saved-draft reviews. They survive
  reload, retain source/context/account bindings and disclose that the provider
  was not contacted. No provider fee quote, funding total, job posting, hiring or
  payment is invented. Remote provider dry-run/fee preview is not implemented.

Tool selection now records exact installation bindings for new objectives.
Queued work retains its selected versions even after a project changes its
selection; revoked or changed bindings fail closed. Focused runtime tests execute
distinct registered fixture handlers, not only catalog filtering. The built-in
native image configuration still permits one image per capability; alternatives
require complete operator-installed bindings to distinct actual implementations.
No UI changes or arbitrary plugin-installation claim follows from this feature.

The tool-policy API normalizes a displayed lane with both `id` and `version`
explicitly null as no override, while rejecting partial selections and unknown
lanes. One unchanged frontend limitation remains: `engineering_tools.dart`
requires both displayed CAD and PCB lanes to have usable selections before its
Save button is enabled. A backend with only CAD installed therefore cannot save
from that screen, even though the API accepts a CAD-only policy. This is a client
validation limit, not a reason to advertise an uninstalled PCB tool.

Research now requires actual retrieval receipts, preserves engineering context,
and publishes candidate summaries and citations in the existing conversation.
The independent review passed 48 focused checks, including composed runtime flows
with mocked search/model providers. The source-bound SearXNG report is test-only
and rejected for development/production installation. No live search instance is
qualified or installed by default. The Meta built-in
web-search protocol does not document a hard query-count bound per tool call, so
it is not enabled under a fabricated cost ceiling. The combined backend checkpoint
passed 796 tests with seven explicit opt-in skips; Ruff passed. This checkpoint
predates the subsequent archive/editing/test-print work and does not establish
full frontend cutover, live search, or engineering feasibility.

## 3 November 2026 — preview integration and remaining workbench parity

This dated source audit supersedes the earlier missing engineering-preview claim.
The production `Runtime` now constructs `EngineeringPreviews` and injects it into
`ApplicationServices`. Existing engineering-preview and circuit-simulation routes
are connected without Flutter or OpenAPI changes. Current, authorized published
CAD files produce per-part meshes in manufacturing coordinates; supported routed
PCB files produce actual board/pad/straight-track geometry. Unsupported circuit
simulation stays `UNKNOWN`, and companion application hosting remains unavailable.
See `src/kleos/execution/PREVIEWS.md` for exact representation and parser bounds.

Verification: 32 preview/runtime-focused tests passed; 18 existing runtime and
application-service tests plus the new runtime preview test passed. These checks
used fixture artifacts, migrated test storage and mocked graph persistence; no
model, provider, Docker build or physical action ran. The separate preview data
record parses the preserved exact-image CAD fixture exports. This is not a full
workbench cutover or arbitrary-device qualification.

Prioritized concrete remaining gaps, preserving the requested full scope:

1. **Independent release coverage beyond CAD.** `api/engineering.py` still selects
   only `CAD_POLICY` for `project.verify`. Generator PCB ERC/DRC and firmware/app
   compilation cannot substitute for separate qualified release policies. A full
   device release remains incomplete even when all generator files exist.
2. **Existing editing and workflow controls.** `project.research`,
   `project.toggleFeature`, `project.swapBattery`, `project.selectTool`,
   `project.setToolPolicy`, `project.selectPrint`, `project.assemblyStep` and
   `project.setTrainingConsent` lack service handlers. Deletion also remains
   unimplemented at `DELETE /projects/{project_id}`. These are real current Flutter
   actions, not merely old OpenAPI paths. Workspace `trainingOptedIn` is still
   hardcoded false; recording consent is not permission to begin training.
3. **Eight directly referenced HTTP operations still return unavailable.** These
   are `GET /connectors`, project deletion, `POST companion-preview`, the four
   Bambu test-print create/status/source/plate operations, and the human-draft
   provider preview operation. Connector loading occurs during ordinary workbench
   refresh and exposes the missing catalog as a connection error.
4. **Preview and import completeness.** The application ZIP needs a separate-origin
   ticket/hosting path before the existing iframe can run it. The unchanged PCB
   viewer/parser contract supports rectangular outlines and unrotated pad extents;
   unsupported rotations/outlines remain unavailable and vias/arcs/zones are
   explicitly not drawn. Circuit/device simulation remains unqualified. Project
   context import is individual files, not a safe archive/repository importer.
5. **Operational capability breadth and end-to-end journeys.** A pinned registry
   is not an installed MCP/MM-plugin bridge or broad research/catalog capability.
   Full mixed-discipline, imported-project, procurement and human-verification
   journeys still require actual service-boundary verification. External-event
   continuation and active-work budget changes are being reviewed separately;
   this snapshot does not declare them complete.

Purchasing, physical acceptance, shipping, activation and direct printer send
remain server-policy blocked. The existing frontend labels several subsequent
steps as simulated; matching that progression safely is still a product/adapter
gap, not authorization to permit live physical effects. These findings do not
reduce the user's requested scope or imply that route presence closes a feature.

Evidence: `api/{services,engineering,previews,external,bambu}.py`,
`infrastructure/runtime.py`, `projects/views.py`,
`tests/{test_preview_runtime,test_previews}.py`,
`tests/contracts/frontend-obligations.json`, and the unchanged Flutter
`production_stages.dart`, `stages.dart`, `production_app.dart`,
`production_legacy_api_service.dart` transport calls.

> The inventory below records the 2 November snapshot. The 3 November interface,
> verification and capacity phase supersedes the specific gaps listed here for
> `project.solveInterfaces`, `project.verify`, shared compute admission, model
> spending projections and the private invocation-record endpoint. See
> [the progress record](rewrite-progress.md) for combined verification evidence.
> The old operation totals below are historical classifications, not current
> completion counts.

Current remaining priorities after that phase:

- Imported PCB projects need an explicit user-approved way to declare circuit
  identity without first generating a visual concept. Existing approved peripheral
  concepts now have a reviewed, injective circuit-reference binding path.
- Long-lived external events/timers still need to resume waiting LangGraph work.
- Engineering/circuit/companion previews, archive import, broader independent
  validation and several workbench commands remain incomplete.
- Backend MCP/MM-plugin deployment, broader qualified tools, packaging and actual
  full-workbench cutover remain unverified. CAD post-run verification does not
  certify PCB, firmware, applications or physical behavior.
- Uncertain container creation/removal deliberately retains compute reservations;
  operator reconciliation for unrecoverable missing acknowledgements remains a
  usability limitation, rather than permission to repeat work or free capacity.

Read-only implementation audit, except this new document. The target is the user's
fresh modular LangGraph/LangMem backend with flexible hardware tasks and the
existing Flutter workbench. The earlier DBOS attachment is historical context;
the later explicit complete-rewrite instruction defines this implementation.
No provider, printer, native build, paid call or cloud operation ran for this audit.
This is a source/contract inventory, not an end-to-end qualification report.

## Conclusion and next work

The replacement has real authority, persistence, model/tool accounting, dynamic
tasks, imported source storage and isolated native execution. It is **not ready
for frontend cutover or a complete device workflow**. A route, tool registration,
installed SDK or passing narrow fixture is not sufficient evidence of that feature.

Prioritize these phases, preserving current edits and frontend behavior:

1. **Connect authoritative interface editing and post-run validation.**
   `project.solveInterfaces` has no service handler. Design confirmation records
   approved geometry/requirements but never `interfaces.circuit` or
   `interfaces.firmware.board`; uploads only modify the source manifest. The native
   gateway correctly requires these authoritative values. Thus PCB and firmware
   qualification fixtures seed authority that a normal workbench user currently
   cannot establish. Implement a reviewed, revision-fenced interface proposal and
   explicit acceptance path, including approved peripheral-ID coverage, circuit
   reference identity, board selection and engineering constraints. Do not let
   generated files self-approve interface values. Separately connect
   `project.verify` to exact current candidates without invalidating their revision
   or granting a generator acceptance. Current verification is only available as
   a validation task within an active candidate objective.
2. **Restore workbench read/write functionality.** Engineering preview, circuit
   simulation, companion preview, invocation transcript, project deletion,
   connector catalog, tool-policy updates and several project commands remain
   unavailable. Keep unsupported physics explicitly UNKNOWN; rendering a scene
   is not a circuit or device check. Restore schema/behavior parity through the
   existing transport and widgets, then verify the unchanged frontend with the
   actual replacement service. Do not replace unavailable behavior with invented
   successes or empty stand-ins.
3. **Add shared native capacity admission and long waits.** Individual container
   limits do not bound aggregate demand. ObjectiveEngine permits four concurrent
   tasks and the slicer runs separately; two 4 GiB CAD jobs or one 6 GiB app job
   plus another lane exceed the approximately 7.65 GiB local Docker VM. No shared
   durable CPU/memory/scratch reservation or admission queue was found across
   native jobs and slicing. Add shared bounded admission before claiming full-load
   multispecialist execution. Native maintenance was missing at the initial audit
   but is now wired and hardened, as recorded below. External jobs persist `nextPollAt` and events, but no process connects provider
   events/timers to waiting LangGraph objectives. User-answer interruptions are
   implemented; days-long sourcing/human/printing waits are not. Keep LangGraph
   responsible for workflow state instead of adding a parallel graph engine.
4. **Finish independent checks and full task scenarios.** Only CAD export-integrity
   has a separately isolated concrete checker and release policy. PCB ERC/DRC run
   in the generator; firmware/app compilation is not independent device evidence.
   Add qualified checks for the supported claims, not blanket PASS assertions.
   Verify bracket-only, imported PCB routing, component replacement, scoped part
   redo, human-verification waiting, source-preserving repair and full-device
   coordination through real service boundaries using deterministic providers.
5. **Finish qualified capabilities, then packaging/cutover.** There is no installed
   general research tool or backend MCP/MM-plugin worker bridge, no project archive
   importer, no qualified Android delivery, and no companion preview server. External
   account installation requires reviewed exact-source evidence. The corrected
   adapters now have independently reviewed mocked-HTTP qualification, with no
   live account access verified. Do not enable accounts from synthetic test records. Replace stale DBOS/removed-path deployment
   assets and docs, add secure fresh runtime packaging, complete contract transport
   verification, and then start the intended local services with explicit settings.

Physical purchase, hiring/payment, shipping, printer upload/start and physical
acceptance remain **deliberately policy-blocked**, not implementation promises.
The task's requested printing/hiring experience is therefore not currently delivered;
any future policy change is separate from making the digital preparation reliable.

## What works, and what its evidence does not establish

| Area | Implemented boundary/evidence | Remaining functional limit |
|---|---|---|
| Project authority | PostgreSQL application state, scoped memberships, revisions, command receipts/events, immutable artifacts; fresh migrations and opt-in PostgreSQL/process tests | No general requirement/interface editor or whole-project archive import; local BlobStore rather than a deployed object-storage adapter |
| Dynamic orchestration | LangGraph dependency DAG, bounded parallel task execution, replan/supersession, exact input snapshots, cancellation, user-question interrupts, persisted worker locks/fences | Bounded objectives, not a standing project agent; no provider-event/timer resume bridge; no proven complete-device run |
| Model harness | Explicit Meta/Modal installations, image-capability checks, lane policy pins, usage reservation/settlement and configured Modal rate-limit fallback | No paid/live provider qualification in this rewrite evidence; workbench transcript is unavailable; UI budget projection is not populated from the ledger |
| Capture and geometry | Typed concept scene, image input, explicit vision routing, questions, isolated selected-part candidate replacement, explicit user confirmation; shared geometry fixture tests | Concept units are relative illustration; prompts/fixtures do not establish arbitrary complex engineering competence or dimensionally valid CAD |
| Shared memory | Real LangMem extraction with evidence references; LangGraph PostgreSQL store; tenant/project authorization and advisory authority | Runtime constructs store without an embedding index and Knowledge with semantic search off: retrieval is recent project records, not semantic/vector retrieval; no document ingestion/indexing pipeline |
| CAD | Exact local build123d image, parts STEP/STL/SVG exports, immutable candidate closure; real native-only rebuild through production admission/graph/Docker | Fixed source contract and local Docker Desktop qualification; no universal manufacturability, load/fit/thermal/device validation; no UI engineering-preview adapter |
| PCB/routing | Fresh KiCad/Freerouting flow, exact accepted reference/pin/net mapping, actual ERC/DRC and routed Gerber/drill fixture | Requires manually seeded accepted circuit authority today; narrow two-layer/local-library contract, not arbitrary boards/components; checks are not an independent release policy |
| Firmware | Real ELF/BIN compilation for explicit qualified `nrf52840dk/nrf52840` | No reachable board/interface approval UI path; no firmware execution, emulator, flashing, BLE or peripheral verification; other boards unqualified |
| Companion application | Fresh offline Flutter web fixture, fixed build/finish, private SDK/cache, hard 4 GiB tmpfs / 6 GiB RAM bound | Web ZIP only; no preview origin/server, browser/device test, BLE claim, APK or iOS delivery |
| Source import/repair/rebuild | Per-file authenticated hash-checked upload; canonical input manifest; scoped repairs pin exact prior candidates; native-only rebuild preserves sources/image/resources and prior history | No ZIP/repository import UX or context manifest reader; candidate input files alone cannot become accepted circuit/board interfaces; uncertain actions still block until proven reconciled |
| Independent validation/release | Separate CAD checker image reads STEP/STL, excludes generator Python; exact subject/policy pins; independent acceptance command and private release ZIP | CAD export integrity and explicit numeric constraints only; no PCB/fw/app release policies, no post-run verify command, no manufacturing/physical acceptance |
| Procurement | Scoped persistent BOM jobs and read-only exact-SKU DigiKey adapter with mocked HTTP evidence | Not installed by default; reviewed source-bound report/account setup required; no general part discovery/substitution, inventory reservation, order or payment |
| Human verification | Scoped work orders/local drafts, owned external-job link/sync, evidence/review records, read-only RentAHuman adapter | Provider posting preview unavailable, no hire/message/payment; no automatic wait/resume; HTTP evidence submissions are explicitly synthetic and cannot satisfy physical acceptance |
| Bambu/Orca | Local discovery/certificate/pair/status boundary; encrypted credentials; separate digital slicer worker; narrow A1 mini/A1/P1S PLA/0.4 mm STL/OBJ qualification | No real-printer qualification here; local test-print endpoints missing; unsupported formats/repair paths not qualified; upload/start blocked |
| Plugins | Versioned immutable capability registry, typed args, authorization/effect checks, skill instructions and pinned installations | Registry infrastructure is not an operational MCP/MM-plugin deployment bridge or plugin marketplace; no user-supplied executable authority |

Source evidence: `src/kleos/infrastructure/runtime.py`, `api/services.py`,
`api/engineering.py`, `capabilities/design/service.py`, `projects/engineering.py`,
`orchestration/{engine,worker,planner}.py`, `memory/service.py`,
`orchestration/persistence.py`, `execution/{gateway,native_runner,pcb_runner}.py`,
`infrastructure/validation.py`, `capabilities/external_tools.py`,
`infrastructure/external.py`, and the owning qualification directories.

Relevant real local evidence already produced before this audit:

- `src/kleos/execution/qualification/{cad,pcb,firmware,web}-exact-image.json`
- `src/kleos/execution/qualification/rebuild-attempt-1.json`
- `src/kleos/validation/qualification/cad-check-exact-image.json`
- `src/kleos/capabilities/fabrication/qualification/slicer-qualified-2026-11-02.json`

These reports bind particular images, fixtures and local policies. They do not
qualify arbitrary source programs, cloud execution, live external accounts or
physical behavior. Preserve failed attempts alongside final reports.

## Frontend HTTP obligation inventory

The frozen inventory is `tests/contracts/frontend-obligations.json`: 97 historical
paths, 108 operations, 96 schemas, with **59 operations actually referenced by the
frontend**. The table below covers those 59, not all historical routes. Paths omit
`/api/v1`. “Service” means real behavior exists when its stated configuration and
authority prerequisites are satisfied; it does not mean a live integration or
full DTO/UX parity has been qualified. “Partial” names incomplete transport behavior;
“Blocked” is explicit server policy; “501” is an unimplemented service path.

The existing route-coverage test only establishes that these method/path pairs are
registered. Most endpoints return untyped dicts; the frozen OpenAPI is deliberately
not generated from this runtime. Selected shape tests do not prove all response
schemas, events, paging, optional fields and existing UI transitions.

| Method | Path | State | Implementation / limit |
|---|---|---|---|
| POST | `/projects/{project_id}/reference-images` | Service | `artifacts/service.py` — Private normalized image storage/content; configured vision model needed for interpretation. |
| GET | `/projects/{project_id}/reference-images/{image_id}/content` | Service | `artifacts/service.py` — Private normalized image storage/content; configured vision model needed for interpretation. |
| GET | `/printers/bambu/local` | Service | `api/bambu.py` — Local administrator transport + explicit enablement/key; discovery/pair/status only, not printing. |
| DELETE | `/printers/bambu/local` | Service | `api/bambu.py` — Local administrator transport + explicit enablement/key; discovery/pair/status only, not printing. |
| POST | `/printers/bambu/local/discover` | Service | `api/bambu.py` — Local administrator transport + explicit enablement/key; discovery/pair/status only, not printing. |
| POST | `/printers/bambu/local/certificate` | Service | `api/bambu.py` — Local administrator transport + explicit enablement/key; discovery/pair/status only, not printing. |
| POST | `/printers/bambu/local/connect` | Service | `api/bambu.py` — Local administrator transport + explicit enablement/key; discovery/pair/status only, not printing. |
| POST | `/printers/bambu/local/refresh` | Service | `api/bambu.py` — Local administrator transport + explicit enablement/key; discovery/pair/status only, not printing. |
| POST | `/printers/bambu/local/test-prints` | 501 | `api/bambu.py; api/services.py` — Test-print preparation/status/source/plate has no implemented handler. |
| GET | `/printers/bambu/local/test-prints/{ident}` | 501 | `api/bambu.py; api/services.py` — Test-print preparation/status/source/plate has no implemented handler. |
| GET | `/printers/bambu/local/test-prints/{ident}/source` | 501 | `api/bambu.py; api/services.py` — Test-print preparation/status/source/plate has no implemented handler. |
| GET | `/printers/bambu/local/test-prints/{ident}/plate` | 501 | `api/bambu.py; api/services.py` — Test-print preparation/status/source/plate has no implemented handler. |
| POST | `/projects/{project_id}/print-dispatches` | Blocked | `api/fabrication.py` — Persists a blocked dispatch; authorization flag cannot enable printing. |
| GET | `/projects/{project_id}/print-dispatches` | Partial | `api/fabrication.py` — Returns durable blocked records; cancel/reconcile do not imply a hardware effect. |
| POST | `/projects/{project_id}/print-dispatches/{ident}/send` | Blocked | `api/fabrication.py` — 403 physical_policy; no upload/start. |
| POST | `/projects/{project_id}/print-dispatches/{ident}/cancel` | Partial | `api/fabrication.py` — Returns durable blocked records; cancel/reconcile do not imply a hardware effect. |
| POST | `/projects/{project_id}/print-dispatches/{ident}/reconcile` | Partial | `api/fabrication.py` — Returns durable blocked records; cancel/reconcile do not imply a hardware effect. |
| GET | `/rentahuman/status` | Service | `api/external.py` — Status is local; search/owned list need configured reviewed account adapter. No live-access evidence. |
| GET | `/rentahuman/humans` | Service | `api/external.py` — Status is local; search/owned list need configured reviewed account adapter. No live-access evidence. |
| GET | `/rentahuman/bounties` | Service | `api/external.py` — Status is local; search/owned list need configured reviewed account adapter. No live-access evidence. |
| POST | `/projects/{project_id}/human-task-drafts` | Service | `api/external.py` — Local draft/list or owned provider link/sync; no hiring/payment or automatic workflow wake-up. |
| GET | `/projects/{project_id}/human-task-drafts` | Service | `api/external.py` — Local draft/list or owned provider link/sync; no hiring/payment or automatic workflow wake-up. |
| POST | `/projects/{project_id}/human-task-drafts/{ident}/preview` | 501 | `api/external.py` — Provider posting previews unavailable; local saved draft retained. |
| POST | `/projects/{project_id}/human-task-drafts/{ident}/link` | Service | `api/external.py` — Local draft/list or owned provider link/sync; no hiring/payment or automatic workflow wake-up. |
| POST | `/projects/{project_id}/human-task-drafts/{ident}/sync` | Service | `api/external.py` — Local draft/list or owned provider link/sync; no hiring/payment or automatic workflow wake-up. |
| GET | `/printers/bambu/catalog` | Service | `api/bambu.py` — Configured profile catalog; explicitly no direct printing support. |
| POST | `/projects/{project_id}/print-preparations` | Service | `api/fabrication.py` — Persisted digital preparation/list; separate worker and qualified slicer required. |
| GET | `/projects/{project_id}/print-preparations` | Service | `api/fabrication.py` — Persisted digital preparation/list; separate worker and qualified slicer required. |
| GET | `/connectors` | 501 | `api/services.py` — No connector catalog service delegation. |
| DELETE | `/projects/{project_id}` | 501 | `api/services.py` — No project deletion service path. |
| GET | `/auth/members` | Service | `access/service.py` — Persisted session/member/invite behavior; configured bootstrap and CSRF rules apply. |
| POST | `/auth/members/{subject}/role` | Service | `access/service.py` — Persisted session/member/invite behavior; configured bootstrap and CSRF rules apply. |
| DELETE | `/auth/members/{subject}` | Service | `access/service.py` — Persisted session/member/invite behavior; configured bootstrap and CSRF rules apply. |
| POST | `/auth/members/{subject}/reset-password` | Service | `access/service.py` — Persisted session/member/invite behavior; configured bootstrap and CSRF rules apply. |
| GET | `/auth/invites` | Service | `access/service.py` — Persisted session/member/invite behavior; configured bootstrap and CSRF rules apply. |
| POST | `/auth/invites` | Service | `access/service.py` — Persisted session/member/invite behavior; configured bootstrap and CSRF rules apply. |
| DELETE | `/auth/invites/{invite_id}` | Service | `access/service.py` — Persisted session/member/invite behavior; configured bootstrap and CSRF rules apply. |
| GET | `/engineering-tools` | Service | `api/catalog.py` — Explicit installed configuration; engineering tool policy selection remains missing. |
| GET | `/model-catalog` | Service | `api/catalog.py` — Explicit installed configuration; engineering tool policy selection remains missing. |
| POST | `/commands` | Partial | `api/services.py` — Command-specific coverage below; not a generic successful command bridge. |
| GET | `/projects` | Service | `projects/views.py` — Real project/run/artifact/check projections; several legacy view fields remain minimal. |
| GET | `/projects/{project_id}/workspace` | Service | `projects/views.py` — Real project/run/artifact/check projections; several legacy view fields remain minimal. |
| GET | `/projects/{project_id}/engineering-preview` | 501 | `api/services.py` — No preview/simulation service implementation. |
| GET | `/projects/{project_id}/circuit-simulation` | 501 | `api/services.py` — No preview/simulation service implementation. |
| POST | `/projects/{project_id}/companion-preview` | 501 | `api/services.py` — No preview/simulation service implementation. |
| GET | `/projects/{project_id}/events` | Service | `api/app.py; projects/views.py` — Persisted event stream with cursor and authorization rechecks. |
| GET | `/runs/{run_id}` | Service | `projects/views.py` — Real project/run/artifact/check projections; several legacy view fields remain minimal. |
| GET | `/artifacts/{artifact_id}/download` | Service | `artifacts/service.py` — Authorized immutable artifact descriptor/content. |
| GET | `/artifacts/{artifact_id}/content` | Service | `artifacts/service.py` — Authorized immutable artifact descriptor/content. |
| GET | `/releases/{release_id}/download` | Service | `api/releases.py` — Private exact-manifest ZIP; requires independent accepted candidate. |
| GET | `/invocations/{invocation_id}/transcript` | 501 | `api/services.py` — Invocation transcript service not delegated. |
| GET | `/native-jobs/{job_id}/logs` | Service | `projects/views.py` — Authorized redacted saved native action summaries, not unrestricted container logs. |
| GET | `/auth/config` | Service | `access/service.py` — Persisted session/member/invite behavior; configured bootstrap and CSRF rules apply. |
| GET | `/auth/session` | Service | `access/service.py` — Persisted session/member/invite behavior; configured bootstrap and CSRF rules apply. |
| GET | `/auth/organizations` | Service | `access/service.py` — Persisted session/member/invite behavior; configured bootstrap and CSRF rules apply. |
| POST | `/auth/signup` | Service | `access/service.py` — Persisted session/member/invite behavior; configured bootstrap and CSRF rules apply. |
| POST | `/auth/login` | Service | `access/service.py` — Persisted session/member/invite behavior; configured bootstrap and CSRF rules apply. |
| POST | `/auth/setup` | Service | `access/service.py` — Persisted session/member/invite behavior; configured bootstrap and CSRF rules apply. |
| POST | `/auth/logout` | Service | `access/service.py` — Persisted session/member/invite behavior; configured bootstrap and CSRF rules apply. |

Inventory totals: 11 501, 2 Blocked, 4 Partial, 42 Service. These are operation classifications, **not feature-completion percentages**.

## Frontend project command inventory

The 47 frozen literal command names include **32 project commands** and **15 non-project commands sent to the separate simulation backend**. Connector/developer/provider simulation commands do not prove replacement-backend capabilities and must retain their existing mock transport. `project.delete` uses DELETE rather than `/commands`.

| Command | State | Service / limit |
|---|---|---|
| `project.acceptKit` | Blocked | ExternalFacade returns 403 physical_policy. |
| `project.acceptPhysical` | Blocked | ExternalFacade returns 403 physical_policy. |
| `project.acceptRelease` | Service | ReleaseFacade/ValidationService; requires independently qualified current candidate, currently concrete CAD export policy only. |
| `project.activate` | Blocked | ExternalFacade returns 403 physical_policy. |
| `project.answer` | Service | DesignFacade/DesignService (create autoStart; message is capture only before confirmation or for selected-part redo, otherwise dynamic objective). Models must be explicitly configured. |
| `project.assemblyStep` | 501 | No implemented ApplicationServices handler (delete has its own missing DELETE action). |
| `project.cancel` | Service | EngineeringService; scoped dynamic objective, candidate repair, cancellation or bounded same-history resume. |
| `project.capture` | Service | DesignFacade/DesignService (create autoStart; message is capture only before confirmation or for selected-part redo, otherwise dynamic objective). Models must be explicitly configured. |
| `project.confirm` | Service | DesignFacade/DesignService (create autoStart; message is capture only before confirmation or for selected-part redo, otherwise dynamic objective). Models must be explicitly configured. |
| `project.create` | Service | DesignFacade/DesignService (create autoStart; message is capture only before confirmation or for selected-part redo, otherwise dynamic objective). Models must be explicitly configured. |
| `project.createPhysicalTasks` | Partial | ExternalFacade; nonbinding catalog observation or dynamic digital preparation objective, not fulfillment. Provider/tool prerequisites apply. |
| `project.delete` | 501 | No implemented ApplicationServices handler (delete has its own missing DELETE action). |
| `project.generate` | Service | EngineeringService; scoped dynamic objective, candidate repair, cancellation or bounded same-history resume. |
| `project.message` | Service | DesignFacade/DesignService (create autoStart; message is capture only before confirmation or for selected-part redo, otherwise dynamic objective). Models must be explicitly configured. |
| `project.placeOrders` | Blocked | ExternalFacade returns 403 physical_policy. |
| `project.prepareKit` | Partial | ExternalFacade; nonbinding catalog observation or dynamic digital preparation objective, not fulfillment. Provider/tool prerequisites apply. |
| `project.repair` | Service | EngineeringService; scoped dynamic objective, candidate repair, cancellation or bounded same-history resume. |
| `project.requestQuotes` | Partial | ExternalFacade; nonbinding catalog observation or dynamic digital preparation objective, not fulfillment. Provider/tool prerequisites apply. |
| `project.requestReview` | Service | DesignFacade/DesignService (create autoStart; message is capture only before confirmation or for selected-part redo, otherwise dynamic objective). Models must be explicitly configured. |
| `project.research` | 501 | No implemented ApplicationServices handler (delete has its own missing DELETE action). |
| `project.retry` | Service | EngineeringService; scoped dynamic objective, candidate repair, cancellation or bounded same-history resume. |
| `project.retryNative` | Service | NativeRebuildService; exact saved sources/pins, linked bounded grant, no model/retrieval calls; uncertain history blocks. |
| `project.selectPrint` | 501 | No implemented ApplicationServices handler (delete has its own missing DELETE action). |
| `project.selectTool` | 501 | No implemented ApplicationServices handler (delete has its own missing DELETE action). |
| `project.setModelPolicy` | Service | PolicyService; validates explicit configured models and preserves active run pins. |
| `project.setToolPolicy` | 501 | No implemented ApplicationServices handler (delete has its own missing DELETE action). |
| `project.setTrainingConsent` | 501 | No implemented ApplicationServices handler (delete has its own missing DELETE action). |
| `project.ship` | Blocked | ExternalFacade returns 403 physical_policy. |
| `project.solveInterfaces` | 501 | No implemented ApplicationServices handler (delete has its own missing DELETE action). |
| `project.swapBattery` | 501 | No implemented ApplicationServices handler (delete has its own missing DELETE action). |
| `project.toggleFeature` | 501 | No implemented ApplicationServices handler (delete has its own missing DELETE action). |
| `project.verify` | 501 | EngineeringFacade explicitly requires same-run validation; post-run workbench verification is not connected. |

Additional new objective/answer/cancel/source/artifact/memory routes are not a substitute for these actual existing calls. In particular, an API-only per-file import capability does not establish a complete existing-project import experience.

## Code-size target and existing edits

Measured from the verified pre-rewrite archive versus the current replacement,
using physical Python source lines (blank/comment lines included). This snapshot
is subject to ongoing implementation changes; it is not a quality/completion score.

| Comparable scope | Files | Bytes | Physical lines |
|---|---:|---:|---:|
| Archived `backend/kleos/**/*.py` | 149 | 1,666,304 | 29,557 |
| Current `backend/src/kleos/**/*.py`, excluding qualification scripts | 83 | 650,419 | 12,079 |
| Current runtime including qualification scripts | 92 | 708,048 | 13,089 |
| Current tests, shown separately | 42 | 391,286 | 7,960 |

Runtime-only reduction is **59.13% by lines, 60.97% by bytes, 44.30% by file count**.
Including new qualification scripts gives **55.72% fewer lines**. Therefore an
unqualified “more than 60% less code” claim is not supported by the line metric.
Most old implementation files were removed, but deleted-file percentage is not
net code reduction, and required missing features will add code. Do not remove
necessary authority/checks or compress formatting to make a percentage pass.

The frontend already has substantial working-tree edits relative to HEAD. This
audit cannot attribute those existing edits to the rewrite; preserve them. No
frontend files were edited for this audit. “Keep UI unchanged” needs the actual
existing workbench tests and flow checks, not a clean-diff assumption.

## Verification gates and concrete commands

Commands below are proposed follow-up checks, not executions performed by this
audit. Tests that require PostgreSQL must use a disposable local verification DB
and the explicit opt-in; no paid provider/native run is implied.

1. Authority/contract gate: add tests proving an ordinary frontend command can
   establish accepted circuit/firmware/validation interfaces and that model output
   cannot self-approve them; post-run verification retains exact candidate hashes.
   Run `cd backend && .venv/bin/pytest -q tests/test_design.py tests/test_engineering_commands.py tests/test_application_services.py tests/test_api.py tests/test_validation_tools.py tests/test_release_api.py`.
2. Recovery gate: worker process death after native submission, native finish vs
   maintenance race, cancellation publication fence, shutdown cleanup, missing
   container uncertainty, and external-event/timer wake-up without resetting spend.
   Run `cd backend && .venv/bin/pytest -q tests/test_worker.py tests/test_execution.py tests/test_native_rebuild.py tests/test_external_jobs.py tests/test_external_tools.py`.
   Run `tests/test_postgres_runtime.py` and `tests/test_runtime_launch.py` with
   `KLEOS_TEST_POSTGRES_URL` only after selecting the intended disposable database.
3. Existing frontend contract gate: execute each of the 59 actual operations and
   32 project command paths with expected success/unavailable policy, validate the
   full corresponding frozen response schemas and exercise existing UI transitions.
   After deliberate compatibility work, export to a separate runtime contract:
   `cd backend && .venv/bin/python -m kleos.cli export-runtime-contracts`.
   Do not overwrite the frozen target or hand-edit generated Dart DTOs.
4. UI gate: `cd kleos-ui && python3 tool/generate_transport.py --check && flutter analyze && flutter test`.
   Verify capture images/drop, selected-part replacement, generate/check/accept,
   preview/download, imported repair, retry/cancel, provider settings, Bambu pairing
   and digital preparation with the replacement API and deterministic providers.
5. Full backend gate: `cd backend && .venv/bin/ruff check src tests migrations && .venv/bin/pytest -q`,
   then `git diff --check`. Unit fixture counts are scoped evidence only.
6. Deployment gate (partly done 3 November 2026): the broken `deploy.yml`, the
   Conductor/Qdrant/DBOS Terraform and their variables were removed. Still open:
   `infra/compute.tf` control task commands/env still target the retired runtime
   (`kleos.kernel.api`, `KLEOS_*`, ECS native dispatch, SQS completions); rewire to
   `python -m kleos.cli`, `KLEOS2_*` settings and hosted adapters, then add a new
   release workflow. Terraform edits were `fmt`-checked and reference-scanned only
   (`validate` needs the provider registry). Validate packaging locally; do not push,
   deploy or mutate cloud resources as part of these checks.

## Follow-up completed while the audit was open

The integrator added `infrastructure/native_maintenance.py` alongside the objective
worker plus an explicit `native-maintenance` CLI command. Each cycle has a bounded
timeout and no model calls. The execution owner separately hardened cleanup:
reserved termination/cleanup receipts bind the job/request, final receipt insertion
locks the existing job row, and completed terminal jobs are excluded from later
cycles. Failed cleanup retries; uncertain action receipts are never cleared by
termination. An overlapping finish/maintenance regression passes. The focused
execution/native-rebuild/runtime suite passed 79 tests with the upstream LangMem
deprecation warning; the integrator owns the final combined/PostgreSQL checkpoint.
This closes the missing maintenance integration finding, not global admission.

The independent external adapter review also completed after corrections.
`docs/external-readonly-review.md` and source-bound
`capabilities/qualification/{digikey,rentahuman}-readonly.json` now qualify the
reviewed mocked-HTTP scope only. No accounts were configured and no live lookup,
purchase, hire, message or payment was performed. Earlier failing review evidence
remains preserved in the same review document.
