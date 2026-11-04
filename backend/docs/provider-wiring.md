# Existing Meta and Modal settings

The replacement uses the existing names without rewriting credentials:

| Existing setting | Purpose |
| --- | --- |
| `KLEOS_META_API_KEY` | Meta Model API bearer credential |
| `KLEOS_MODAL_LLM_KEY` | Existing combined Modal proxy bearer credential |
| `KLEOS_MODAL_LLM_URL` | Existing deployed Modal endpoint |

The process environment takes precedence over `backend/.env`, whose absolute
location is resolved from the installed source rather than the current directory.
Only these three provider values (`KLEOS_META_API_KEY`, `KLEOS_MODAL_API_KEY`/`KLEOS_MODAL_LLM_KEY`,
`KLEOS_MODAL_BASE_URL`/`KLEOS_MODAL_LLM_URL`) are selected from that file. Database URLs, authentication modes, execution settings and
physical policies are not imported from it. Explicit constructor settings win;
`_env_file=None` disables file loading, and test-mode settings ignore private files.
The Modal names ending in `_LLM_KEY`/`_LLM_URL` remain accepted aliases; no renaming is necessary.

When no explicit model catalog is supplied, configured Meta credentials install
`meta-muse-spark` → `muse-spark-1.3` at `https://api.meta.ai/v1`. A configured Modal
URL/key pair installs `modal-muse-spark-open` →
`meta-models/Muse-Spark-Open-816B-A42B`; the URL receives `/v1` only when absent.
Both installations support images and typed function tools. Meta Spark is the
text/vision default when both providers are configured. With only Modal configured,
Modal is the default. Existing provider controls can select either installation.
An explicit `KLEOS_MODEL_CATALOG`, text/vision selection or per-project lane choice
takes precedence. An explicit empty catalog remains disabled.

These names and capabilities follow the official [Meta model catalog](https://dev.meta.ai/docs/models),
[Meta Chat Completions protocol](https://dev.meta.ai/docs/protocols/chat-completions),
[Meta vision inputs](https://dev.meta.ai/docs/cookbook/vision-input),
[Modal Muse model](https://modal.com/library/meta/muse-spark-open) and
[Modal endpoint authentication](https://modal.com/docs/guide/webhook-proxy-auth).
They use Standard mode, not a Contributor/training opt-in. Presets conservatively
bound inputs to 131,072 tokens and outputs to 16,384 tokens. Price estimates use
$1.25 input/$4.25 output per million tokens, without claiming cached discounts;
see [Meta pricing](https://dev.meta.ai/docs/pricing-rate-limits) and the Modal model page.
The explicit catalog can pin different qualified deployments and limits.

Per-attempt model/tool/configuration pins and spend receipts remain unchanged.
There is no implicit provider retry or switch. The existing explicit
`KLEOS_MODAL_RATE_LIMIT_FALLBACK_KEY` policy still permits a single recorded
Modal-429 fallback, preserving the same run call/spend limits. Configure it as
`meta-muse-spark` only when that behavior is intended and Meta is available.

## Scoped verification, 3 November 2026

Read-only `/v1/models` requests using the preserved local credentials returned
HTTP 200 from both services. Meta advertised `muse-spark-1.3`; the existing Modal
endpoint advertised `meta-models/Muse-Spark-Open-816B-A42B` as its sole model.
No credentials or private endpoint URL were logged or rewritten. No inference,
GPU deployment, purchasing or physical operation was started by those checks.

The listing proves authentication and advertised model availability at that time.
Mocked HTTP fixtures separately test image/structured-output request construction,
runtime routing, recorded fallback and allowance behavior. Neither check proves
live generation quality or arbitrary engineering correctness. Source/profile
changes and provider changes require verification of the relevant boundary again.
