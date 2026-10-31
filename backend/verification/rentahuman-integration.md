# RentAHuman.ai: scoped integration verification

Date: 1 November 2026. Branch: `feature/ngspice-simulation`.

Implemented: real public search; organization-scoped credential configuration;
release-bound local task drafts; forced noncommitting provider previews; manual
operator handoff; and owned-bounty/submission status observations. Existing
simulated human tasks, portal and physical acceptance remain unchanged. No live
job posting, hiring, messaging, escrow, payment, worker tracking, deployment,
Terraform or paid inference occurred.

## What actually ran

| Check | Result and scope |
|---|---|
| Backend full suite | **1169 passed, 29 skipped** |
| Focused new backend regressions | **26 passed**, fixture provider transport, real API/SQLite transitions |
| Ruff | `ruff check .` from backend: PASS |
| Flutter analysis | PASS |
| Flutter full suite | **245 passed, 3 skipped**, including 10 new RentAHuman DTO/widget cases |
| Mock backend | **15 passed**; simulated journey preserved |
| API/Dart contract | Exported OpenAPI, regenerated DTOs, `generate_transport.py --check`: PASS |
| Real public API probe | **PASS**: one anonymous HTTPS directory search; zero matching profiles, no personal data retained |
| Diff/docs/data | Whitespace, local documentation links and JSON: PASS |

Tests needed no local `.env` or provider key. Default skips cover opted-in live,
PostgreSQL/recovery/native image tests, not universal qualification. Warnings
remain the existing local Qdrant index/model-pricing warnings and the mock suite's
asyncio fixture-scope warning. Flutter 3.47.8 / Dart 3.13.7 were used.

The [public probe record](rentahuman-public-probe.json) describes the one live
request. It ran the actual DNS-pinned public HTTPS client without credentials,
using a one-result `electronics assembly` search. The result was an empty valid
page, not proof that no suitable workers exist. No names, profiles, addresses or
provider response bodies were retained. No authenticated endpoint was probed.

## What used fixtures

The accepted digital release/artifact, task text, worker, job and submission data
are test fixtures. API requests, local persistence, idempotency checks, permissions,
revision/release/contract/artifact gates and event records ran against the actual
application with SQLite. Provider responses were injected; no private account,
real job, person, wallet or worker evidence was accessed.

Tests establish these implementation behaviors:

- Only an accepted current release can support a draft. Task inputs are bounded,
  finite, explicit about price/hours/evidence and location; extra commit flags
  are rejected. Saving is local, idempotent and distinct from posting.
- Preview body is precisely the reviewed task plus trusted `dryRun: true`.
  Provider confirmation must explicitly report a dry-run. A key is necessary
  for account actions; organization B never inherits organization A's key.
- Redirects are blocked before forwarding credentials; destination/response bounds
  use the existing restricted fetcher. Public outputs exclude email, addresses,
  wallets and invalid numbers. Provider failures do not become synthetic success.
- Linking first calls the owner-only submissions endpoint. Reading a public
  listing cannot establish ownership. Only submission IDs/statuses are retained;
  no worker files or arbitrary links are fetched. Completed/approved observations
  create no Validation and do not set physical acceptance.
- Changed revision, release, contract, accepted artifact, cancelled draft or
  revoked membership blocks late-response publication. A link cannot be silently
  replaced by another bounty. Local cancellation sends no provider request.
- UI tests cover explicit/paginated public search, visible outages, stable local
  retry keys, no guessed budget, missing-key/release guards, explicit preview/link
  actions, read-only viewers and phone widths 320/390 plus desktop 1000 pixels.

The transport generator now emits null-aware serialization for optional lists
and nested DTOs; a regression checks omitted defaults and populated nested
values. Generated models were regenerated, not edited by hand.

## Research and remaining qualification

Primary references reviewed:

- [Canonical RentAHuman docs](https://rentahuman.ai/docs): REST base, API-key
  header, public directory, owner job listing, fixed-price/estimated-hours
  inputs, dry-run funding preview, and owner-only submission reads.
- [Account API keys](https://rentahuman.ai/account/api-keys): operator credential
  creation; account keys identify the caller, never body-supplied owner IDs.
- [Webhook docs](https://rentahuman.ai/docs/webhooks): vendor-specific raw-body
  HMAC and headers. These are not the existing Kleos generic webhook contract;
  registration/ingestion is not implemented in this adapter.

The API is unversioned. Its documented current schemas were implemented with
visible failure on unsupported private responses; no npm MCP dependency was
installed. A real authenticated preview and owned-job/submission read are
**NOT RUN** because no account/key or operator-chosen job was available. The
fixture quote is not a real marketplace price, fee guarantee or charge. Live
profile normalization remains only partially exercised because the public probe
returned no profiles. Rate limits, account permissions and private schema
compatibility still need account-specific qualification.

To finish account qualification, create an organization account/key, install
`KLEOS_RENTAHUMAN_API_KEYS` privately in the trusted API environment, verify a
reviewed dry-run, and explicitly link an existing owned job for read-only status.
Hosted ECS secret injection is not automatically wired by this change; operators
must add it through their normal deployment path. No deployment was performed.
There is no enabled Kleos live-posting/payment path. Keep all real commitments
on RentAHuman until a separately authorized physical-work flow is implemented.

[Setup and operation guide](../docs/rentahuman.md).
