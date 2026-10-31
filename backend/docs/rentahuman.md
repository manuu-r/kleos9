# RentAHuman.ai integration

The adapter supports public worker discovery, local task drafts, provider-side
**dry-run** previews and observations from an existing owned bounty. It uses the
documented [REST API](https://rentahuman.ai/docs), without installing an MCP
package or exposing provider keys to models, native images or Flutter.

Live hiring, messages, worker selection, escrow, payment, provider cancellation
and physical acceptance are not enabled. The existing simulated human tasks and
portal remain intact. A provider's completed/approved status is an observation,
not a Kleos acceptance check. New live effects require a separately reviewed
authorization, budget and reconciliation flow; adding a key does not enable them.

## What to create and configure

Public discovery needs no RentAHuman account. Account previews and owned-job
observations need:

1. An organization-owned [RentAHuman.ai account](https://rentahuman.ai).
2. An account API key created at [Account → API Keys](https://rentahuman.ai/account/api-keys).
3. The Kleos organization ID, available from the authenticated organizations API.
4. This mapping installed in the **trusted API process's private environment**:

```dotenv
KLEOS_RENTAHUMAN_API_KEYS='{"<kleos-organization-id>":"<rentahuman-account-api-key>"}'
```

For local Compose, put it in the existing private `backend/.env`, preserving all
other settings, and recreate the API service to load the changed environment.
Do not paste keys into chat, project descriptions, frontend settings or Git.
Hosted installations need explicit secret injection into the trusted API runtime
through the operator's deployment configuration. The current ECS secret-key list
does **not** automatically forward this new setting; that wiring and rollout are
still required for hosted account features. This change deploys nothing and does
not modify infrastructure. Use a separate provider account/key per Kleos tenant;
there is no global fallback key. A configured key is not a verified connection.

Defaults are `{}`. Missing/invalid credentials yield explicit unavailable/auth
errors. Free directory search and local drafting still work. An outage never
substitutes fake people or marks a task completed. Ordinary tests use fixtures
and need neither credentials nor a local `.env`.

## Using the workbench

- **Connections → Human tasks** and **Human tasks** expose the real directory.
  Search is explicit and paginated, with skill, city, country and hourly budget.
  It sends search filters, not project files. The simulated task list stays labelled.
- In the project's **physical-check stage**, prepare a task against the current
  accepted digital release. Review/edit the instructions, required evidence,
  fixed USD worker budget, estimated hours and work location. In-person tasks
  require a city and country. Saving creates only a local durable draft.
- Review the saved task, then explicitly choose **Preview on RentAHuman** to
  share its text. The sole outbound POST is `/api/bounties` with `dryRun: true`
  forced by trusted code. Unknown extra fields, including caller `dryRun: false`,
  are rejected. The provider must confirm a dry-run response. Its funding total
  is USD; `perSeat` values are US cents. Missing estimates stay unknown.
- **Copy saved task details** and **Continue on RentAHuman** provide a manual
  handoff. Review fees, location, lead times, funding and workers on RentAHuman
  before independently choosing to post/hire/pay there. Kleos does none of those
  actions. No files, addresses, credentials or conversation messages are attached
  automatically; you control what goes into the reviewed text.
- After manually posting, **Load my RentAHuman jobs** lists account-owned jobs
  with pagination. Select and link one. The adapter verifies access through the
  provider's owner-only submissions endpoint before reading the listing, then
  records title/status and limited submission IDs/statuses. It does not download
  worker files, fetch signed links, track location or read private conversations.
- **Refresh provider status** polls only that explicitly linked job. An observation
  does not prove correspondence between a provider job and the approved device;
  the local link records the user's choice, not independent physical evidence.

Task drafts retain their exact release, revision, contract and content hash.
Changing instructions means saving a new draft with a new idempotency key.
Different inputs cannot reuse an existing key. Revision/release/accepted-artifact
changes invalidate current applicability; a discarded or stale draft cannot
publish a late provider response. Membership/write access is rechecked after
provider I/O. A linked bounty cannot silently be replaced by another bounty.

API cancellation discards only the **local draft**. It neither cancels a provider
job nor refunds/stops work; manage those effects directly on RentAHuman. There is
no automated job-posting or background polling daemon. Provider observations are
separate `ExternalIntent` receipts, with `authority: provider_observation` and
`physicalAcceptance: false`; they create no Validation or release acceptance.

## API surface

All routes require normal Kleos organization authentication. Draft writes also
require current project write access; provider actions carry `draftSha256` to
identify the exact saved task being shared or linked.

| Route under `/api/v1` | Behavior |
|---|---|
| `GET /rentahuman/status` | Safe configuration/capability status; no key values |
| `GET /rentahuman/humans` | Bounded public search; `skill`, optional city/country/maxRate/cursor, limit ≤20 |
| `GET /rentahuman/bounties` | Always `mine=true`; requires the current organization's key |
| `GET/POST /projects/{id}/human-task-drafts` | Current drafts / local save with `Idempotency-Key` and accepted release |
| `DELETE /projects/{id}/human-task-drafts/{draftId}` | Local cancellation only |
| `POST .../human-task-drafts/{draftId}/preview` | Noncommitting provider preview of the saved text |
| `POST .../human-task-drafts/{draftId}/link` | Explicit `bountyId`, owner-authorized observations only |
| `POST .../human-task-drafts/{draftId}/sync` | Refresh the existing link, no provider mutation |

The upstream origin is fixed to `https://rentahuman.ai/api`. Each request uses the
existing public HTTPS/DNS-pinned fetch boundary, a 12-second deadline and 500 KB
response bound. Redirects are refused, including GET redirects, to prevent key
forwarding. Responses are allowlisted/redacted, non-finite numbers are excluded,
and raw provider errors/credentials are not returned. Retrying a preview or
observation is safe to repeat because it performs no committing provider action.
Authenticated API contract drift fails visibly rather than assuming success.

The [official webhook protocol](https://rentahuman.ai/docs/webhooks) uses different
headers/signatures from Kleos's generic connector webhook. This adapter does not
register or ingest those webhooks; polling is explicit. Do not configure a
RentAHuman webhook against `/api/v1/webhooks/{id}` expecting compatibility.

See [scoped verification](../verification/rentahuman-integration.md). Only public
search was probed live; real authenticated preview/ownership/status qualification
requires your account/key and an existing job you choose to link. No wallet
funding is required to test discovery or documented dry-run previews.
