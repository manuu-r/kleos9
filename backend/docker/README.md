# Container builds

Build from the repository's backend directory:

    docker build --platform linux/amd64 -f docker/Dockerfile -t kleos-control:0.1.0 .
    docker build --platform linux/amd64 -f docker/Dockerfile.native --target mechanics -t kleos-mechanics:0.1.0 .
    docker build --platform linux/amd64 -f docker/Dockerfile.native --target validate -t kleos-validate:0.1.0 .

The other native targets are electronics, firmware, and application.
An optional renode-validator target adds a pinned UART emulator adapter; its
checked-in qualification manifest keeps it unavailable until the actual specimen
has passed. See renode/README.md for the image checksum, board and promotion steps.
Record their final ECR digest and tool versions in deployment inputs. Building a
tool image does not qualify a board, layout flow, simulation, or dependency set.
Run the golden engineering fixtures before enabling a new image.

The control image runs the API by default. Override its command with
python -m kleos.worker --kind engineering or
python -m kleos.worker --kind connectors for the worker services.

Native tasks must run the image entrypoint with KLEOS_JOB_ID,
KLEOS_JOB_TOKEN, and KLEOS_INTERNAL_BASE_URL. The supervisor starts as root
only to drop generated subprocesses to UID 10001. Its task has no AWS task role,
no provider credentials, no database access, and no outbound internet route.
Do not run the native runner directly on a development host. The token permits
reading exactly one job's pinned artifact closure and submitting one candidate
result. In session mode it also polls and completes that job's bounded tool steps;
the trusted worker chooses those steps. It cannot accept a release.

The application build is offline. Pre-cache any selected Flutter template's
dependencies in the image; arbitrary generated package dependencies remain
unsupported. The firmware profile uses the GNU Arm Embedded toolchain and a
pinned Zephyr tree. Board and peripheral support must be qualified separately.
See templates/ELECTRONICS.md for the pinned atopile configuration, required
exports and the explicit circuit-correspondence qualification blocker.
An accepted watch-telemetry-v1 scope enables bounded independent C/Dart protocol
tests alongside real compiled outputs. Templates and the exact API contract are
in templates/watch-telemetry-v1. BLE, sensor/display and physical validation remain
UNKNOWN; explicitly requested peripheral coverage blocks release. A successful
protocol harness is not evidence of simulated or physical hardware behavior.
Nonblocking scope requires explicit accepted requiredPeripheralCoverage: [] and
all six named excludedCoverage entries in the template contract. Omitted or
conflicting exclusions remain required UNKNOWNs.
Companion output includes a complete compressed web package plus inspectable
index.html/main.dart.js artifacts. Large compiler intermediates are allowed in
bounded scratch space while final transfer limits remain enforced. The JavaScript
build disables Flutter's optional Wasm dry run.
The pinned SDK's Apache-licensed Roboto font is copied into the project and added
to pubspec.yaml when the default family is absent, preserving existing families.
Its font/license hashes are verified. Each build returns companion-source.zip with
the exact prepared source, font, license and preparation manifest; the runnable
web ZIP also contains the license. Builds reject changes to these captured source
files. Rebuild a candidate to apply this preparation; old artifacts are immutable.

Run the mechanical image smoke with no network and a read-only root:

    docker run --rm --platform linux/amd64 --network none --read-only --tmpfs /tmp:rw,size=512m --entrypoint python -v "$PWD/docker/smoke_mechanics.py:/smoke.py:ro" kleos-mechanics:0.1.0 /smoke.py

It exports real STEP/STL files, reparses the STEP in an independent process and
checks dimensions and actual clearance intersections. A hollow enclosure passes;
a solid block with matching outer dimensions fails. Component lower corners are
relative to the centered internal box's minimum. STEP contains enclosure solids
only; component visualization solids belong in a separate preview artifact.
Other lanes correctly remain UNKNOWN in this smoke.
build123d 0.10.0's OpenCascade wheels require linux/amd64 for the pinned image;
building this target natively on an ARM developer machine is unsupported.

The local Compose file starts PostgreSQL and Qdrant only by default. Its
Conductor profile is explicitly optional and development-only; set
DBOS_CONDUCTOR_IMAGE to an approved pinned image and provide the license before
using it. Do not deploy its unauthenticated local configuration to AWS.

## Local complete backend

With development authentication, a persistent session key and explicit allowances
configured in backend/.env, start the real control plane from the repository root:

    docker compose -f backend/compose.yaml --profile backend up --build -d api engineering connectors

The API listens on localhost:8000; health is /api/v1/health. Existing PostgreSQL
and Qdrant volumes are preserved. Alembic runs before the API starts. The services
load .env at runtime; .dockerignore excludes credentials, local data and virtual
environments from image build contexts. Source mounts are read-only; restart the
appropriate service to load a code change.

Local native execution is available only in development/test. Trusted worker
containers receive the Docker socket, which confers control over the local Docker
daemon; never expose these workers to arbitrary untrusted plugin code. Native
containers receive no socket, provider credentials, database credentials or network.
Docker Engine API 1.45 or later is required for per-job volume subpaths.
Trusted API/workers share UID 1000 so private artifact files remain readable by
both. Set DOCKER_SOCKET_GID to the socket's group ID on Linux hosts; Docker Desktop
uses the default 0 here. This grants the trusted workers socket access without
making artifact files readable by all local users.
Each job pins its actual local image ID, carries the normal artifact hashes and
fence, uses a separate 0700 control directory, and returns a supervisor-written
result bound to the request hash. Generated UID 10001 cannot modify that directory.
Admission, cancellation, budget and release gates remain the same as AWS.

New local jobs pin per-image CPU, memory, temporary storage and process ceilings
from `kleos/native-resources.json`. Defaults range from 1 CPU/2 GiB for CAD to
4 CPUs/8 GiB for Android, with 256 processes; build parallelism is bounded.
Global and per-project admission limits are independent. Existing jobs retain
their original pinned resources. See [native capacity](../../infra/native-capacity.md).
Application jobs use a private disposable disk volume for `/tmp` so Android's
toolchain copies do not consume several GiB of RAM. Docker removes this anonymous
scratch volume during job cleanup; it has no hard disk quota. Other lanes retain
their bounded tmpfs. The application root remains read-only.
An out-of-memory result remains a visible resource failure. Other projects or
containers are not stopped to obtain memory. Missing tool images return an explicit
unavailable error; their configured names do not imply qualification.

The application image includes the pinned BLE dependencies and offline Android
toolchain described in [android-toolchain.md](android-toolchain.md). APKs use a
public development certificate. iOS delivery remains source-only.

Build the native targets one at a time and run their actual fixtures before use.
The local CAD refresh Dockerfile may reuse the previously inspected qualified CAD
dependency image; the dispatcher then pins the refreshed immutable image ID.
The temporary-ledger integration smoke exercises a real Docker CAD job without
calling a model or changing a user's project:

    docker compose -f backend/compose.yaml run --rm --no-deps -T engineering python - < backend/docker/smoke_docker.py
