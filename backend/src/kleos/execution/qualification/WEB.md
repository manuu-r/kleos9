# Exact local Flutter web qualification — 2026-11-02

**Passed: offline web compilation with hard storage bounds. Android APK is not qualified.**

Image `sha256:7c5aedd853f6be70c0d629a90134297fdf94716213cf32b3e2a743339776ea8d`
uses toolchain parent
`sha256:91bc45c72e5c139517fe3862af898e4a6b4bb62538b6218d2963ce3cdbb913ad`.
The whole-filesystem package audit found only the fresh `/opt/kleos-native/kleos`
package. Old backend, app templates, precompiled apps and old Gradle initialization
scripts were removed. `web-image-audit.json` records the runtime source hashes and
Flutter 3.35.4 (`d693b4b9dbac2acd4477aea4555ca6dcbea44ba2`) / Dart 3.9.2 versions.

The source fixture was written fresh. Its lockfile was resolved inside the isolated
image from existing dependency caches. No old application sources were copied.
`web-fixture-sources.json` contains the exact four-file source closure. All generated
code ran inside Docker; no generated code ran on the development host.

Resources are pinned: 2 CPUs, 6 GiB RAM with no extra swap, 256 PIDs, ten-minute
session and **4 GiB hard-bounded tmpfs**. Each job receives private writable copies
of Flutter and pub caches. The image root and host input mount are read-only;
`/tmp` is explicitly executable for the private Dart SDK. The trusted root-only
journal lives separately in `/run`. The local Docker Desktop policy restricts
socket creation to Unix sockets, with network `none`. There is no storage or
network fallback.

`qualify_web.py` used the production gateway and fixed native session actions to
patch the sources, resolve dependencies offline, run Flutter analysis, build web,
and finish. It verified source and artifact hashes, JavaScript/web entry files,
container resource bounds and idempotent finish. `web-attempt-1.json` preserves
the successful raw run; `web-exact-image.json` adds the exact toolchain audit and
scope. The earlier fixture preparation failure (implicit tmpfs `noexec`) is
preserved in `web-preparation-attempt-1.json`; the explicit `exec` mount fixed it.

The qualified export is `outputs/companion-web.zip`. This does not qualify browser
execution, BLE, device interaction, Android, iOS, physical behavior or independent
acceptance. New dependencies need an offline cache and their own qualification.
Installed Android/Gradle tooling alone is not APK qualification; its private cache
requirements exceed this tested storage scope. No paid, cloud or hardware work ran.

Runtime registration must require `applicationTarget: web`,
`storagePolicy: hard-bounded-private-tmpfs` and the exact resource contract from
the report. Candidate exports do not replace the Kleos Flutter workbench.

Opt-in reproduction from repository root:

```bash
PYTHONPATH=backend/src backend/.venv/bin/python \
  backend/src/kleos/execution/qualification/qualify_web.py \
  --image sha256:7c5aedd853f6be70c0d629a90134297fdf94716213cf32b3e2a743339776ea8d \
  --socket /Users/manu/.docker/run/docker.sock \
  --output /private/tmp/kleos-web-qualification.json
```
