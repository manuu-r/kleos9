# Engineering response budgets and streaming

Engineering lanes use `KLEOS_ENGINEERING_MAX_OUTPUT_TOKENS=32768` per response and
`KLEOS_ENGINEERING_MODEL_TIMEOUT_SECONDS=600` for the complete invocation,
including tools, initial capacity retries and an explicitly configured fallback.
Planning retains its separate 5,000-token output ceiling and 180-second deadline.
Coordination/research/review retain their existing non-engineering settings,
except new initial capture attempts request low reasoning effort.
Session deadlines and cumulative request/token/spend ceilings remain additional
bounds; development project usage can still be unlimited.

Initial capture pins the project's search-capable research policy before reserving
an invocation, rather than selecting a provider silently at execution time. Its
context/manifest records `modelPolicyLane: research` and the versioned
`captureResearch` contract. Existing resumed attempts keep their original choice
and context; following preference turns use coordination and reuse saved facts.
Meta/Gemini native search supplies early design references, not engineering or
physical evidence. Search fees/usage remain in the existing invocation accounting.
The native search tool receives a small context request; a 1–2 search target is
prompt guidance, not a claim that every provider offers a hard query limit.
Observed native search calls are recorded; if no search occurs, one existing
output-validation retry requests it. A still-ungrounded result has citations
removed and an explicit assumption limitation. Up to three capture URLs are
collected through the existing scoped research collector. Later feasibility
research and confirmation gates are unchanged; private reasoning is not stored.

`KLEOS_ENGINEERING_STREAMING=true` consumes complete provider streams through
Pydantic AI. This avoids Meta's non-streaming server time limit. Completed typed
outputs and final usage are recorded durably; partial text is never an artifact
or acceptance evidence. Private reasoning is not copied into product events.
An interrupted response keeps an uncertain billing outcome and does not silently
retry or fall back after a stream opens. Engineering/session DBOS model steps do
not automatically retry failed streams. Successful recorded responses are reused
on recovery. An unrecorded response remains subject to provider billing ambiguity.

Reasoning consumes the same output-token budget as visible text, as documented
by [Meta](https://dev.meta.ai/docs/reasoning). Streaming and the non-streaming
server limit are documented in the [Responses API](https://dev.meta.ai/docs/protocols/responses).

`tools/check_long_model_runtime.py --live` explicitly sends one bounded streamed
JSON request to Wonder and one to Modal (4,096 tokens each, no retries/fallback).
`verification/long-model-runtime.json` records latency, stream events, token usage
and estimated cost without credentials or response bodies. The synthetic SDK
suite verifies output above the old 12,000-token ceiling and total deadlines.
The bounded live probe is not a full 32,768-token stress test or a ten-minute soak.
