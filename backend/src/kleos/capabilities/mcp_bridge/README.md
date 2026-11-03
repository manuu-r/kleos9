# Backend MCP bridge

This is a real protocol/authority adapter with a fixed read-only artifact
inspection operation and an isolated Docker runner. The exact-image local
MM-core PNG/JPEG fixture has an [independent qualification record](qualification/mm-core-read-image.json)
and [review/evidence guide](qualification/REVIEW.md). Installation remains explicit
and absent by default. FreeCAD, Blender, desktop application
connections, paid asset generators and host process launches are unavailable here.

The official Python SDK is pinned to `mcp==1.26.0`, using its tagged
[`ClientSession`](https://github.com/modelcontextprotocol/python-sdk/blob/v1.26.0/src/mcp/client/session.py)
and protocol `2025-11-25`. The client consumes bounded runner-owned byte channels;
it never calls the SDK subprocess launcher. The MCP specification defines newline
JSON-RPC framing for [stdio](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports).
The bridge negotiates identity, checks one pinned input schema and invokes that
tool through `tools/call`. It deliberately supports a narrow subset: no server
sampling, elicitation, roots, resource fetching, changing catalogs or remote task
execution. Unknown server messages and unsupported versions fail closed.

## Integration contract

Construct `ArtifactInspector(installation, report, environment=..., repository=...,
artifacts=..., runner=...)`, then call `register_inspector(registry, inspector)`.
`repository` is the existing authoritative `GraphRepository`; `artifacts` is the
existing `ArtifactService`. Configuration/report are trusted operator inputs,
never project uploads. The report binds exact image/package identifiers, every
bridge source hash, independent review identity and scoped checks. Synthetic
reports work only with `environment='test'`. The supplied report covers only its
exact local image, sources and bounded PNG/JPEG inspection operation.

`runner.open(scope, task, installation, inputs, action_id=...)` is an async context manager
returning `ByteChannel.receive(max_bytes)` / `send(bytes)`. It receives immutable
verified bytes and server-owned relative staging paths `inputs/000`, etc. Its
production implementation reserves shared capacity, starts the exact approved
image with a fixed image-owned entry point, enforce all native resource/isolation
limits, and terminates/reconciles on context exit. Closing a pipe is insufficient
termination proof. It must not inherit host credentials or expose a Docker socket,
database credentials, another run's files, user-supplied commands or host paths.

The remote tool consumes `RemoteArguments.model_json_schema(by_alias=True)` and
returns `CallToolResult.structuredContent` matching `InspectionResult`. Each record
must correspond exactly, in order, to the staged artifact ID/hash/size/media type.
Text/metadata are advisory extracted content, not independent engineering evidence.
The narrow `PASS` observation means extraction completed with the exact inputs;
it does not assert physical behavior, feasibility or design acceptance. Resource
links, embedded files and non-text content are not accepted by this operation.
There is no artifact publisher call because inspection does not create files.
Future write tools require a separate typed candidate-publication contract.

Before every dispatch and result publication, the bridge rechecks current scope,
membership, task inputs and deadlines. Existing graph receipts bind a stable host
action ID to exact installation/task/input hashes. Replay returns the saved result;
a reused identity with changed inputs conflicts. Lost or malformed responses leave
the action uncertain and are not resent. MCP JSON-RPC IDs are correlation IDs,
not remote idempotency guarantees. ModelSpecialist's existing outer receipts also
remain conservative: an interrupted outer receipt may require reconciliation even
when the inner MCP receipt completed. No retry history is erased.

## First actual MM-plugin image

The image contains the unmodified MM-core `read_image` function and only its
required shared helpers from commit `33a54695774020e5da2d6b1ed13119d4014f848f`
(core version 1.3.0). The small vendored archive has individual source hashes and
the original Apache-2.0 license. It excludes the plugin framework, autolaunch,
model clients and optional Office/video/browser renderers. Its separate locked
environment uses the upstream-compatible Pillow version without changing the
control plane's dependencies. Runtime downloads are disabled.

The wrapper invokes that actual handler with `budget=small` and reports original
and normalized dimensions, media type and normalized-content hash. It accepts
single-frame PNG/JPEG, up to 8 MiB total and 8 million pixels per image. It returns
no inferred image meaning, OCR, manufacturability or acceptance. Original artifact
bytes and source closure stay unchanged. `QWEN_MM_NATIVE_MODE=1` and
`QWEN_MM_AUTOLAUNCH=0` are fixed; no external/paid tool is registered.

Docker's bounded attach stream carries MCP stdin/stdout over the control plane's
Unix socket, which is never mounted in the container. The server runs as UID/GID
10001, with a read-only root and inputs, no capabilities, no-new-privileges,
Unix-only seccomp, no network, one CPU, 512 MiB RAM, 64 processes and 256 MiB tmpfs.
An image-owned GNU `timeout --signal=KILL` enforces the exact installation lifetime
even if the controller disappears. The live fixture uses 30 seconds.

`McpSession` pins scope/action/source/schema/image/resource/deadline/daemon identity.
Capacity waits preserve the same claimed action and recheck authority; they do not
repeat model calls. Missing-image preflight happens before effect admission.
Cleanup verifies the original daemon, image, job labels, immutable container ID
and absence of both ID and name before capacity release. Uncertain creation or
removal keeps its reservation. `reconcile_due(limit=16)` handles cancelled, stale,
revoked and expired sessions with a persisted fair scan order, including malformed
or foreign-pool rows. Cleanup remains possible after an installation is removed.

`infrastructure/mcp.py` owns opt-in composition; no daemon request occurs at startup.
`KLEOS_MCP_INSTALLATIONS` entries contain an exact `installation` and an
operator-owned `qualificationReference`. The qualification helper's synthetic
test permit is never production evidence.

The locally cached MM FreeCAD 1.2.0 and Blender 1.2.0 packages are desktop bridges:
FreeCAD uses XML-RPC on loopback port 9875; Blender uses a raw TCP bridge on 9876.
Both expose generated Python and optional autolaunch. Their desktop `uvx` manifests
resolve Git tags and allow downloads, so they cannot become server installations
unchanged. Their app/addon state requires a per-run process, not a shared desktop
singleton. Later images need either a qualified Unix-socket adaptation or a new,
separately reviewed loopback-only policy inside a no-egress container. Never relax
the qualified existing lanes to accommodate these bridges. External downloads and
paid Blender asset generators stay excluded. Streamable HTTP is a separate future
authenticated allowlisted backend-service transport, not implemented here.

## Verification scope

`tests/test_mcp_bridge.py` uses the actual SDK client and SDK server over bounded
in-memory byte framing, real SQLite-backed command/effect state and real private
artifact bytes. It tests extraction, model-tool observation/replay, identity/schema
changes, uncertain disconnects, output bounds, reverse requests, cancellation,
authorization and qualification fences. `tests/test_mcp_runner.py` covers shared
capacity, cleanup, original-daemon binding, fair maintenance, staging, framing
and image-source provenance with mocked Docker.

The opt-in `tests/qualify_mcp_core.py` also ran against exact local image
`sha256:c48f10ec68186f6e2425577e19bbd8cf8e352c1bd76926305920bbf82abdae93`.
Attempt 1 stopped before start because inherited image provenance labels were
incorrectly rejected; its container was verified removed and capacity settled.
Attempt 2 passed actual image inspection/replay, malformed-file and pixel-limit
rejection, and controller-loss watchdog exit 137 after about 30 seconds. Capacity
stayed reserved until removal and returned to zero. Both attempts' private SQLite
ledgers and raw evidence remain under `.data/qualification/`. The successful raw
records retain their historical `qualified:false` fields. The new derived
[installation report](qualification/mm-core-read-image.json) records subsequent
independent scoped approval. It distinguishes the original watchdog host snapshot
from a later receipt-scope change, verified by fresh same-image PNG/JPEG exchanges
and an authorized-second-editor regression. Original evidence is retained unchanged.
This is local fixture evidence, not cross-database capacity, universal parser
security, FreeCAD/Blender or engineering acceptance qualification.
