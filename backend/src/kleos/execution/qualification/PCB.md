# Exact local PCB qualification — 2026-11-02

**Passed for the recorded local two-layer routing fixture.**

- Image: `sha256:6e3efde745d5a1e773737bcaf86d56320576b6c2ccf27c57cf6d5b62c3cf421f`
- Parent toolchain: `sha256:7356657ac009106fb31dd7902d0795efe1675e678381597312bb49d166e33a4b`
- KiCad CLI/pcbnew: 9.0.8; Freerouting: 2.1.0; Java: Temurin 21.0.13.
- Router JAR SHA-256: `2c07d58f75dac03782664081e7a58b41c25400d871a9fcf166a2ea6fe60d5def`.
- Protocol: `kleos-native-session-v2`, explicit local Docker Desktop daemon seccomp.
- Bounds: 2 CPUs, 4 GiB memory/no additional swap, 128 PIDs, 2 GiB tmpfs,
  ten-minute native deadline; router heap 768 MiB, two threads and 20 routing passes.
- No model, cloud, paid, fabrication or device work.

The parent supplied installed tools only. All old backend packages, distribution
metadata and `/opt/kleos` were removed before the rewritten runtime was installed.
`pcb-image-audit.json` records a whole-filesystem legacy-code check and hashes of
the exact new native Python files. The existing qualified CAD image/report were
not modified.

`pcb-exact-image.json` combines actual run evidence, the image audit and measured
engineering checks. `pcb-attempt-8.json` is the unchanged raw successful run.
The fixed fixture source files were created inside the fresh isolated image by
`pcb_fixture.py`; no generated KiCad/Python code executed on the host.

The fixture explicitly seeds accepted authority for connector J1, resistor R1,
their exact pin/net maps, two copper layers, minimum 0.25 mm track width, minimum
0.2 mm clearance and maximum 42 × 32 mm outline. The user-approved peripheral
`power-connector` maps uniquely to J1. Native jobs pin this contract and its hash
before their first action. Approval is not inferred from the source files.

The negative check changes R1 to R99, leaving the accepted contract unchanged.
The native checker rejects the extra/wrong reference and missing accepted R1
before routing. After restoring the original source through an expected-hash
patch, the full pipeline succeeds:

1. Export real schematic XML netlist and verify exact component/value/footprint/
   pin/net correspondence in both schematic and board.
2. Export Specctra DSN, route with the pinned offline Freerouting JAR, import SES.
3. Recheck all reference identities, component positions, outline and copper/net
   constraints; require routed tracks.
4. Run actual KiCad ERC and DRC, including unconnected-item checks.
5. Export nine manufacturing Gerber layers, drill, routed board/project/rules,
   netlist, accepted circuit snapshot and a portable project ZIP with local libraries.
6. Publish candidates, verify every returned file hash, and replay finish idempotently.

The run produced **two routed tracks, zero ERC violations, zero DRC violations,
zero unconnected items, and 28 candidate artifacts**. The portable ZIP contains
routed board, schematic, clean project/design-rule files and local symbol/footprint
libraries. It does not inherit input project DRC exemptions. Source input files
remain unchanged; the routed board is a separate candidate output.

The failed attempts preserve actual integration findings: strict hierarchical net
names, KiCad 9 SWIG footprint binding differences, required clean schematic project
files for local library discovery, Gerber filename conventions and the KiCad zone
API. These were fixed rather than suppressing rule failures or relaxing identity
checks. Electrical-rule failures now return a bounded diagnostic report to the
agent so it can make a targeted repair.

## Current supported scope

PCB actions are `read`, `patch`, `build` and `finish`; `build` includes routing.
Only explicit two-copper-layer layouts are supported. Copper zones, multiple
physical pads sharing one logical pin, unnumbered mechanical pads, differential
pairs, controlled impedance and multilayer designs require further contracts and
qualification. Unsupported inputs fail rather than silently changing constraints.
Neither successful routing nor ERC/DRC grants independent acceptance, fabrication
permission or evidence of physical device behavior.

Run the opt-in local qualification from repository root:

```bash
PYTHONPATH=backend/src backend/.venv/bin/python \
  backend/src/kleos/execution/qualification/qualify_pcb.py \
  --image sha256:6e3efde745d5a1e773737bcaf86d56320576b6c2ccf27c57cf6d5b62c3cf421f \
  --socket /Users/manu/.docker/run/docker.sock \
  --output /private/tmp/kleos-pcb-qualification.json
```

The CLI parameters follow the pinned
[Freerouting 2.1.0 configuration implementation](https://github.com/freerouting/freerouting/blob/v2.1.0/src/main/java/app/freerouting/settings/GlobalSettings.java).
The runtime additionally disables analytics/API/UI and retains network isolation.
