# Human work

`ExternalRequest(kind="human_work", work_order=...)` accepts a focused procedure,
specimen identity, evidence requirements and optional measurement ranges, plus
exact project artifact hashes. It does not require a completed product or release.
Discovery and drafting do not hire, message or fund anyone.

`RentAHumanDiscovery(api_key=SecretStr(...) | None)` uses the official
[RentAHuman REST documentation](https://rentahuman.ai/docs), checked 2026-11-02.
GET `/api/humans` discovers profiles with bounded skill/location filters. Optional
credentials are injected by server configuration. Public profile fields remain
untrusted, listed hourly rates are not binding quotes, and pagination is explicit.
An owner can link existing work through `jobs.observe_existing(actor, job_id,
external_id, key)`; the provider must verify ownership before this local link is
saved. For a linked identity, authenticated GET of the owner-protected
bounty submissions route observes submission summaries. An approved submission
alone cannot establish bounty completion, payment, or physical acceptance.
There is no posting, direct messaging, hiring, funding or payment implementation.
No live RentAHuman calls were made during development.

`HumanEvidence(jobs)` exposes `submit(actor, job_id, EvidenceSubmission, key)`,
`review(actor, job_id, evidence_id, ReviewRequest, key)` and scoped `list`.
Submissions must match the exact work-order specimen, revision and artifact
bindings. Every requested evidence category and reading needs a source artifact;
readings include finite values, units, instrument, method and timezone-aware time.
Registered project artifact hashes are rechecked during submission and review.
Readings outside requested ranges produce FAIL; undefined ranges and synthetic
observations stay UNKNOWN. These checks describe submitted readings, not verified
physical device behavior.

Only a different project owner can review. Review decisions are immutable;
changes require a new submission. Acceptance rejects synthetic, failed or unknown
measurements. Independent local review never releases a payment or grants physical
acceptance. Tables `ExternalJob` and `HumanEvidenceRecord` use the shared fresh
SQLAlchemy metadata; startup migrations must import both model modules.

The `api.external.ExternalFacade` preserves the existing draft, search, job and
review response shapes. Local drafting can omit a release association for focused
work; a supplied release must belong to the current project. Every job URL is
bound to its project before reads, linking, cancellation or evidence handling.
The frozen evidence endpoint always records `evidenceKind: synthetic`; it cannot
promote these submissions to physical acceptance. Its `specimen` object supplies
`id`, exact `artifactBindings` and optional `measurementProvenance` by criterion.
Each measurement's provenance requires instrument, method, source artifact and
observation time. `eventKey` deduplicates evidence even across different HTTP
idempotency keys. Reviews use a separate authorized owner.

Owned bounty listing explicitly sends `mine=true`; clients cannot substitute an
owner selector. Search preserves bounded pagination, rate and location filters.
The provider-preview route returns an explicit unavailable error because this
installation is GET-only. No local draft is misrepresented as a provider preview.
