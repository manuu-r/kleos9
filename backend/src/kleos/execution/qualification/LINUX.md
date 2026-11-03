# Strict Linux-engine qualification — 3 November 2026

The earlier CAD and PCB records were made on Docker Desktop with its daemon-side socket filter. These runs use a
plain Linux Docker Engine instead (the supervisor installs the libseccomp socket filter itself), with toolchain
parents built from `../toolchains/Dockerfile`. They are separate, narrowly scoped records, not replacements.

Environment: Docker Engine 29.9.2, Linux kernel 6.18 (cgroup v1, no IPv6), container network `none`.

| Lane | Native image | Toolchain parent | Evidence |
|---|---|---|---|
| CAD | `sha256:31729cee7be71109542d7cf4448151d8ef494ee8ec770d9e28ee8c8d9a8474cf` | `sha256:4765e4236200f30190dee984c5639f84fac1249c4a9135c153fdf2d3b26e79fa` | `cad-linux-strict-2026-11-03.json` |
| PCB | `sha256:5c02cf4b3292afb89ce9de121bb616efdc282632ee9799653c61bf01d744d677` | `sha256:57d564558ad4940153bc500a2235e5d1fba82b06feafd1a224bb8e097f136270` | `pcb-linux-strict-2026-11-03.json` |
| Web (Flutter) | `sha256:50f070cf4bae30f529e44f8eb17804e2d4c1d9d7d4dd0973935dbb0e1ad8ec17` | `sha256:a34070c14bff09f3a31285f77f6bbf73d01857a05aa8942c7da1e2ff9f1fed64` | `web-linux-strict-2026-11-03.json` |
| Firmware | `sha256:ce590cbe375da00753b24f9a6b00d1b72ed52ba3e2a481c20be13b91c025ffeb` | `sha256:d149b201aaabc9668b3bdaa86f7170be89033e17630958e1200e3e84bcac53a7` | `firmware-linux-strict-2026-11-03.json` |

Commands: `python -m kleos.execution.qualification.qualify_cad|qualify_pcb|qualify_firmware|qualify_web --image <native image ID>
--parent-image <toolchain ID> --strict-linux --output <file>`.

**CAD** ran the production gateway against the image: a fresh build123d two-part model was patched, built,
measured against closed-form volumes, rendered and exported (eight artifacts, STEP/STL content and hashes
checked), finish replay was idempotent and a path-scope escape was rejected. The generated fixture itself also
asserted UID/GID 10001, zero effective capabilities, no-new-privileges, an enforced seccomp filter, denied IPv4
sockets, read-only authority paths and the exact CPU, memory and PID cgroup limits.

**PCB** ran the fixed two-layer fixture through real KiCad 9.0.8 netlist export, ERC, Freerouting 2.1.0
routing, DRC and Gerber/drill export (28 artifacts including `routed.kicad_pcb`), and rejected wrong and missing
accepted references.

The runtime's evidence gate (`native_qualification`) accepts these records for their exact images under the
`in-process-seccomp` policy; `tests/test_qualification_records.py` enforces that. Findings fixed while running them: Zephyr fell back to a read-only cache path because the supervisor never created
`$HOME/.cache` (it now does, in each job's private home); the supervisor treated a missing `/proc/net/ipv6_route` (a kernel without
IPv6) as a failure (now only that case is tolerated, regression in `tests/test_execution.py`), and the CAD
fixture's cgroup assertions only knew the v2 layout (now layout-aware, same limits). Both scripts gained
`--strict-linux` and `--parent-image` so they no longer hardcode the Docker Desktop mode or an old parent ID.

**Web (Flutter)** ran the fixed four-file companion fixture through offline `pub get`, `flutter analyze`,
`flutter build web` and font packaging inside a 6 GiB / 256-PID job with a 4 GiB hard-bounded tmpfs and network
`none`, producing an 11.4 MB `companion-web.zip` (46 files, canvaskit and fonts bundled locally). The toolchain
matches the earlier record exactly (Flutter 3.35.4 framework `d693b4b9...`, engine `c298091351`, Dart 3.9.2).
Beyond the recorded run, the exact artifact was extracted from the blob store and opened in Chromium with software
GL (`browse_web_bundle.js`): it rendered, the counter went from `Local samples: 0` to `1` after clicking the
button, with no page errors and no requests outside the local server. That check is not part of the JSON record.

**Firmware** compiled the fixed three-file Zephyr application for `nrf52840dk/nrf52840` with Zephyr 4.2.0 (commit
`413b789d...`, the one in the earlier record), west 1.5.0 and GNU Arm 12.2.1, producing `firmware.elf` and
`firmware.bin`. It was compile-only: nothing was flashed or executed.

All four records were re-run after the final supervisor change below, so each image ID corresponds to the
committed code. Earlier runs on superseded images are not retained.

**Not established:** independent CAD/PCB checker images (those adapters still require Docker Desktop and were not
changed or re-run), Android/iOS/BLE application builds, packages beyond the baked default set, any composed multi-lane release, hosted/ECS execution, or
physical acceptance. A different kernel (cgroup v2, IPv6) was not tested by these runs.
