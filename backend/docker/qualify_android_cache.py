"""Build an immutable, public Android dependency cache during trusted image bake.

No user project, provider credentials, production signing key, or accepted state is
available here. The report qualifies build/cache availability, not physical BLE.
"""
import hashlib
import json
import os
import shutil
import signal
import subprocess
from pathlib import Path

MANIFEST = Path("/opt/kleos-companion-runtime.json")
TEMPLATE = Path("/opt/qualified-companion")
OUTPUT = Path("/opt/qualified-android")
FIXTURE = Path("/tmp/android-cache-fixture")
FLUTTER = "/opt/flutter/bin/flutter"
CACHE_SOURCES = {Path("/var/cache/kleos-gradle"): Path("/opt/gradle-cache"),
                 Path("/var/cache/kleos-pub"): Path("/opt/pub-cache")}


def command(args, *, cwd=FIXTURE, environment=None):
    print("Android cache stage:", " ".join(map(str, args)), flush=True)
    process = subprocess.Popen(args, cwd=cwd, env=environment or os.environ.copy(),
                               start_new_session=True)
    try:
        status = process.wait(timeout=900)
        if status:
            raise subprocess.CalledProcessError(status, args)
    except BaseException:
        # Stop this stage's descendants before preserving caches on failure.
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)
        raise


def sha256(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def configure_gradle(project, settings):
    wrapper = project / "android/gradle/wrapper/gradle-wrapper.properties"
    lines = [line for line in wrapper.read_text().splitlines()
             if not line.startswith(("distributionUrl=", "distributionSha256Sum="))]
    lines += ["distributionUrl=" + settings["gradleDistributionUrl"].replace(":", r"\:"),
              "distributionSha256Sum=" + settings["gradleDistributionSha256"]]
    wrapper.write_text("\n".join(lines) + "\n")
    (project / "android/gradle.properties").write_text(
        "org.gradle.jvmargs=" + settings["gradleJvmArgs"] + "\n"
        "org.gradle.daemon=false\norg.gradle.workers.max=2\n"
        "org.gradle.parallel=false\norg.gradle.vfs.watch=false\n"
        "kotlin.compiler.execution.strategy=in-process\n"
        "android.useAndroidX=true\nandroid.enableJetifier=false\n"
    )


def qualify():
    settings = json.loads(MANIFEST.read_text())
    if FIXTURE.exists() or OUTPUT.exists():
        raise RuntimeError("Android cache qualification requires fresh directories")
    OUTPUT.mkdir(mode=0o755)
    overlays = {path.relative_to(TEMPLATE): path.read_bytes()
                for platform in ("android", "ios", "web")
                for path in (TEMPLATE / platform).rglob("*") if path.is_file()}
    command([FLUTTER, "create", "--no-pub", "--platforms", "android,ios,web",
             "--org", "com.kleos", "--project-name", settings["projectName"], str(FIXTURE)], cwd="/tmp")
    # Use a fixed Flutter-generated hello-world entrypoint to warm plugin/native
    # dependencies; app-specific source has its own independent execution checks.
    for name in ("pubspec.yaml", "pubspec.lock"):
        source = TEMPLATE / name
        if source.is_file():
            shutil.copy2(source, FIXTURE / name)
    configure_gradle(FIXTURE, settings)
    # Included builds (Flutter's Gradle plugin) do not inherit a parent project's
    # gradle.properties. User-home properties and ORG_GRADLE_PROJECT apply to all.
    gradle_cache = Path(os.environ["GRADLE_USER_HOME"])
    gradle_cache.mkdir(parents=True, exist_ok=True)
    shutil.copy2(FIXTURE / "android/gradle.properties", gradle_cache / "gradle.properties")
    os.environ["ORG_GRADLE_PROJECT_kotlin.compiler.execution.strategy"] = "in-process"
    offline = gradle_cache / "init.d/kleos-offline.gradle"
    offline.unlink(missing_ok=True)
    command([FLUTTER, "pub", "get"] + (["--enforce-lockfile"]
            if (FIXTURE / "pubspec.lock").exists() else []))
    # Warm the host Flutter tester and prove the standard generated widget test;
    # application-specific unit/widget tests are exercised separately by the runner.
    command([FLUTTER, "--no-version-check", "test", "--no-pub"])
    # Store the exact lock and complete platform scaffolds for source delivery.
    shutil.copy2(FIXTURE / "pubspec.lock", TEMPLATE / "pubspec.lock")
    for platform in ("android", "ios", "web"):
        shutil.copytree(FIXTURE / platform, TEMPLATE / platform, dirs_exist_ok=True)
    for relative, content in overlays.items():
        destination = TEMPLATE / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
    home = FIXTURE / ".bake-home"
    (home / ".android").mkdir(parents=True)
    key = home / ".android/debug.keystore"
    command([str(Path(settings["javaHome"]) / "bin/keytool"), "-genkeypair", "-noprompt",
             "-keystore", str(key), "-storepass", "android", "-alias", "androiddebugkey",
             "-keypass", "android", "-keyalg", "RSA", "-keysize", "2048", "-validity", "10000",
             "-dname", "CN=Kleos Public Development Only,O=Kleos,C=US"])
    key.chmod(0o644)  # Public test credential; never use this key for distribution.
    shutil.copy2(key, OUTPUT / "debug.keystore")
    env = os.environ.copy()
    env.update(HOME=str(home), JAVA_HOME=settings["javaHome"],
               PATH=str(Path(settings["javaHome"]) / "bin") + os.pathsep + env.get("PATH", "/usr/bin:/bin"),
               ANDROID_HOME=settings["androidHome"], ANDROID_SDK_ROOT=settings["androidHome"],
               ANDROID_USER_HOME=str(home / ".android"),
               GRADLE_USER_HOME=str(gradle_cache),
               JAVA_TOOL_OPTIONS=("-XX:ActiveProcessorCount=2 -Djava.net.preferIPv4Stack=true "
                                  f"-Duser.home={home}"))
    build = [FLUTTER, "--no-version-check", "--verbose", "build", "apk", "--release",
             "--target-platform", "android-arm64", "--no-pub"]
    command(build, environment=env)
    # The offline init script is also a runtime policy, copied into each job's
    # GRADLE_USER_HOME. Gradle still needs task-local TCP/UDP for JVM/lock IPC.
    init = gradle_cache / "init.d/kleos-offline.gradle"
    init.parent.mkdir(exist_ok=True)
    init.write_text("gradle.startParameter.offline = true\n")
    shutil.rmtree(FIXTURE / "build")
    shutil.rmtree(FIXTURE / "android/.gradle", ignore_errors=True)
    command([FLUTTER, "pub", "get", "--offline", "--enforce-lockfile"], environment=env)
    command(build, environment=env)
    apk = FIXTURE / "build/app/outputs/flutter-apk/app-release.apk"
    if not apk.is_file() or apk.stat().st_size > settings["apkMaxBytes"]:
        raise RuntimeError("Qualified ARM64 APK is missing or exceeds the 16 MiB artifact ceiling")
    signer = Path(settings["androidHome"]) / "build-tools/35.0.0/apksigner"
    certificate = subprocess.check_output(
        [str(Path(settings["javaHome"]) / "bin/keytool"), "-exportcert", "-keystore", str(key),
         "-storepass", "android", "-alias", "androiddebugkey"], env=env, timeout=60)
    certificate_sha = hashlib.sha256(certificate).hexdigest()
    signature_report = subprocess.check_output(
        [str(signer), "verify", "--verbose", "--print-certs", str(apk)],
        env=env, timeout=60, text=True)
    if f"certificate SHA-256 digest: {certificate_sha}" not in signature_report:
        raise RuntimeError("APK was not signed by the declared public development certificate")
    cache = gradle_cache
    # Never preserve stale daemons, local socket state, or lock ownership.
    shutil.rmtree(cache / "daemon", ignore_errors=True)
    for lock in cache.rglob("*.lock"):
        if lock.is_file():
            lock.unlink()
    report = {**settings, "qualification": "trusted-fixture-online-and-offline-build",
              "pubspecLockSha256": sha256(TEMPLATE / "pubspec.lock"),
              "fixtureApkSha256": sha256(apk), "fixtureApkBytes": apk.stat().st_size,
              "debugKeystoreSha256": sha256(OUTPUT / "debug.keystore"),
              "developmentCertificateSha256": certificate_sha,
              "gradleCacheBytes": sum(p.stat().st_size for p in cache.rglob("*") if p.is_file()),
              "physicalBleValidated": False, "iosBuilt": False}
    (OUTPUT / "qualification.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report), flush=True)
    shutil.rmtree(FIXTURE)


def main():
    # Downloads live in BuildKit cache mounts and survive a failed compile. Copy
    # them into ordinary image layers in finally; final images never depend on a
    # build-cache mount being available. No customer code uses these shared mounts.
    for source in CACHE_SOURCES:
        source.mkdir(parents=True, exist_ok=True)
    os.environ["GRADLE_USER_HOME"] = "/var/cache/kleos-gradle"
    os.environ["PUB_CACHE"] = "/var/cache/kleos-pub"
    try:
        qualify()
    finally:
        for source, destination in CACHE_SOURCES.items():
            if destination.exists():
                shutil.rmtree(destination)
            shutil.copytree(source, destination)


if __name__ == "__main__":
    main()
