# Kleos9 backend

This is the production backend alongside the existing mock. It implements durable commands,
engineering attempts, direct Muse/Gemini adapters, isolated native job contracts,
independent release gates, scoped retrieval, connectors, Modal compute, and the Flutter
production transport. Physical fulfillment is explicitly simulated and live dispatch is blocked.

The code is runnable locally. AWS/account provisioning, real provider qualification and the
complete smartwatch acceptance run remain deployment checks; tests do not establish those outcomes.

## Local Docker backend and Gemini

`GEMINI_API_KEY` in `backend/.env` is supported directly. The prefixed
`KLEOS_GEMINI_API_KEY` is also supported and takes priority if both are present.
Keep the key server-side. Set `KLEOS_DEFAULT_MODEL_PROVIDER=google` to make Gemini
an explicit default for every lane on new projects. Omit the setting to retain the
original per-lane defaults. Existing project policies and running attempts remain
pinned; change their choices explicitly through Providers. This setting does not
automatically fall back to another provider when credentials or credits are unavailable.
Only Meta and Gemini are supported. Firmware and application lanes default to Gemini;
the explicit Google default above continues to select Gemini for every lane locally.
The provider-policy migration moves unsupported choices on existing builds to Gemini,
records a policy-change event and applies the new choice to subsequent attempts.
Configure the local identity/signing secrets and operator allowances shown in `.env.example`.
Development Compose enables `KLEOS_DEVELOPMENT_UNLIMITED_USAGE` by default; set it to `false`
to exercise production-style usage limits. Outside Compose the setting defaults to `false`, and
staging/production reject `true`. Unlimited development skips monetary ceilings, project admission
limits and lifetime manual retry caps while preserving real cost/reservation records, finite automatic
repair chains, concurrency, runtime, security and engineering approval gates. The API reports
`budget.unlimited=true` and `allowanceUsd=null`, never an invented credit balance.

```sh
docker compose --profile backend up -d --build
docker compose --profile backend exec api python -m kleos.cli bootstrap-dev
```

This runs the API on `127.0.0.1:8000`, engineering and connector workers, PostgreSQL and Qdrant.
The development profile mounts application source read-only; restart the affected service after
editing Python code. Generated code runs in separate containers with no network, provider keys,
database credentials or Docker socket. Only the trusted local workers can access that socket.
Build and qualify the native images in [docker/README.md](docker/README.md) before engineering
execution; an absent image is an explicit blocker. This local Docker adapter cannot be enabled
in staging or production. The cloud execution backend remains ECS.

`KLEOS_API_PROCESSES` adjusts local API processes within the single container (default 1).
The verified 100-client configuration uses 2 with tracing enabled; account for per-process
database pools when increasing it. See the [runtime verification record](verification/README.md)
for measured results, actual generated artifacts and remaining release blockers.

Run Flutter from `kleos-ui` with `--dart-define=KLEOS_BACKEND=production` and web port `5180`.
The development token authenticates the local application only; never use it as a provider key.
Generation and embedding reservations are separate configured allowances, not account credit balances.
When unlimited development is off, set `KLEOS_DAILY_BUDGET_USD=unlimited` to disable only the daily ceiling for models, embeddings,
native jobs and custom compute while retaining usage accounting. Zero blocks unconfigured paid
work; positive values enforce a daily ceiling. Project budgets and concurrency controls apply
independently. Recreate the API and workers after changing the setting.

For a LAN preview, set `KLEOS_API_BIND_ADDRESS=0.0.0.0` in `.env`, add
`http://<LAN-IP>:5180` to `KLEOS_CORS_ORIGINS`, and recreate the API container.
Run Flutter with `--web-hostname 0.0.0.0 --web-port 5180` and
`--dart-define=API_BASE_URL=http://<LAN-IP>:8000/api/v1` alongside the production-mode flag.
The database, Qdrant and diagnostic ports retain their loopback bindings. Update the LAN URL
and allowed origin if the computer's IP changes.

For local distributed traces, set `KLEOS_DOCKER_OTLP_ENDPOINT=http://otel:4318/v1/traces`
and include `--profile observability` when starting Compose. The collector logs sanitized span
metadata with the same trace IDs shown in the run inspector. Local container logs rotate at
10 MiB × 3 files; deployed CloudWatch log groups apply the configured 30-day retention.

## Run locally

Use Python 3.12, Docker and Flutter. From this directory:

```sh
python3.12 -m venv .venv
.venv/bin/pip install --require-hashes -r requirements-dev.lock
.venv/bin/pip install --no-deps -e .
cp .env.example .env
docker compose up -d postgres qdrant
.venv/bin/alembic upgrade head
.venv/bin/python -m kleos.cli bootstrap-dev
.venv/bin/uvicorn kleos.api:app --host 127.0.0.1 --port 8000
```

Replace the two development secrets in `.env` before starting. Keep the API, workers and CLI
in the same directory so they read the same settings. In two additional terminals:

```sh
.venv/bin/python -m kleos.worker --kind engineering
.venv/bin/python -m kleos.worker --kind connectors
```

Then start the existing Flutter application from `kleos-ui`:

```sh
flutter run -d chrome --web-port 5180 --dart-define=KLEOS_BACKEND=production
```

Enter the local development token in the sign-in screen. The mock remains the default when
`KLEOS_BACKEND` is omitted. See [Flutter setup](../kleos-ui/README.md) for mobile host addresses,
secure storage and Cognito configuration. No provider credential belongs in Flutter.

To enable hosted AI, configure the Meta and Gemini API keys and, when unlimited development is disabled, positive project/daily allowances.
New projects copy the configured project allowance. Provider availability is explicit in the
catalog; an outage never silently selects another provider. The default host process disables native
execution. The Compose profile enables qualified local Docker images; AWS needs qualified ECS
task definitions, networking and callback access. Generated
sources remain available when a native tool is unavailable; that is a blocker, not a passing check.

## Execution and authority

### Conversational capture and concept previews

New Flutter builds submit `project.create` with `autoStart: true`. Capture returns
an immediate, explicitly provisional concept, then a bounded planning agent asks
guided questions. `project.answer` with `autoResume: true` and `project.message`
save user input durably and resume planning automatically. Required answers lead
into feasibility research; the same scene is refined from those findings. Review
can request fresh research for substantive changes, once per input version.
Engineering still requires explicit confirmation.

Concurrent inputs coalesce into the latest planning turn. Each turn pins its input
version; superseded output cannot publish a concept or overwrite current choices.
Client message IDs, command idempotency keys and question hashes make retrying
safe. Selected-part messages pin both a part ID and the displayed concept version.
Provider failures pause visibly rather than silently switching or looping.

The [Anatomy skill](https://skills.sh/wheresryan22/anatomy/anatomy) is vendored under
`kleos/skills/anatomy` at commit
`1482e8469b0a9e0d8cc18ea2740a01423d29ae80`, with its MIT license, source checksums
and a compact runtime adaptation. Agents emit a validated data-only scene of
relative shapes; Flutter renders it natively. No generated JavaScript or CAD is
executed for a concept preview. Scene versions, skill provenance and context
hashes are available in the run records. Concepts are references, never evidence
for engineering or physical acceptance.

Planning uses a separate structured output contract without source files or
interface generation. Defaults cap each model response at 5,000 output tokens and
each turn at four model requests (`KLEOS_PLANNING_MAX_OUTPUT_TOKENS` and
`KLEOS_PLANNING_MAX_MODEL_TURNS`). Context contains one current scene, bounded chat,
saved choices and bounded research excerpts. Capture and visual preference
updates skip retrieval; qualified engineering keeps its separate execution path.

`api.py` accepts commands and serves scoped queries. `domain.py` enforces the nine logical
business modules' transitions, permissions, revision checks, release rules and simulated
commitments. `engine.py` records bounded attempts and invokes models/tools. `worker.py` registers
DBOS workflows and dispatches the PostgreSQL outbox. `execution.py` submits/reconciles isolated
ECS tasks; `native_runner.py` executes the pinned tool and independent checks.

One transaction commits each command receipt, project change, event and outbox entry. Stable
workflow/job IDs make repeat delivery safe. Pydantic AI DBOS checkpoints record model requests;
application steps record context, reservations and results. A recorded result is replayed without
another billed call. A provider response lost before recording can still require a second call.
An operator can reconcile uncertain charges through the authenticated invocation/compute APIs.

Every attempt pins provider/model, model-policy revision, engineering revision, source hashes and
fencing token. A policy change applies to the next attempt. Engineering failures can schedule two
targeted repairs; one additional pivot needs a recorded justification. Provider changes and new
run IDs do not reset this engineering-attempt allowance. Infrastructure outages and unsupported
tools do not trigger speculative engineering repairs.

`project.retryNative` rebuilds exact current sources using `{projectId, jobId}` for a completed build,
or `{projectId, sourceAttemptId}` when a recorded model response produced complete current sources
but native submission never started. The latter rejects missing files, stale contracts, unrecorded
responses and attempts that already have a native job. Flutter exposes this as **Rebuild saved sources**.
It preserves source model/policy and artifact hashes, makes no model/retrieval call, records native
compute costs and requires fresh independent validation. Built-in tool rebuilds may use the current
qualified image; custom adapters retain their original qualified installation. Outside unlimited
development, two rebuilds per source lineage are permitted and the separate engineering repair
limit remains in force.

Firmware workers explicitly translate the legacy `nrf52840dk_nrf52840` target to Zephyr 4.2's
`nrf52840dk/nrf52840`, recording both names and the alias policy in job measurements. The image
bakes Zephyr's release-pinned Nordic HAL. This development-board compile does not qualify a
custom PCB or establish BLE, display, sensor or physical behavior.

Only independent validation records can satisfy release gates. Uploaded user files are inputs,
model outputs are candidates, and native exports retain their job/image provenance. Validation
must cover the exact current candidate closure. Revision changes and cancellation fence late
workers. Accepted release manifests preserve earlier baselines. ZIP downloads include hashed
files and a placement reference derived from accepted geometry; insertion order, wiring and
physical assembly validation remain explicit limitations.

Organization admins can manage invited project members. Members access projects they own or
have been assigned; project viewers cannot mutate them. Authorization is rechecked for queries,
retrieval, artifacts and event streams. Workspace snapshots use repeatable-read transactions and
a bounded cache keyed by the authoritative revision, with authorization checked before reuse.

## Engineering scope

CAD/PCB generators now use versioned per-build tool policies. Defaults remain
build123d and atopile/KiCad; project writers select qualified installed alternatives
under Connectors → Engineering tools. See [adapter contracts and installation](../docs/engineering-tool-plugins.md)
and the [backend review](../docs/backend-review-2026-10-30.md).

See [native image setup](docker/README.md) and
[watch-telemetry-v1](docker/templates/watch-telemetry-v1/README.md). The initial software profile
tests the actual C/Dart telemetry decoders using independent valid, boundary and invalid frames.
It requires real compiled outputs. It does not qualify BLE, displays, sensors, radio, thermal or
physical behavior. Explicitly requested unsupported coverage remains a required `UNKNOWN`.

KiCad ERC/DRC and build123d export/inspection are executable adapters. Atopile layouts, board
support, tool/image compatibility and peripheral simulation require concrete qualification.
Arbitrary generated dependencies cannot download packages in native tasks. Renode integration
is not yet qualified and no Renode result is reported as passing. iOS companion source can be
produced, but signed iOS builds need a macOS worker and signing setup.

The 25 [golden briefs](fixtures/golden_briefs.json) cover deterministic interface acceptance,
incompatible envelopes/voltages and missing/invalid evidence. SDK contract tests exercise all
seven lanes across both adapters, schema failures and provider failures without paid calls.
These fixtures are a regression baseline, not a claim that live model engineering quality has
been qualified. Run account-backed engineering evaluations before promoting models/prompts/images.

## Integrations and operations

See [integration contracts](../docs/backend-integrations.md) for research fetching, DigiKey,
Qdrant indexing, bridge telemetry, simulated provider portal, alternative ngspice connector,
Modal inference/training, consent, promotion, retention and deletion APIs.

The event ledger is authoritative. OpenTelemetry links API/workflow/model operations; native
jobs carry the same trace identity. AI transcripts omit private reasoning and redact credentials
and signed URLs. Trace export also removes callback query strings, headers, SQL text and exception
bodies from automatic instrumentation. Transcript/native-log reads are restricted and audited. Native stdout is bounded
and stored separately from artifact metadata. The connector worker enqueues hourly maintenance:
30-day detailed records, 90-day online events, one-year event metadata archives. Accepted files
persist until explicit project deletion, which immediately revokes access and queues full erasure.

Allowances are conservative admission reservations. Provider token-price estimates, when known,
are recorded separately with a pricing version; they are not account credit balances. Transport
uncertainty requires reconciliation before releasing reservations. Set reservation ceilings to
cover the qualified prompt/output/search budget; native infrastructure and provider invoices must
also be reconciled operationally.

AWS deployment, isolated task networking, RDS roles/migrations, Cognito, S3/KMS, Qdrant snapshots,
Conductor, dashboards, backups and recovery are in [infra/README.md](../infra/README.md).
No AWS deployment, live purchase, printer dispatch, shipping action or paid training was run
during local verification.

## Verification

```sh
.venv/bin/ruff check .
.venv/bin/pytest -q
KLEOS_TEST_POSTGRES_URL=postgresql://kleos:local-development-only@localhost:5432/kleos .venv/bin/pytest tests/test_postgres.py tests/test_recovery.py -q
.venv/bin/python -m kleos.cli export-contracts
```

The PostgreSQL tests create and drop disposable databases. The recovery drill kills a real worker
after durable model/usage recording and restarts it, checking one provider transport invocation,
artifact, publication and debit. It uses a Pydantic test model, not a paid provider.

From `kleos-ui`, `python3 tool/generate_transport.py --check` verifies generated DTOs against
`backend/contracts/openapi.json`. Regenerate after intentionally changing an API response schema.

For a measured control-plane load check, set `KLEOS_LOAD_TOKEN` and optionally
`KLEOS_LOAD_ORGANIZATION`, then run:

```sh
.venv/bin/python tools/load_control_plane.py --url http://localhost:8000 --cleanup
```

It connects 100 event streams across 10 projects, measures reads, accepts configuration commands
and verifies event delivery. It makes no model calls. Its thresholds are p95 500 ms / 1 s / 2 s.
This is distinct from a staging run with ten concurrently active engineering projects and real
provider/native capacity. Also execute restore, live Cognito, account quota, full engineering
journey and model-quality checks before opening invitations.

For a local preview without a sign-in screen, set
`KLEOS_AUTH_MODE=development` and `KLEOS_DEVELOPMENT_NO_LOGIN=true`.
Requests then use the existing `development` user's organization/project
permissions. This does not create a new organization or replace existing data.
Staging/production and Cognito configurations reject this setting. Keep it
`false` when a development token should be required.

## Guided requirements and build removal

Research produces `clarificationQuestions` with stable IDs, plain prompts, choice
options, help text, and optional numeric bounds/units. The workspace projects
`state.clarification` with saved answers, current and resolved questions, progress,
and confirmation readiness. Legacy text questions remain supported.
`project.answer` accepts `questionId` and `values`; it saves without inference.
`project.requestReview` incorporates choices into the proposal. Required unanswered
questions and unreviewed edits block `project.confirm`. Model responses cannot
remove unanswered required decisions; reviewed choices remain editable.
Each explicit review turn has a bounded, durable retry allowance; engineering
repair/pivot limits and monetary ceilings apply independently.

Owners/admins can delete a build with
`DELETE /api/v1/projects/{id}?expectedRevision=N`. It disappears immediately,
active work is fenced/cancelled, and asynchronous cleanup erases project content,
files, retrieval records, and eligible training copies/checkpoints. Minimal
operational tombstones/accounting records remain. Flutter exposes this through
its confirmed **Delete and clear build** action and clears matching pending
commands so deleted builds cannot reappear during recovery.
