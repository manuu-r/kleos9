# Isolated Flutter startup view

The first browser run of the fresh exact-image Flutter ZIP failed before rendering:
the compiled default startup passed `serviceWorkerSettings`, and the loader read
`navigator.serviceWorker` inside the intentionally opaque frame. The browser throws
without `allow-same-origin`. The frame remains `sandbox="allow-scripts"`.

`api/companion_bootstrap.py` recognizes the reviewed loader prefix by SHA-256,
requires the exact bounded local-CanvasKit/dart2js build configuration, and removes
only the exact default service-worker startup argument. Its preview view ends in
`_flutter.loader.load()`. Flutter's [official initialization guidance](https://docs.flutter.dev/platform-integration/web/initialization)
documents startup without `serviceWorkerSettings`. This covers one loader revision,
not arbitrary future Flutter versions or custom bootstrap code. Unrecognized
service-worker bootstraps are rejected instead of rewritten heuristically.

The archive, immutable blob, source hashes and candidate identity stay unchanged.
The served JavaScript carries `X-Kleos-Preview-Transform`, `X-Kleos-Source-Sha256`
and `X-Kleos-View-Sha256`. The transform version participates in encrypted ticket
authentication, so a semantics change invalidates old links. Existing lease
limitations disclose the view without changing the frozen response schema.

The [fresh build record](companion-native-browser-build.json) records compilation
and HTTP evidence. The original bootstrap failed browser execution. The first
no-service-worker retry also remained blank. A redacted browser request trace
then showed a startup asset (`main.dart.js`) receiving503 while local CanvasKit
JS/WASM received200: parallel browser loads exhausted the nonblocking processing
gate. A bounded admission queue now allows issue/asset handlers to wait up to five
seconds before acquiring any project lock, with at most ten admitted requests and
two file processors. Metadata under an existing project lock remains nonblocking.
A concurrent three-asset HTTP regression verifies all200 and at most two processors.
The next actual browser attempt rendered the Flutter scaffold and floating plus,
but title/sample text was absent: the exported bundle lacks a local default text
font and its remote Roboto request is blocked. Functional browser qualification
therefore remains incomplete. There is no external-font exception. A correct
follow-up must make the exported bundle self-contained with pinned, licensed
font assets; a preview-only font substitution would conceal an export defect.

Manual helpers are explicit local opt-ins:

- `tests/qualify_companion_web.py` reproduces the existing trusted source fixture in
  the pinned image with a persistent scoped capacity ledger. Its pool cannot
  coordinate another database; the recorded run used an exclusive native window.
- `tests/companion_native_browser_fixture.py --work <directory>` verifies those
  exact bytes and native job provenance, then serves them on ports 18794/18795.
  The app executes only in the restricted browser frame.
- `tests/companion_browser_fixture.py --adversarial` hosts a separate trusted
  browser-contract fixture and local request trap. Client-reported attempts alone
  are not proof; the trap and positive control distinguish blocked requests from
  a broken listener. This fixture does not contain Flutter.

No helper starts model calls, external providers, host application execution,
physical actions or accepted releases. Native compilation remains an explicit
resource-consuming opt-in, not a routine unit test.

The HTML view also enforces `crossorigin="anonymous"` on script elements, including
inline module imports, without adding credentialed CORS. Attribute values are
parsed and re-escaped; prior `use-credentials` values are replaced. Its separate
document policy version participates in ticket authentication. The
[HTML Standard](https://html.spec.whatwg.org/multipage/scripting.html) defines how
these script fetch options govern dynamic imports. The controlled retry established partial rendering only; missing text and input
verification remain open.

## Qualified font-bearing fixture

The follow-up image `sha256:572fd5f8d64fd6d7a1bed7c955ee33c8223529a43fc0dedd8e53dcc81328e315`
adds `execution/web_bundle.py`. This trusted, data-only packaging step reads twelve
exactly pinned Roboto faces and their complete Apache-2.0 license from the SDK.
It adds them to `build/web` with a merged font manifest and hash provenance before
ZIP publication. An app-declared Roboto family takes precedence and remains
unchanged; sources, package locks and Dart code are not rewritten. Bounded reads,
reserved-path checks and symlink/hardlink rejection protect this output-only step.
The generated ZIP therefore contains the font bytes itself; hosting does not inject
an untracked replacement font or enable a remote font origin.

The [new scoped record](companion-native-font-browser.json) preserves the new image,
unchanged original fixture sources, ZIP hash, all font/license hashes, capacity
reservation and release, and versioned preview transformations. The root integrator
observed the actual compiled app in the in-app browser: “Hardware companion” and
“Local samples: 0” rendered, and the keyboard interaction changed the count to1.
The browser error/warning log was empty afterward. Redacted request observations
showed successful local bootstrap, app, CanvasKit and manifest responses.

This qualifies one trusted fixture and this browser configuration. It does not
accept arbitrary generated apps, arbitrary scripts/fonts, other browser engines,
hardware behavior, BLE, APK output or physical work. Earlier failed images and
reports were preserved. The temporary browser servers were stopped after evidence
capture; native capacity had already returned to zero.

For explicit local operator installation, the same committed
`backend/docs/companion-native-font-browser.json` includes the real patch/build/finish
and isolation observations required by `native_qualification`. The installation
entry, relative to a backend working directory, is:

```json
{
  "capability": "application",
  "imageDigest": "sha256:572fd5f8d64fd6d7a1bed7c955ee33c8223529a43fc0dedd8e53dcc81328e315",
  "providerVersion": "flutter-web-3.35.4-roboto-v1",
  "qualified": true,
  "qualificationReference": "docs/companion-native-font-browser.json"
}
```

It requires the explicit local `native_docker_desktop_compatibility` policy. The
report is rejected for another image or strict/ECS execution. Tests exercise this
real committed reference. No installed tool, default operator setting or historical
attempt was automatically changed; existing attempts keep their old pins.
