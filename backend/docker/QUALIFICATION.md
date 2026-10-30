# Engineering execution verification — 2026-10-30

These are actual local checks. AWS resources were not deployed and hosted model
credits were not used by this verification.

| Check | Result |
|---|---|
| Execution contracts/security, deployment, Docker and Renode adapter tests | 71 passed; 11 independent review regressions also passed |
| Python lint for execution, native runner, bootstrap and smoke | Passed |
| Terraform formatting and provider-backed validation | Passed; AWS provider 6.71.0, random 3.9.1 |
| Local Compose PostgreSQL 17.6 | Healthy; kleos, kleos_dbos and conductor databases present |
| Local Compose Qdrant 1.15.5 | HTTP 200 healthz |
| Control Docker image | Hash-locked runtime dependencies installed; API, worker and execution imports passed with network disabled |
| Isolated CAD smoke | Real STEP/STL export; hollow-enclosure clearance passed, solid-block obstruction rejected |
| C and Dart protocol fixtures | 30 frame vectors each, actually compiled/executed by independent harnesses |
| Trusted companion Flutter web fixture | Analysis passed; actual build/web package produced with Flutter 3.47.8 / Dart 3.13.7 |
| Renode adapter | Contract and failure tests passed; actual portable runtime starts, UART/board qualification remains unavailable |
| Local Docker native CAD adapter | Real STEP/STL job passed with no network, 1 CPU and 1 GiB memory |
| Local Docker Zephyr native_sim | Real watch-telemetry-v1 fixture compiled to a 381,000-byte ELF with no network and 1 GiB memory |
| Local Docker Flutter 3.35.4 | Offline dependency resolution, analysis and release web build passed at 1 CPU / 1 GiB; complete ZIP 10,216,684 bytes |
| Full validator image | Actual GCC 12.2 and Dart 3.9.2 independently executed all 30 protocol vectors each under 256 MiB |
| KiCad 9.0.8 negative fixture | Actual ERC/DRC reported zero violations on an empty circuit; independent XML netlist rejection correctly blocked it |
| atopile image | Actual atopile 0.12.5 CLI ran offline in Python 3.13.7; circuit/layout qualification remains unavailable |

## Reproduce the checks

From the repository root:

    backend/.venv/bin/python -m pytest backend/tests/test_execution.py -q
    backend/.venv/bin/ruff check backend/kleos/execution.py backend/kleos/native_runner.py backend/tests/test_execution.py backend/docker
    terraform -chdir=infra init -backend=false
    terraform -chdir=infra validate
    docker compose -f backend/compose.yaml up -d postgres qdrant
    docker compose -f backend/compose.yaml ps
    docker build --platform linux/amd64 -f backend/docker/Dockerfile -t kleos-control:smoke backend
    docker run --rm --platform linux/amd64 --network none kleos-control:smoke python -c 'import kleos.api, kleos.worker, kleos.execution'
    docker build --platform linux/amd64 -f backend/docker/Dockerfile.native --target mechanics -t kleos-mechanics:smoke backend
    docker run --rm --platform linux/amd64 --network none --read-only --cpus 2 --memory 2g --tmpfs /tmp:rw,size=1024m --entrypoint python -v "$PWD/backend/docker/smoke_mechanics.py:/smoke.py:ro" kleos-mechanics:smoke /smoke.py

The CAD smoke executed on linux/amd64 with no network, a read-only root, a bounded
temporary filesystem, UID-separated generated subprocesses, process/resource
limits and independent parsing. A 40 × 30 × 10 mm enclosure with a centered
38 × 28 × 8 mm cavity produced valid STEP and STL. The independent inspector
measured volume=3488 mm³, solidCount=1, valid=true, zero internal obstruction
and zero intersection with the declared battery envelope: PASS. A solid block
with identical outer dimensions was rejected: its internal obstruction measured
8512 mm³ and battery intersection measured 1500 mm³. The dimension tolerance is
0.1 mm and the intersection tolerance is 0.0001 mm³.

STEP exports contain enclosure solids only. Component positions are lower corners
relative to the minimum corner of the centered internal clearance box. The
interface validator independently rejects component-component overlap. Arbitrary
assembly geometries without this declared clearance profile remain unsupported.

The image was iteratively tested using the same pinned build123d 0.10.0 and
cadquery-ocp 7.8.1.1.post1 packages, adding missing GL/Xrender system libraries and
pinning ocp-gordon 0.1.18. The final test image used those corrected dependencies
and the current native_runner.py. The Dockerfile contains those fixes. Avoid
unconstrained ocp-gordon upgrades: newer versions require a different OCP API.

To reproduce the companion build, create a temporary Flutter web project, replace
its lib/ and pubspec.yaml with templates/watch-telemetry-v1/application, remove the
generated counter-app test and lint configuration, then run:

    flutter pub get --offline
    flutter analyze --no-pub
    flutter build web --no-pub --no-web-resources-cdn

This is a trusted repository fixture. Generated customer code must run only in
the isolated native worker. The container fixture was subsequently built and
executed with Flutter 3.35.4 as recorded above; the earlier host SDK check alone
did not qualify it.

## Limits that remain explicit

The CAD smoke qualifies export, solid validity and declared rectangular clearances,
not a complete smartwatch enclosure, insertion paths, actual wall thickness,
manufacturing tolerances or physical safety.
The C/Dart harness qualifies only watch-telemetry-v1 decoding and malformed-frame
handling. BLE, sensors, display interaction, analog/RF/thermal behavior and real
physical outcomes remain UNKNOWN and cannot satisfy a physical gate.

The complete atopile/KiCad, physical-board Zephyr, additional Flutter dependencies and Renode scenarios still
need tool-image builds and specimen-specific golden fixtures before enabling
production image digests. KiCad is explicitly pinned to a modern backport because
Debian bookworm's default package lacks the required CLI capabilities. Native
jobs have no internet access: qualified libraries, board dependencies and Flutter
packages must already be in the image.
The Renode UART adapter, fixed monitor scenario, Zephyr specimen and optional image
target are implemented. The manifest remains qualified=false; requested emulator
coverage therefore returns required UNKNOWN until real qualification succeeds.
Mock process tests do not count as Renode or peripheral validation.
The portable .NET image actually reported Renode 1.15.3 and exited successfully
with network disabled. The bounded ARM specimen build reached its 45-second
deadline at CPU1/512MiB; it produced no accepted ELF or emulator evidence.
The manifest remains false. That run exposed discarded timeout logs; the source
runner now preserves bounded compiler output and labels deadlines separately
from source errors. This final logging change has unit coverage but the already
built images were not refreshed again during the main journey verification.

The local Zephyr fixture used Zephyr v4.2.0, West 1.5.0 and GCC 12.2 with multilib.
Its initial offline run found West attempting to update a read-only configuration;
the image now records zephyr.base during its build. The repeat succeeded under
UID-separated native execution. This qualifies compilation for native_sim only;
it does not prove board peripheral behavior or qualify the Renode fixture.

The Flutter 3.35.4 / Dart 3.9.2 container fixture passed after separating its
compiler intermediate-file ceiling (256 MiB) from transferable artifacts (16 MiB
each). The optional Wasm dry run is disabled for this JavaScript build. The full
offline web package is compressed as companion-web.zip, with index.html and
main.dart.js also retained as inspectable outputs. The compiled JavaScript measured
1,778,744 bytes. This does not qualify arbitrary dependencies or peripheral/UI
interactions; the independent protocol harness remains a separate check.

During the live application journey, Flutter's analyzer exhausted the original
128-process ceiling and returned an inner Dart thread error. The wrapper initially
misclassified this as a source error, triggering an unnecessary AI repair. The
runner now recognizes that resource error and uses a bounded 256-process ceiling.
The succeeding second attempt is not evidence of a source-repair test.

Browser testing also exposed Flutter's external default Roboto request despite
--no-web-resources-cdn. The image now prepares a verified local SDK font and
publishes source/font/license provenance. Old ZIPs remain unchanged; browser
verification applies only after rebuilding a candidate with this preparation.

Electrical approval is now explicitly blocked by electronics_correspondence:
ERC/DRC are recorded separately, while accepted loads/interfaces must still be
bound to the actual circuit by a qualified template. Independent KiCad XML netlist
export rejects empty components or nets. Passing checks on an irrelevant circuit
cannot unlock a release.

No hosted inference, AWS RunTask call, Cognito login, Conductor license/SSO setup,
backup restoration, paid connector operation, full smartwatch release or
100-client load target is claimed by this report.

The manual deployment workflow and release orchestrator have schema/unit coverage
for task-definition preservation, observed migration success and retaining old
workers when draining fails. They have not been executed against an AWS account.
