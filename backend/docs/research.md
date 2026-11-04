# Read-only project research

`project.research` accepts the existing `{projectId}` payload. It asks the dynamic
orchestrator for web evidence relevant to the current brief and exact project
context. It returns `web_research_unavailable` when no qualified web provider is
installed; project-source inspection is never silently substituted. A caller can
explicitly select `scope: "project_sources"` and an optional bounded `objective`.

Admission authorizes the actor, checks the UI revision, prevents overlapping
active objectives, saves an idempotency receipt and pins the installed read-only
research tools. It advances only the UI revision: source/context revisions,
uploaded images, approved design, interfaces and previous native candidates stay
intact. Existing candidates remain eligible for independent checking. No fixed
four-track device pipeline is imposed.

The internal source tool reads only assigned authorized artifacts, verifies their
size/hash, rejects binary content and bounds bytes and returned excerpts. Literal
search offsets refer to the original text. Skipped sources are explicit coverage
blockers. A successful read establishes only that text was retrieved.

`ResearchRepository` requires matching completed tool receipts before publishing
successful task results, and the final graph completion check requires actual
retrieval evidence for the requested scope. A source receipt cannot complete a
web request. Findings remain candidate evidence in `state.research`; actual public
summaries and source URLs/hashes are also appended to the existing workbench
conversation. Capture proposals, accepted requirements and source manifests are
not rewritten. Legacy feasibility-track completion percentages are not fabricated.

## Optional SearXNG installation

SearXNG exposes JSON search through its documented HTTP search endpoint. JSON
must be enabled on the chosen instance; many public services disable it.
The adapter uses one bounded GET per request, a fixed allowlisted HTTPS `/search`
URL, a pinned engine selection and no redirects. It never opens returned source
URLs. It returns title/snippet/URL candidates, so a citation does **not** imply
that the source page was fetched or that its claims are independently validated.
See the [official search API](https://docs.searxng.org/dev/search_api.html).

Only an explicitly operated instance configured without per-query fees is
supported. Operator deployment qualification must verify its engines, plugins,
network access and billing policy; this repository does not configure or qualify
a live instance. Query engine selectors/redirect bangs are rejected so user/model
queries cannot choose other engines. See the documented
[search syntax](https://docs.searxng.org/user/search-syntax.html).

The operator setting is `web_search_installation`, with exactly:

```json
{
  "provider": "searxng",
  "config": {
    "endpoint": "https://operator-search.example/search",
    "allowedHost": "operator-search.example",
    "version": "operator-pinned-version",
    "billing": "self_hosted_no_query_fees",
    "engines": ["wikipedia"],
    "qualified": true,
    "maxRequestsPerRun": 8,
    "timeoutSeconds": 20,
    "maxResponseBytes": 262144,
    "maxResults": 8
  },
  "qualificationReference": "/absolute/path/to/reviewed-report.json"
}
```

This is a configuration example, not a working endpoint or qualification claim.
The setting defaults to absent. The qualification loader requires matching
source hashes, named passing checks and independent review metadata. A report
marked `testOnly: true` is accepted only in the test environment. Other environments
require separate `operator-instance-read-only` evidence bound to the exact
installation configuration. Changing tool source/configuration changes its pin.

Request allowances are reserved durably on the original run, including failures
and unknown outcomes. A completed action replays its saved result; changed input
under the same action ID conflicts. An uncertain action is not silently repeated.
Cancellation, membership, context and worker ownership are checked before IO and
again before publication. Responses have bounded size/time/result count; compressed
responses are rejected before reading. Raw provider diagnostics are not retained.
Partial or empty evidence blocks completion. Subsequent model synthesis still
uses the existing pinned provider policy and model spend ledger.

## Why Meta built-in search is not enabled yet

Meta's documented search grounding uses the Responses/Messages APIs, not the
existing Chat Completions path. A response can skip searching even when the tool
is enabled, so callers must inspect actual search calls and URL annotations.
See [Meta search grounding](https://dev.meta.ai/docs/search-grounding).

The [Responses schema](https://dev.meta.ai/docs/api-reference/responses/schemas)
documents `max_tool_calls`, but a search action can contain multiple `queries`.
Meta's [pricing](https://dev.meta.ai/docs/pricing-rate-limits) charges $2.50 per
1,000 search queries in addition to tokens (checked 2026-11-03). A bound on tool
calls therefore does not establish a hard bound on billable queries. No documented
per-action query maximum was found. The adapter must remain unavailable until a
provider-enforced bound and corresponding spend reservation can be qualified;
an invented multiplier or prompt instruction is insufficient. No paid probe was
made and no new silent provider fallback was added.

## Verification scope

Focused tests cover actual authorized source bytes, HTTP-mocked SearXNG requests,
bounded/redacted failures, query selectors, request replay/uncertainty, preserved
candidate checking, exact scope receipts and the real API/worker/model harness
with mocked model responses. These tests establish adapter behavior only. They
do not establish live search availability, physical feasibility, component
availability, engineering validity or accepted release authority.
