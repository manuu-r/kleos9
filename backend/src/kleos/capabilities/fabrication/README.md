# Fabrication: local Bambu connections

The first fabrication capability is real, bounded LAN discovery, TLS identity
verification and MQTT status observation. It reimplements the earlier Bambu
connection semantics without importing the deleted backend. Supported enrollment
models are A1 mini (`N1`), A1 (`N2S`), P1S (`C12`) and X1 Carbon (`BL-P001`). This
identification list does not qualify every printer or firmware for physical work.

## Integration interface

Construct `BambuConnections(enabled=...)` once per local process. Its methods:

- `await discover(access)` -> `DiscoveryView`
- `await probe(access, ProbeRequest)` -> unverified `CertificateView`
- `await pair(access, PairRequest)` -> `ConnectionState`
- `await refresh(access)` -> `ConnectionState`
- `state(access)` / `forget(access)` -> `ConnectionState`

Models use the shared camelCase `WireModel`. Constructor-injected discovery,
certificate verification, MQTT transport, monotonic clock and `CredentialStore`
allow module tests without hardware. Call `close()` during application shutdown. `PairRequest.access_code` is a masked
`SecretStr` excluded from serialization. Returned states never contain secrets.

The API constructs `LocalAccess(actor, local_admin=True)` only after authenticating
an administrator and verifying explicitly enabled development mode, actual
loopback client/hostname, permitted local Origin and absence of forwarding headers.
It must return a fixed validation error for these routes: Pydantic/FastAPI error
input fields can otherwise echo a rejected access code. The service defaults off.
LocalAccess is trusted server authorization, never an agent/client input.

## Boundaries

Only literal RFC1918 IPv4 destinations and fixed MQTT8883/FTPS990 ports are used.
Certificate inspection sends no credentials and proves no identity. Automatic
pairing validates the pinned public printer CA bundle and selected serial; owner
verified manual leaf pins are also supported. The subsequent MQTT socket checks
the exact leaf fingerprint before sending an access code. Observation subscribes
only; it never publishes a command. Retained data is explicitly unknown and cannot
identify a current print. Normalized progress is never physical acceptance.

The default development credential adapter is bounded expiring process memory.
For restart persistence inject `EncryptedCredentialStore(project_store.sessions,
key_bytes)` using a deployment-owned persistent 32-byte encryption key. Register
its `PrinterCredential` model in the new database migration. It stores only
AES-GCM ciphertext, expiry and operation generation in `kleos2_printer_credentials`;
tenant/user identity is authenticated as additional data, so ciphertext cannot be
moved between principals. The key stays outside the database and agent settings.
Wrong keys or tampering fail closed without destroying recoverable ciphertext.

Both stores enforce a non-sliding 30-minute lifetime and bounded capacity.
The durable adapter persists UTC expiry and reconstructs remaining monotonic time,
so service restarts do not extend access. Reads/claims purge expired rows;
`purge_expired()` is also available for periodic maintenance. Memory-adapter timers
erase idle entries. Refresh transient network failures preserve pairing until its
original expiry; identity/authentication failure discards it. Store claim/commit
generations reject late results across service processes, including a concurrent
forget/re-pair. Callers cannot resurrect a forgotten pairing by committing an old
observation. PostgreSQL advisory locking and SQLite immediate transactions bound
capacity and serialize the small credential ledger.

The memory adapter requires pairing again after process restart; the encrypted
adapter recovers only unexpired pairings. Neither adapter exposes credentials to
LangGraph checkpoints, learned memory, artifact storage or logs. Closing the
service clears in-flight local claims; durable ciphertext stays until expiry or
explicit forget, while closing the memory adapter erases its entries.

Discovery has a 3-second deadline, 256-packet and 32-result limits; MQTT observation
has a 20-second deadline and bounded frames/packet count. All raw device/socket
errors are mapped to fixed public messages. Slicing, uploads, printing and
physical actions are outside this connection service and remain unavailable
until independently authorized execution adapters exist.

## Public certificate provenance

`bambu_ca.pem` is the preserved unmodified public Bambu Studio printer bundle,
SHA256 `36f2bcee347ec7adce719b5fd350099591a4d3d0ec4e039c7019890d78e152a0`,
from revision `79df7ae309be398ed30c9f397c13c9911c2edc57`,
[`resources/cert/printer.cer`](https://github.com/bambulab/BambuStudio/blob/79df7ae309be398ed30c9f397c13c9911c2edc57/resources/cert/printer.cer).
It contains five public certificates and no private keys. The original observed
X1 Carbon CA/serial handshake qualification is archived; this rewrite has only
offline fixture verification until explicitly checked against hardware.

Run: `cd backend && .venv/bin/pytest tests/test_bambu.py -q`.

## Offline print preparation

The fresh `slicing.py` service converts triangulated OBJ or STL into a sanitized
binary STL, removes duplicate and degenerate triangles, repairs face winding,
and rejects open, nonmanifold, disconnected, oversized or invalid-coordinate
meshes. It runs OrcaSlicer 2.4.2 with fixed 0.4 mm / 0.20 mm / Bambu PLA Basic /
Textured PEI presets for A1 mini, A1 and P1S. Input 3MF settings, custom G-code,
STEP conversion, hole filling and automatic supports are not qualified. Export
STEP to STL in the isolated CAD lane first. Self-intersection and actual fit,
strength, printability and physical behavior remain `UNKNOWN`.

`PrintPreparations.create` stores a queued request transactionally with an event
and idempotency receipt. The separate fabrication worker calls `process_next`
and `reconcile`. It pins source hash, engineering revision, tool image and preset
bundle. Cancellation fences publication immediately; changed engineering context
cannot publish a current package. A crashed or missing container fails its
preparation instead of silently repeating work. Successful packages are candidate
artifacts, presented as `ready_for_operator` to the unchanged workbench.

The rewritten supervisor runs in a read-only, network-disabled container with
2 CPUs, 4 GiB RAM, 128 processes, 2 GiB disposable scratch, bounded files and a
570-second child deadline. The worker uses UID10001, zero effective capabilities
and no-new-privileges. A pinned daemon seccomp baseline permits Unix sockets only;
startup verifies denied IP/netlink/packet sockets. Trusted results live in a
root-only Linux tmpfs and are transferred through a fixed Docker exec reader.
Host bind permissions are not used as the result trust boundary. The worker
cannot write either trusted control location. The image removes old backend
Python and receives no credentials, database connection or Docker socket.

Preparation accepts a current project mesh for focused work without requiring a
fixed release workflow. If a release ID is supplied, it must be the project's
accepted release. Client completion measurements cannot substitute for native
provenance. Dispatch requests create durable `blocked` records; upload, start,
physical acceptance and claims about printer cancellation are unavailable.

`qualify_slicing(installation, control_root)` validates an operator-supplied exact
image/report pairing before exposing profiles. The installation keys are `image`,
`report`, `dockerDesktop: true`, optional `deployment: "local"` and
`daemonControlRoot`. The report binds the current supervisor hash, Docker syscall
baseline, preset bundles, environment, archive checks and isolation observations.
No frontend-supplied qualification flag is accepted. The frozen `studioVersion`
field remains `2.8.2.61` for DTO compatibility; `slicer: "orca"` and
`slicerVersion: "2.4.2"` identify the tool actually used. Bambu Studio is not run.

The exact-image report is
[`qualification/slicer-qualified-2026-11-02.json`](qualification/slicer-qualified-2026-11-02.json).
It covers a closed 20 mm cube for all three profiles on local Docker Desktop
AMD64 emulation, plus rejection of an open mesh and a modified preset. It establishes digital fixture preparation and isolation only,
not general shape correctness, hardware compatibility, ECS or printing. Earlier
failed namespace checks and initial passes are retained separately.

Official Orca sources consulted:
[CLI mode](https://github.com/OrcaSlicer/OrcaSlicer/wiki/cli_mode),
[CLI actions](https://github.com/OrcaSlicer/OrcaSlicer/wiki/cli_actions),
[CLI miscellaneous options](https://github.com/OrcaSlicer/OrcaSlicer/wiki/cli_misc).
The installed binary's help and bundled BBL profiles were inspected directly.
CLI bounds `--mtcpp` and `--mstpp` are fixed; `--no-check` is never used.

Run focused checks from `backend`:

```sh
.venv/bin/pytest tests/test_slicing.py tests/test_fabrication_api.py -q
```

The explicit local `qualification/qualify.py` command runs only fixed synthetic
meshes inside the pinned isolated image; it never contacts a printer.

Slicer workers now use the same durable capacity pool as native generation and
independent CAD checking. A slicer claim atomically reserves 2 CPU, 4 GiB memory
and 2 GiB + 64 MiB scratch before Docker creation. Busy jobs stay queued. Result collection,
cancellation and failure retain the reservation until identity-checked removal is
confirmed; a successful result by itself does not release compute admission.
