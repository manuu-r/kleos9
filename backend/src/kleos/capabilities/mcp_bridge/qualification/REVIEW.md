# MM-core image inspection: independent local qualification

The usable installation record is [mm-core-read-image.json](mm-core-read-image.json).
It approves the exact local image
`sha256:c48f10ec68186f6e2425577e19bbd8cf8e352c1bd76926305920bbf82abdae93`
for bounded PNG/JPEG decoding and normalization through MM-core 1.3.0's
unmodified `read_image` handler. This is an installation qualification, not
independent acceptance of an inspected image or engineering design.

The implementation agent assembled this derived record after independent review
by `/root/rewrite_bambu`, completed at `2026-11-02T22:04:13+00:00`. The reviewer
inspected the source, exact image, original source archive and license, private
fixture ledgers, staged byte hashes, container identities and removal/capacity
proofs. The reviewer independently reran the 62 MCP bridge, runner and runtime
tests and scoped Ruff checks. No actionable finding remained in this scope.
The reviewer did not install a tool, change an environment or rerun native jobs.

## Evidence and the host-only change

All original JSON records below are copied byte-for-byte from the retained private
qualification directories. Their hashes are listed in the installation record.
Their historical `qualified:false` and `independentReview:pending` fields remain
unchanged. They are raw observations; the new derived record contains the later
independent approval.

| Record | What it establishes |
|---|---|
| [Initial failed attempt](evidence/mcp-core-attempt-1.json) | The host rejected benign inherited image provenance labels before starting the container. This is not passing tool evidence. |
| [Failed-attempt cleanup](evidence/mcp-core-attempt-1-cleanup.json) | Exact ownership was verified after the label comparison fix; the unstarted container was removed and capacity settled. |
| [PNG and watchdog attempt](evidence/mcp-core-attempt-2.json) | Actual MM-core PNG inspection, replay, malformed/pixel-limit rejection, and controller-loss watchdog exit 137 after about 30.115 seconds. Capacity remained reserved until verified removal. |
| [First JPEG supplement](evidence/mcp-core-jpeg-attempt-1.json) | Actual `image/jpeg` input, normalized output metadata/hash and replay on the same exact image. |
| [Current-host PNG checks](evidence/mcp-core-scope-attempt-1.json) | PNG inspection/replay and malformed/pixel-limit rejection after explicit actor/project/run/revision receipt binding. |
| [Current-host JPEG checks](evidence/mcp-core-scope-jpeg-attempt-1.json) | JPEG inspection/replay after the same receipt change. |

Only `bridge.py` differs among the nineteen bound source inputs between the
watchdog attempt and the current-host attempts. The change adds the actor,
project ID, run ID and revision to the existing inner receipt request hash.
The exact old/current hashes are in `hostOnlyDelta` in the installation record.
The authorized-second-editor regression verifies an identity conflict without
another tool dispatch. The current-host PNG/JPEG records match all current source
hashes and retain the same installation, image and seccomp policy.

The older watchdog evidence applies only to the unchanged exact image, server,
runner, Docker transport, watchdog command, resource/isolation settings and
cleanup behavior. It is not represented as a watchdog rerun against the newer
host snapshot. The image was not rebuilt and no source override was used.

## Scope and limits

The wrapper calls the pinned upstream handler with a fixed private input path
and `budget=small`; it reports original/normalized dimensions, media type and
normalized-content hash. The real fixtures include 64×32 PNG and JPEG inputs.
The wrapper accepts only bounded single-frame PNG/JPEG files, rejects malformed
and excessive-pixel inputs, and makes no semantic interpretation or engineering
claim. Container isolation includes UID/GID 10001, an immutable root, read-only
inputs, no capabilities, no-new-privileges, Unix-only seccomp, no network, one CPU,
512 MiB memory, 64 processes, 256 MiB scratch and an image-owned process watchdog.

Protocol/authority tests additionally cover exact schema/server identity,
oversized and malformed messages, unsupported reverse requests, source and user
scope, uncertain effects, cleanup after revocation, original-daemon binding and
fair maintenance across interruptions. Qualification is limited to this local
Docker image and these supported operations. It does not qualify arbitrary MCP
servers, universal parser security, FreeCAD, Blender, other MM-core readers,
cloud deployment, OCR, design behavior or physical/commercial actions.

No model/provider call or generated host execution was used. Fixture capacity
was recorded durably in separate SQLite qualification ledgers during an exclusive
native work window; this does not prove global admission across separate
databases. All fixture containers were removed and their capacity reservations
settled. Existing project data, environment files and default installations were
not changed.

## Operator use

Load the exact `installation` object from the JSON record together with its
absolute path as `qualificationReference` in `KLEOS2_MCP_INSTALLATIONS`.
The runtime checks the independent record, exact source hashes and installation
before registering the tool. Registration alone does not contact Docker or start
a server. The image must be present on the explicitly configured local daemon;
preflight checks it before an effect is claimed. Qualification changes cannot
silently replace an active attempt's installation.

The tests in `tests/test_mcp_qualification.py` validate the committed report through
the actual loader and an ephemeral registry while forbidding Docker construction.
They do not enable a default installation or modify the user's environment.
