# Initial capture design research

1 November 2026, feature/ngspice-simulation, based on merged main at `bdf00cd`.

New initial capture attempts pin the project's selected research model before
invocation reservation. They remain coordination tasks in the product journey,
but use the configured search-capable research provider/model; no new provider
or fallback is invented. Context/manifest records the selected policy lane and
`initial-design-research-v1` contract. Existing running attempts retain their
provider/context. Follow-up preference capture turns retain coordination and
reuse existing findings rather than repeating initial searches.

The planning agent receives native web search with a low search context and low
reasoning effort. It is instructed to make 1–2 focused primary-source lookups,
apply practical power/component-space/access/mounting/sensor/ventilation facts
to the first scene, state assumptions for sparse briefs, and ask only important
choices. This is an initial concept pass, not a BOM or detailed engineering.
Search count is a target; existing cumulative spend, request, token and deadline
limits remain the hard bounds. At most three distinct source URLs are retained
and collected through the existing tenant/project-scoped research service.

If no native search is observed, one existing bounded output-validation retry
asks for it. If still unobserved, sources are cleared and an explicit ungrounded
research limitation is added. Invocation usage records observed search calls
and whether grounding was observed, never private reasoning. Initial grounding
does not mark feasibility complete or bypass user confirmation/release gates.

Actual verification:

- `ruff check kleos tests`: PASS.
- `python -m pytest -q -p no:cacheprovider`: **1113 passed, 29 skipped**.
- New adapter/lifecycle cases verify Meta low reasoning + native search, Gemini
  LOW thinking + Google search, missing grounding correction/disclosure, initial
  versus continuation routing, attempt pinning across policy changes, and a
  minimal brief's citations/source records saved before clarification completes.
- Existing Modal rate-limit fallback lifecycle coverage is retained using a
  genuinely legacy pinned capture context; new capture does not route to Modal.
- `git diff --check`: PASS. No API/schema, Flutter or dependency changes.

All provider replies/search metadata and source collection in these tests were
fixtures, with provider networking forbidden. No paid inference, real internet
research, deployment or physical validation was run. These checks establish the
execution/settings/lineage contract, not subjective design quality or current
hosted-provider availability. A live minimal-brief evaluation remains a separate
credit-spending qualification, with the configured project research model.
