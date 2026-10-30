# Electronics tool contract and current qualification limit

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
`electronics_correspondence` remains a required UNKNOWN until a reviewed template
maps actual components and nets to the accepted loads and interface IDs. Neither
an irrelevant circuit nor a generated check report can unlock release.

The live Gemini electronics run on 2026-10-30 made three recorded attempts:
obsolete configuration key, missing entry module, then an atopile process that
did not supply the required PCB/schematic closure. The run remains visibly blocked.
Those are real compatibility failures; they are not qualified electronics output.
