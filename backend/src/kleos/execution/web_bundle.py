"""Data-only, pinned default-font packaging inside the isolated application builder.

Only build/web output is changed. App-declared Roboto wins. No network, source
rewrites, font installation, font execution, or host application execution.
"""
import hashlib
import json
import os
import stat
from pathlib import Path

VERSION = 'flutter-sdk-roboto-v1'
SDK_FONTS = Path('/opt/flutter/bin/cache/artifacts/material_fonts')
LICENSE_SHA256 = 'cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30'
# Bytes inspected from the qualified Flutter3.35.4 toolchain; new bytes require qualification.
FONTS = (
    ('Roboto-Thin.ttf', 100, 'normal', '3d91f7aa69cb7f7064035895c566ac5cb9b2084582d351af7267bb4e0fba60f5'),
    ('Roboto-ThinItalic.ttf', 100, 'italic', 'f5e89a344894a60f0f98f1c0182ba51f5046e29dde3118820d39025445aeaae8'),
    ('Roboto-Light.ttf', 300, 'normal', 'a6d343d425bc38db90152fa06058b1c7391eca9264f334ef65c1ce175085c6f6'),
    ('Roboto-LightItalic.ttf', 300, 'italic', '364a23e13c64937c3fcea3db778628b89379c893849bbade6e5bf83c5f605c6a'),
    ('Roboto-Regular.ttf', 400, 'normal', '79e851404657dac2106b3d22ad256d47824a9a5765458edb72c9102a45816d95'),
    ('Roboto-Italic.ttf', 400, 'italic', '5fce8b6f8ba9f4d19f0d535e241d56a2b8e72bb07e7df711d968d092ef7f9fca'),
    ('Roboto-Medium.ttf', 500, 'normal', 'f205cc511821ea56078a105557fcea6253129404d411c997e1866fbd006abb68'),
    ('Roboto-MediumItalic.ttf', 500, 'italic', 'f177eed10d2470d13ec68d04907a582829d0ee8281f8a02a906f6954c7816e58'),
    ('Roboto-Bold.ttf', 700, 'normal', '7d0b991ee3e0be7af01ad7ea8cd2beea6c00a25e679a0226b6737f079aafff86'),
    ('Roboto-BoldItalic.ttf', 700, 'italic', 'a4bcac14f419a97de0917198a4bc51c3ed4fc4a3db9f68a5102f23664ee01354'),
    ('Roboto-Black.ttf', 900, 'normal', '3872e9b39760a1b59ac1e192633dbb3b58e595b4d423930ac7ded525e9ae25e0'),
    ('Roboto-BlackItalic.ttf', 900, 'italic', '013d22a4fb2638adba28555ee19366f4585f6dc533b7c332f4931a231497cb22'),
)
MANIFEST = 'assets/FontManifest.json'
DIRECTORY = 'assets/fonts/kleos-default-roboto'
PROVENANCE = 'assets/kleos-default-fonts.json'


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _path(root, relative):
    path = Path(relative)
    if (not isinstance(relative, str) or not relative or path.is_absolute() or '\\' in relative
            or any(part in {'', '.', '..'} for part in relative.split('/'))):
        raise ValueError('Unsafe font asset path')
    current = Path(root)
    if any(parent.is_symlink() for parent in (current, *current.parents)) or not current.is_dir():
        raise ValueError('Font output/resource root is unsafe')
    for part in path.parts:
        current /= part
        if current.is_symlink():
            raise ValueError('Symlink in font output/resource path')
    return current


def _read(root, relative, limit):
    target = _path(root, relative)
    fd = os.open(target, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit:
            raise ValueError('Font resource is not a bounded regular file')
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError('Font resource exceeds byte limit')
    return raw


def _write(root, relative, raw, *, replace=False):
    target = _path(root, relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    _path(root, relative)  # Recheck created path components.
    if target.exists() and not replace:
        raise ValueError('Reserved font output path already exists')
    temporary = target.with_name(target.name + '.kleos-tmp')
    with temporary.open('xb') as stream:
        stream.write(raw)
    try:
        if target.exists():
            info = target.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError('Unsafe font output destination')
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _json(raw):
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise ValueError('Duplicate JSON key')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs)


def package_web_fonts(web_root, *, sdk_fonts=SDK_FONTS):
    """Add fixed Roboto resources only when the compiled app has no Roboto family.

    Caller passes its fixed build/web directory after Flutter finishes, before ZIP
    publication and the existing input-source hash recheck. This function imports
    no application code and invokes no subprocess. Returned provenance is also
    included in the exported ZIP for added defaults.
    """
    root = Path(web_root)
    raw = _read(root, MANIFEST, 256 * 1024)
    manifest = _json(raw)
    if (not isinstance(manifest, list) or len(manifest) > 256
            or any(not isinstance(row, dict) or not isinstance(row.get('family'), str)
                   or not isinstance(row.get('fonts'), list) for row in manifest)):
        raise ValueError('Invalid bounded Flutter font manifest')
    roboto = [row for row in manifest if row['family'] == 'Roboto']
    if len(roboto) > 1:
        raise ValueError('Ambiguous app Roboto family')
    if roboto:
        entries = roboto[0]['fonts']
        if not 1 <= len(entries) <= 32:
            raise ValueError('App Roboto family requires bounded local fonts')
        resources = []
        for item in entries:
            if not isinstance(item, dict) or not isinstance(item.get('asset'), str):
                raise ValueError('Invalid app font asset')
            data = _read(root, 'assets/' + item['asset'], 4 * 1024**2)
            resources.append({'path': 'assets/' + item['asset'], 'sha256': _sha(data), 'size': len(data)})
        if sum(item['size'] for item in resources) > 16 * 1024**2:
            raise ValueError('App font resources exceed aggregate bound')
        if _path(root, PROVENANCE).exists():
            record = _json(_read(root, PROVENANCE, 32 * 1024))
            expected = [{'asset': 'fonts/kleos-default-roboto/' + name, 'weight': weight, 'style': style}
                for name, weight, style, _ in FONTS]
            if (entries != expected or record.get('version') != VERSION or record.get('family') != 'Roboto'
                    or record.get('manifestSha256') != _sha(raw) or record.get('fonts') != resources
                    or [item['sha256'] for item in resources] != [item[3] for item in FONTS]
                    or _sha(_read(root, DIRECTORY + '/LICENSE.txt', 32 * 1024)) != LICENSE_SHA256):
                raise ValueError('Existing generated font provenance changed')
            return record
        return {'version': VERSION, 'mode': 'application-supplied', 'family': 'Roboto', 'fonts': resources,
            'manifestSha256': _sha(raw), 'sourceMutation': False}
    if _path(root, DIRECTORY).exists() or _path(root, PROVENANCE).exists():
        raise ValueError('Reserved default font output already exists')
    prepared = []
    for name, weight, style, checksum in FONTS:
        data = _read(sdk_fonts, name, 512 * 1024)
        if _sha(data) != checksum:
            raise ValueError('SDK Roboto resource differs from pinned bytes')
        prepared.append((name, weight, style, data))
    license_bytes = _read(sdk_fonts, 'Roboto_LICENSE.txt', 32 * 1024)
    if _sha(license_bytes) != LICENSE_SHA256 or sum(len(row[3]) for row in prepared) > 3 * 1024**2:
        raise ValueError('SDK font license or aggregate bound differs')
    entries, resources = [], []
    for name, weight, style, data in prepared:
        destination = DIRECTORY + '/' + name
        _write(root, destination, data)
        entries.append({'asset': 'fonts/kleos-default-roboto/' + name, 'weight': weight, 'style': style})
        resources.append({'path': destination, 'sha256': _sha(data), 'size': len(data)})
    _write(root, DIRECTORY + '/LICENSE.txt', license_bytes)
    manifest.append({'family': 'Roboto', 'fonts': entries})
    derived = (json.dumps(manifest, separators=(',', ':'), ensure_ascii=False) + '\n').encode()
    record = {'version': VERSION, 'mode': 'pinned-sdk-default', 'family': 'Roboto',
        'fonts': resources, 'license': {'path': DIRECTORY + '/LICENSE.txt', 'sha256': LICENSE_SHA256,
            'identifier': 'Apache-2.0'}, 'sourceResource': '/opt/flutter/bin/cache/artifacts/material_fonts',
        'originalManifestSha256': _sha(raw), 'manifestSha256': _sha(derived), 'sourceMutation': False,
        'notice': 'Roboto font binaries copied unchanged from the pinned Flutter SDK; embedded notices retained.'}
    _write(root, MANIFEST, derived, replace=True)
    _write(root, PROVENANCE, (json.dumps(record, indent=2) + '\n').encode())
    return record
