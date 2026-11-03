# Independent PCB checker — implementation and qualification boundary

The replacement backend now selects digital verification policy from every native
candidate lane in the completed objective. `project.verify` keeps its existing
payload/UI. CAD-only objectives retain `cad.export-integrity`; PCB uses
`pcb.board-rules`; mixed CAD/PCB requires both checks under `digital.cad-pcb`.
An absent/revoked checker or an unsupported firmware/application lane blocks
admission. It never silently verifies only the CAD subset. The preparation and acceptance
boundaries also require every current published covered output, preventing a
model-selected subset or later lane publication from bypassing coverage.
Validation tasks carrying producer references do not duplicate candidates or
resurrect superseded producer outputs after repairs. Selected outputs must
still share the exact proposed source closure; mismatched parallel producer
closures require an explicit rebuild. Generic internal fixture policies remain
available for testing.

Covered release outputs are paired CAD STEP/STL and, for PCB, exactly
`outputs/routed.kicad_pcb`. SVG, parts manifests, generated ERC/DRC reports,
Gerbers, drills and portable ZIPs are explicitly outside this phase's release
coverage. Retaining other baseline sources is context preservation, not a claim
that they passed PCB/CAD checks. Physical acceptance remains unavailable.

## Fresh checker boundary

`validation/pcb.py` resolves every subject reference through current scoped
repository authority. It validates the explicitly accepted circuit/concept or
import identity, native producer request hash, pinned accepted circuit, source
hashes, project/run/task and image provenance. It repeats authority checks after
IO. Inputs are hash-addressed immutable blobs; only KiCad source/library data and
the routed board enter the child. Generated scripts, project rule exemptions and
generator reports are excluded.

`pcb_check.py` is a standalone trusted entrypoint. It copies bounded verified
inputs into private scratch, creates clean project/rule files from accepted
constraints, independently exports the schematic netlist, inspects source and
routed boards using pcbnew, and runs fresh KiCad ERC/DRC commands. Exact reference,
value, footprint and pin/net correspondence is required. Routing must preserve
source component positions, pad geometry/layers/drills and straight-segment outline geometry and satisfy
explicit two-layer, dimension and track constraints. Unnumbered/repeated pads,
zones, vias, complex pad shapes and non-segment outlines remain unsupported (`UNKNOWN`). New supported
shapes require qualification; a zero process exit alone is insufficient.

`pcb_process.py` uses a separate pinned image and fixed entrypoint. It preserves
read-only root/input mounts, UID/GID10001, no capabilities, no new privileges,
verified local Docker Desktop socket restrictions, no network, 2CPU/2GiB memory,
64PIDs, 256MiB scratch, bounded stdout and a 180-second host timeout. A fixed in-container GNU timeout
watchdog kills the complete checker process group after 175 seconds, independently
of host worker liveness; the image must qualify that timeout case. Shared durable
capacity is reserved before dispatch. Uncertain create, failed deletion or a
renamed live immutable container keeps its reservation; name disappearance alone
never releases capacity. No provider keys, DB credentials or Docker socket reach
the checker. ECS is unqualified.

## Installation and evidence gate

`pcb_checker_installation` is absent by default. Its fields match the CAD
installation configuration: `imageDigest`, `qualificationReference`, optional
`daemonControlRoot`. Configuring an invalid or incomplete report fails startup.
`Dockerfile.pcb` removes inherited backend and generator runtime and installs only
the fresh checker. Existing CAD image bytes and checker entrypoint are unchanged.

The report must bind protocol `kleos-pcb-check-v1`, exact image ID, local deployment,
no source override, source hashes for the adapter/entrypoint/transport/Dockerfile,
fixed resource/network policy and expected positive/negative case statuses.
`independentReview` records a distinct reviewer, timezone-aware review time,
approval and SHA-256 of the evidence object excluding that review. The installation hash binds the image, complete reviewed report and timeout, so
requalifying changed host logic fences existing checker pins even for the same
container image. Evidence files
are bounded regular files opened without following a final symlink.

Required real cases: valid board PASS; wrong reference/net, moved component,
changed outline, moved/resized/relayered pads, clearance violation, unconnected copper, ERC violation, byte
mismatch and untrusted rule exemption FAIL; unsupported zone UNKNOWN; parser
watchdog expiry ERROR. Inspect
actual container configuration and verify generator code/credentials are absent.
Review measured evidence independently before registering the installation.
Fixture reports in unit tests are test data only; no report or checker image has
been qualified by this implementation phase.

## Local inventory and verification limits

Read-only `docker image inspect` confirmed these exact recorded dependency images
present locally on 2026-11-03, all linux/amd64:

- PCB `sha256:6e3efde745d5a1e773737bcaf86d56320576b6c2ccf27c57cf6d5b62c3cf421f`
- Firmware `sha256:1ea0aa4e85133be3a85fd7399d53c94cb2d1dc6d426b603235fcf44beb93e3bc`
- Web `sha256:7c5aedd853f6be70c0d629a90134297fdf94716213cf32b3e2a743339776ea8d`

The implementation-only phase ran no image build, container execution, model/provider call or physical action.
The subsequently authorized actual isolated qualification is documented in
[the qualification record](../src/kleos/validation/qualification/PCB.md); retained
raw attempt reports and independent review determine installation eligibility.
Unit tests use bounded static fixtures, mocked KiCad/process calls, real project
storage and actual Runtime composition with explicitly synthetic qualification
records. They establish control behavior, not native parser/tool qualification.
Actual isolated positive/negative/watchdog execution subsequently passed its 15-case
fixture; independent review has approved that exact scope, while deployment installation
remains explicit and was not enabled automatically. Firmware
and application independent checkers, manufacturing-export correspondence,
browser/device behavior and whole-product acceptance remain separate work.
