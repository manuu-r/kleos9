# Kleos UI

Flutter frontend for the Kleos9 engineering workbench, with separate production and simulation modes.

The default mock mode preserves the existing design fixtures and simulated journey against `mock-backend`. Production mode connects to the sibling `backend` service and uses scoped Riverpod state, authenticated REST commands, and reconnectable project events. Physical fulfillment remains explicitly simulated in both modes.

## Production backend

Start `backend` using its README, then run:

```bash
flutter run -d web-server --web-hostname 127.0.0.1 --web-port 5180 \
  --dart-define=KLEOS_BACKEND=production \
  --dart-define=API_BASE_URL=http://127.0.0.1:8000/api/v1
```

The local no-login setup uses backend settings `KLEOS_AUTH_MODE=development` and
`KLEOS_DEVELOPMENT_NO_LOGIN=true`; it opens without a development token. See the
[root setup guide](../README.md#start-the-real-backend-locally). If token-based
development authentication is deliberately selected instead, use its configured
access token when prompted. Native development tokens use secure device storage;
browser development tokens remain in memory. No credentials are compiled into Flutter.

Production sign-in is invite-only email and password. A person redeems a single-use invite code once to create an account (the "I have an invite code" form), then signs in with email and password. Web sessions use HttpOnly cookies plus a CSRF token, so deploy the web app and API on the same site (the same origin by default, or sibling subdomains with `API_BASE_URL`). Native apps receive a session bearer token and keep it in secure device storage. On a new deployment the sign-in page first offers a one-time "Set up Kleos9" form for the first owner. Owners and admins then manage invites, roles and passwords from the Admin page in the account menu. `python -m kleos.cli create-invite` remains as an operator fallback.

For a production web bundle:

```bash
flutter build web --no-web-resources-cdn --dart-define=KLEOS_BACKEND=production
```

On HTTPS web the API defaults to `/api/v1` on the same origin. Set `API_BASE_URL=https://your-api.example/api/v1` for native release builds. Local Android development defaults to `http://10.0.2.2:8000/api/v1`; iOS defaults to localhost. Configure the API's allowed origin for local web development. Native transport settings currently allow local HTTP development; use HTTPS for hosted deployments.

Production includes project creation, per-lane Muse/Gemini settings, research and engineering commands, requirements and battery revisions, independent-check results, artifacts, explicit simulated fulfillment steps, opt-in training consent, and a run inspector with task dependencies, attempts, provider/model details, context, outputs, usage, costs, and role-restricted redacted transcripts. Unconfigured providers and unsupported checks remain visible as blockers. Provider changes apply to subsequent attempts; the backend pins current attempts.

Configured providers also offer an explicit “Use Gemini for all lanes” (or Muse) shortcut. It changes the editable choices only; creating the project or saving settings persists them. The backend's explicitly configured Modal rate-limit fallback to Meta is the only provider fallback exception; it is recorded on the invocation and event stream. Other outages or missing credits do not silently switch providers.

The organization menu switches between invited memberships. Its remembered choice is scoped to the signed-in account; revoking that choice requires explicit reselection. In-flight commands remain pinned to their original organization. Accepted release ZIPs can be downloaded from the workspace, including a previous accepted baseline after revisions. The run inspector includes native job inputs, tool versions, limitations, and administrator-only redacted engineering logs.

Completed native build jobs offer project writers “Rebuild saved sources.” This uses compute allowance without new AI generation, reads the latest project revision, journals the command before submission, and opens the resulting run. Uncertain submissions reuse their original receipt. The backend enforces source freshness and rebuild limits; validation jobs and running jobs cannot be rebuilt from this action.

Commands are journaled before dispatch in app-support storage (native, flushed temporary file then rename) or browser local storage. Uncertain requests keep their original key, payload, and expected revision across restarts. The project list offers “Check request” to reconcile them. Storage failure prevents dispatch. Clearing browser/app data removes that local journal. Event streams deduplicate sequence numbers, reconnect from the last cursor, and reload the workspace on expired cursors or gaps. Opening generated artifacts downloads/shares bytes; generated application code is never rendered in the authenticated workspace.

Production code lives in `data/models/production.dart`, `data/repositories/production_repository.dart`, the `production_*` services, `state/production_store.dart`, and `ui/features/production`. The repository validates responses with generated transport DTOs before adapting them to immutable UI models; free-form engineering evidence stays JSON. Account invites, provider credits, and signed native release builds require environment setup and are not implied by local tests.

The transport models in `lib/data/models/api.generated.dart` are generated from `backend/contracts/openapi.json`. After exporting an API schema change, run these commands from `kleos-ui` (Python 3 and the Flutter/Dart SDK are required; no generator package download is needed):

```bash
python3 tool/generate_transport.py
python3 tool/generate_transport.py --check
```

The deterministic generator follows response component references, retains typed nested models, lists and maps, and fails on unsupported schema shapes. The check detects drift against the exported contract. Artifact downloads verify their SHA-256 digest before sharing or saving.

## Live production UI check

`test_driver/production_live_test.dart` drives the real production application in headless Chrome against the running backend. It does not mock HTTP, authentication, command handling, or model responses. Its current fixture requires the Meta Model API (Spark/Wonder), and the create flow can make billed calls. Use it only with a disposable local test project and an authorized model allowance. Training consent is switched on and back off on the test project only.

This existing driver exercises token-based development sign-in and requires
`KLEOS_E2E_TOKEN_FILE`; it cannot run unchanged against the ordinary no-login
setup. For no-login API acceptance without paid calls, use the backend's opt-in
`tests/test_http_runtime.py`. Do not change a working environment's authentication
just to run a documentation check.

Run a ChromeDriver matching the installed Chrome release on local port 4444, then stop any Flutter preview using port 5180 and run:

```bash
KLEOS_E2E_TOKEN_FILE=/absolute/path/to/local-dev-token \
flutter drive --driver=test_driver/production_live_test.dart \
  --target=test_driver/production_app.dart -d web-server \
  --web-hostname=127.0.0.1 --web-port=5180 --driver-port=4444 --headless \
  --dart-define=KLEOS_BACKEND=production \
  --dart-define=API_BASE_URL=http://127.0.0.1:8000/api/v1 --timeout=600
```

The host process reads the development token at runtime and enters it through the real sign-in form. Driver command/file logging is disabled; the token is never a Dart define or application asset. The test-only entry point enables FlutterDriver; normal builds do not. Screenshots are captured only after sign-in, alongside a result report in ignored `build/e2e/`.

Set `KLEOS_E2E_PROJECT_ID` to resume an existing smoke project without creating another. Add `KLEOS_E2E_INSPECT_ONLY=1` to inspect an existing engineering run without making hosted model calls. `KLEOS_E2E_OUTPUT_DIR` selects a separate evidence folder, and `--browser-dimension=390x844@1` enables Chrome device emulation at that exact viewport (the explicit pixel ratio avoids desktop Chrome's minimum window width). Restart the ordinary production preview afterward using the command above in the production-mode section.

`KLEOS_E2E_RUN_ID` chooses a particular historical run and `KLEOS_E2E_ARTIFACT_NAME` chooses its exact artifact name (for example `native/outputs/model.step`). The check verifies both the authorized response and the file saved by the test browser against the artifact hash. Downloads stay in a unique folder inside the evidence directory. It also opens available, authorized redacted AI records and native execution logs.

On a disposable smoke project only, `KLEOS_E2E_BATTERY_CHANGE=1` submits a battery revision through the UI and checks that prior interfaces and candidate artifacts become stale. This changes the fixture and does not start new inference.

## Run on web

Install Flutter, then from this folder:

```bash
flutter pub get
flutter run -d web-server --web-hostname 127.0.0.1 --web-port 5180
```

Start the sibling `mock-backend` in another terminal using its README instructions.

Web and the iOS simulator use `http://127.0.0.1:8000/api` by default. Android uses the standard emulator host alias, `http://10.0.2.2:8000/api`. To point at another local simulation endpoint:

```bash
flutter run -d web-server --web-port 5180 --dart-define=API_BASE_URL=http://127.0.0.1:8000/api
```

Open http://127.0.0.1:5180. Existing hash routes and `?s=` project-stage links are supported. Cmd/Ctrl+K opens the command menu.

## Design system

The look lives in one folder, `lib/ui/theme/` ("Signal"): tokens, corner shapes, HUD ornaments,
Flutter's `ThemeData` and the shared `K*` widgets. Screens only use those; see
[`lib/ui/theme/README.md`](lib/ui/theme/README.md) for the map and the rules. To iterate on the look
without a backend, run `flutter run -t lib/theme_gallery.dart -d chrome` (every token and component on
one page; hot reload works).

- **Flat and sharp.** Near-black panels outlined by a 1px line, no shadows or gradients in chrome.
  Controls are 3px rectangles; panels have two opposite corners cut at 45 degrees (`KShape.panel`).
- **Two hues, two jobs.** Blue is action and selection (primary button, the single tab indicator that
  glides between labels, sliders, focus); coral is the product and live work (running loaders, the
  part you touch in a 3D view). Status keeps its own hues and is always paired with a word.
- **Readouts.** Small uppercase mono labels (`KLabel`) for stat labels and meta lines; viewfinder
  brackets and a faint grid behind products (`KGridBackdrop`).
- **Type.** Geist for text, Geist Mono for ids, figures and readouts; warm off-white text, body 15px,
  nothing under 11.5px. Every text colour clears 4.5:1 (`test/design_system_test.dart`).
- **Touch.** Controls are at least 44px, page margins 16px on a phone (`KSpace.gutter`), tables become
  two-line rows below 600px, tab rows scroll sideways inside their own frame. `KTap` gives slightly
  under the finger; `KHaptics` ticks on navigation and primary actions on native.
- **Loading.** Show the final layout with flat placeholder bars at their real size (`KPulse` +
  `KSkeletonBar` / `KSkeletonRows` / `KPageSkeleton`), never a shimmer. Work in progress uses
  `KLineLoader` or `KSpinner`; `KButton(loading: true)` ignores taps. All of it stands still under
  reduced motion. Errors and empty states stay inside their block (`KNotice`, `KErrorState`,
  `KEmptyState`).
- **3D.** `CadAssemblyPreview` orbits with one finger (with inertia), pinch-zooms with two, zooms with
  the wheel, resets on double tap and opens full screen on a phone; `ConceptPreview` shares the 44px
  controls. Rendered product visuals keep their own shading.

Because the shell shows a live loader while any project runs, tests that include a running project
pump a bounded time instead of `pumpAndSettle`.

## Source layout

- `lib/data/models`: JSON boundary helpers, immutable snapshots and command results.
- `lib/data/services`: injected HTTP client with error handling, request timeouts and retry-safe idempotency keys.
- `lib/data/repositories`: versioned state, polling, stale-response protection and lifecycle cleanup.
- `lib/ui/app_controller.dart`: shared application state, request deduplication and user commands.
- `lib/ui/core`: theme, responsive widgets, navigation, command menu, human-task card and native vector device painter.
- `lib/ui/features`: all routed screens and the ten-stage project flow.
- `assets`: original device geometry and locally bundled Geist fonts. The device is drawn by Flutter; no browser view or React wrapper is used.

Form drafts, navigation and expansion state remain in widgets. Simulated outcomes, workflow transitions, data and timers live in FastAPI. Browser reloads preserve state while the mock backend process is running; restarting the backend resets it.

## Verify

```bash
flutter analyze
flutter test
flutter build web --no-web-resources-cdn
```

Tests cover immutable state, pending-request deduplication, stale response ordering, uncertain retry keys, project-stage pinning, human evidence validation, phone layouts and the native device geometry.

## iOS and Android

The `ios` and `android` folders contain native Flutter targets. Select production mode with the same `KLEOS_BACKEND` flag, or omit it to keep the simulation fixtures.

Start the backend in another terminal, then choose a simulator or emulator from `flutter devices`:

```bash
flutter run -d <device-id>
```

For a physical phone, connect it to the same local network as your computer. In the backend folder, start the mock API with `.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000`. Then run the app with your computer's local address:

```bash
flutter run -d <device-id> --dart-define=API_BASE_URL=http://<computer-local-IP>:8000/api
```

Allow the iOS local-network prompt. Native HTTP access is enabled specifically for this local mock prototype. State still lives in the backend process and resets when that process restarts.

## Build all platforms

From this folder:

```bash
flutter build web --no-web-resources-cdn
flutter build apk --release
flutter build ios --release --no-codesign
flutter build ios --simulator --debug
```

Outputs:

- Web: `build/web/`
- Android APK: `build/app/outputs/flutter-apk/app-release.apk`
- Unsigned iOS device app: `build/ios/iphoneos/Runner.app`
- iOS simulator app: `build/ios/iphonesimulator/Runner.app`

Add `--dart-define=API_BASE_URL=http://<computer-local-IP>:8000/api` when building for a physical phone. The default Android APK uses the emulator address; `adb reverse tcp:8000 tcp:8000` plus a build configured with `127.0.0.1` is an alternative for a USB-connected Android phone.

The Android APK uses a development signing key for local installation. The unsigned iOS app requires your Apple signing team before installing on a physical device. Open `ios/Runner.xcworkspace` in Xcode, choose your team, then run or archive. Store distribution is not configured.

## Adaptive app layouts

Phones use a compact header, bottom destinations, readable stacked tables and a project-stage picker. Tablets use side navigation when there is enough vertical space; short landscape windows keep bottom navigation. Large windows retain the desktop header. Navigation also adapts when system text is enlarged. Forms remain scrollable above the keyboard, and native safe areas and system bars are respected.

Tests cover all routed screens and all ten project stages from 320 to 1280 logical pixels, phone landscape, enlarged text, safe areas, touch navigation, the on-screen keyboard, and platform-specific API defaults.

### Original interface with backend features and simulations

The original dashboard, connectors, human tasks, provider portal, developer kit,
and simulation controls remain in place. New builds use the real backend;
existing simulated records keep their original interactions. Simulation reset
and speed controls never reset or speed up real backend projects. Failed real
commands never fall back to fabricated results.

AI models are under **Providers → AI models**, alongside the original Services
portal. Set per-lane defaults for new builds or change an existing real project's
policy there. Defaults are saved per browser/device, user, organization, and API.

**Connectors → Engineering tools** also offers per-build CAD/PCB tool choices.
The existing build123d and atopile/KiCad defaults remain. Qualified installed
alternatives appear in the appropriate lane; unavailable tools show their status.
Saving applies to subsequent attempts. Active attempts retain their pinned tools,
and selecting another generator never bypasses independent validation. Installation
uses the [backend adapter contract](../docs/engineering-tool-plugins.md).

For the local network preview, run the original simulation service separately:

```sh
# From mock-backend, using an environment with its requirements installed:
KLEOS9_UI_ORIGINS=http://YOUR_LAN_IP:5180 python -m uvicorn app.main:app \
  --host 0.0.0.0 --port 8002
# From kleos-ui:
flutter build web --release --dart-define=KLEOS_BACKEND=production \
  --dart-define=API_BASE_URL=http://YOUR_LAN_IP:8000/api/v1 \
  --dart-define=KLEOS_SIMULATION_API_BASE_URL=http://YOUR_LAN_IP:8002/api
python3 tool/serve_web.py --port 5180
```

Use the same UI origin in the backend CORS configuration. The preview server
sends `Cache-Control: no-store`. Development no-login is controlled by the backend
setting `KLEOS_DEVELOPMENT_NO_LOGIN=true`; no credentials are bundled into Flutter.
The simulation server keeps its original in-memory state and resets on restart.

Requirements use a planning conversation with guided choices, saved progress,
optional custom answers and an editable summary. Sending an answer or message
automatically resumes the plan. New builds start at capture with an immediate
rough 3D concept, then update it from Gemini and feasibility research. The preview
supports drag to rotate, pinch/zoom controls, assembled/exploded views and part
selection. Selecting a part attaches its ID and scene version to the next message.
The same scene is supplied to the agent as a reference. Chat history is bounded
on screen and follows new replies; earlier messages remain accessible.

The native data-only renderer adapts the pinned, MIT-licensed Anatomy skill
bundled in the backend. Its license is included in Flutter's license registry.
Concepts use relative proportions and do not generate CAD or satisfy engineering
checks. Explicit confirmation still starts design. Technical proposal details
stay expandable, and all existing simulation sections retain their behavior.

Build cards and workspaces expose **⋯ → Delete build**, followed by an explicit
**Delete and clear build** confirmation. Real build cleanup continues server-side
in the background; simulation deletion only clears the selected simulation.
