# Circuit simulation: M0 inspection and implementation plan

Inspected 1 November 2026, starting at `90af501` on a clean `main` working tree.
Work branch: `feature/ngspice-simulation`. This record establishes the starting
point; it does not claim simulation implementation or qualification.

## Verified starting point

- `ngspice_connector.py` contains a numeric-only fixed RC example, not a general
  circuit simulator. Preserve its refusal of caller-authored SPICE.
- `circuit_validation.py` defines `circuit-netlist-v1`, component pins and load
  coverage. `pcb_compiler.py` exists and has the image-owned R/C/LED and two/four
  pin connector catalog described in the brief.
- `models.py` documents the power contract. `native_runner.validate_interfaces`
  currently compares declared supply/load budgets, not simulated rail values.
- `run_command` drops to UID 10001, supplies a clean environment, installs network
  restrictions and enforces process/time/file limits. Reuse this boundary.
- `validate_candidates` is independent of `build_lane`.
  `EngineeringEngine.finish_native` persists digital checks, binds them to input
  artifact hashes and the accepted contract, and fences stale/cancelled work.
- `Dockerfile.native` does not install ngspice. `validate-toolchain` is shared by
  validation and electronics targets, so it is the intended installation stage.
- Existing authorized workspace, engineering-preview and artifact content/download
  routes provide integration points. Validation output artifacts need careful
  handling: adding a file to the candidate closure after storing checks can make
  those checks inapplicable. Resolve and regression-test this in M3.
- Flutter has no chart dependency. Its engineering preview already has native
  painters, responsive controls and Riverpod-backed production data. Prefer a
  small custom painter using the existing design system, subject to M5 review.
- Recent history: `90af501` updates the Flutter workbench; `c1cedd3` contains the
  current backend integration work; earlier `7c1db77` introduces circuit validation
  and engineering plugins. No existing general circuit simulation was found.

## Environment actually checked

| Tool | Result |
|---|---|
| Backend environment | Existing `backend/.venv`, Python 3.12.11; pytest 9.2.0 and Ruff 0.17.3 import successfully |
| Package tooling | Python 3.12 and uv available; the existing venv has no pip module, which does not prevent tests |
| ngspice | Not found on PATH |
| Docker | CLI installed; daemon unavailable even after the socket permission check was authorized |
| Flutter | 3.47.8 stable, Dart 3.13.7; version invocation required SDK cache write access |

No Docker job, generated code, provider call, deployment or infrastructure command
was executed. Native image smoke remains unrun. M2 may use the requested direct
numeric-only simulator fallback if ngspice becomes available; that cannot qualify
the image or its Linux sandbox.

## Important design constraints discovered

The contract has no explicit source/ground selection or load measurement pin.
`loadIds` alone does not distinguish a load supply rail from a resistor's two
terminals. Use only a documented unambiguous supported topology/binding, otherwise
report UNKNOWN. Do not guess pins, sources, diode identities or device ratings.
An image-owned illustrative LED model must name its assumptions and safe-current
policy; arbitrary `Device:LED` values do not identify a manufacturer model.

Release acceptance excludes checks only when **both** `required: false` and
`scopeExcluded: true` are present. Independent native validation separately
excludes such UNKNOWNs, but currently counts **all** FAIL/ERROR checks as job
failures. M3 must make new simulation FAIL/ERROR results advisory in aggregation
without weakening existing required checks, preserving their displayed status.
No stricter simulation release policy is authorized by this plan.

## Milestone sequence and verification

1. **M1:** deterministic typed compiler from accepted circuit/power only, numeric
   nodes and generated element IDs, strict value whitelist, fixed control block,
   trusted model table and bounded scenario/corner plan. Pure unit tests cover
   determinism, hostile values, unsupported/ambiguous bindings and independent
   analytic divider, RC and LED-resistor references. Analytic tests are fixtures,
   not evidence that ngspice ran.
2. **M2:** install a pinned ngspice package in `validate-toolchain`; checksum any
   direct download. Add an offline smoke under `backend/docker` exercising actual
   numeric-only decks through `run_command` with read-only root, bounded scratch,
   CPU/memory/process/time/output/sample limits. Distinguish direct execution from
   exact-image and source-override evidence.
3. **M3:** independent DC rail, startup/settling, LED current, total current and
   fixed tolerance-corner checks. Unknown models cannot yield affected PASSes.
   Reject nonfinite/oversized outputs; hash decks and canonical results; retain a
   bounded waveform with an honest corner envelope. Persist digital advisory
   checks and waveform evidence without invalidating artifact closure identity.
   Add release, aggregation, stale-result and artifact-provenance regressions.
4. **M4:** expose revision-bound evidence through existing authorized routes;
   protect cross-tenant reads and stale artifacts. Export OpenAPI and regenerate
   Dart transport together if response schemas change.
5. **M5:** integrated Flutter waveform viewer with net selection, replay/scrub,
   limits, tolerance band, per-check status and LED indication from simulation
   samples. Respect reduced motion, accessible controls and phone layouts. Label
   it simulated, not measured; never animate invented evidence as a real result.
6. **M6:** real solver sample runs for supported, deliberately failing and
   unsupported-model circuits. Compare numeric results to analytic expectations;
   exercise storage/API/viewer paths with fixtures where services are unavailable
   and clearly distinguish them from full end-to-end runs.
7. **M7:** document supported bindings, model assumptions, ideal-part/no-layout/
   thermal/RF limits, policy and operations. Record exact commands, versions,
   hashes, real runs, fixtures and unrun qualifications under verification.

Report and commit each milestone, then push only this feature branch. Run focused
checks during implementation and the requested backend pytest/Ruff and Flutter
analyze/test suites for the completed scope. No paid inference, Terraform,
deployment, PR or main-branch push is part of this work.
