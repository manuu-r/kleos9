# Separate companion preview host

The existing workbench can request a lease with
`POST /api/v1/projects/{projectId}/companion-preview` and `{artifactId}`. Only an
exact current published native `outputs/companion-web.zip` is eligible. A regular
uploaded ZIP, source file or arbitrary remote URL is not a runnable preview.
Engineering preview metadata advertises the exact artifact ID/hash when the
configured host can represent the bundle. No frontend changes are required.

Hosting is disabled by default. Operators must explicitly enable
`KLEOS_COMPANION_PREVIEW_ENABLED`, configure distinct `KLEOS_API_ORIGIN`,
`KLEOS_FRONTEND_ORIGIN` and `KLEOS_PREVIEW_ORIGIN`, and run the separate
`preview-serve` process. The API application never serves companion content.
Origin validation normalizes ports and IP addresses and rejects ambiguous numeric
address spellings and Unicode hostnames; use explicit ASCII/punycode names.
Enabled production origins require HTTPS. The preview service checks its actual
request origin, so proxy/TLS routing must preserve the intended origin without
trusting arbitrary forwarded headers.

```bash
.venv/bin/python -m kleos.cli preview-serve --host 127.0.0.1 --port 8004
```

Keep API/preview instances on the same persistent session secret, private blob
storage and replacement database. The secret derives a separate AES-GCM preview
key; it is never included in a lease or application request. Leases last 30–600
seconds (default 300), contain encrypted actor/project/context/artifact/publication
bindings and expire without changing underlying artifacts. They are bearer
capabilities: anyone holding an unexpired URL can request that scoped bundle
while its actor remains authorized. Do not log URLs at the process, reverse proxy
or CDN. The CLI disables access logs for the preview process.

Every asset request rechecks the account, organization membership, project
membership, current engineering context, exact artifact hash and publication
receipt. Membership and publication checks are separate from API login cookies;
the generated app receives no Kleos session or provider credentials. Revocation,
supersession, changed context/publication or expiry blocks further requests.
Responses are not cached. Requests currently being served hold the corresponding
authority locks during bounded IO, so a committed revocation fences later reads.

The host reads ZIP members in memory, never extracts named files or executes
application code on the host. Bounds are 32 MiB compressed input/per member,
128 MiB declared expansion, 1,024 entries, 200:1 per-entry expansion and 256 KiB
HTML. Entry-count/size bounds precede allocation of ZIP member objects. Traversal,
ambiguous paths, links, special files, encryption and ZIP64/alternate-name metadata
are rejected. Each requested member checks CRC and declared size. Private members,
source maps and standard service-worker entrypoints are not served. Capacity is
bounded without waiting for a capacity slot while holding a project lock.

## Browser contract and current limits

The unchanged Flutter widget uses an iframe with `sandbox="allow-scripts"`,
`credentialless` and `referrerpolicy="no-referrer"`. Its document has an opaque
origin. The preview host returns a small trusted wrapper with a second sandboxed
iframe. Its frame-source policy confines the inner document to the lease path;
the application response separately restricts scripts, asset fetches, images,
fonts and media to that path, with narrowly allowed inline styles/scripts and
WebAssembly compilation. Workers, service workers, nested application frames,
forms, persistent origin access and device permissions are restricted. These are
intended browser controls, not a claim that HTTP tests prove every browser's
behavior. See the [CSP specification](https://www.w3.org/TR/CSP/) and
[frame-source documentation](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Content-Security-Policy/frame-src).

HTML is served as a derived preview representation with its original base tags
replaced by the lease's absolute asset directory. The stored ZIP/hash is unchanged.
Opaque-origin asset reads receive `Access-Control-Allow-Origin: null`, never
credentials-enabled CORS. This supports relative Flutter assets, fonts and WASM
without granting the app the preview server's origin. See
[sandbox behavior](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Content-Security-Policy/sandbox).

Native Flutter builds already request local web resources using
`--no-web-resources-cdn`; external CDNs and application backend calls remain
blocked. Service-worker-dependent applications, browser storage, device APIs,
external fonts and worker-based rendering can therefore be unavailable. The
server does not rewrite arbitrary JavaScript to make unsupported apps appear
functional. See [Flutter web deployment](https://docs.flutter.dev/deployment/web)
and [initialization options](https://docs.flutter.dev/platform-integration/web/initialization).

HTTP tests cover exact issue/serve bindings, asset paths/MIME/base rewriting,
revocation/expiry/tampering, opaque CORS, ZIP boundaries, canonical origin
separation and actual runtime composition. They execute no JavaScript. The
independent review also checked those scoped boundaries; browser behavior is a
separate qualification requirement.

A trusted browser-contract fixture is available without inference or a native
build:

```bash
.venv/bin/python tests/companion_browser_fixture.py
```

It starts loopback ports 18792/18793 and prints only the frontend URL. Open the
frontend and check successful relative JSON loading, WASM compilation, opaque
origin, restricted storage/service workers and the Increment button. This is an
authored static fixture, not the actual generated Flutter application or a native
qualification report. Do not generalize that check to Flutter renderer behavior,
all browsers, external-network escape prevention or physical device correctness.

## Observed browser fixture — 3 November 2026

The integrator started `tests/companion_browser_fixture.py` on loopback ports
18792/18793 and opened it in the available in-app browser. Its outer iframe uses
the unchanged workbench's `sandbox="allow-scripts"`, `credentialless` and
`referrerpolicy="no-referrer"` settings. The visible trusted fixture reported
`Loaded successfully`, opaque origin `null`, blocked local storage and service
worker access, a successful relative JSON fetch and successful compilation of the
small local WASM fixture. Keyboard activation changed the visible counter from
zero to one inside the nested sandbox. The page was visually inspected; the tab
and both temporary fixture servers were then stopped.

This is observed rendering/interaction evidence for the authored trusted fixture
in that browser only. It does not qualify a generated Flutter bundle, CanvasKit,
all browsers, external navigation restrictions, arbitrary adversarial JavaScript,
BLE/device APIs, or engineering correctness. HTTP/authority regressions and static
CSP review remain separate evidence. Hosting remains disabled by default.

## Local-only negative browser probes — 3 November 2026

The trusted adversarial fixture attempted fetch, image, script, child-frame, form,
beacon, WebSocket, worker, service-worker and child navigation requests against a
separate loopback test server. The in-app browser showed a blocked frame for the
navigation attempt and the independent server counter remained empty. A subsequent
explicit control request was recorded as the sole request, confirming that the
counter/listener worked. WebSocket construction itself returned an object; the
observed claim is no connection reached the test server, not that construction
threw. The fixture tab and all three temporary servers were stopped.

These probes are bounded evidence for those attempts in the observed browser.
They do not prove arbitrary-browser or arbitrary-program isolation.
