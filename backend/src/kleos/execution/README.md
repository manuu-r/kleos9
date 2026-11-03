# Isolated native execution

`NativeGateway` is the control-plane boundary. `authorize(scope, task, arguments)`
checks run authority and available pinned images. `invoke(scope, task, action,
arguments)` accepts only `read`, `patch`, `build`, `analyze`, `render`, `finish`.
Every action requires the harness-generated `actionId`. Reusing an identity with
different arguments fails. A durable job pins its source hashes, task contract,
image ID, resources, revision and deadline; restarting a worker does not reset
these limits. A pending action can reconcile the existing private result file;
interrupted native commands are never silently re-executed.

Instantiate with `(projects, artifacts, NativeSettings, DockerClient,
run_repository, publisher=artifacts.publish_native)`. Import the gateway from
`kleos.execution.gateway` and settings/client from `kleos.execution.docker`.
Tables `kleos2_native_jobs` and `kleos2_native_actions` share project metadata;
production deployment needs migrations. The private control root must refer to
the same directory in the API/worker and Docker daemon (`daemon_control_root`
sets the daemon-side path). Only each individual job directory reaches its
container as a read-only mount. The supervisor journal and results live in root-only
Linux tmpfs at `/run/kleos-control`; host file-sharing permissions are not trusted
as a privilege boundary. A fixed root-only `result_reader.py` transfers a bounded
result through Docker exec; models cannot select its executable or arbitrary paths.
The supervisor waits for a durable host acknowledgement before finishing.
Call `reconcile()` periodically to stop terminal, cancelled, superseded and
expired jobs and remove their disposable container scratch. It never publishes
artifacts or bypasses the objective worker fence.

## Shared native capacity

Runtime composition injects one `CapacityService` into native generation,
independent CAD checking and slicing. All workers targeting the same Docker host
must use the same persistent pool ID and limits. Defaults admit at most two jobs,
4 CPUs, 6 GiB of aggregate container memory and 5 GiB of aggregate temporary
storage. A 6 GiB application build therefore occupies the memory budget alone;
two 4 GiB generation/slicing jobs cannot run together. Per-container limits and
the original request/image contracts remain unchanged. Memory includes container
tmpfs usage; the scratch dimension separately accounts for each declared workspace and its
bounded supervisor journal (/run). The web footprint is 4.125 GiB of scratch;
its original 4 GiB workspace contract is unchanged.

`CapacityService.reserve(key, project_id, request_hash, resources, session=...,
details=...)` returns false when a new job cannot fit. It never creates a Docker
container. Stable native/slicer/checker keys retain their exact resource and input
binding on replay. Nonblocking PostgreSQL transaction advisory locks serialize the aggregate
check and insertion across processes, including initial pool creation. Different
limits for an existing pool fail closed; changing them requires an explicit drained
operator change, not an implicit worker restart. SQLite write locks support local
fixtures only. The application tables require the new capacity migration.

Native admission waits during host authorization, before the tool-effect claim,
with cancellation and original deadline checks. No model call, new action identity,
extended deadline or workflow state machine is introduced by waiting. New requests
pin `capacityPoolId`. Unreserved pre-admission native/slicer compute blocks new
admission until it is reconciled; unknown old resource contracts are not guessed.

Reservations do not expire with heartbeats or deadlines. Stopping a process does
not release its scratch allocation. Adapters release only after verifying the
owned container was removed/absent; the persisted proof binds the reservation's
container name and request hash. Missing or failed deletion retains the reservation.
The native `_dispatch` control receipt is written before first creation. If that
request's response is lost, an absent lookup cannot rule out late creation, so
termination/release remain blocked until the owned container is actually observed
and removed. This deliberately exposes unresolved capacity rather than silently
oversubscribing. Native maintenance skips completed cleanup receipts and never
clears uncertain engineering action history.

This is compute admission, not a workflow scheduler, a measured cost allowance,
an account spend limit or qualification of arbitrary host capacity. Operators must
leave host/control-service headroom and must not create independent pool IDs for
the same physical resource. Uncertain checker/slicer creation needs the same
explicit reconciliation; no lease timeout can prove a container is gone.

## Source contracts

| Capability | Sources | Candidate exports |
| --- | --- | --- |
| CAD | `model.py`, importing build123d, defines `parts: dict[str, Shape]` with 1–24 stable physical IDs. Other task-scoped source files may be imported explicitly. | Per-part STEP and STL plus `parts.json`; optional SVG projections. Manufacturing geometry remains in its actual assembly coordinates. |
| PCB | `circuit.kicad_sch` and `board.kicad_pcb`, with all local libraries included in input closure. | Accepted circuit/reference validation, fixed offline DSN/Freerouting/SES routing, ERC/DRC, routed project ZIP, Gerbers and drills. All checks must complete without violations. |
| Firmware | Complete Zephyr app (`CMakeLists.txt`, `prj.conf`, `src/main.c` and any referenced sources). Explicit `interfaces.firmware.board`, allowed by the installation's qualification report. | `firmware.elf` and `firmware.bin`. Only `nrf52840dk/nrf52840` currently qualified; no implicit board, firmware execution or device flashing. |
| Application | Complete Flutter project, including `pubspec.yaml`, `pubspec.lock`, `lib/main.dart`, web entry files and bundled assets. Dependencies must already exist in the pinned offline cache. | `companion-web.zip`. APK packaging is not implemented here. |

Input artifact paths retain their source names. Prior output artifacts appear at
`inputs/<artifactId>/<basename>`. Duplicate paths fail. Source closure is limited
to 50 files/16 MiB; patches are at most 1 MiB and require the exact previous hash
(or null for a new file). Only task `allowedPaths` are editable. A patch invalidates
previous build outputs. `read` returns a bounded byte window and full-file hash.
`analyze` and `render` currently apply only to CAD. Render accepts `front`, `top`,
`isometric` and is illustrative, never physical validation. A successful build
observation is `UNKNOWN`, not a validation `PASS`.

`finish` is a trusted handshake: all source and export bytes are hashed, limited,
checked for symlinks/hardlinks and tied to the durable job/action envelope. The
gateway verifies the source manifest against authorized patches and binds export
names to each lane's fixed contract. Publication records candidates only through
the injected callback; it cannot accept its own work or replace canonical sources.
The callback independently fences cancellation/revision and checks baseline source
hashes. Total finished file bytes are limited to 32 MiB.

## Native image and resource boundary

Build `execution/Dockerfile` with backend as context and a pinned toolchain parent.
The Dockerfile removes known old application/package locations and installs only
the newly written protocol/supervisor. Each parent image requires an independent
inspection for additional old-code locations before qualification. Never invoke
an old runner or qualify this image by source override.

The control process uses Docker HTTP; its socket is never mounted into a job.
Native containers have a read-only root, network `none`, private IPC, no privilege
escalation, dropped capabilities, capped CPU/memory/PIDs and a fixed deadline.
Only the trusted supervisor starts as root; generated subprocesses use UID/GID
10001, an explicit environment, no provider/database credentials, and inherited
libseccomp filters denying non-Unix sockets. The supervisor checks the network
namespace again. There is no silent fallback if socket filtering is unavailable.
An explicit `docker_desktop_compatibility=True`, `deployment='local'` mode verifies
the Docker daemon identifies as Docker Desktop and installs a daemon-side profile
derived from the pinned Apache-2.0 Docker default profile, with only Unix socket
creation allowed. The child verifies that filter is active and rejects IPv4,
IPv6, packet and netlink sockets. This mode is forbidden for ECS/nonlocal deployment.
Double-forked generated processes are stopped before files are inspected.

Every current lane uses hard size-limited tmpfs. Flutter web uses a private 4 GiB
tmpfs with writable SDK/pub copies, 6 GiB RAM and 256 PIDs. Only its scratch mount
is executable so private Dart binaries can run. There is no named-volume or soft
quota fallback. Android packaging is not qualified by the web fixture.

The exact local PCB image passed reference, routing and manufacturing-export qualification
documented in `qualification/PCB.md` and `qualification/pcb-exact-image.json`.
The exact local CAD image passed the two-part qualification documented in
`qualification/README.md` and `qualification/cad-exact-image.json`. This qualifies
that image's local Docker Desktop protocol only. Explicit NRF52840 firmware
compilation and Flutter web compilation passed separately in `qualification/FIRMWARE.md`
and `qualification/WEB.md`. Other boards, Android/iOS, device execution and
nonlocal/strict Linux deployment remain unqualified. Leave unsupported scopes
unregistered. No paid calls or physical work ran during these checks.

## Exact-source native-only rebuilding

`rebuild_snapshot(session, actor, job)` accepts only terminal jobs with no pending
action and a trusted `_termination` control receipt. The Docker boundary verifies
job/protocol labels and pinned image, stops the immutable container ID and confirms
it is stopped or absent. A timeout or terminal database status is not that proof.
Authorized patch bytes are stored in the immutable blob store before dispatch;
only a verified successful response advances the durable source manifest.

`invoke_rebuild(scope, task, snapshot, action, arguments)` is a server-only interface,
never a model tool. It requires the exact admitted objective snapshot and bounded
grant, preserves the original image, resource, network, interface and source
contracts, and allows only build/render/finish. New settings cannot silently change
an existing contract. Missing original time bounds, lost source blobs, uncertain
actions or missing termination proof block admission. Rebuilds create new linked
history, retain prior spend, publish candidates only and use native action receipts
for recovery without a new model or retrieval call.

## Accepted PCB authority

`project.data.interfaces.circuit` contains `references` (unique reference, value,
footprint and pin-to-net map), injective `conceptReferences`, and `constraints`
(maximum width/height, minimum clearance/track width, explicit copper layers).
The concept-map keys must exactly match the authoritative user-approved
`approvedDesign.peripheralConceptIds`. Missing, duplicate or invented mappings
fail before Docker dispatch. Each job pins this full snapshot and hash. A model
cannot grant authority by writing a JSON source file. Only the narrow two-layer
contract in `qualification/PCB.md` is currently qualified.
