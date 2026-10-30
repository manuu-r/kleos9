# Development usage, generated previews, and automated circuit checks

The development Docker stack enables `KLEOS_DEVELOPMENT_UNLIMITED_USAGE=true`
by default. This removes monetary ceilings, organization/platform project
admission ceilings, and lifetime manual retry limits. It preserves usage records,
uncertain-call reconciliation, tenant authorization, artifact/revision fencing,
engineering release gates, process isolation, resource/deadline limits, and
bounded automatic repair chains. Additional simultaneous work queues behind
available model/native capacity instead of consuming unlimited local resources.

To test allowances again, set `KLEOS_DEVELOPMENT_UNLIMITED_USAGE=false` in
`backend/.env` and recreate the API and workers. Ordinary settings default to
false outside the development Compose configuration. Staging and production
reject the bypass. The public budget reports `unlimited: true` and
`allowanceUsd: null`; this is not a provider credit balance.

## Previews without inference

The original Flutter workspace shows CAD, PCB, app, and firmware Code tabs during interface
planning and file generation. The read-only, project-authorized
`GET /api/v1/projects/{id}/engineering-preview` endpoint decodes existing
interfaces and immutable current candidate/accepted files. It calls no model,
embedding, research, or generated-code execution service.

- CAD starts with accepted size envelopes, then displays an orbitable sampled
  STL mesh from the actual native export.
- PCB starts with an explicitly approximate enclosure/component arrangement,
  then displays actual KiCad footprint placements, traces, and outline bounds
  when a usable board file exists. An empty export stays unavailable rather
  than acquiring fabricated geometry.
- App shows a static native screen sketch derived from generated Dart labels,
  alongside literal source. It does not run generated HTML or JavaScript with
  Kleos credentials.
- Actual source names, candidate status, generation status, and limitations are
  visible. Missing or malformed files fall back to the reference view. Cached
  responses recheck project authorization; stale/deleted artifacts are excluded.

Streams trigger revision-scoped refreshes. Parsers have byte/token/depth/mesh
ceilings; generated Python/Dart/HTML are never evaluated by the preview service.
Visual previews grant no engineering acceptance authority.

## Automated PCB correspondence

The validator compares an accepted, complete component/pin/net contract against
an independently exported KiCad schematic netlist and actual PCB pad bindings.
It reruns ERC/DRC using an isolated workspace and image-owned standard libraries,
excluding generated project rule/library settings. Matching supported circuits
can pass automatically. Missing contracts/files remain UNKNOWN; actual
component, pin, net, or evidence mismatches fail.

See [supported contract and qualification scope](../backend/docs/circuit-correspondence.md).
Digital correspondence does not establish physical operation or analog/RF/thermal
performance. Synthetic physical evidence still cannot satisfy a live gate.

## Source-preserving native recovery

`project.retryNative` supports an existing `jobId` or a recorded generated
`sourceAttemptId` that never reached native submission. Both preserve exact
source hashes and contract applicability, reject stale/replaced/cross-tenant
inputs, and perform no model or retrieval call. Fresh outputs require independent
validation before release.

The Zephyr runner records requested/resolved targets for its explicit legacy
nRF52840 DK alias; the pinned firmware image includes the required Nordic HAL.
Companion preparation fills missing web bootstrap files from the immutable image
and records prepared source hashes while preserving existing user files.

The current smartwatch's app and firmware were rebuilt successfully from saved
sources with no new model calls. Its PCB export remains incomplete: it has no
placed components/routing and no matching schematic or complete accepted circuit
contract. These are real engineering blockers, separate from the removed
development allowances. Revising circuit interfaces creates a new engineering
revision and retains previous files in history.

Runtime records: [native rebuild results](../backend/verification/development-native-recovery.json).
Desktop/phone preview screenshots and the browser report are generated under
`kleos-ui/build/engineering-preview-check/`.

Final verification: **547 backend tests passed** (nine optional checks skipped),
**155 Flutter tests passed** (three optional checks skipped), clean static analysis
and generated transports, real offline positive/negative PCB checks on both
final worker images, and desktop/phone browser checks for all four preview tabs.
No hosted inference was invoked for these previews or native rebuilds.
