# Strict Linux-engine qualification — 3 November 2026

The earlier CAD and PCB records were made on Docker Desktop with its daemon-side socket filter. These runs use a
plain Linux Docker Engine instead (the supervisor installs the libseccomp socket filter itself), with toolchain
parents built from `../toolchains/Dockerfile`. They are separate, narrowly scoped records, not replacements.

Environment: Docker Engine 29.9.2, Linux kernel 6.18 (cgroup v1, no IPv6), container network `none`.

| Lane | Native image | Toolchain parent | Evidence |
|---|---|---|---|
| CAD | `sha256:9896224c9b02bfafa67703fd89222ceb6de6e396fc5002e8670a7db12a529697` | `sha256:4765e4236200f30190dee984c5639f84fac1249c4a9135c153fdf2d3b26e79fa` | `cad-linux-strict-2026-11-03.json` |
| PCB | `sha256:ed5fad655ba694c3ba6f1c3249cafa032c716d378ab12522f2e0f705e7f5a904` | `sha256:57d564558ad4940153bc500a2235e5d1fba82b06feafd1a224bb8e097f136270` | `pcb-linux-strict-2026-11-03.json` |

Commands: `python -m kleos.execution.qualification.qualify_cad|qualify_pcb --image <native image ID>
--parent-image <toolchain ID> --strict-linux --output <file>`.

**CAD** ran the production gateway against the image: a fresh build123d two-part model was patched, built,
measured against closed-form volumes, rendered and exported (eight artifacts, STEP/STL content and hashes
checked), finish replay was idempotent and a path-scope escape was rejected. The generated fixture itself also
asserted UID/GID 10001, zero effective capabilities, no-new-privileges, an enforced seccomp filter, denied IPv4
sockets, read-only authority paths and the exact CPU, memory and PID cgroup limits.

**PCB** ran the fixed two-layer fixture through real KiCad 9.0.8 netlist export, ERC, Freerouting 2.1.0
routing, DRC and Gerber/drill export (28 artifacts including `routed.kicad_pcb`), and rejected wrong and missing
accepted references.

Findings fixed while running them: the supervisor treated a missing `/proc/net/ipv6_route` (a kernel without
IPv6) as a failure (now only that case is tolerated, regression in `tests/test_execution.py`), and the CAD
fixture's cgroup assertions only knew the v2 layout (now layout-aware, same limits). Both scripts gained
`--strict-linux` and `--parent-image` so they no longer hardcode the Docker Desktop mode or an old parent ID.

**Not established:** independent CAD/PCB checker images (those adapters still require Docker Desktop and were not
changed or re-run), Flutter web and Android lanes, any composed multi-lane release, hosted/ECS execution, or
physical acceptance. A different kernel (cgroup v2, IPv6) was not tested by these runs.
