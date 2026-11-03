# Sourcing and external jobs

`ExternalJobs` in `capabilities.external` persists project/revision-scoped requests,
preparation observations, owner approvals, deterministic action identities and
provider observations. A BOM can describe a single missing component; no release
or fixed product journey is required. The service authorizes every read/write via
`ProjectStore`. Provider installations are keyed by `(tenant_id, user_id,
provider_id)` and remain bound to the job creator's account and provider version.
Empty configuration produces `unavailable`, never simulated supplier results.

`create(actor, ExternalRequest, key)`, `approve(actor, job_id, key)` and
`cancel(actor, job_id, key)` are idempotent commands. `get/list` are scoped reads.
Async `discover/quote/draft` persist bounded read-only preparation results.
Async `dispatch` durably claims one action before I/O. A lost response or a crash
requires `reconcile`; no uncertain action is repeated. Async `poll` returns the
persisted wait until its next poll time; deadlines stop normal polling without
inventing remote failure. Cancellation is local and does not claim an external
cancellation. Read-only reconciliation can still settle uncertain effects after
cancellation or expiry without reopening the local job.

The default effect policy blocks purchasing and paid hiring. Shipped providers
also reject submission. No production configuration enables the trusted effect
hook used by test fixtures. Future effect adapters need independent qualification
and must enforce the approved scope, currency and ceiling themselves. These
modules do not grant release or physical acceptance authority.

`DigiKeyCatalog(client_id=SecretStr(...), access_token=SecretStr(...))` performs
GET-only exact package SKU lookups using Product Information V4. Tokens come from
server configuration; OAuth acquisition/refresh is outside this adapter. Inject
`account_id=SecretStr(...)` when required by the account or two-legged OAuth. Up to
20 BOM items are observed per call, with exact package inventory and applicable
standard price breaks. Results are nonbinding and exclude shipping/tax. Missing
exact packages remain unknown. No purchase, reservation or order-status API is
implemented. Currency is explicitly USD for this installation.

All HTTP calls use fixed HTTPS origins, no ambient proxy/credentials, no redirects,
10-second socket timeouts, a 15-second total GET bound and a 256 KB response bound. The lifecycle bounds each operation
to 20 seconds. Credentials and upstream error bodies are never returned.

Provider protocol checked against the official
[DigiKey Product Information V4 schema](https://developer.digikey.com/node/2360/oas-download)
on 2026-11-02. Tests use `httpx.MockTransport` and explicit fixture providers only;
no live catalog, purchasing or paid inference was used for verification.

The API facade maps old preparation buttons to genuine objectives when no typed
BOM or work order exists. A supplied BOM uses the server-selected catalog for
read-only observations. Physical order, acceptance and fulfillment buttons remain
blocked. API responses do not manufacture legacy stage progress, orders or hires.
