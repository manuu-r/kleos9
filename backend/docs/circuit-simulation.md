# Deterministic circuit simulation (M1)

`kleos.circuit_simulation.plan_simulation(interfaces)` is a pure compiler. It
returns READY plus nominal/corner decks, hashes and measurement metadata, or
UNKNOWN plus reasons and **no decks**. READY is not a validation PASS. This
milestone neither invokes ngspice nor changes release gates or API responses.
The existing fixed-RC connector remains unchanged.

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

## Qualified compiler scope

- One explicitly bound two-pin supply connector. Other two/four-pin connectors
  are electrically open; connected nets must have a DC path through the supported
  network. No fake leakage or guessed external circuit is added.
- At most 16 components, 32 external nets and four R/C parts. All resistor ±5%
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

M1 exports policy ceilings of 15 seconds per deck, 5,000 raw samples and 2 MB per
output file for the future runner. It does **not enforce execution limits** or
parse solver output because it does not execute anything. M2/M3 must enforce
those limits through the existing native sandbox and reject nonfinite, truncated
or oversized output. Tiny/large time constants may be unresolved in the fixed
observation window and must not be presented as proven settling behavior.

All future results are digital simulation only: ideal lumped parts, no layout,
thermal, RF, parasitic, physical safety or manufacturing qualification. Analytic
unit tests independently interpret the generated divider, integrate the RC KCL
against the closed-form ramp/step reference, and solve the LED/resistor Shockley
equation. They are fixtures, not evidence of an ngspice or sandbox run.
