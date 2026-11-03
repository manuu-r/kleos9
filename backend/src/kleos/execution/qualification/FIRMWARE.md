# Exact local firmware qualification — 2026-11-02

**Passed: compile only for `nrf52840dk/nrf52840`.**

Image `sha256:1ea0aa4e85133be3a85fd7399d53c94cb2d1dc6d426b603235fcf44beb93e3bc`
uses the inspected toolchain parent
`sha256:1df009d82d363e87a0f5d779ecaf172d9349aba6f15c380c037fd35d339fb313`.
Old backend packages, distribution metadata and `/opt/kleos` were removed before
installing the new runtime. `firmware-image-audit.json` records its exact files.

Tools: Zephyr 4.2.0 at `413b789deb391d3a37d06b463288a5fe765ee57e`, west 1.5.0,
GNU Arm compiler 12.2.1. Resources: 2 CPUs, 4 GiB memory/no additional swap,
128 PIDs, 2 GiB bounded tmpfs and a ten-minute session deadline. The run uses
explicit local Docker Desktop daemon seccomp. No source override was used.

`qualify_firmware.py` supplies a fresh three-file Zephyr app; it does not copy old
backend templates. The fixed native build produced a real ELF32 ARM executable
and nonempty BIN. Qualification checks the ELF architecture/header, output size,
all source/artifact hashes, candidate-only publication and idempotent finish.
`firmware-exact-image.json` is the final report; `firmware-attempt-1.json` preserves
the raw successful run.

The accepted interface is exactly `interfaces.firmware = {"board":
"nrf52840dk/nrf52840"}`. The gateway requires an explicit choice, checks it against
`NativeSettings.firmware_boards`, and pins the target and hash in the durable job.
The settings allowlist defaults empty and must come from the qualified
installation report's `firmwareBoard`. The supervisor also verifies this snapshot.
There is no silent default board. Installed `native_sim` support is not qualified
by this report and remains unavailable unless separately verified and configured.

No firmware was executed, emulated, flashed or connected to hardware. Compilation
is not evidence of device behavior, peripheral wiring, timing, BLE behavior or
physical acceptance. No paid or cloud work ran.

Opt-in reproduction from repository root:

```bash
PYTHONPATH=backend/src backend/.venv/bin/python \
  backend/src/kleos/execution/qualification/qualify_firmware.py \
  --image sha256:1ea0aa4e85133be3a85fd7399d53c94cb2d1dc6d426b603235fcf44beb93e3bc \
  --socket /Users/manu/.docker/run/docker.sock \
  --output /private/tmp/kleos-firmware-qualification.json
```
