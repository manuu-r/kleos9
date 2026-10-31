# Electronics tool contract and current qualification limit

## Default: deterministic KiCad compiler (`atopile-kicad@2`)

New project defaults use accepted `interfaces.circuit` JSON as the source. The
fixed compiler emits a schematic and placed board, exports DSN, runs pinned
Freerouting 2.1.0, imports SES, and runs KiCad ERC/DRC plus Gerber/drill exports.
Independent validation checks fresh netlist/PCB pin correspondence against the
accepted contract. Model-written success reports cannot supply that evidence.

The image includes pinned KiCad 9 libraries and a hash-verified catalog of
resistors, capacitors, LEDs and pin headers. Additional component/footprint pairs
need qualification; incomplete or unsupported contracts remain visible blockers.
See the [native engineering review](../../docs/reviews/2026-10-31-native-engineering.md)
and [compiled circuit evidence](../../verification/compiled-circuit-runtime.json).

## Legacy: atopile source workflow (`atopile-kicad@1`)

The following configuration and historical failures describe the separately
selectable legacy workflow, not the default deterministic compiler.

The local image actually runs atopile 0.12.5 in an isolated Python 3.13.7 tool
environment and KiCad CLI 9.0.8. This verifies tool availability, not a supported
smartwatch circuit, parts library, routing flow or interface correspondence.

The pinned atopile release uses this project-configuration shape:

```yaml
requires-atopile: "==0.12.5"
paths:
  src: .
builds:
  default:
    entry: main.ato:Main
```

`ato-version` is obsolete for this release. An entry requires both a file and a
module section (`:Main` here), and that module must actually exist. See the
[upstream project configuration](https://github.com/atopile/atopile/blob/v0.12.5/src/atopile/config.py).
This configuration fragment is guidance, not a buildable or qualified circuit.

Native jobs have no network. Dependencies, symbols, footprints and reviewed
component choices must be provided locally or baked into the immutable image.
A successful `ato build` process alone is insufficient: the current build contract
requires exactly one actual `.kicad_pcb` and one `.kicad_sch`, then invokes real
ERC, DRC, Gerber and drill exports. Missing exports return UNKNOWN/unsupported.

Independent validation repeats ERC/DRC and exports a KiCad XML netlist. Empty
components or nets fail, even if both rule checks report zero violations.
`electronics_correspondence` automatically compares complete accepted
`interfaces.circuit` pin/net expectations with fresh XML export and actual PCB
pad bindings. Missing expectations remain UNKNOWN; wrong components, pins or nets
fail. Neither an irrelevant circuit nor a generated report can unlock release.
See [supported scope and contract](../../docs/circuit-correspondence.md).

The live Gemini electronics run on 2026-10-30 made three recorded attempts:
obsolete configuration key, missing entry module, then an atopile process that
did not supply the required PCB/schematic closure. The run remains visibly blocked.
Those are real compatibility failures; they are not qualified electronics output.
