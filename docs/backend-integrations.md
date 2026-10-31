# Backend integrations and custom compute

The production API uses authenticated, organization-scoped adapters. Default tests use injected transports and local SQLite/Qdrant and make no paid calls. The separately opted-in runtime suite verifies real PostgreSQL/Qdrant, public research fetches, local printer simulators, and bounded live Gemini embeddings. No test deploys Modal applications, purchases parts, or controls physical printers. Production account permissions, quotas, tool compatibility and model quality still require operator qualification.

## Runtime services

`kleos.integration_api.register_routes` mounts the integration API inside the production FastAPI application. Ordinary user routes use the same organization membership and cookie-CSRF (or bearer-session) checks as the control API. Connector webhooks use per-connector signatures; the local printer bridge uses an opaque, rotated, connector-scoped token.

| Route | Behavior |
|---|---|
| `GET/POST /api/v1/connectors` | List tenant connectors; administrators register manifests as disabled and schema-verified |
| `POST /api/v1/connectors/{id}/invoke` | Invoke a qualified read-only REST/MCP capability with `Idempotency-Key` |
| `GET /api/v1/catalog/parts?q=...` | DigiKey product search; unavailable until developer credentials are configured |
| `GET/POST /api/v1/projects/{id}/sources` | List current sources or fetch an explicit public HTTPS research document |
| `DELETE /api/v1/projects/{id}/sources/{sourceId}` | Immediately revoke authority in PostgreSQL and queue index removal |
| `POST /api/v1/projects/{id}/memory/query` | Bounded Gemini-embedded retrieval with PostgreSQL authorization/applicability checks |
| `POST /api/v1/projects/{id}/memory/rebuild` | Administrator queues reconstruction from current, unrevoked authoritative sources |
| `POST /api/v1/webhooks/{id}` | Verify, deduplicate, and persist a provider observation for durable processing |
| `POST /api/v1/bridges/{id}/token` | Administrator creates/rotates the local bridge token; return it once |
| `POST /api/v1/bridges/{id}/telemetry` | Accept current read-only printer observations; never dispatch printer commands |
| `GET /api/v1/printers/bambu/catalog` | Authenticated pinned model/nozzle catalog and operator-qualified slicing profiles |
| `GET/POST /api/v1/projects/{id}/print-preparations` | List or durably prepare a current accepted-release STL for local offline slicing |
| `GET/DELETE /api/v1/projects/{id}/print-preparations/{preparationId}` | Inspect or cancel a revision-fenced digital preparation |
| `POST /api/v1/projects/{id}/print-preparations/{preparationId}/complete` | Validate bounded uploaded package structure and save a separate prepared artifact; no physical acceptance |
| `GET /api/v1/projects/{id}/portal/tasks` | Current simulated fulfillment assignments |
| `POST /api/v1/portal/tasks/{id}/observations` | Record synthetic human-task progress without changing engineering acceptance |
| `GET/POST /api/v1/learning/datasets` | Administrator views/creates versioned, consent-aware dataset manifests |
| `POST /api/v1/learning/datasets/{id}/rights-review` | Record the operator's data/model-output rights review |
| `GET/POST /api/v1/learning/models` | List models or register a bounded training candidate |
| `POST /api/v1/learning/models/{id}/evaluation` | Administrator records held-out metrics and the supporting report artifact |
| `POST /api/v1/learning/models/{id}/promotion` | Enforce sequential shadow → canary → promoted gates |
| `POST /api/v1/learning/models/{id}/rollback` | Retire a model immediately for subsequent inference admission |
| `POST /api/v1/projects/{id}/compute/inference` | Explicitly dispatch a promoted Modal model with `Idempotency-Key` |
| `POST /api/v1/learning/models/{id}/training` | Explicit administrator training dispatch, separate from dataset creation |
| `GET /api/v1/compute/{id}` | Inspect the durable compute intent, provider call ID and allowance debit |
| `POST /api/v1/compute/{id}/poll` | Fetch a completed Modal result and copy it to immutable artifact storage |
| `POST /api/v1/compute/{id}/reconcile` | Administrator reconciles an ambiguous dispatch against provider evidence |

API requests use camelCase JSON, reject unexpected fields, and bound payloads, limits, runtime, and numeric metrics. Paid inference/training submissions return `202`; explicit research collection returns its persisted source after the bounded read completes. No endpoint accepts end-user provider API keys.

## Research and retrieval

The fetch service permits public HTTPS on port 443 only. It resolves and checks every destination, rejects all non-global DNS answers, and pins the validated numeric address while retaining the original TLS hostname. Redirect targets are independently checked; POST redirects are refused. Proxies, cookies and ambient credentials are not inherited. Responses are bounded to 2 MB and text/HTML/JSON/PDF; PDF extraction is limited to 100 pages and 2 million text characters. HTML executable content is excluded. Private LAN addresses belong only in the locally installed bridge.

Research stores content hashes, collection timestamps, sanitized source URLs, revision and `research` authority. URL query strings and credentials are removed from captured provenance. Research text is untrusted evidence and cannot overwrite accepted requirements or satisfy validation merely by claiming a result.

Gemini embeddings use the configured `KLEOS_EMBEDDING_MODEL` and 768 dimensions. `gemini-embedding-2` uses the documented query/document text prefixes; `gemini-embedding-001` uses the `task_type` parameter. The direct API does not accept the enterprise-only `auto_truncate` parameter. See [Google's embedding formats](https://ai.google.dev/gemini-api/docs/embeddings). Collection names and cached invocation identities include both the embedding model and input-format version, with an alias insulating callers. A format change requires reconstruction through the memory rebuild endpoint; old incompatible vectors are never silently reused. Qdrant stores tenant, project, revision, source ID, content hash, authority and chunk offset. Every retrieval hit is rechecked against PostgreSQL; stale, revoked, deleted and cross-tenant records are dropped even if index cleanup has not completed. Full index reconstruction reuses recorded vectors where applicable instead of charging twice for completed embeddings.

`KLEOS_EMBEDDING_RESERVATION_USD` defaults to $0.01 per bounded embedding invocation, separate from generation reservations. The qualified request policy caps text at 6,000 characters and checks the reservation against the provider's full input-token ceiling multiplied by the published text rate and three transport attempts. The policy version is saved in the intent. For Embedding 2, 8,192 tokens × $0.20 per million × three attempts is below $0.005. This is an admission bound based on [Google's published pricing](https://ai.google.dev/gemini-api/docs/pricing), not measured billing or a credit balance. Changing models or rates requires updating this qualified policy; unknown models and undersized reservations are rejected before dispatch.

Outbox contracts:

- `index_source`: `{"sourceId": "..."}` → `MemoryIndex.index(source_id)`.
- `delete_source`: `{"sourceId": "..."}` → `MemoryIndex.delete_points([source_id])`.
- `webhook`: `{"inboxId": "..."}` → `ConnectorService.process_webhook(inbox_id)`.

Both project and daily allowances must be positive before a paid embedding or Modal call. Paid intents and reservations precede dispatch. Recovery reuses completed embedding vectors and recorded Modal call IDs. An unconfirmed result remains uncertain with its reservation intact. Operators can attach a discovered Modal call ID or settle the failed intent conservatively; an ambiguous operation is never blindly resubmitted. Allowance debits currently use the full reservation and are explicitly labeled `conservative_reservation`, not a provider billing statement. Embeddings share the global hosted-call admission ceiling with the model workers; Modal has independent container ceilings.

## DigiKey and connector contracts

CAD/PCB worker adapters use a separate [versioned engineering-tool contract](engineering-tool-plugins.md).
The read-only connector invocation endpoint does not submit engineering builds or
grant release authority. User artifact uploads accept up to 16 MiB decoded with
authentication, CSRF and project-write checks before the larger body is buffered.

Configure `KLEOS_DIGIKEY_CLIENT_ID`, `KLEOS_DIGIKEY_CLIENT_SECRET` and optionally `KLEOS_DIGIKEY_SANDBOX=false` in server secrets. The adapter uses client-credentials OAuth and v4 keyword product search. Sandbox observations are labeled `sandbox`; production observations are read-only and are never reservations, guaranteed quotes or orders. See [DigiKey OAuth](https://developer.digikey.com/tutorials-and-resources/oauth-20-2-legged-flow) and [v4 keyword search](https://developer.digikey.com/products/product-information-v4/productsearch/keywordsearch).

Generate the example REST manifest and inspect required fixtures locally:

```sh
cd backend
.venv/bin/python -m kleos.connector_cli init /tmp/kleos-connector.json
.venv/bin/python -m kleos.connector_cli fixtures /tmp/kleos-connector.json
.venv/bin/python -m kleos.connector_cli conformance /tmp/kleos-connector.json
```

The CLI validates the manifest and lists operational fixtures. Passing schema checks never marks a connector operationally qualified. Registration always starts disabled; an operator must review independently exercised fixtures before setting the registered connector to `qualified` and enabled. There is no generator-controlled self-qualification endpoint.

REST adapters implement `POST {endpoint}/v1/invoke`:

```json
{
  "schemaVersion": "1",
  "operationId": "stable-caller-id",
  "capability": "parts.search",
  "payload": {"query": "resistor"}
}
```

MCP adapters use the [2025-11-25 Streamable HTTP protocol](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports): initialization, initialized notification, tool discovery, and tools/call. The MVP supports bounded JSON and complete SSE responses. A server requiring an indefinitely open stream, sampling, server-initiated operations, or stream resumption returns an explicit unsupported/failure result. Tool capability declarations are checked before calls; arbitrary remote writes are blocked even if a manifest claims permission.

Webhook headers are `X-Kleos-Timestamp` (Unix seconds), `X-Kleos-Event-Id`, and `X-Kleos-Signature` (`sha256=` plus hexadecimal HMAC). Sign the UTF-8 prefix `timestamp.eventId.` followed by the exact raw request bytes, using the per-connector secret configured in `KLEOS_WEBHOOK_SECRETS`. The accepted skew is five minutes; the maximum body is 1 MB. Reusing an event ID with a different captured payload is rejected. Webhooks can create unverified provider observations, never accepted releases or physical PASS evidence.

## Customer-installed printer bridge

Register a `bridge` connector with `printer.status` and optionally `printer.profile`, mode `read_only`. An administrator creates the bridge token; the server stores only its SHA-256 digest. Configure the local machine, for example:

```json
{
  "kind": "octoprint",
  "printerOrigin": "http://192.168.1.20",
  "platformUrl": "https://your-kleos-host.example",
  "connectorId": "registered-connector-id",
  "intervalSeconds": 30
}
```

Set `KLEOS_PRINTER_API_KEY` and `KLEOS_BRIDGE_TOKEN` in that machine's protected environment, then run:

```sh
.venv/bin/python -m kleos.bridge --config /path/to/private-bridge.json
```

OctoPrint reads `/api/printer` and `/api/printerprofiles`; Moonraker reads `/printer/info`. The bridge pins a local validated address and sends no credentials to redirected or rebound destinations. Its only outbound platform operation submits telemetry. It neither polls for commands nor executes response commands, files, scripts, G-code, or action URLs. Telemetry older than the configured freshness threshold is shown as disconnected. Reported printer state is never physical acceptance evidence.

The [Bambu adapter](../backend/docs/bambu-printers.md) adds local read-only MQTT/TLS
status with an owner-verified certificate pin, reported model detection and a
14-model dropdown fallback. A separate authenticated local preparation client
slices an accepted binary STL in the pinned offline image and uploads the real
single-plate `.gcode.3mf`. The platform records a digital adapter report; it does
not independently establish printability or permit physical acceptance. Operator
review/start happens in Bambu Connect. Defaults contain no qualified slicing
profiles; printer/firmware/material qualification is separate. No direct printer
control, FTP upload or cloud-signature bypass is implemented.

The provider portal is currently organization-scoped for invited users. Assignments and observations are visibly simulated and stay tied to their engineering revision. Vendor-specific authentication, real labor commitments, printing and payments need separately qualified live adapters; the current server blocks those effects.

## Modal model lifecycle

`kleos.modal_app` contains a deployable, bounded inference/training recipe. Importing it starts no GPU work. Deployment and model provisioning are operator setup steps; this implementation has not deployed or run it.

The application name defaults to `kleos-models`; functions are `infer` and `train`. The recipe uses an L4, two inference containers maximum, one training container maximum, a 600-second inference timeout and a 3600-second training timeout. It downloads only checksum-verified, short-lived URLs from the configured private artifact bucket, and never receives database credentials or full project access. The secret named `kleos-artifact-access` contains `KLEOS_ARTIFACT_BUCKET` and `AWS_REGION`; it does not need a long-lived AWS access key for presigned reads. A future direct AWS read/write extension should use [Modal OIDC](https://modal.com/docs/guide/oidc-integration).

Provision licensed base weights in the `kleos-model-weights` Volume before training. Caller input cannot request arbitrary model downloads or enable remote model code. The operator-maintained `/models/registry.json` maps promoted model IDs to relative local weight paths and must also declare `status: "promoted"`. Both the AWS registry and this deployment registry must permit a model before inference runs.

Training steps:

1. Opt in each customer project using the normal project training-consent command.
2. Create a dataset from at least two projects with current accepted digital releases. The manifest records artifact hashes and consent versions and separates entire projects into training/evaluation splits.
3. Review data and model-output rights. This is an explicit operator attestation, not an automated legal determination.
4. Register a candidate with the provided `causal_lm_sft` recipe, licensed base model, provisioned `baseModelPath`, positive `budgetUsd`, and `maxGpuSeconds` of 1–3600.
5. Explicitly dispatch training with an idempotency key and funding project. Only the training split is materialized. Consent and artifact applicability are rechecked before dispatch.
6. The training recipe checkpoints every 20 steps to `kleos-training-checkpoints`, commits saves, and resumes the latest checkpoint on an explicit retry. Candidate weights are never promoted automatically.
7. Independently evaluate at least 20 held-out examples. Record the report artifact, critical failures, pass rates, and cost/latency ratios. Promotion requires zero critical failures, no quality regression and a measured improvement.
8. Explicitly promote through shadow and canary, recording successful outcomes before general promotion. Provision the approved weights/registry on Modal before use. Rollback prevents subsequent AWS dispatches.

The provided recipe is a bounded causal-language-model training starting point, not a universal CAD/electronics model trainer. Training checks elapsed runtime at step boundaries, while invocation-specific Modal timeouts enforce the requested outer bound. API admission limits active custom inference to two calls and training to one; dynamic Modal container pools cannot bypass those limits. Each result records GPU type and function execution seconds, excluding provider startup time. GPU billing accounting currently relies on conservative allowance reservations; actual provider billing reconciliation and measured GPU pricing remain operator setup. Custom training recipes require separate qualification.

Modal call IDs are saved durably. Completed results are copied into immutable S3 objects and hashed before the compute intent completes; S3 is the durable output location, not Modal's temporary result cache. See [Modal asynchronous jobs](https://modal.com/docs/guide/job-queue) and [checkpointed training](https://modal.com/docs/examples/long-training).

Opt-out prevents future dataset use and prevents promotion using an invalidated dataset; existing model lineage is preserved for operator handling. Growth does not trigger training, inference, or promotion automatically.

## Alternative engineering connector example

`kleos.ngspice_connector` implements the actual Kleos REST contract for the bounded `electronics.simulate` capability. It runs an installed [ngspice](https://ngspice.sourceforge.io/docs.html) executable against a fixed ideal RC circuit and returns measurements derived from the simulation output. Numeric parameters are the only accepted input: arbitrary netlists, shell commands, filenames, includes and model downloads are rejected. Batch mode disables user startup scripts, strips inherited credentials, caps concurrency at two, limits steps and runtime, and records idempotent receipts in a mounted SQLite file.

Start the example locally only after installing ngspice:

```sh
cd backend
KLEOS_CONNECTOR_RECEIPTS=/path/to/persistent/receipts.sqlite .venv/bin/uvicorn kleos.ngspice_connector:create_app --factory --host 127.0.0.1 --port 8090
```

The fixture at `backend/fixtures/connectors/ngspice.json` contains the manifest, a numeric RC request and the independent qualification requirements. Missing ngspice, unsupported output or a timeout produces `UNKNOWN`. Successful output is digital simulation evidence requiring independent platform validation; it cannot satisfy physical gates or accept a release. The development fixture tests contract behavior with an injected process runner. No real ngspice execution was performed on this workstation because the executable is not installed. Deploy this example only as an isolated, restricted CPU adapter behind an access-controlled and rate-limited gateway; the fixture is not production qualification.

## Retention and explicit deletion

`DELETE /api/v1/projects/{id}?expectedRevision=N` requires an organization administrator and the current workspace revision. It returns `202` after one transaction tombstones the project, cancels active engineering work, revokes source applicability and training consent, excludes dependent datasets, retires dependent models, and records a durable `delete_project` outbox job. Tombstoned project data is immediately inaccessible through project-authorized APIs.

`Maintenance.purge_project(project_id)` is the background handler. It can run only for an explicitly tombstoned project. It removes artifact/transcript/result object versions and delete markers, Qdrant records, source text, detailed run content, accepted artifact content and derived training copies. Scoped project-prefix sweeps also remove orphan objects from interrupted database transactions without touching neighboring projects. A failed remote cleanup leaves references available for retry. Reservations for ambiguous paid work are conservatively debited instead of refunded. Known Modal jobs are cancelled; a late-arriving job ID queues another deletion pass. Training jobs using an excluded dataset are cancelled even if a different project funded them.

Before erasing application content, `CheckpointRetention` captures exact project-owned DBOS run, task, repair, source, webhook and deletion workflow IDs into durable cleanup receipts. Undispatched generation/source jobs are suppressed. Cleanup uses the public DBOS client to inspect metadata with input/output loading disabled, follows recorded child and fork relationships, protects active ancestors, and checks the entire family twice before deleting. Unknown states, ownership/shard mismatches, unavailable DBOS and families exceeding the bounded 1,000-workflow scan remain pending. Recently cancelled workflows receive an additional maximum execution-time grace period so in-flight steps can unwind. Only proved terminal families are removed, with `delete_children=False` to avoid deleting unexamined descendants.

`purge_project` returns `checkpoint_cleanup_pending` until system-database erasure is verified; the existing hourly retention worker resumes the durable cleanup receipts. This includes the deletion workflow itself, which cannot be erased while it is executing. The project becomes `purged` only after application cleanup and all captured checkpoint cleanup receipts complete. Command receipts, project tombstones and operational cancellation records remain to prevent accidental replay. Terminal-workflow administrative replay/resume must be excluded during cleanup; the DBOS public deletion API has no atomic conditional-status argument, so the application rechecks immediately before deletion and never exposes a replay route for deleted projects.

Minimal project tombstones, financial records and dataset/model lineage remain. Already-trained remote weights and checkpoints require operator handling according to the retained model lineage; this application does not claim model unlearning. The AWS registry immediately retires affected models so they cannot be newly dispatched. Native job termination is independently observed by the cancellation worker. Cleanup retains the pinned cluster, reservation settlement and termination metadata until that observation; it removes input bodies and log pointers without prematurely releasing native capacity.

Run retention on a scheduled trusted worker:

```sh
cd backend
.venv/bin/python -m kleos.maintenance retention --batch-size 250
```

Each pass bounds detailed-record and event batches. AI transcripts, terminal execution logs and custom inference result captures expire after the configured 30 days. Completed DBOS checkpoint families are eligible 30 days after actual completion, including terminal child/fork checkpoints; active or recently completed ancestors/descendants keep the family available for recovery. The scanner uses recorded outbox workflow ownership and a persisted paging cursor. Structured output still needed by an active workflow remains available for durable recovery. Accepted artifact files do not expire automatically. Events older than 90 days move to an immutable archive containing metadata only, with one-year expiry measured from the original event. `event_floor` advances so reconnecting clients receive `410 cursor_expired` and load a new workspace snapshot. Dataset consent scans use a persisted paging cursor and remove derived training copies when consent is withdrawn or changed.

Archive receipts and maintenance cursors use already-delivered outbox records, so maintenance metadata never blocks the application dispatcher. The maintenance role needs scoped S3 `ListBucketVersions` and `DeleteObjectVersion` permissions; a normal delete marker does not count as erasure. S3 backups, DB backups, external provider retention, logs in other accounts and trained weights must follow the operator's documented deletion/restore policy; restoring a backup must reapply retained tombstones before enabling dispatch.

## Runtime verification

`backend/tests/test_runtime_integrations.py` is skipped by default. It reads local service configuration from `backend/.env`, creates a uniquely named PostgreSQL schema and Qdrant collection, and deletes only those test resources on exit. Its FastAPI requests use the production application through an in-process HTTP transport with actual development-token and organization authorization; it does not claim to test a deployed password-auth session or network load balancer.

```sh
cd backend
KLEOS_TEST_RUNTIME_INTEGRATIONS=1 .venv/bin/pytest -q tests/test_runtime_integrations.py
```

Add `KLEOS_TEST_LIVE_EMBEDDINGS=1` only when two small paid Gemini embedding invocations are authorized. That test permits at most two inputs under 500 characters, reserves $0.01 per invocation, and uses isolated $0.25 project/daily allowances. SDK transport retries remain bounded to three. Repeated retrieval reuses the recorded query vector. Allowance debits are conservative accounting values, not provider invoices or credit balances.

On 2026-10-30, the live embedding check passed with `gemini-embedding-2`, real PostgreSQL and server Qdrant: 768-dimensional document/query vectors, retrieval, cross-tenant rejection before inference, immediate revocation enforcement, cached-query reuse, point deletion, and reservation settlement. Public HTML and PDF fetch/parsing passed. OctoPrint and Moonraker checks used actual localhost HTTP simulator sockets and the authenticated telemetry API; all printer requests were GETs. Signed webhook deduplication, synthetic provider observations, blocked live connectors, unavailable DigiKey/Modal behavior, consent exclusion, transcript/event retention and explicit project/orphan cleanup were also exercised. A separate opt-in PostgreSQL/DBOS test executed a synthetic parent/child workflow with a real stored context checkpoint, then verified terminal workflow and step-history erasure through the public DBOS APIs. Default tests cover active ancestors/descendants, forks, cancellation settling, race rechecks and deferred cleanup. Digital release records in lifecycle tests are labeled fixtures; these checks do not establish engineering-tool qualification, physical acceptance, training quality, real printer interoperability, or production restoration readiness.

`KLEOS_TEST_HTTP_RUNTIME=1 .venv/bin/pytest -q tests/test_http_runtime.py` runs a separate no-paid acceptance check against the actual local Docker API on port 8000. It reads the development token from `.data/local-dev-token` without reporting credentials, creates one labeled fixture project, checks authenticated commands, model-policy versions, consent, artifact transfers/integrity, ordered HTTP SSE/reconnect, unavailable services and live-operation gates, then deletes only that fixture. It verifies immediate authorization denial and eventual application/checkpoint erasure, using a fixture-scoped checkpoint sweep if needed. Replaying the original create command after erasure must return `410 project_deleted` without recreating a project. On 2026-10-30 this test passed all eight check groups in 3.83 seconds, with zero project runs, model invocations or external intents. Evidence is saved in `backend/verification/http-runtime.json`; this is actual Docker HTTP evidence, distinct from the in-process integration suite. Password-auth production sign-in and real external service availability remain outside this local check.

`KLEOS_TEST_BACKUP_RESTORE=1 .venv/bin/pytest -q tests/test_backup_restore_runtime.py` verifies the local application database backup while source services remain online. It shares an exported read-only PostgreSQL snapshot with `pg_dump`, fingerprints selected application tables, copies every referenced Artifact-table file into a private backup directory, and restores into a uniquely named disposable database. The restored database is read-only and receives no workers or dispatchers. The check compares restored records and artifact hashes, verifies that writes are rejected, observes unchanged pending state, and drops only the disposable database. Dumps, manifests and copied files remain under `.data/recovery_checks` with directory mode `0700` and file mode `0600`.

On 2026-10-30, that check passed in 3.22 seconds: all 17 checked tables matched the snapshot, and 43 artifact references covering 42 unique files passed SHA-256 and size validation. One pending model invocation remained frozen. There were no nonterminal external intents or pending outbox records in that snapshot, so their active recovery/reconciliation case was not exercised. Evidence is in `backend/verification/backup-restore-runtime.json`. This verifies local application data and Artifact-table files; it does not establish DBOS database recovery, complete transcript/log object recovery, production external-intent reconciliation, Qdrant restoration, or AWS RDS/S3 disaster recovery. Separate Qdrant indexing, retrieval and cached-query checks are recorded in `backend/verification/integrations-runtime.json`.
