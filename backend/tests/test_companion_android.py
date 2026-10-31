"""Post-signature packaging checks; these ZIP fixtures are not compiled APK proof."""
import base64
import zipfile

import pytest

from kleos.companion_android import MAX_APK_BYTES, apk_metadata
from kleos.native_runner import MAX_FILE_BYTES, collect_file

PROFILE = {'developmentCertificateSha256': 'a' * 64, 'signing': 'public-development-debug-certificate'}
SIGNATURE = 'Signer #1 certificate SHA-256 digest: ' + 'a' * 64


def test_real_app_sized_package_survives_metadata_and_generated_file_transport(tmp_path):
    package = tmp_path / 'app-release.apk'
    with zipfile.ZipFile(package, 'w', compression=zipfile.ZIP_STORED) as archive:
        for name in ('AndroidManifest.xml', 'classes.dex', 'lib/arm64-v8a/libflutter.so'):
            archive.writestr(name, b'fixture')
        with archive.open('lib/arm64-v8a/libapp.so', 'w') as library:
            for _ in range(20):
                library.write(b'x' * 1024**2)
    assert 16 * 1024**2 < package.stat().st_size < MAX_APK_BYTES <= MAX_FILE_BYTES
    metadata = apk_metadata(package, PROFILE, SIGNATURE)
    file = collect_file(package, tmp_path)
    assert len(base64.b64decode(file.content)) == metadata['bytes']
    assert metadata['physicalBleValidated'] is False and metadata['status'] == 'built'


def test_oversized_apk_reports_actual_size_before_loading_archive(tmp_path):
    package = tmp_path / 'app-release.apk'
    with package.open('wb') as handle:
        handle.truncate(MAX_APK_BYTES + 1)
    with pytest.raises(ValueError, match=f'{MAX_APK_BYTES + 1} bytes'):
        apk_metadata(package, PROFILE, SIGNATURE)


def test_apk_symlink_cannot_be_packaged(tmp_path):
    target = tmp_path / 'target.apk'
    target.write_bytes(b'fixture')
    link = tmp_path / 'app-release.apk'
    link.symlink_to(target)
    with pytest.raises(ValueError, match='non-symlink'):
        apk_metadata(link, PROFILE, SIGNATURE)
