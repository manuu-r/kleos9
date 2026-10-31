# Native engineering backend fixes

The nine changes were implemented in the requested order. The Kleos Flutter UI
and simulated physical/commercial stages retain their existing behavior. The
companion template is a separate application.

| Order | Change | Verification and scope |
|---|---|---|
| 1 | Accepted `interfaces.circuit` compiles to KiCad schematic and placed board; pinned Freerouting 2.1.0 runs before ERC/DRC and manufacturing exports. The electronics image contains pinned KiCad 9 libraries and a hash-verified part catalog. | Real offline routing, ERC/DRC and independent circuit-correspondence checks pass. The initial catalog covers resistors, capacitors, LEDs and pin headers; unsupported parts require qualification. Alternate engineering adapters remain selectable. |
| 2 | Native commands retain the last 100,000 output bytes, including timeout output. | Regression tests retain the actual terminal error after long output. |
| 3 | Per-attempt read/patch/build/analyze/render sessions run under a trusted-worker model loop with persisted step, time and spend bounds. Real STEP-derived PNGs return to the model. | Actual native sessions exercise repair, replay, read-only controls, rendering and candidate-only completion. Only independent validation creates acceptance checks. Custom tool images retain the original build flow until their session runtime is qualified. |
| 4 | Qualified watch firmware/application base files enter bounded context; unchanged immutable sources survive partial repairs and native-only rebuilds. | Source-closure tests cover mixed attempts, exact hashes, stale inputs, provenance and products depending on final patched sources. |
| 5 | Engineering output defaults to 32,768 tokens with streaming and a 600-second total deadline; planning retains separate limits. | Actual Wonder and Modal streaming probes pass. SDK fixtures reconstruct responses above 12,000 tokens, enforce deadlines and prevent replay/fallback after an interrupted billed stream. Live probes are deliberately small, not full-window stress tests. |
| 6 | Restricted ECR/Logs endpoint policies and fail-closed Route 53 DNS Firewall in a dedicated native VPC. Generated-process socket filtering supplements task isolation. | Terraform mocked tests and real Linux filter probes pass. Android permits IP/netlink families for Gradle IPC under task-level network restrictions; other lanes permit Unix sockets only. AWS networking has not been deployed. |
| 7 | Independent global/per-project admission, fair dispatch, pinned per-image resources, native usage/pull timing and a $0.10 native allowance reservation. | Concurrent PostgreSQL dispatch tests and actual native resource measurements pass. Reservations remain conservative estimates; cold ECR transfer and AWS invoice cost are unmeasured. |
| 8 | Application root is read-only. Flutter/pub/Gradle writes use private job scratch; Java home and temporary files are scoped there. | Real offline web builds pass. Docker application scratch uses a disposable anonymous disk volume after tmpfs exhausted local host memory. Actual Docker API cleanup removes that volume and preserves shared backend data. Fargate already uses task-local ephemeral disk. |
| 9 | Pinned compatible BLE dependencies, permission/connection/notification handling, offline Android SDK, ARM64 development APK, portable Android/iOS sources and a hashed build manifest. | BLE template analysis, 12 tests and web compilation pass. The Android toolchain passes online and clean offline APK compilation/signature checks. Final complete companion packaging results are recorded separately below. iOS remains unsigned source only. |

## Verification results

- Backend: 1,019 passed, 10 conditional tests skipped, including real PostgreSQL recovery tests in the passing run.
- Kleos Flutter: 181 passed, 3 conditional tests skipped; static analysis and generated transport checks pass.
- Mock backend: 15 passed. Terraform formatting, validation and 11 mocked tests pass; no AWS changes were applied.
- The real companion template passes strict analysis, 12 tests, web compilation and an offline ARM64 APK build. The APK is 16,975,317 bytes, verified independently with `apksigner` and `aapt`. The portable source package has 83 files, 80 editable source entries, and excludes caches/machine settings.
- Exact-image companion qualification uses a read-only root and disabled networking. Native execution took 299.301 seconds under local AMD64 emulation, using 635.041 CPU-seconds, a 5,392,695,296-byte cgroup memory peak (including filesystem cache) and 126 processes. The full output closure is 30,113,617 bytes; private scratch/container cleanup passed.

The real Gemini session also passes: three hosted requests drive read/build/render/finish, return three actual CAD PNGs, and preserve the source hash without creating any validation or release. Known AI test estimates total **$0.0558265**: Wonder $0.037292, Modal $0.00659525, the original diagnostic Gemini call $0.00204825, and successful post-fix Gemini $0.009891. The original failed diagnostic is retained separately; neither paid attempt can be rerun automatically. These are token-price estimates, not provider credit balances or invoices.

The local backend is running with sessions enabled, worker version `local-native-sessions-20261031`, four global/four per-project native slots and no development login. The additive session migration is applied; Alembic reports no outstanding schema changes. The real HTTP fixture passes without AI calls and deletes only its own temporary project. Existing projects and artifacts are preserved.

## Reliability fixes found by the real runs

The model-latency probe exposed a control-directory ownership race. Step
directories are now published with the correct private ownership atomically,
without adding filesystem capabilities. Final Docker finish responses enter the
durable ledger before cleanup. Session cancellation settles its reservation
exactly once; observed model usage survives cancellation without granting more
execution authority. Raising worker settings cannot enlarge an already-pinned
session, and model deadlines include time already spent queued.

Actual Android runs exposed Gradle heap, descriptor and temporary-file needs.
The pinned cache build retains R8 optimization with a 2 GiB heap. Application
processes receive 4,096 descriptors, while other lanes retain 256. Heap/descriptor
exhaustion is a resource failure and cannot trigger a billed code repair. A real
generated-UID Gradle A/B test verifies the explicit private Java temporary path.
The APK artifact ceiling is 32 MiB, with base64 transport headroom; aggregate
decoded native results remain bounded to 32 MiB.

Repeated app builds clear their old manifest and generated machine settings
before packaging. Portable sources exclude SDK copies, caches, signing files
and machine-local settings. Workspace/preview metadata includes full mobile
scaffolds and prioritizes current artifacts, so they cannot hide the generated
APK or other lanes behind the old 200-file cutoff.

## Evidence

- [Backend/session tests](../../tests/test_engineering_sessions.py), [streaming tests](../../tests/test_model_streaming.py), [APK envelope tests](../../tests/test_companion_android.py).
- [Native capacity](../../verification/native-capacity-runtime.json), [native session](../../verification/native-session-runtime.json), [compiled circuit](../../verification/compiled-circuit-runtime.json).
- [Real BLE/web template](../../verification/ble-template-runtime.json), [Java temporary-directory A/B](../../verification/gradle-temp-runtime.json), [actual Docker scratch cleanup](../../verification/native-scratch-runtime.json).
- [Companion APK packaging](../../verification/companion-apk-runtime.json), [Gemini SDK dry run](../../verification/gemini-session-dry-runtime.json), [bounded Wonder/Modal calls](../../verification/long-model-runtime.json), [successful Gemini qualification](../../verification/gemini-session-postfix-runtime.json).

Verification files identify exact images, source overrides when used, scope,
usage and limitations. Failed tmpfs/descriptor/packaging qualification reports
are preserved beside the final report. They are diagnostic history, not accepted
engineering evidence. No physical BLE device, iOS build, production signing,
paid fulfillment or live AWS deployment is claimed by these checks.
