# Exact local CAD qualification — 2026-11-02

**Passed, limited to local Docker Desktop and the recorded CAD image.**

- Image: `sha256:62280256393a1f822fa1a256eb2991220bb2ec225f7e17894dbb52ec7a02d918`
- Toolchain parent: `sha256:50fe4f6f1c6f90bcd7a0886e09f313136edd7a72ecdfbe2b2bc4ea67603c8173`
- build123d: `0.10.0`; Linux amd64 image on the local Docker Desktop daemon.
- Protocol: `kleos-native-session-v2`.
- Mode: `docker-desktop-daemon-seccomp`, explicitly local-only.
- Resources: 2 CPUs, 4 GiB memory/no extra swap, 128 PIDs, 2 GiB scratch tmpfs,
  128 MiB supervisor tmpfs, ten-minute session deadline.
- No source overrides, paid inference, cloud calls or physical operations.

`cad-exact-image.json` combines the actual successful run with a final read-only
image filesystem/source audit. `cad-attempt-6.json` is the unchanged raw success
record. Earlier attempts are retained as scoped failure evidence.

The parent contained old backend code at `/opt/kleos`, the installed
`site-packages/kleos` package, and `kleos_backend-0.1.0.dist-info`. The child image
removed these, related distribution metadata and old CLI scripts before installing
only the new supervisor, protocol, bootstrap and result reader. A whole-filesystem
read-only search, excluding kernel pseudo-filesystems, found only the new package.
`cad-image-audit.json` records its exact file hashes and dependency version.

The fixture builds a bored base and separate lid with stable physical IDs. It
checks real exported part volumes and dimensions, STEP/STL file content and hash,
SVG previews, eight candidate artifacts, scoped editing, and idempotent finish
replay. These are runtime/geometry fixture checks, not independent manufacturing
acceptance. Exported coordinates are the actual part coordinates.

The generated subprocess fixture also verifies UID/GID 10001, zero effective
capabilities, no provider/database credentials, no-new-privileges, an enforced
seccomp filter, denied IPv4 sockets, read-only root and input mounts, inaccessible
private supervisor results and exact cgroup CPU/memory/PID limits. The supervisor
separately verifies IPv6, netlink and packet socket denial before launching work.

## What the failures found

1. Initial startup treated Linux `bonding_masters` as an interface directory.
   The corrected check considers only interface directories; a regression covers it.
2. The diagnostic rerun captured that same startup fault.
3. In-process libseccomp could not install a filter under local amd64 emulation.
   The explicit Desktop mode uses a daemon-installed filter retaining the official
   Docker default syscall baseline with stricter Unix-only socket creation. It is
   refused for nonlocal deployments and daemons that are not Docker Desktop.
4. Docker Desktop file sharing did not provide the expected Linux permission
   boundary on the host bind mount. The mount is now read-only; journals/results
   are private Linux tmpfs, inaccessible to generated UID 10001.
5. Docker archive transfer could not see that tmpfs. A fixed, bounded, root-only
   result reader now transfers JSON through Docker exec. Its only argument is a
   server-derived SHA-256 filename; no shell or agent-selected command is accepted.
   Finish waits for acknowledgement of durable result receipt.
6. All fixture actions and security assertions passed with these corrections.

The daemon profile derives from the Apache-2.0
[Moby default seccomp profile](https://github.com/moby/profiles/blob/6fe7deb1b9fb7c0397a4593480d7d22b9ee8caef/seccomp/default.json).
The original bytes, license and pinned SHA-256 are in `../security/`. The final
restricted profile fingerprint is recorded in the success report; qualification
must match both image and network policy.

## Reproduce locally

From repository root, with the exact image already present and the backend virtual
environment installed:

```bash
PYTHONPATH=backend/src backend/.venv/bin/python \
  backend/src/kleos/execution/qualification/qualify_cad.py \
  --image sha256:62280256393a1f822fa1a256eb2991220bb2ec225f7e17894dbb52ec7a02d918 \
  --socket /Users/manu/.docker/run/docker.sock \
  --output /private/tmp/kleos-cad-qualification.json
```

This is an explicit native workload, not a unit test. The host script only drives
Docker and inspects JSON/artifact bytes; CAD code executes inside the fixed image.
The script removes only its own disposable containers; temporary qualification
state and artifact bytes remain available under the system temporary directory.

This report does **not** qualify PCB, firmware, Flutter, strict in-process Linux
mode, ECS, arbitrary tool plugins, fabrication or general device acceptance.
