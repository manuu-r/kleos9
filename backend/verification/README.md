# Local implementation and runtime verification

Verified on **30 October 2026** using the Gemini key in `backend/.env`. Credentials remain
server-side and are excluded from Git, Docker build context, Flutter bundles and native jobs.
This is local verification, not an AWS deployment or a complete smartwatch release.

Running endpoints:

- Kleos Flutter: <http://127.0.0.1:5180>
- API documentation: <http://127.0.0.1:8000/docs>
- Generated companion preview: <http://127.0.0.1:5181> (separate origin, restrictive network policy)
- Local development sign-in token: `backend/.data/local-dev-token`; do not use a provider key to sign in.

The Docker stack runs PostgreSQL, Qdrant, API, engineering/connector workers and an OTLP collector.
There is one API container with **two API processes**, two hosted-call slots and one native-job slot.
An unrelated existing local cluster was left running. Native jobs use separate containers with
bounded resources, no network, no provider/database credentials and no Docker socket.

**Final regression results:** 296 backend tests passed, 9 opt-in tests skipped; 82 Flutter tests
passed. The skipped live-service suites were exercised separately where configured, as recorded
below. One test-local Qdrant warning concerns unsupported in-memory payload indexes. Ruff,
Flutter analysis, generated OpenAPI/Dart consistency, frontend web build and Android debug build
passed. Terraform validation passed; no AWS resources were provisioned.

The principal generated project is `7d0bbc33-7b78-4fb1-866a-3ae2d8d23150`.

| Area | Actual runtime result |
|---|---|
| Gemini research and coordination | Direct hosted calls, grounded source records, clarification, confirmed requirements and shared interfaces |
| Mechanical generation | Real build123d STEP/STL exports; independent inspection measured the requested 40 × 30 × 10 mm solid, empty cavity and component clearance |
| Firmware | Real Zephyr `native_sim` compilation; compiled decoder symbol present; 30 independent C protocol vectors passed |
| Companion application | Actual isolated Flutter web build, downloadable web/source ZIPs and licensed bundled Roboto; 30 independent Dart protocol vectors passed |
| Electronics | Three actual generation/build attempts; atopile configuration errors were repaired, but a supported PCB-and-schematic closure was not produced; electronics and circuit correspondence remain `UNKNOWN` |
| Release gates | Release acceptance, ordering, physical acceptance and shipment all refused the incomplete closure; no release accepted |
| Rebuild/recovery | Saved sources rebuilt with a new pinned tool image, no model/embedding call, one native reservation and one publication; same isolated container survived a trusted-worker kill and explicit restart |
| Flutter browser flows | Gemini configuration, research progress, clarification, interface solution, provider changes, consent, downloads/hash checks, run inspection, AI transcripts, native logs, reconnect and battery invalidation |
| Retrieval | Actual Gemini embeddings and PostgreSQL/Qdrant indexing/query, cache reuse, scope denial, revocation and deletion |
| Connectors | Real restricted HTML/PDF fetches; authenticated HTTP bridge fixtures for OctoPrint/Moonraker; portal/webhook/simulated-fulfillment contracts and live-operation blocks |
| Backup/restore | Local repeatable-read PostgreSQL dump restored into a disposable read-only database; 17 tables and 43 artifact references/42 unique blobs matched the captured snapshot |
| Observation | Real API/worker/native spans reached the local collector; query/header canaries and configured secrets were absent from exported/access logs; detailed AI records remain in the application ledger |

The generated companion's five valid/invalid frame controls passed on desktop and a 390 × 844
responsive browser. Text is legible after the offline-font fix. A remaining `.AppleSystemUIFont`
ellipsis measurement attempts a Noto symbol fallback during mobile emulation; CSP blocks it and
no external request escapes. Current labels fit and displayed data is unaffected. This is not a
physical Android/iOS-device qualification. The immutable ZIP was not patched after publication.

The native recovery harness initially expected Docker's restart policy to handle a deliberate
CLI kill automatically. It was corrected to explicitly restart the worker; the same job was
reconciled without another build. Its original failed assertion is retained in local diagnostic
history, and the completed ledger/container/artifact checks are recorded separately.

[Final Docker load evidence](docker-load.json) covers 100 independent clients, 100 event streams,
10 temporary projects, 1,000 reads, 40 configuration commands and 300 delivered events, with tracing
enabled. **p95 reads 370 ms, command acceptance 21 ms, event delivery 493 ms**, with zero errors.
The first single-process reading was 677 ms; reducing repeated authorization/metadata reads improved
it to 558 ms. Adding a second API process met the 500 ms target. Both earlier measurements are retained.
Permissions are still freshly checked for every read; no authorization cache was introduced.
The final load test ran no provider or native work and does not prove ten concurrent engineering
projects, cloud network latency, provider quotas or production infrastructure capacity.

Evidence files:

- [Engineering checks and blocked release](engineering-runtime.json)
- [Native worker recovery and exact-source rebuild](native-recovery-runtime.json)
- [Final HTTP API checks](http-runtime.json)
- [Connectors, embeddings, consent and retention](integrations-runtime.json)
- [Local backup restoration](backup-restore-runtime.json)
- [Flutter and generated companion](flutter-runtime.json)
- [Trace/access-log privacy](observability-runtime.json)
- [Usage and configured allowances](usage-runtime.json)
- [Native images and qualification limits](../docker/QUALIFICATION.md)

The ledger contains 17 successful hosted generation calls with about **$0.72 in known token-price
estimates**. This is not an invoice or credit balance and may omit search/embedding charges; an
initial SDK smoke call was outside the project ledger. Conservative allowance debits are separate:
$38.16 of the configured $40 daily ceiling and $19.15 of the main project's $20 ceiling, with no
remaining reservations. These were the ceilings during verification. The daily ceiling was
subsequently disabled at the user's request; project ceilings remain independent.

Muse, DigiKey, Modal, Cognito, AWS and licensed Conductor were not live-account qualified;
their available contracts/guards were tested without claiming configured access. The 25 golden
briefs and provider SDK/failure matrix are deterministic regressions, not live model-quality
evaluations across 25 engineering journeys. Renode starts, but its ARM specimen hit its bounded
deadline; its qualification manifest remains false. Full electronics correspondence, required
peripheral simulation, complete smartwatch release/fulfillment, deployed disaster recovery and
ten-project engineering load remain release checks. Synthetic physical evidence cannot replace
those results. No live purchase, printing, shipping, training or customer-data promotion occurred.
