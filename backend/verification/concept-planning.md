# Conversational concept planning — 2026-10-30

Verified locally with the Docker backend, direct Gemini API, and release Flutter
web build. This check exercised concept planning only, without CAD or native jobs.

- Creation immediately supplied a provisional handheld scene at capture.
- Gemini asked two guided questions. Saving their answers automatically resumed
  planning and ran feasibility research, without a manual resume/review command.
- Feasibility produced version 4 with nine assemblies, including an isolated
  sensor chamber and thermal barrier. No engineering artifacts were generated.
- A message sent through Flutter selected the main enclosure and requested 20%
  more width. The durable message pinned part `body` and concept version 4.
- The automatic review published version 5: width changed from 2.6 to 3.12
  relative units, with height/depth preserved. No additional research was required
  for this visual preference, and engineering remained unconfirmed.
- Desktop and 390 × 844 browser checks passed assembled/exploded views, zoom,
  reset, part selection, live scene refresh and the preserved simulation sections.
- Chat history stays bounded on screen and follows new replies. Earlier messages
  remain accessible. Component tests cover sending while planning, draft retention
  after errors, duplicate-send prevention and automatic answer command payloads.
- Backend regressions cover coalesced input, stale result rejection, part/version
  references, stopping conditions, required answers and the engineering boundary.
- PostgreSQL/DBOS checks passed concurrent command deduplication, migration locking,
  outbox delivery and recovery after killing a worker at a recorded model boundary.
  The crash drill uses a test model; the conversational checks used live Gemini.

Final checks: 366 backend tests passed (16 optional checks skipped), four separate
PostgreSQL/recovery checks passed, and 138 Flutter tests passed (three optional
checks skipped). Ruff, Flutter analysis, generated transport consistency and the
release web build passed.

Anatomy is pinned at `1482e8469b0a9e0d8cc18ea2740a01423d29ae80` with
MIT licensing, verified source hashes and a bounded adaptation. Planning emits a
typed relative scene, not executable HTML/JavaScript or fabrication evidence.
There is no measured claim about token savings or engineering quality here.

Screenshots and live-check reports are in the ignored Flutter
`build/concept-live/` directory. The disposable check build was removed after QA;
the live planning checks only modified that temporary build. The LAN preview is
served on port 5180, with a larger connection queue for concurrent asset loading.
