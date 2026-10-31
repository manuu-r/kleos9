# Deterministic circuit simulation

`kleos.circuit_simulation.plan_simulation(interfaces)` is a pure compiler. It
returns READY plus nominal/corner decks, hashes and measurement metadata, or
UNKNOWN plus reasons and **no decks**. READY is not a validation PASS.
`circuit_simulation_runtime.py` runs those decks only through independent native
validation's existing sandbox executor. `engine.finish_native` stores digital
checks and an immutable waveform artifact; the Flutter PCB view replays it.
The existing numeric-only fixed-RC connector remains unchanged.

## Accepted bindings

In addition to `circuit-netlist-v1` and the existing power budget, an accepted
interface must explicitly identify the supply connector and each load's probe
terminals. For a resistor divider with J1 pins 1/2 = supply/ground, and R2 pins
1/2 = divider output/ground carrying `loadIds: ["load"]`, use:

```json
{
  "supplyVoltageV": 3.3,
  "maxCurrentMa": 20,
  "loads": [
    {"id": "load", "minVoltageV": 1.8, "maxVoltageV": 2.4, "currentMa": 2}
  ],
  "simulation": {
    "source": {"componentId": "J1", "positivePin": "1", "negativePin": "2"},
    "loads": {
      "load": {"componentId": "R2", "positivePin": "1", "negativePin": "2"}
    }
  }
}
```

This object is `interfaces.power`; component IDs refer to `interfaces.circuit`.
No source or load rail is inferred from net names, reference prefixes or load
order. Existing contracts without these bindings remain unsupported by this
optional planner. This does not add a required field to existing API contracts.
Loads must already be present electrically; `currentMa` is a declared budget,
not an invented constant-current sink added to the deck. Load probe polarity is
explicit and need not be relative to source ground.

## Supported compiler scope

- One explicitly bound two-pin supply connector. Other two/four-pin connectors
  are electrically open; connected nets must have a DC path through the supported
  network. No fake leakage or guessed external circuit is added.
- At most 16 components, 16 power loads, 32 external nets and four R/C parts. All resistor ±5%
  and capacitor ±20% endpoint combinations are generated, plus nominal: at most
  17 decks. This envelope is not a proof of worst-case interior tolerance values.
- R values: decimal/scientific numbers, optionally `R`, `k`, `M` or `meg`;
  0.01 ohm–1 Gohm. C values: decimal/scientific numbers, optionally `p`, `n`,
  `u` or `m`; 1 pF–0.1 F. For example `330`, `1k`, `2.2meg`, `100n`, `1e-6`.
  Whitespace, expressions, embedded decimal suffixes such as `4k7`, unit text,
  negative/zero/nonfinite numbers and SPICE options are rejected.
- Illustrative image-owned `Device:LED` value `kleos-red-led-v1` uses Shockley
  Is=1e-20 A, N=2, Rs=0 at 27 C, with a fixed advisory 5 mA current limit.
  `Device:D` value `kleos-silicon-diode-v1` uses Is=1e-14 A, N=1, Rs=0 at 27 C
  and has no qualified current rating. Both use KiCad cathode pin 1/anode pin 2.
  These are explicit ideal model selections, not manufacturer component models.
  A generic `LED`, `Red`, part number or unknown symbol has no trusted model.
  Diode simulation support does not extend the independently qualified PCB catalog.
- Unknown parts invalidate the whole simulation plan conservatively; they are
  never omitted to permit an affected check to pass.

Net names are mapped to generated numeric nodes. Component names, output paths,
models and control commands are image-owned/generated. Candidate SPICE, scripts,
includes, paths and control blocks are never accepted. Source and power contract
hashes bind the plan to accepted inputs; each UTF-8 deck has a SHA-256 hash.
Component/pin dictionary order does not affect emitted decks. Contract hashes
retain the exact accepted array order for provenance.

Each deck requests a DC operating point, then a transient with a fixed 1 us
linear supply ramp, zero capacitor initial conditions, 100 us output interval
and 100 ms observation window. Supply current and diode/LED series-sense current
are exported along with every external non-ground node voltage. Passive current
can subsequently be derived from its accepted terminals and corner resistance.
No independent check or result is produced by this compiler.

## Execution and evidence limits

The runtime enforces 15 seconds per command, 255 seconds per scenario, 5,000
samples and 2 MB per solver output file. Native `run_command` additionally limits
ngspice files/logs with RLIMIT_FSIZE and retains its UID/network/CPU/process bounds.
The image pins Debian ngspice `39.3+ds-1` via authenticated APT. Parser/version
policy also permits the explicit direct ngspice 47 qualification fallback.
Unknown versions remain UNKNOWN. No model/provider or network call is needed.

Malformed headers, nonfinite/absurd values, duplicate/sparse/out-of-order times,
partial windows, oversized files, nonzero exits, solver errors and symlinks do not
produce PASSes. Missing tools/models are UNKNOWN; execution/data failures are
ERROR. DC/current limit violations are FAIL. Settling requires the final 5 ms
within 2% (minimum 1 mV) of the DC result and an observed 90% rise. Unresolved
startup is UNKNOWN: the contract does not define an acceptable startup deadline.
DC voltage limits are not incorrectly applied to the initial zero-voltage ramp.

Checks use raw output, before linearization/downsampling. Peak supply draw covers
DC and startup, including inrush; LED checks use a zero-volt series current probe.
The <=501-point view includes nominal values and raw/corner extrema in 200 us bins.
It is a sampled envelope, not continuous worst-case tolerance proof. Extremely
small/large time constants may remain unresolved. Data stores deck/output hashes
and a canonical `resultsSha256` computed over the envelope excluding that field.
The immutable whole-file SHA-256 is stored separately in the artifact/validation.

The artifact is <=4 MB, status `evidence`, role `validationEvidence`, tied to the
native job, exact input closure, contract hash and engineering revision. It is
outside the candidate/release file set, avoiding self-invalidating checks. Only
the independent validate callback can publish this evidence; cancellation and
stale-contract/file fences apply before publication. An unrelated unsupported
lane does not erase completed circuit checks.

## API and viewer

`GET /api/v1/projects/{id}/circuit-simulation` reads current evidence without
starting compute. Tenant/project membership is checked on every read. The route
checks contract, revision, latest native-rebuild boundary, candidate closure,
independent Validation bindings and artifact/result hashes. It returns explicit
unavailable data for missing/stale/corrupt evidence. Existing artifact content and
download routes also provide authorized historical immutable bytes. OpenAPI and
Dart transport include the new typed envelope.

In Flutter, open the build's PCB view and scroll to Power-on. Select a rail, net
or current, replay/pause, drag the plot or use the time slider. Startup zoom shows
the first 5 ms; switch to the full 100 ms for the entire record. Limit lines,
tolerance band, check markers and numeric readouts remain visible on phones.
The LED indication uses recorded forward current >=0.1 mA; it is illustrative,
not a luminous-intensity model. Reduced motion disables replay, preserving scrub.
No unavailable data is replaced with an animated fixture. Saved checks describe
the full run/all corners, not just the selected replay instant.

## Advisory policy and proposed stricter gate

The four checks (`circuit_dc_rails`, `circuit_startup`, `circuit_led_current`,
`circuit_total_current`) use `required: false`, `scopeExcluded: true`, and an
explicit advisory label. PASS/FAIL/UNKNOWN/ERROR/NOT_APPLICABLE stay distinct.
Simulation FAIL/ERROR does not fail the overall native job or block an existing
release. Existing required checks and physical gates retain their behavior.

**Proposal only, not implemented:** after exact-image and real component-model
qualification, offer an explicitly accepted requirement for specified simulation
checks. Those projects should require PASS from those checks, with UNKNOWN/ERROR
blocking their release. Specify startup deadlines, reverse-voltage/current
limits and model applicability before making them required. Do not silently
upgrade existing projects or let a generator select its own tests/acceptance.

All results are digital simulation only: ideal lumped parts, no layout,
thermal, RF, parasitic, physical safety or manufacturing qualification. Analytic
unit tests independently interpret the generated divider, integrate the RC KCL
against the closed-form ramp/step reference, and solve the LED/resistor Shockley
equation. They are fixtures, not evidence of an ngspice or sandbox run.

## Reproduce and qualify

See [native image commands](../docker/README.md), [M6 real run](../verification/ngspice-m6.md)
and [final verification](../verification/ngspice-m7.md). Ordinary tests require no
solver, secrets, paid calls or local `.env`. The six direct-solver integration
tests are opt-in; never describe them as pinned-image evidence. Build and run
both validation/electronics smoke images offline before enabling the new images
in deployment. No deployment, Terraform or physical action is part of this change.
