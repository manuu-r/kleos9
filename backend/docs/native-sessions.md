# Bounded native work sessions

`SessionSandbox(request, directory, execute)` supervises one engineering lane and
one native attempt. It exposes `apply({id, action, arguments})` with fixed actions
`read`, `patch`, `build`, `analyze`, `render` and `finish`. Responses use
`{status: succeeded|failed, result: {..., checks: []}}`. A completed tool may report
its own nested failed/unsupported status. These diagnostics never create a
Validation or accept/release engineering work.

The trusted supervisor calls `finish_result()` after `finish` to obtain the actual
source/output `JobResult`. The model-visible finish response contains metadata,
not large artifact bodies. The final closure includes exact patched sources and
source hashes plus real latest-build outputs, within a 256-file/32 MiB native
result limit for full mobile scaffolds; model changes remain capped at 50 files.
A patch invalidates previous outputs until another build. Accepted state
and independent release validation remain outside this session.

`read` accepts a tracked source or recorded output path, optional byte `offset`
and `maxBytes`. It returns the complete file SHA-256 and a bounded UTF-8 content
window; binary files return metadata. `patch` accepts `path`, complete UTF-8
`content` and mandatory `expectedSha256` (null only to create a new file). Paths
cannot traverse, follow symlinks/hardlinks, reach another lane, or edit build
outputs, private caches, supervisor control files or trusted preparation paths.

No action accepts a shell command, custom executable or arbitrary command flags.
`build` calls the selected pinned builder; its existing subprocess UID, clean
environment, process reaping and resource bounds still apply. Repeated builds
clear derived output/cache paths rather than reusing stale files. The generated
process cannot read or replace the journal, which is a supervisor-owned mode0700
sibling outside every generated writable/chown tree. Source backups and the
latest build envelope stay private there. Stable action IDs replay recorded
results without repeating the tool. Changed arguments for an existing ID fail.
An interrupted atomic patch recovers the exact authorized bytes; an interrupted
build restores source snapshots and requires a new bounded build action.

`analyze` uses fixed lane commands only: actual STEP inspection, actual KiCad PCB
outline/package measurements, ELF header inspection, or Flutter analysis. Its
reports are diagnostic. It cannot manufacture release evidence or substitute for
an independent validator identity.

`render` imports the actual recorded STEP in a fresh credential-free process,
using fixed `front`, `top` and `isometric` orthographic cameras. It executes no
generated Python. A bounded tessellation is depth-rasterized to actual 512×384 PNG
views using the standard library; no Pillow, browser, display server or hosted
image model is required. Up to three images include `contentBase64`, `mediaType`,
`sha256`, `name` and the exact source `stepSha256`. Each PNG is at most 512 KiB and
the complete step JSON is at most 2 MiB. Complex/unsupported geometry produces a
visible diagnostic failure rather than a generated replacement picture.

`request.sessionLimits` defaults to 32 actions and 900 seconds for the complete
session, with hard ceilings of 64 actions/1800 seconds. Read windows are at most
256 KiB, text patches at most 1 MiB, and the source closure defaults to 16 MiB
(hard maximum32 MiB). Each subprocess timeout is bounded by remaining session
time. Reserve the final action for `finish`.
Persisted job limits survive worker restarts. Raising operator limits applies
only to new jobs; lowering them can stop existing work. Provider requests are
bounded by the session's actual remaining lifetime, including time already queued.

`tools/check_native_session_runtime.py --source-override` qualifies development
source with the existing isolated CAD image, network disabled and zero model
calls. It exercises an actual syntax failure, source read/patch, real build,
generated-UID journal denial, three hashed STEP PNGs, fixed geometry analysis,
resume/replay and candidate-only finish. Run without `--source-override` after
image refresh for image-bound evidence in `verification/native-session-runtime.json`.

The trusted engineering worker opens the job before reserving model capacity,
then drives the session through Pydantic AI tools. The task receives no provider
credentials. `KLEOS_NATIVE_SESSIONS_ENABLED` is an explicit rollout switch: enable
it only with images qualified for this protocol. The worker enforces persisted
step/deadline fences and the configured session model spend ceiling on every
response and request; partial observed usage survives a failed loop. One stable
model tool-call ID maps to one durable session step. No transport failure retries
a completed billed model turn. A completed model turn enqueues the reserved final
`finish` action and yields to normal native reconciliation. Cancellation fences
publication immediately; actual compute termination is reconciled separately.
Known usage returned after cancellation is recorded through a numeric-only
observer before the live-session gate rejects further work. It grants no tool,
artifact, reservation-settlement or validation authority. Cancelling a session
settles its native reservation once, refunding undispatched jobs and retaining
the conservative debit for dispatched compute.

The private step ledger retains original tool observations for the configured
30-day transcript retention period, while provider context retains only recent
large observations and the latest rendered views. Run inspection exposes bounded,
redacted summaries. Deletion removes source/image contents from retained job
records as well as the step ledger.

Generated-process network isolation adds a Linux seccomp filter after UID drop.
CAD, PCB, firmware and validation use Unix-domain IPC only. Android's Gradle/JVM
needs IP sockets and interface enumeration, so application tasks permit those
families under the dedicated no-NAT VPC, endpoint policies and DNS firewall.
This is a family filter, not a claim that seccomp restricts socket destinations
to loopback. Alternate ABI/io_uring bypasses are denied. Native ARM container
tests exercise both actual policies. Docker Desktop
cannot install that filter inside these emulated amd64 images; its explicitly
configured local fallback verifies that only loopback is up and no external
IPv4/IPv6 routes exist. Generated code has no network administration capability,
and the fallback flag is omitted from its environment. ECS never receives this
local flag and fails closed if the filter cannot be installed. Dedicated native
VPC DNS and endpoint restrictions are additional cloud controls. See
`infra/native-network.md`.

The complete portable companion ZIP retains platform scaffolds, fonts and
preparation metadata. Machine-local SDK settings, toolchains and build caches
are excluded. Fonts/preparation markers are excluded from the editable source
manifest and re-created from the pinned image on the next build. Products depend
on the final patched source artifact IDs, not the older pre-model input set.
Docker reconciliation records the final finish response before marking the job
terminal and cleaning its private control directory.
