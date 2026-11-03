# Independent CAD export checker

`cad-check-exact-image.json` records qualification of image
`sha256:e3c5823091b7dde83d1043bd455ef96567939424ab447a3efed1149dc1030e69`.
This is a separate checker image derived from the already-qualified CAD dependency
image. It contains the trusted parser and removes the generator runtime. Candidate
Python sources are neither mounted nor executed. The retained generator fixture's
four STEP/STL exports are read by their exact hashes.

The six real container cases pass their expected outcomes: valid exports PASS;
incorrect dimension, incorrect byte binding and swapped mesh FAIL; unsupported
strength and unsupported STEP units UNKNOWN. The parser verifies explicit STEP
millimetre units, exactly one valid positive-volume solid per part, finite bounds,
closed consistently oriented STL triangles, STEP/STL bounds (0.02 mm) and volume
(0.5% or 0.02 mm³) correspondence, and the supplied numeric dimensional constraints.
STL is unitless: its coordinates are interpreted under the explicit server-owned
millimetre contract, then compared to the STEP export. No unit is inferred from
an STL filename or model assertion.

The exact local Docker Desktop execution policy is inspected in each run:
read-only root and input mount; user/group 10001; no capabilities; no new
privileges; pinned daemon seccomp denying internet sockets; no network; 2 CPUs;
2 GiB RAM; 64 processes; bounded scratch, 120-second timeout and bounded output.
The checker itself observes its unprivileged execution, socket denial and absent
generator code. No credentials or host Docker socket are mounted. ECS and other
deployment modes are not qualified by this record.

This evidence does **not** establish mesh self-intersection freedom, complete
surface correspondence, fit, stress, materials, manufacturability, PCB behavior,
physical safety or arbitrary-device acceptance. Unknown requirements remain
UNKNOWN. A separate explicit server policy decides whether this narrow evidence
is sufficient for a particular digital export release.

Integration uses `CadChecker(repository, blobs, process, control_root,
requirements_loader, daemon_root=None)` where `process` is
`IsolatedCadProcess(docker, pinned_image, deployment='local')`. The async trusted
loader receives `(scope, ValidationInput)` and returns `CadRequirements`: `units`
is `mm`; numeric constraints identify the relative export stem (`outputs/base`),
metric (`extent_x_mm`, `extent_y_mm`, `extent_z_mm`, `volume_mm3`), expected value
and absolute tolerance. Other requested checks belong in
`unsupportedRequirements`. Do not translate vague user statements into guessed
numeric assertions or use generated measurements as their own expected values.

`install_cad_checker(registry, adapter, evidence_path)` verifies the image,
checker-source digest, actual positive/negative evidence and socket-policy digest
before registering only `cad.export-integrity`. It creates no default release
policy. The existing validation service owns durable check claims, authorization,
revision/cancellation fences and independent evidence publication. Each checker
invocation has its own execution ID, distinct from the generator job.

Attempts 1–3 retain the failed isolation audit: Docker recreated an empty inherited
working directory after removal. Setting the new working directory before cleanup
fixed that; attempt 4 passed the parser cases. Attempt 5 additionally records and
checks actual container configuration. Attempt 6 repeats those checks after
rejecting mixed or conversion-based STEP unit declarations and is the final evidence.

The explicit opt-in runner is `qualify_cad_check.py`. It requires the pinned image,
Docker socket, retained blob directory, generator qualification report and a new
output report path. It performs no model calls or generated-source execution.
Unit tests use mocked transport and a trusted tetrahedron fixture; they are not
substitutes for this exact-image evidence.

The import behavior follows the official
[build123d import/export documentation](https://build123d.readthedocs.io/en/v0.11.0/import_export.html)
and [direct API reference](https://build123d.readthedocs.io/en/latest/direct_api_reference.html).
The installed dependency is pinned by the parent image; the real qualification,
rather than assumptions about newer documentation, establishes its actual behavior.
