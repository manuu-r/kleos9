# Independent PCB checker qualification — 2026-11-03

The new checker image is derived solely from the already fresh PCB dependency
image `sha256:6e3efde745d5a1e773737bcaf86d56320576b6c2ccf27c57cf6d5b62c3cf421f`.
The minimal build context contains only `Dockerfile.pcb` and `pcb_check.py`;
there are no credentials, package downloads or generated source mounts. Build
steps use `--network=none --pull=false --platform=linux/amd64`. Docker's existing
local tag was inspected to match the exact parent before use. An initial attempt
using an unqualified `sha256:...` FROM string failed registry metadata resolution;
a second attempt exposed the architecture mismatch. Neither ran build steps.
The successful build log resolved the local tag to the exact required image.

The current exact checker image is
`sha256:cb1cfd18a6b0ca4fb9246f2b9e77801556e4272ed14384304a30c536764a3b5a`.
It removes `/opt/kleos-native` and inherited backend packages and installs only
the trusted standalone checker. The old CAD images and reports are unchanged.

## Evidence and supported claim

`qualify_pcb_check.py` creates bounded data-only fixtures from the rewritten PCB
source fixture, with two explicit copper segments. It does not execute pcbnew,
KiCad, model code or generated engineering code on the host. Actual parsing,
netlist export, ERC and DRC run only in isolated checker containers.
`pcb-check-fixture.json` retains the exact successful-run baseline source bytes
and accepted contract. Candidate `.kicad_pro`/`.kicad_dru` files are never trusted.
Clean rules use accepted minimum clearance/track width and explicit project
schema/severity settings. Both board and schematic use project-local libraries.

Raw attempt reports preserve each run without rewriting failures. The final
complete attempt is `pcb-check-attempt-8.json`: all 15 cases returned their expected
statuses, actual container checks passed, and its persistent capacity ledger ended
at zero. The watchdog exited 137 after 175.287 seconds and its container was removed. The raw attempt keeps `qualified: false`. After independent review,
`pcb-check-exact-image.json` was derived as the explicit installation evidence;
[the separate review record](pcb-check-review-2026-11-03.md) states its exact scope.
No deployment was automatically enabled.
The fixture-specific scope is two components, two copper layers, straight outline,
simple through-hole pads, exact accepted reference/net maps, and source placement/
pad/outline preservation. It does not qualify arbitrary circuitry, manufacturing
exports, electrical function or physical behavior.

Every real invocation reserves shared capacity in its own persistent SQLite
qualification ledger before creation and releases only after identity-checked
removal/absence. This ledger proves serial fixture admission and cleanup; it does
not claim cross-database arbitration with deployment workers. Before running,
Docker had only the disposable PostgreSQL container active and the host had
54 GiB free. The test run is serialized with other native qualification work.

The report records actual container image/resource/security/mount fields, bounded
KiCad output, exact input and checker hashes, case outcomes and capacity after
each case. Environment audit output contains booleans, not credential values or
whole environment arrays. Home and hostname are fixed explicitly; other inherited
nonoperational environment values are emptied. KiCad 9.0.8, UID/GID10001, socket
denial and absent generator runtime are observed inside the production entrypoint.

The `parser_timeout` case replaces only the parsed workload with a trusted sleep
under the same exact `/usr/bin/timeout --signal=KILL 175` wrapper. It mounts no
source override. Expected evidence is observed exit 137 after approximately 175s,
followed by confirmed removal and capacity 0. This proves the independent watchdog
mechanism; it is not a claim that a particular malformed board takes175s to parse.

## Preserved findings

- Attempt1: strict environment audit rejected Docker runtime home/hostname values.
  The runtime now fixes both explicitly instead of allowing arbitrary values.
- Attempt2: routed board was staged under a different project directory from its
  local libraries. Staging now uses one project root; no checks were suppressed.
- Attempts3–5: the initially intended clearance-negative fixture was not a valid
  negative. Its added segment touched only the first net's pads; merely naming a
  segment as another net did not establish a physical cross-net connection.
  KiCad returned PASS. This was investigated and retained, not counted as proof.
- Attempt6: independently missing copper produced the actual `unconnected_items`
  DRC violation, demonstrating the tool was running connectivity checks.
- Attempt7: corrected clearance fixture anchors copper to the second net's pad
  and ends 0.05mm from the first net's trace. Actual DRC rejects it. The prior
  fixture result does not establish a bug in native generation.
- Attempt8: complete positive, negative, unsupported-input and watchdog suite.
  See its terminal evidence and independent review status before installation.

The KiCad flags follow the official
[KiCad9 CLI reference](https://docs.kicad.org/9.0/en/cli/cli.html#pcb-drc).
No live model, paid service, cloud resource, printer, fabrication or hardware was
used. Local image inspection/builds and these bounded isolated checks are the only
native operations in this phase.
