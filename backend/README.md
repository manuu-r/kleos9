# Kleos backend rewrite (in progress)

The previous implementation was removed after a SHA-256-verified local snapshot.
The replacement is a modular LangGraph/LangMem backend under `src/kleos`.
Only Bambu connection semantics may be reused. No historical database migration
or legacy runtime is part of this implementation.

The old `.env`, `.data`, PostgreSQL databases and Docker volumes are preserved.
They must not be used as the replacement schema. Old API/workers are stopped;
the replacement is NOT yet ready to serve the Flutter frontend.

## Development

`uv sync --locked` installs the pinned runtime and test dependencies.
`uv run pytest -q` runs the new tests; `uv run ruff check src tests` checks code.
Tests use fake model responses; no paid inference or physical operations.

The fresh [local container package](docs/local-runtime.md) provides separate API,
setup, worker and preview entry points with persistent storage. Its
[smoke-test evidence](docs/local-runtime-qualification.json) covers a specific
source snapshot, not full frontend cutover or cloud/native execution support.

`contracts/openapi.json` is the frozen frontend compatibility target, NOT the
currently implemented API. `tests/contracts/frontend-obligations.json` records
actual UI calls. API parity must be proven before cutover.

See `docs/rewrite-progress.md` for module ownership and outstanding gates.

## Module ownership

| Package | Owns | Does not own |
|---|---|---|
| `contracts` | Typed requests, references and observations | Runtime behavior |
| `projects`, `access` | Project authority, revisions, permissions, command receipts | Tool execution |
| `orchestration` | LangGraph checkpoints, dynamic plans, model routing and accounting | Acceptance |
| `memory` | LangMem extraction and advisory retrieval | Authoritative project facts |
| `capabilities` | Versioned tool bindings and domain adapters | Arbitrary executables supplied by users |
| `execution` | Isolated native sessions and durable source/action identities | Provider credentials or release authority |
| `validation` | Separate evidence, scoped policies and accepted manifests | Generator self-approval |
| `artifacts` | Immutable bytes, source closures and private delivery | Public unscoped storage |
| `api` | Existing workbench shapes and service adapters | A mandatory full-device workflow |
| `infrastructure` | Explicit deployment composition and settings | Hidden fallback providers |

Agents working on these modules should own disjoint files and focused tests.
The integrator owns shared contracts, dependency locks, migrations and composition.
Specialists exchange exact artifact references and observations through the
orchestrator. They do not directly mutate another specialist's state.

## Documentation index (`docs/`)

| Document | Topic |
|---|---|
| [rewrite-progress](docs/rewrite-progress.md), [rewrite-remaining-audit](docs/rewrite-remaining-audit.md) | Current scope, verification and open gaps |
| [local-runtime](docs/local-runtime.md), [provider-wiring](docs/provider-wiring.md) | Container packaging and model/provider configuration |
| [repository-import](docs/repository-import.md), [project-deletion](docs/project-deletion.md) | Existing-project intake and erasure |
| [research](docs/research.md), [external-readonly-review](docs/external-readonly-review.md) | Research and read-only sourcing/human-work adapters |
| [independent-export-checkers](docs/independent-export-checkers.md), [independent-pcb-checker](docs/independent-pcb-checker.md) | Independent validation scope |
| [companion-preview](docs/companion-preview.md), [companion-preview-derivation](docs/companion-preview-derivation.md) | Separate-origin companion hosting |
| [bambu-test-preparation](docs/bambu-test-preparation.md) | Offline slicing and test-print preparation |
| [workbench-progress](docs/workbench-progress.md), [workbench-requirement-edits](docs/workbench-requirement-edits.md) | Workbench preference and requirement-edit commands |
| [prerequisite-continuation-plan](docs/prerequisite-continuation-plan.md) | Interface-prerequisite approval design |

## Test layout

`tests/` holds module-focused suites plus these named groups: `tests/contracts/`
(frozen frontend obligations), `tests/scenarios/` (Phase 4 workflows over fake
capabilities in the real LangGraph engine: route a PCB, replacement parts and
affected work, human verification with exactly-once dispatch, complete device with
dependency order and repair) and `tests/fixtures/`. The missing-part and
resume-without-duplicate-effects workflows run through the real runtime in
`tests/test_dynamic_journeys.py` and `tests/test_external_wait_graph.py`.
Scenario tests prove orchestration behavior, not engineering quality.

## Explicit process commands

Run from this directory with the locked environment. Configuration uses the
`KLEOS2_` prefix. Existing provider names `KLEOS_META_API_KEY`,
`KLEOS_MODAL_LLM_KEY` and `KLEOS_MODAL_LLM_URL` also work unchanged, from the
process environment or the preserved `backend/.env`. Only these provider values
are read from that file; old database/authentication/execution settings are not
inherited. Set a **fresh local
PostgreSQL database URL** and a persistent `KLEOS2_SESSION_SECRET` through your
private environment before running these commands.

```bash
.venv/bin/python -m kleos.cli migrate
.venv/bin/python -m kleos.cli serve --host 127.0.0.1 --port 8000
```

`migrate` applies the replacement Alembic revisions and LangGraph persistence
schema. It refuses unrelated application tables. API startup verifies migrations;
it does not silently create the business schema.

Separate worker processes:

```bash
.venv/bin/python -m kleos.cli worker
.venv/bin/python -m kleos.cli fabrication-worker
.venv/bin/python -m kleos.cli native-maintenance
.venv/bin/python -m kleos.cli verification-worker
.venv/bin/python -m kleos.cli external-worker
```

Companion web hosting has a separate, explicitly enabled process:

```bash
.venv/bin/python -m kleos.cli preview-serve --host 127.0.0.1 --port 8004
```

It requires `KLEOS2_COMPANION_PREVIEW_ENABLED=true` and distinct
`KLEOS2_API_ORIGIN`, `KLEOS2_FRONTEND_ORIGIN` and `KLEOS2_PREVIEW_ORIGIN` values.
The API and preview processes share the same fresh database, artifact store and
persistent session secret. Hosting is disabled by default. The preview process
has no API/authentication routes and disables URL access logs because preview
URLs contain short-lived bearer capabilities. A reverse proxy must likewise
omit/redact those paths from logs and preserve the configured preview Host.
This process serves bounded static files; it does not execute generated server
code or grant engineering acceptance. Browser qualification remains separate
from archive and HTTP checks.

The objective worker can incur model charges when explicit qualified models and
credentials are configured. The fabrication worker performs bounded isolated
digital slicing only and requires a qualified slicer installation. Neither
process is started by the HTTP API. An absent installation remains unavailable.
The objective worker also runs bounded native reconciliation alongside graph work.
It also dispatches queued independent verification. `verification-worker` runs
those checks separately without planning, retrieval or model calls.
`native-maintenance` runs that cleanup separately when no objective worker is active;
it starts no generation or model calls and retains uncertain outcomes for retry.
`external-worker` polls already-linked external work and existing print preparation,
then wakes the exact saved LangGraph interrupt. The objective worker also runs this
bounded observer alongside its normal work. Unchanged polls make no model calls.

`GET /api/v1/ready` reports partial capability availability; a successful API
startup is not frontend parity or engineering qualification.

## Installation and evidence boundaries

- `KLEOS2_MCP_INSTALLATIONS` accepts an explicit list of `installation` and
  `qualificationReference` pairs for reviewed isolated artifact inspectors.
  The default list is empty. Report validation and tool registration launch no
  plugin or container. The objective worker and `native-maintenance` reconcile
  persisted sessions even after an installation is removed. A matching actual
  image qualification is still required; synthetic test reports are rejected
  outside test mode. The first MM-core runner is under qualification and is not
  enabled by default. Local Compose does not currently expose a Docker socket
  or install these tools.
- Existing Meta credentials enable Muse Spark 1.3; an existing Modal URL/key pair
  enables the documented Muse Spark Open deployment. Meta Spark is the default
  for text and image capture when both are configured; Modal remains selectable
  through the existing provider controls. `KLEOS2_MODEL_CATALOG`,
  `KLEOS2_TEXT_MODEL_KEY` and `KLEOS2_VISION_MODEL_KEY` override these installations.
  See [provider wiring](docs/provider-wiring.md) for precedence and verification.
  Capture with images
  requires a vision-capable model. Only the configured Modal rate-limit fallback
  is allowed; changing settings does not rewrite active run pins or spend history.
- `KLEOS2_NATIVE_TOOLS` entries specify capability, exact image digest, provider
  version and qualification-reference path. Local Docker Desktop compatibility
  requires its explicit setting and matching evidence. Firmware also requires an
  accepted `interfaces.firmware.board` present in the image's qualified board set.
- `KLEOS2_CAD_CHECKER_INSTALLATION` uses `imageDigest`,
  `qualificationReference` and optional `daemonControlRoot`. Its separate image
  reads only STEP/STL exports. `cad.export-integrity` covers export integrity and
  explicitly bound numeric constraints; it does not establish device function,
  strength, manufacturability or physical acceptance.
- `KLEOS2_SLICER_INSTALLATION` uses `image`, `report`, `dockerDesktop` and optional
  `daemonControlRoot`. Current local fixtures qualify A1 mini, A1 and P1S PLA/0.4 mm
  profiles from STL or triangulated OBJ. Unsupported formats and unqualified
  repairs remain unavailable. Printer upload/start remains blocked by policy.
- Native generation, digital slicing and independent CAD checking share durable
  aggregate capacity reservations. `KLEOS2_NATIVE_CAPACITY_*` settings choose the
  pool ID, CPU, memory, scratch and job ceilings. Defaults are 4 CPUs, 6 GiB memory,
  5 GiB scratch and two jobs, including bounded supervisor scratch. Every process targeting the same Docker daemon must
  use the same pool ID and limits; changing settings does not free old reservations.
  Unconfirmed container creation or removal retains capacity. Existing unreserved
  work must be reconciled before new admission; no legacy resource defaults are guessed.
- Bambu local access requires explicit enablement, a persistent encryption key,
  authenticated local transport and pairing. Connection credentials do not enter
  model context or native workers.

Qualification reports live with their owning module under `qualification/`.
They describe exact fixture/image/platform scopes. Failed attempts remain evidence;
they are not silently promoted by a passing unit test.

## Interfaces and verification

`project.solveInterfaces` proposes typed circuit/firmware configuration using the
approved concept and bounded imported-source context. The proposal is shown in the
existing conversation. Only an explicit `approve interfaces` message against the
current visible revision (or an exact proposal ID/hash approval command) accepts
it. Circuit references must uniquely cover the approved peripheral concept IDs.
Configuration approval is not independent engineering acceptance.

`project.verify` queues independent checking of a completed objective's exact
current candidate sources/outputs. It does not create a generation objective or
replace accepted sources. The current configured policy covers CAD export integrity;
other lanes still need qualified independent policies. `project.acceptRelease`
remains a separate authorized command after checks finish. Uncertain in-flight
checks are retained and cannot be repeated as fresh attempts.

Project spending views derive estimates and unresolved reservations from durable
model receipts, including historical objectives. An absent project-wide allowance
is shown as unknown rather than invented from per-objective ceilings. The private
invocation-record endpoint requires both project membership and organization
administration; it returns operational metadata, never raw prompts or reasoning.

## Verification and outstanding work

```bash
.venv/bin/ruff check src tests migrations
.venv/bin/pytest -q
.venv/bin/python -m kleos.cli export-runtime-contracts
```

PostgreSQL/process tests require `KLEOS_TEST_POSTGRES_URL` pointing to a disposable
local verification database. Tests do not require paid inference or physical work.
The runtime contract export is separate from the frozen frontend target.

Frontend cutover remains incomplete. Remaining work includes broader independent checking,
broader qualified integrations, companion previews/APK delivery, deployment packaging and full
workflow/UI verification. See the progress record for current evidence rather than
treating a registered route or installed toolchain as a completed capability.
