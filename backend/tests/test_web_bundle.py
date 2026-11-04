"""Trusted data packaging only; no application compiler, code execution or network."""
import hashlib
import json
import os

import pytest

from kleos.execution import web_bundle as fonts


def sha(value):
    return hashlib.sha256(value).hexdigest()


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    web, sdk = tmp_path / 'build/web', tmp_path / 'sdk-fonts'
    (web / 'assets').mkdir(parents=True)
    sdk.mkdir()
    original = b'[{"family":"Existing","fonts":[]}]'
    (web / fonts.MANIFEST).write_bytes(original)
    regular, italic, license_bytes = b'trusted test font regular', b'trusted test font italic', b'trusted test license'
    monkeypatch.setattr(fonts, 'FONTS', (('Roboto-Regular.ttf', 400, 'normal', sha(regular)),
        ('Roboto-Italic.ttf', 400, 'italic', sha(italic))))
    monkeypatch.setattr(fonts, 'LICENSE_SHA256', sha(license_bytes))
    for name, data in [('Roboto-Regular.ttf', regular), ('Roboto-Italic.ttf', italic), ('Roboto_LICENSE.txt', license_bytes)]:
        (sdk / name).write_bytes(data)
    source = tmp_path / 'lib/main.dart'
    source.parent.mkdir()
    source.write_bytes(b'unchanged user source; never executed')
    return web, sdk, source, original


def test_default_fonts_are_local_pinned_licensed_and_idempotent(bundle):
    web, sdk, source, original = bundle
    before = source.read_bytes()
    record = fonts.package_web_fonts(web, sdk_fonts=sdk)
    manifest = json.loads((web / fonts.MANIFEST).read_bytes())
    assert manifest[0] == json.loads(original)[0]
    assert manifest[1]['family'] == 'Roboto' and len(manifest[1]['fonts']) == 2
    assert record['mode'] == 'pinned-sdk-default' and record['originalManifestSha256'] == sha(original)
    assert record['license']['sha256'] == sha((web / fonts.DIRECTORY / 'LICENSE.txt').read_bytes())
    assert source.read_bytes() == before
    assert fonts.package_web_fonts(web, sdk_fonts=sdk) == record
    assert json.loads((web / fonts.PROVENANCE).read_bytes()) == record


def test_explicit_application_roboto_is_preserved_byte_for_byte(bundle):
    web, sdk, source, _ = bundle
    custom = b'custom font bytes'
    (web / 'assets/custom.ttf').write_bytes(custom)
    raw = b'[ { "family" : "Roboto", "fonts" : [{"asset":"custom.ttf", "weight":500}] } ]'
    (web / fonts.MANIFEST).write_bytes(raw)
    (sdk / 'Roboto-Regular.ttf').unlink()  # Does not silently substitute its own SDK font.
    result = fonts.package_web_fonts(web, sdk_fonts=sdk)
    assert result['mode'] == 'application-supplied'
    assert (web / fonts.MANIFEST).read_bytes() == raw
    assert (web / 'assets/custom.ttf').read_bytes() == custom
    assert not (web / fonts.PROVENANCE).exists()


@pytest.mark.parametrize('name', ['Roboto-Regular.ttf', 'Roboto_LICENSE.txt'])
def test_changed_pinned_resource_fails_before_output_write(bundle, name):
    web, sdk, _, original = bundle
    (sdk / name).write_bytes(b'changed')
    with pytest.raises(ValueError, match='differs'):
        fonts.package_web_fonts(web, sdk_fonts=sdk)
    assert (web / fonts.MANIFEST).read_bytes() == original
    assert not (web / fonts.DIRECTORY).exists()


@pytest.mark.parametrize('bad', [b'{}', b'[ {"family":"Roboto","fonts":[]}]',
    b'[{"family":"Roboto","family":"Other","fonts":[]}]',
    b'[{"family":"Roboto","fonts":[{"asset":"../escape.ttf"}]}]',
    b'[{"family":"Roboto","fonts":[]},{"family":"Roboto","fonts":[]}]'])
def test_invalid_or_ambiguous_manifest_rejected(bundle, bad):
    web, sdk, _, _ = bundle
    (web / fonts.MANIFEST).write_bytes(bad)
    with pytest.raises(ValueError):
        fonts.package_web_fonts(web, sdk_fonts=sdk)


def test_symlink_output_and_hardlinked_manifest_cannot_mutate_sources(bundle):
    web, sdk, source, original = bundle
    (web / 'assets/fonts').symlink_to(source.parent, target_is_directory=True)
    with pytest.raises(ValueError, match='Symlink'):
        fonts.package_web_fonts(web, sdk_fonts=sdk)
    (web / 'assets/fonts').unlink()
    (web / fonts.MANIFEST).unlink()
    os.link(source, web / fonts.MANIFEST)
    with pytest.raises(ValueError, match='regular file'):
        fonts.package_web_fonts(web, sdk_fonts=sdk)
    assert source.read_bytes() == b'unchanged user source; never executed'


def test_reserved_outputs_and_modified_generated_evidence_are_rejected(bundle):
    web, sdk, _, _ = bundle
    (web / fonts.DIRECTORY).mkdir(parents=True)
    with pytest.raises(ValueError, match='already exists'):
        fonts.package_web_fonts(web, sdk_fonts=sdk)
    (web / fonts.DIRECTORY).rmdir()
    fonts.package_web_fonts(web, sdk_fonts=sdk)
    (web / fonts.DIRECTORY / 'Roboto-Regular.ttf').write_bytes(b'tampered')
    with pytest.raises(ValueError, match='provenance changed'):
        fonts.package_web_fonts(web, sdk_fonts=sdk)


def test_committed_font_image_report_is_usable_only_for_exact_local_installation():
    from pathlib import Path

    from kleos.contracts.models import DomainError
    from kleos.infrastructure.runtime import NativeToolConfiguration, native_qualification
    image = 'sha256:572fd5f8d64fd6d7a1bed7c955ee33c8223529a43fc0dedd8e53dcc81328e315'
    config = NativeToolConfiguration(capability='application', image_digest=image,
        provider_version='flutter-web-3.35.4-roboto-v1', qualified=True,
        qualification_reference=Path(__file__).parents[1] / 'docs/companion-native-font-browser.json')
    pin = native_qualification(config, desktop=True)
    assert pin['imageDigest'] == image and pin['applicationTarget'] == 'web'
    with pytest.raises(DomainError):
        native_qualification(config, desktop=False)
    with pytest.raises(DomainError):
        native_qualification(config.model_copy(update={'image_digest': 'sha256:' + 'a'*64}), desktop=True)
