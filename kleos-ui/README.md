# Kleos UI

Native Flutter frontend for the Kleos9 simulated hardware workbench. It preserves the dark workbench design, routed screens, ten project stages, connector flows, provider portal, human tasks, connector kit and external-assistant example.

The app contains no backend implementation and makes no real engineering, purchasing, printing, auth or marketplace calls. All operations go to the sibling FastAPI `mock-backend` over HTTP. Its state is rendered through an immutable snapshot repository and an injected `ChangeNotifier` view model.

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

The `ios` and `android` folders contain native Flutter targets. Both run the same UI and HTTP repository as web; every backend operation is still simulated.

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
