# Real compiled companion previews

The App tab can run the actual `native/companion-web.zip` returned by a successful
isolated application build. It is pinned to the current project engineering
revision, native job, artifact ID, and archive hash. Source-only attempts retain
the labeled sketch until compilation succeeds. No model or embedding call is
needed to render either the app or CAD parts.

## Deployment and access

Configure `KLEOS_COMPANION_PREVIEW_BASE_URL` as a dedicated origin and
`KLEOS_COMPANION_PREVIEW_SIGNING_KEY` as a separate random key with at least 32
characters. The authenticated API and static preview service share this preview
key. The static service receives neither provider credentials nor the control
session signing key. Cloud origins require HTTPS; the frontend additionally
rejects a preview URL sharing its own or the API's origin.

Development Compose serves the preview on port 8004. The configured public URL
must be reachable by the phone/browser, rather than the container's internal
hostname. The Flutter UI receives the URL from the API; it does not hardcode it.
For independent deployment run `uvicorn kleos.preview_api:app --no-access-log` on
the dedicated origin, with database/artifact read access and the preview key.
Use a read-only database/storage identity in cloud deployment.

`POST /api/v1/projects/{id}/companion-preview` accepts `{artifactId}` through the
normal project authorization and cookie CSRF checks. It audits the read without
recording the capability. The resulting ten-minute ticket authorizes only that
one compiled archive. Every asset request rechecks current organization and
project access, artifact/native-build state, and engineering revision, including
cache hits. Deleted, revoked, stale, superseded, or expired access is rejected.

Access-log collection must stay disabled or redact capability paths on the
preview service and any proxy/CDN in front of it. Tickets are absent from accepted
release and manufacturing manifests. The service sets no Kleos authentication
cookies and ignores any incoming ones.

## Browser and archive boundary

Web Flutter uses an iframe with `sandbox="allow-scripts"`, no same-origin grant,
no referrer, and credentialless mode where supported. A trusted outer document
constrains its generated inner frame's navigation to that ticket's path. The
inner app also has an opaque origin, scoped resource CSP, and restricted device
permissions, forms, popups, downloads, and top navigation. The control API does
not allow the opaque `null` origin. Native Flutter offers an external-browser
view using the same restricted server document.

These are browser restrictions, not an operating-system network firewall. CSP
controls normal resource/API fetches; do not infer that it disables every browser
network mechanism, such as WebRTC. The preview has no control-plane credentials
or direct provider access. Runtime device/backend behavior depends on what the
generated app implements and what the preview permits.

The static server never executes generated application code. ZIPs are read
without filesystem extraction; entries, paths, duplicate names, encryption,
compression types, symlinks, compressed/uncompressed sizes, and entry counts are
bounded before member decompression. MIME types and no-store/nosniff headers are
explicit. Original archive bytes and their hash remain immutable. Only served
HTML base URLs are rewritten to the scoped preview path, with a response header
declaring that projection. Accepted downloads retain original compiled/source
ZIPs and can be hosted separately when the companion is delivered.

## Verification

Backend tests cover authorization and revocation after warming the archive cache,
ticket tampering/expiry, archive attacks, response policy, and immutable sources.
The local browser qualification uses a disposable operator-authored Flutter
fixture, built by the actual isolated application worker: its screen renders and
its navigation button opens a second screen in desktop and phone browsers.
Storage and parent-document access are denied by the opaque frame. No hosted
inference or release acceptance is part of that check.

See [CAD parts and manufacturing packages](cad-physical-parts.md) for the stable
part IDs, original assembled geometry, independent part checks, and release
manifests that accompany the same build.

Runtime evidence: [live artifact previews](../verification/live-artifact-previews-runtime.json)
and [independent CAD checks](../verification/cad-parts-runtime.json). The disposable
test project is deleted after verification; its preview capability is checked to
be revoked immediately. Original geometry/archive hashes are verified again after
interactive browser use.
