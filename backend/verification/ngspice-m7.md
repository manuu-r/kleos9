# Circuit simulation: final scoped verification (M7)

Date: 1 November 2026. Branch: `feature/ngspice-simulation`.
Implementation milestones M0–M7 are complete. **Pinned-image qualification is
still outstanding**: Docker's daemon was unavailable in this environment.
No deployment, Terraform, paid provider call, PR or main-branch push was performed.

## What actually ran

| Check | Result and scope |
|---|---|
| Backend full suite | **1097 passed, 29 skipped**, fixture/default run |
| Real direct solver integration + runtime regressions | **28 passed**: six real ngspice 47 / temporary ledger/API cases plus 22 fixture regressions |
| Ruff | `ruff check kleos tests docker/ngspice_fixture.py docker/smoke_ngspice.py`: PASS |
| Flutter analyze | PASS, Flutter 3.47.8 / Dart 3.13.7 |
| Flutter full suite | **228 passed, 3 skipped** |
| Mock backend | **15 passed**; original scripted simulation remains intact |
| OpenAPI/Dart | Exported contract, regenerated transport, `--check`: PASS |
| Diff/docs/data | Whitespace, documentation file links, JSON parsing and recorded result hashes: PASS |

Full backend skips include six explicitly opted-in direct solver cases; those
six were separately executed successfully. Other live/PostgreSQL/image opt-ins
remain skipped. Warnings concern local Qdrant payload indexes, unavailable mock
model pricing and the mock suite's existing unset asyncio loop scope. No local
`.env` creation or credential setup was needed.

The last hardening rejects sparse transient output even when it has the correct
start/end times, preventing a single late point from claiming observed settling.
Oversized-output rejection is also explicit in regression coverage. Known version
prefix lookalikes (390/470/47evil) are rejected. Planning guidance now explains
the optional accepted terminal bindings and forbids model-authored SPICE/tests or
renaming real parts into illustrative models to obtain a PASS.

## Real runs versus fixtures

[M2 direct smoke](ngspice-m2.json) ran actual ngspice 47 on a bounded trusted RC
deck. [M6 runtime evidence](ngspice-m6-runtime.json) records real divider, RC,
LED/resistor and combined power-on responses, including analytic comparisons,
hashes and observed PASS/FAIL/UNKNOWN statuses. The deliberately undersized LED
resistor failed; an unmodelled LED was UNKNOWN and invoked no solver command.

Project/model setup and SQLite ledgers were temporary fixtures. The solver,
independent validation handling, immutable artifact writes, authorized API reads,
workspace checks and artifact downloads actually ran. Other lanes/PCB
correspondence in those specimens remain UNKNOWN, so no complete device release
was qualified. [Recorded power-on data](ngspice-power-on-sample.json) is a replay
fixture extracted from that real run, never a production fallback.

Flutter tests consume the real recorded data to verify replay, 5 ms startup zoom,
100 ms inspection, LED state and all four check markers. A separate client test
passes it through ProductionApi HTTP decoding/generated DTO and the provider/UI
panel using a mock HTTP transport. That last transport is a fixture, not a live
browser connection to the temporary API. Phone renders were inspected, with
test-runner font/icon fallback limitations recorded in M5/M6.

## What has not run or been qualified

- Docker image build/smoke; exact Debian ngspice `39.3+ds-1` binary execution.
  The package version is pinned in the validation toolchain and inherited by
  electronics; package hashes are authenticated by APT. The observed local
  ngspice 47 binary is a different runtime, not a silent replacement image.
- Linux UID 10001 / seccomp / network-none behavior for these new decks. The
  implementation reuses the existing boundary, but reuse is not new runtime proof.
- Deployed browser/network end-to-end, physical phones, PostgreSQL recovery,
  real manufacturer LED/diode models, arbitrary active circuits or device safety.
- Layout, thermal, RF, parasitics, continuous tolerance worst case, physical
  acceptance or fulfillment. Tiny/large time constants can remain UNKNOWN.

The [native image smoke commands](../docker/README.md) and
[M6 opt-in command](ngspice-m6.md) are reproducible qualification entry points.
Run both intended immutable validation/electronics images before promoting them.

## Product and policy

The feature deterministically compiles accepted circuit/power contracts, runs
independent checks, records bounded/downsampled hashed digital evidence and
exposes it to an interactive, phone-friendly Flutter power-on view. Missing or
unsupported input never becomes a fabricated waveform. Candidate/generated
programs are not executed on the development host.

Checks remain **advisory**, including FAIL/ERROR. Existing required release and
physical gates retain their behavior. The stricter opt-in gating proposal is
documented separately in [circuit simulation policy](../docs/circuit-simulation.md#advisory-policy-and-proposed-stricter-gate);
it is not implemented or applied to existing projects.

Milestone history:

- [M0 inspection/plan](../docs/ngspice-simulation-plan.md), `745b355`
- [M1 compiler/analytics](ngspice-m1.md), `838ce18`
- [M2 pinned tool/smoke](ngspice-m2.json), `e2002ad`
- [M3 validation/artifacts](ngspice-m3.md), `5b5cd73`
- [M4 API/contracts](ngspice-m4.md), `b3a756d`
- [M5 viewer](ngspice-m5.md), `831b38e`
- [M6 connected solver evidence](ngspice-m6.md), `bbb3af7`
