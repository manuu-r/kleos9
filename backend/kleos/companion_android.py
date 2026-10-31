"""Qualified mobile scaffolds and development APK packaging; no model-selected tools."""
import hashlib
import json
import re
import shutil
import zipfile
from pathlib import Path

PROFILE = Path('/opt/qualified-android/qualification.json')
SCAFFOLD = Path('/opt/qualified-companion')
KEY = Path('/opt/qualified-android/debug.keystore')
MAX_APK_BYTES = 32 * 1024**2
SKIP_DIRECTORIES = {'build', '.gradle', '.dart_tool', 'Pods', '.symlinks', 'ephemeral', '.git'}
SKIP_FILES = {'local.properties', 'Generated.xcconfig', 'flutter_export_environment.sh',
              'GeneratedPluginRegistrant.java', 'GeneratedPluginRegistrant.h', 'GeneratedPluginRegistrant.m'}


def targets_for(request):
    targets = request.get('interfaces', {}).get('application', {}).get('targets', ['web', 'android'])
    if (not isinstance(targets, list) or not targets
            or any(not isinstance(target, str) or target not in {'web', 'android'} for target in targets)
            or len(set(targets)) != len(targets)):
        raise ValueError('Companion targets must be a nonempty selection of web and android')
    return targets


def qualification(path=PROFILE):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 65536:
        raise ValueError('The Android image has no qualified offline build profile')
    result = json.loads(path.read_text())
    expected = {'schemaVersion': 'companion-android-toolchain-v1',
                'qualification': 'trusted-fixture-online-and-offline-build',
                'javaHome': '/opt/java17', 'androidHome': '/opt/android-sdk',
                'gradleCache': '/opt/gradle-cache', 'signingSource': str(KEY),
                'targetPlatform': 'android-arm64', 'buildMode': 'release',
                'signing': 'public-development-debug-certificate', 'iosDelivery': 'source-only-unsigned'}
    if any(result.get(key) != value for key, value in expected.items()):
        raise ValueError('Android build profile does not match the qualified worker contract')
    for field in ('debugKeystoreSha256', 'developmentCertificateSha256', 'gradleDistributionSha256'):
        if not re.fullmatch(r'[a-fA-F0-9]{64}', result.get(field, '').replace(':', '')):
            raise ValueError('Android build profile is missing an integrity hash')
    return result


def _destination(work, relative):
    cursor = work
    for part in relative.parts:
        if part in {'', '.', '..'}:
            raise ValueError('Unsafe mobile scaffold path')
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError('Mobile scaffold cannot follow generated links')
    if not cursor.resolve().is_relative_to(work.resolve()):
        raise ValueError('Mobile scaffold escaped the workspace')
    return cursor


def prepare_platforms(work, root=SCAFFOLD):
    """Fill missing platform source files; never copy build caches or machine settings."""
    added, total = [], 0
    for platform in ('android', 'ios', 'web'):
        source = root / platform
        if source.is_symlink() or not source.is_dir():
            raise ValueError('Qualified Android/iOS/web source scaffold is unavailable')
        for path in sorted(source.rglob('*')):
            relative = path.relative_to(root)
            if any(part in SKIP_DIRECTORIES for part in relative.parts) or path.name in SKIP_FILES:
                continue
            if path.is_symlink():
                raise ValueError('Image-owned mobile scaffold must not contain links')
            if not path.is_file():
                continue
            total += path.stat().st_size
            if total > 16 * 1024**2 or len(added) > 256:
                raise ValueError('Mobile scaffold exceeds its source budget')
            target = _destination(work, relative)
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, target)
                target.chmod(0o755 if path.stat().st_mode & 0o111 else 0o644)
                added.append(str(relative))
    return added


def prepare_runtime(work, runtime, profile, key=KEY):
    if not runtime.get('gradleUserHome'):
        raise ValueError('Qualified offline Gradle cache is unavailable')
    if key.is_symlink() or not key.is_file() or hashlib.sha256(key.read_bytes()).hexdigest() != profile['debugKeystoreSha256']:
        raise ValueError('Public development signing key does not match the image qualification')
    home = work / '.home'
    android = _destination(work, Path('.home/.android'))
    android.mkdir(parents=True, exist_ok=True)
    destination = android / 'debug.keystore'
    if destination.exists() or destination.is_symlink():
        raise ValueError('A generated signing key cannot replace the qualified development credential')
    shutil.copyfile(key, destination)
    destination.chmod(0o600)
    local = _destination(work, Path('android/local.properties'))
    local.write_text(f'sdk.dir={profile["androidHome"]}\nflutter.sdk={runtime["flutterRoot"]}\n')
    return {'home': str(home), 'androidUserHome': str(android), 'gradleUserHome': runtime['gradleUserHome']}


def apk_metadata(path, profile, signature_log):
    if path.is_symlink() or not path.is_file():
        raise ValueError('The ARM64 development APK must be a regular non-symlink file')
    size = path.stat().st_size
    if size > MAX_APK_BYTES:
        raise ValueError(f'The ARM64 development APK is {size} bytes, exceeding {MAX_APK_BYTES} bytes')
    match = re.search(r'Signer #1 certificate SHA-256 digest:\s*([a-fA-F0-9:]+)', signature_log)
    certificate = match[1].replace(':', '').lower() if match else None
    if certificate != profile['developmentCertificateSha256'].replace(':', '').lower():
        raise ValueError('APK signer differs from the public development certificate')
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or any(Path(name).is_absolute() or '..' in Path(name).parts for name in names):
            raise ValueError('APK contains ambiguous or unsafe paths')
        if not {'AndroidManifest.xml', 'classes.dex', 'lib/arm64-v8a/libapp.so', 'lib/arm64-v8a/libflutter.so'}.issubset(names):
            raise ValueError('APK is missing compiled Android/Flutter ARM64 content')
    return {'status': 'built', 'targetPlatform': 'android-arm64', 'signing': profile['signing'],
            'developmentCertificateSha256': certificate, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'bytes': path.stat().st_size, 'physicalBleValidated': False}
