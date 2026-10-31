# Android companion build image

The `application` target in `Dockerfile.native` adds Android compilation to the
existing Flutter web image. SDK installation is separate from template/cache
qualification: changing application source or `pubspec.lock` does not invalidate
the Flutter, JDK, Android SDK, NDK, or engine download layers.

The image pins Flutter 3.35.4, Temurin JDK 17.0.18+8, Android compile/target SDK
36, minimum SDK 24, build tools 35.0.0 and 36.0.0, NDK 27.0.12077973, Gradle 8.12,
Android Gradle Plugin 8.9.1, and Kotlin 2.1.0. The Android/Gradle versions match
the installed Flutter release's `FlutterExtension.kt` and `gradle_utils.dart`.
Installer and Gradle distribution SHA-256 values are checked before use.
Google's SDK manager verifies its downloaded package archives. Installed SDK
package metadata remains in `/opt/android-sdk`.
Android library plugins also require API 34/35 platforms, installed in a separate
additive layer. Platform tools are installed by the SDK manager; their installed
revision is recorded in the image's `source.properties`.

`qualify_android_cache.py` creates a trusted Flutter hello-world fixture with the
template's exact dependencies. It builds an ARM64 release APK online to populate
Maven/Gradle caches, then deletes build outputs and rebuilds with pub and Gradle
offline. The second build must produce an APK no larger than 16 MiB with the
expected signing certificate.

That16MiB threshold qualifies the fixed hello-world cache fixture. The real BLE
companion has a32MiB per-artifact ceiling; its larger AOT application exceeded
the fixture's original limit after successful compilation and signature checks.
Native jobs still enforce32MiB total decoded artifacts, with enough base64 JSON
headroom for that closure. Public input-upload limits are unchanged.

The image retains:

- `/opt/qualified-companion`: complete Android/iOS/web scaffolds, preserved
  template platform overlays, and the exact resolved `pubspec.lock`.
- `/opt/gradle-cache`: prebaked dependency/wrapper caches and an init script that
  enables Gradle offline mode; no stale daemon state or lock ownership.
- `/opt/qualified-android/qualification.json`: build settings, APK hash/size,
  resolved lock hash, cache size, and development certificate hash.
- `/opt/qualified-android/debug.keystore`: an intentionally public development
  signing credential with Android's standard debug alias/password. It is not an
  application distribution identity. The generated release-mode APK is labeled
  as development signed.

Public Gradle/pub downloads use isolated BuildKit cache mounts while baking;
failed compilation does not discard downloaded dependencies. The helper copies
those caches into ordinary `/opt/gradle-cache` and `/opt/pub-cache` image layers
in a `finally` block. Runtime images do not depend on BuildKit mounts. Each bake
command has a 15-minute ceiling. Shared user-home Gradle properties and an
explicit compiler-strategy environment property keep Flutter's included Gradle
build on the same bounded, in-process Kotlin configuration.
The Gradle heap is capped at 2 GiB, metaspace at 512 MiB, and reserved code cache
at 128 MiB, with two workers/processors. The initial 768 MiB heap reached the R8
release optimizer and failed with a real Java heap exhaustion; optimization and
shrinking remain enabled. The application worker's existing 8 GiB ceiling is
unchanged and must still be checked against measured runtime peaks, including
its private toolchain/cache copies. Dart UI/test changes are merged after the
cache qualification layer and do not rerun its fixed Android fixture.

After the first successful bake, export the lock to the checked-in template and
regenerate its manifest. Later bakes enforce that lock. A successful cache bake
does not qualify generated application behavior, physical Bluetooth, pairing,
permissions on a device, iOS compilation, or store publication. Those remain
separate checks. iOS delivery consists of unsigned source only.

At job execution, copy the Flutter SDK, pub cache, and Gradle cache to the job's
private writable directory. Keep `/opt/android-sdk` and `/opt/java17` read-only.
Copy the public development keystore to the private `HOME/.android`; set
`ANDROID_USER_HOME` and Java's `-Duser.home` to that private home too, because
changing `HOME` alone does not change Java's home lookup. The runner applies its
normal deadline, process, output, and resource ceilings.
Application subprocesses allow up to4,096 open descriptors; the original256
descriptor native default prevented Gradle from starting its daemon. Exhaustion
is reported as a resource failure rather than requesting a model code repair.
On local Docker, private disk scratch is required for the4.75GiB toolchain copy;
the actual tmpfs qualification was stopped for host memory pressure. Fargate
already uses task-local ephemeral disk. See [native capacity](../../infra/native-capacity.md).

Gradle 8.12 uses local TCP for daemon communication and UDP for cache lock
coordination, including when a build requests `--no-daemon`. Its lock listener
binds a wildcard address. `java.net.preferIPv4Stack=true` limits the address
family; it does not enforce loopback-only destinations. The application lane
therefore needs the explicit application task network policy: no NAT/internet,
restricted DNS, approved private endpoints, job-scoped callbacks, and no task
role or provider credentials. Do not describe that policy as a process-level
loopback filter. Other engineering lanes retain their stricter socket-family
policy.

Primary references: [Android command-line tools and published checksums](https://developer.android.com/studio),
[Temurin 17.0.18+8 release](https://github.com/adoptium/temurin17-binaries/releases/tag/jdk-17.0.18%2B8),
[Gradle 8.12 distribution checksum](https://services.gradle.org/distributions/gradle-8.12-bin.zip.sha256),
[Gradle daemon behavior](https://docs.gradle.org/current/userguide/gradle_daemon.html),
[Gradle 8.12 lock communicator](https://github.com/gradle/gradle/blob/v8.12.0/platforms/core-execution/persistent-cache/src/main/java/org/gradle/cache/internal/locklistener/FileLockCommunicator.java),
[compatible BLE mobile 5.4.2 metadata](https://pub.dev/api/packages/reactive_ble_mobile/versions/5.4.2).

## Recorded toolchain qualification

On 2026-10-31 the trusted fixture passed its widget test, optimized ARM64 release
build, clean offline release rebuild, and actual APK certificate verification.
The APK was 15,377,172 bytes (below 16 MiB), with SHA-256
`63a727a71315b6a5c1c261c8dde3693d59c8dadaf96b51d49ff7476a42a231db`.
The exact dependency lock hash was
`cf82bbcbbee6d417c894fb4b1a4c6801d1521a9631decc6090b30429b8f3dd37`.
The qualified base image manifest was
`sha256:b65a228546da22a445252984a091a2147b7d11caccd20f3648dfa2c548098ed7`;
later supervisor-source refreshes retain these toolchain layers but produce a
different final image identity. This record establishes the Android cache and
build-tool qualification only; the real companion's read-only worker execution
and physical Bluetooth behavior require their separate evidence.
