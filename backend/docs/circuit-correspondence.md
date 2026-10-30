# Automated circuit correspondence

The `circuit-netlist-v1` checker compares an accepted electrical design with a
fresh KiCad XML schematic export and the actual board's footprint-pad nets. It
does not derive the expected circuit from candidate files. A generator's report,
BOM, screenshot, or claimed PASS never supplies expected topology or authority.

Before engineering, coordination proposes this contract in authoritative
`interfaces.circuit`; every accepted `interfaces.power.loads[].id` must appear
in a component's `loadIds`:

```json
{
  "schemaVersion": "circuit-netlist-v1",
  "components": [
    {
      "id": "supply_header", "reference": "J1",
      "symbol": "Connector_Generic:Conn_01x02", "value": "Input",
      "footprint": "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical",
      "loadIds": [], "pins": {"1": "VCC", "2": "GND"}
    },
    {
      "id": "indicator", "reference": "R1", "symbol": "Device:R", "value": "1k",
      "footprint": "Resistor_SMD:R_0603_1608Metric",
      "loadIds": ["indicator"], "pins": {"1": "VCC", "2": "GND"}
    }
  ]
}
```

This example defines topology for a small fixture, not a suggested device
design. Pin names, values, and full library identifiers must come from reviewed
part choices and source material. Include all physical components, including
connectors and passives, and all symbol pins and PCB pad numbers. `null` means an
explicitly isolated unused pin; it cannot conceal a missing pad or a short.
Names match literally, including hierarchical net prefixes such as `/VCC`.

The interface stage exposes `state.circuitContractCheck` with its engineering
revision and contract hash. This is an early completeness diagnostic, not a
release approval. Existing projects without a circuit contract require interface
regeneration/review; the service does not backfill expectations from generated
boards or rewrite accepted supply voltages or component ratings.

The independent validator:

1. Copies only the pinned schematic and PCB into a fresh directory, excluding
   generated adjacent project settings and reports.
2. Runs actual KiCad ERC/DRC with all severities and violation exit codes, then
   exports an independent XML netlist. DTD/entity declarations are rejected.
3. Uses fixed supervisor code and KiCad's `pcbnew.LoadBoard` to read every actual
   footprint, physical pad number, and assigned net.
4. Checks exact component closure, symbol/value/footprint identity, complete
   pin-to-net bindings, and schematic/PCB net membership. Missing components,
   swapped pins, unexpected parts, wrong values, and net shorts fail.
5. Records accepted requirements/interface hash, schematic/PCB content hashes,
   netlist hash, and board-inspection hash. The release service separately fences
   stale revisions/artifact closures and retains publication authority.

V1 is bounded to KiCad 9.0.x, XML netlists D/E, 128 physical components and 2,048
pins. Virtual KiCad power symbols are ignored. Numberless, unconnected mechanical
holes are permitted. Stacked pad numbers, net ties, do-not-populate footprints,
unavailable complete symbol pin definitions, and unsupported tool versions stay
UNKNOWN. Missing or malformed accepted contracts remain UNKNOWN/FAIL with an
actionable reason; clean ERC/DRC alone does not unblock release.

This is digital correspondence, not analog/RF/thermal/EMC simulation, independent
datasheet verification, library geometry qualification, fabrication evidence,
or proof that a physical device works. Qualified symbols/footprints and actual
manufacturing checks remain necessary for their respective gates.

The validator image explicitly installs `kicad-symbols` and `kicad-footprints`
`9.0.8-1~bpo12+1` alongside KiCad `9.0.8+dfsg-1~bpo12+1`. These are actual Debian
bookworm-backports package versions. Each job receives fresh global library
tables generated exclusively from `/usr/share/kicad` in the immutable image.
Candidate-supplied tables/settings cannot override independent validation.
Missing image libraries produce an operator capability UNKNOWN before rule
execution; they do not trigger paid engineering repair attempts. Private/custom
libraries require a separately reviewed image installation and qualification.

`docker/smoke_circuit_correspondence.py` exercises actual all-severity ERC/DRC,
independent XML export, actual `pcbnew` inspection, and the complete electronics
branch of `validate_candidates` against a two-resistor fixture using the official
Device:R symbol and R_0603 footprint. All three electronics checks pass; changing
an actual pad net fails correspondence. Other engineering lanes are outside this
fixture, so this does not claim a complete project release or physical operation.

`docker/Dockerfile.kicad-libraries` applies the same pinned library/source layer
to an existing local validator or electronics image without rebuilding the full
toolchain; use its `BASE_IMAGE` argument. Qualify a separate image tag with the
offline smoke before replacing a default image tag. Existing running tasks keep
their pinned image identity.

Atopile 0.12 does not guarantee a matching KiCad schematic export. Generation must
deliver both a real `.kicad_sch` and `.kicad_pcb`, or report the missing closure as
unsupported. Previewable partial files do not satisfy correspondence.

Tool interfaces: [KiCad CLI](https://docs.kicad.org/9.0/en/cli/cli.html),
[KiCad XML netlist](https://docs.kicad.org/9.0/en/eeschema/eeschema.html), and
[KiCad footprint/pad API](https://docs.kicad.org/doxygen-python-9.0/classpcbnew_1_1FOOTPRINT.html).
