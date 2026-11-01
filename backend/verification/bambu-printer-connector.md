# Bambu connector: scoped verification

Date: 1 November 2026. Branch: `feature/ngspice-simulation`.

Implemented scope: durable accepted-STL digital preparation, operator-pinned
offline slicing image/bundles, authenticated package upload/download, local
read-only TLS/MQTT status, reported-model detection, all-model dropdown and
explicit Bambu Connect import handoff. Platform physical dispatch/acceptance,
procurement and shipping gates remain unchanged. No live printer command,
deployment, Terraform, paid model call or main-branch push was performed.

## Checks actually executed

| Check | Result and scope |
|---|---|
| Backend full suite | **1143 passed, 29 skipped**, fixtures/default opt-ins |
| Focused Bambu regressions | **30 passed**, including real API/SQLite/artifact bytes with fixture slicer/printer transports |
| Ruff | `backend/.venv/bin/ruff check .` from backend: PASS |
| Flutter analysis | PASS, Flutter 3.47.8 / Dart 3.13.7 |
| Flutter full suite | **235 passed, 3 skipped**; includes 7 Bambu widget cases at 320/390/1000 px and existing workbench/simulation regressions |
| Mock backend | **15 passed**, existing simulated journey |
| API contract | OpenAPI exported, Dart generated and generator `--check`: PASS |
| Real vendor data | **14/14 default 0.4 mm model/profile bundles resolved** from pinned upstream JSON; references/cycles/hashes checked, no slicer executed |
| Image smoke CLI | `smoke_bambu.py --help`: PASS; actual image smoke **NOT RUN** |
| Diff/data/docs | Whitespace, local documentation links, JSON and source metadata checks: PASS |

Default-suite warnings concern local Qdrant indexes and fixture model pricing;
mock tests also report the existing unset asyncio fixture-scope warning. Tests
needed no local `.env` or credentials. Live PostgreSQL/recovery/provider/native
image opt-ins remain skipped; these counts do not certify those systems.

## What used fixtures

Preparation tests make actual authenticated API requests, persist durable records
and immutable artifacts, verify served bytes/hashes and ensure no Validation or
physical acceptance is created. The accepted digital release is a database
fixture, not a freshly engineered/validated physical design. Test G-code/ZIP/STL
content is synthetic; even the minimal nonzero STL bounds fixture is not a closed
printable solid. The separate image smoke uses a trusted closed cube.

Tests cover stale revision/release/source/requirement-interface fences, tenant
denial, candidate rejection, cancellation, unqualified/missing profiles,
idempotency conflicts, pinned settings across operator changes, changed hashes,
unsafe/unsliced/multi-plate ZIPs, transfer limits, inherited/template preset
resolution, script rejection with recomputed checksums and no automatic scaling.
The local adapter recovery test follows download → fixture slicing → real upload
→ completion and recovers a failed local save without reslicing.

Docker invocation tests inspect the real fixed arguments and staged files with
an injected process function. They do not launch Docker. MQTT tests inject byte
streams and a fake TLS socket, proving that credentials are sent only after the
pin check and that outbound packets contain only CONNECT/SUBSCRIBE/DISCONNECT.
They do not prove compatibility with real printer firmware. Connect tests verify
the encoded import URI; no application or print is launched.

## Real public-data research and resolution

The vendor repository was fetched at tag `v02.08.02.61`, commit
`926a7192574bcb9b3a732e1ec59a46d79cb45466`. The checked-in
[catalog](../kleos/bambu-models.json) preserves model-source hashes, the upstream
index hash and attribution. [Resolution evidence](bambu-profile-resolution.json)
records all 14 actual vendor default machine/process/filament choices, resolved
file/bundle hashes and each machine's start-G-code hash. All outputs remained
`qualified: false`; an unused fixture image ID only satisfied the data contract.
Downloaded vendor code was read for research and never executed on the host.

The practical check caught separate JSON `include` templates in modern presets.
Resolution now follows the vendor order: inherited parent, referenced templates,
then explicit fields, removing all remaining references. Generic inherited start
G-code must not replace the selected printer's vendor template. This is data
compatibility evidence; actual CLI/profile behavior still needs image execution.

Primary references used:

- [Bambu CLI manual](https://github.com/bambulab/BambuStudio/wiki/Command-Line-Usage): full settings, filament loading, one-plate slicing/export.
- [Pinned model/preset tree](https://github.com/bambulab/BambuStudio/tree/v02.08.02.61/resources/profiles/BBL): model IDs/nozzles and resolved settings inputs.
- [Pinned preset implementation](https://github.com/bambulab/BambuStudio/blob/v02.08.02.61/src/libslic3r/PresetBundle.cpp): JSON template merge order.
- [Studio release](https://github.com/bambulab/BambuStudio/releases/tag/v02.08.02.61): Ubuntu 24.04 AppImage and published SHA-256, pinned in the Dockerfile.
- [Bambu third-party guidance](https://blog.bambulab.com/updates-and-third-party-integration-with-bambu-connect/): supported Connect handoff versus optional, unsupported raw Developer Mode protocols.
- [Published Connect integration example](https://github.com/SoftFever/OrcaSlicer/pull/8103): nested `bambu-connect://import-file` URI; no signature/private-key bypass.

## Not run / outstanding qualification

Docker CLI is installed but the daemon was unavailable. **No slicer image was
built, no actual Bambu Studio CLI slicing ran, and no image smoke passed here.**
Shared-library/headless-rendering compatibility and real `.gcode.3mf` output need
that check. The image checksum is pinned from the vendor release metadata; it
will be verified against actual downloaded bytes during the image build.

No physical printer, owner-verified certificate/access code or installed Bambu
Connect was available. LAN telemetry/model fields, certificate rotation,
Connect import handling, all firmware/model/nozzle/material combinations,
multi-extruder behavior and actual prints remain unqualified. Dropdown/catalog
coverage of every pinned model does not claim those outcomes.

An operator must build the image, record its immutable identity, resolve and
review exact profiles, run the offline smoke, inspect the actual plate and
perform separately authorized physical qualification before enabling a profile.
The server registry defaults empty. Structural package checks cannot prove that
an adapter ran the claimed slicer or that submitted G-code is safe to print.
Physical start remains an operator action in Connect, and a saved package is
explicitly digital preparation with `authority: local_adapter_report`.

The [setup/operation guide](../docs/bambu-printers.md) includes commands,
credentials kept locally, cancellation semantics and recovery steps. Existing
ngspice simulation and mock fulfillment remain available.

## Connections UI follow-up

Production **Connections → Printers → Set up Bambu Lab** now opens the real
model/build preparation setup instead of the legacy demo wizard. The production
view hides that demo Bambu card; standalone mock mode retains its simulated
wizard. Status comes from actual qualified-profile metadata and fresh read-only
bridge reports, with unconfigured setup shown explicitly. Model selection carries
into the existing preparation panel, and direct links work after delayed loading.

Follow-up checks: Flutter analysis PASS; **256 passed, 3 skipped** in the full UI
suite; **18 passed** in the focused connection/preparation/dialog run. Live local
browser inspection confirmed the real entry and dialog with model/build
selectors, zero installed profiles and an unconnected status bridge. No printer
command or slicing run occurred; the image/hardware qualification limits above
remain unchanged.
