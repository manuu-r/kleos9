# Modal/Muse branch review — 31 October 2026

Reviewed `dev/meta-modal-add` at `7479a1e`, against `main` at `055f520`.
This review preserves the new Muse/Modal defaults, Gemini selection, and existing
simulated services and fulfillment. No AWS deployment or live physical operation
was performed.

## Findings fixed

1. **A late 429 could replay billed work.** `run_agent` restarted the complete
   Pydantic agent after any rate limit, even when an earlier response had already
   triggered a structured-output repair. That could reset usage limits, repeat
   inference and then switch providers. Retries and capacity fallback now stop
   once a model response exists. The existing uncertain-invocation reconciliation
   path handles the interrupted attempt. One deadline covers waits and fallback,
   rather than starting a fresh deadline for every agent run.
2. **Provider controls used only model names as identity.** Configuring the same
   model identifier on two endpoints could create duplicate widget keys or leave
   a dropdown showing the old provider. Keys now contain provider and model.
   Identical Meta reasoning/code aliases also register only one agent and catalog
   entry, while retaining the configured lane mapping.
3. **Unit fixtures loaded the developer's `.env`.** Adding local Modal credentials
   changed fixture policies and broke two historical migration tests. The platform
   fixture now uses explicit fake credentials and isolated settings. Provider SDK
   tests block network access across the entire module.
4. **The HTTP acceptance test assumed development always required a token.** It
   now verifies the explicitly configured no-login development behavior and works
   without a token file in that mode. Password/tenant authorization tests remain
   separate and passing. Production authentication code was not changed.
5. **Fallback needed a dedicated activity event.** Successful allowed switches now
   emit `model.providerFallback`, in addition to preserving `fallbackFrom` usage
   and actual provider/model artifact provenance. The project's selected policy
   remains unchanged for its next attempt.

The UI fixtures now reflect the actual catalog: Modal cannot handle grounded
research and therefore cannot be selected as an all-lanes preset. Research keeps
a search-capable provider.

## Verification

- Backend: **712 passed, 9 optional tests skipped**, including disposable
  PostgreSQL migration, concurrency and recovery checks.
- Flutter: **181 passed, 3 optional tests skipped**; static analysis and generated
  transport checks pass. Production web build succeeds.
- Existing simulation backend: **15 passed**; no simulation implementation changed.
- SDK-fixture flow: Modal capture, saved answer, Meta grounded research, explicit
  confirmation/frozen design and deletion pass with both direct Modal responses
  and an initial-capacity Meta fallback. These tests block external network calls
  and create no native jobs.
- Actual local Docker HTTP flow: **1 passed**, with no paid calls. It checks
  idempotency, policy versioning, tenant denial, artifacts, SSE reconnection,
  release gates and scoped deletion/checkpoint cleanup.
- Actual desktop and 390×844 browser layouts render Spark, Wonder, Modal and Gemini
  without runtime errors or horizontal overflow; no project mutation or model call.
- Repository lint, OpenAPI export, Alembic schema check, Terraform formatting and
  read-only Terraform validation pass.

Evidence: [HTTP acceptance](../../verification/http-runtime.json),
[live provider checks](../../verification/provider-branch-smoke.json),
[browser checks](../../verification/provider-branch-browser.json).

## AI usage and qualification limits

Live checks requested only a tiny JSON connectivity response from each configured
model. Four initial requests were capped at 128 output tokens. Two responses did
not satisfy that small contract and received one explicit bounded recheck each,
capped at 512 tokens. All four models passed. There were six requests in total,
no automatic retries, no search, and no generated CAD, PCB, application, firmware
or image. The configured aggregate output ceiling was 1,536 tokens, including
reasoning. Model API reasoning consumes output budget; see the
[Meta reasoning documentation](https://dev.meta.ai/docs/reasoning).

The four successful checks have a combined token-cost estimate of **$0.0027335**.
Usage/cost for the two initial failed checks was unavailable and is explicitly
excluded from that estimate. This is a connectivity test, not engineering model
qualification or a complete live manufacturing run. Production uses its pinned
dependency lock; live smoke estimates used local `genai-prices` 0.2.1, while the
Docker image retains the committed 0.2.0 lock.
