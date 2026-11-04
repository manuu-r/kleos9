# Native toolchain parents

`../Dockerfile` builds a native execution image on top of a *toolchain-only* parent that has no Kleos code.
These recipes are that parent, so the CAD, PCB and firmware lanes can be rebuilt from this tree instead of from
opaque, previously built images. Each stage installs pinned third-party tools only; every download is checksum
verified, and a build on a restricted network leaves no proxy certificate or proxy configuration in any layer.

| Target | Contents | Verified here |
|---|---|---|
| `mechanics-toolchain` | CPython 3.12.16, build123d 0.10.0, ocp-gordon 0.1.18 | built; native CAD qualification passed |
| `electronics-toolchain` | + KiCad 9.0.8 (and libraries), Temurin JRE 21.0.13, Freerouting 2.1.0, atopile 0.12.5 | built; native PCB qualification passed |
| `firmware-toolchain` | Zephyr v4.2.0, west 1.5.0, ARM GCC, cmsis/cmsis_6/hal_stm32/hal_nordic | built; native firmware qualification passed (compile-only) |
| `application-toolchain` | Flutter 3.35.4 (web artifacts pre-cached) and a pub cache primed from a default `flutter create` app | built; native web qualification passed, bundle ran in Chromium |

**Offline dependency policy (application).** Jobs have no network, so the application image bakes the pub
packages a default `flutter create` app resolves (`cupertino_icons`, `flutter_lints`, `flutter_test` and their
dependencies for this Flutter tag). An app that needs any other package fails `flutter pub get --offline`. Adding
a package means changing the stage, rebuilding and re-qualifying; the specialist is told this limit. The earlier
BLE/permission template dependencies were deliberately not carried over: Android, iOS and BLE are unqualified.

```bash
cd backend
docker build -f src/kleos/execution/toolchains/Dockerfile --target mechanics-toolchain \
  -t kleos-toolchain-mechanics src/kleos/execution/toolchains
docker build -f src/kleos/execution/Dockerfile --build-arg TOOLCHAIN_IMAGE=kleos-toolchain-mechanics \
  -t kleos-native-cad .
docker image inspect kleos-native-cad --format '{{.Id}}'          # pin this image ID
```

On a restricted network add `--network host --build-arg APT_SCHEME=https --build-arg https_proxy=$HTTPS_PROXY
--build-context extra-ca=<directory of *.crt>`. The certificate is bind-mounted only for the build step and
removed in the same step. `docker build --network none` is sufficient for the child native image.

Builds are not bit-for-bit reproducible, so a rebuilt image has a new ID and must be re-qualified; do not copy
an ID from a qualification record onto a different build. Package versions come from the pinned Python image
and Debian's snapshot/backports; if a pinned version disappears upstream the build fails rather than drifting.
