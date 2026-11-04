"""Versioned preview-only removal of a reviewed Flutter service-worker startup call.

No stored ZIP or application source is changed. Unsupported bootstrap programs
are never rewritten heuristically. The loader digest comes from exact-image
7c5aedd853f6be70c0d629a90134297fdf94716213cf32b3e2a743339776ea8d.
"""
import hashlib
import json
import re
from dataclasses import dataclass

from kleos.contracts.models import DomainError

TRANSFORM_VERSION = 'flutter-preview-no-service-worker-v1'
QUALIFIED_LOADER_SHA256 = 'e3081edda6d2ee9064d700a98f7ba8bb634aea05d735980272ff8d2179e48016'
ENGINE_REVISION = 'c29809135135e262a912cf583b2c90deb9ded610'
MARKER = b'\nif (!window._flutter) {\n  window._flutter = {};\n}\n_flutter.buildConfig = '
SUFFIX = re.compile(rb';\n\n_flutter\.loader\.load\(\{\n  serviceWorkerSettings: \{\n    serviceWorkerVersion: "[0-9]{1,20}"\n  \}\n\}\);\n?\Z')


@dataclass(frozen=True)
class BootstrapView:
    content: bytes
    source_sha256: str
    view_sha256: str
    version: str = TRANSFORM_VERSION

    def headers(self):
        return {'X-Kleos-Preview-Transform': self.version, 'X-Kleos-Source-Sha256': self.source_sha256,
            'X-Kleos-View-Sha256': self.view_sha256}


def preview_bootstrap(source):
    """Recognize only the reviewed loader, exact local CanvasKit build and default suffix."""
    if len(source) > 64 * 1024:
        raise DomainError('Flutter preview bootstrap is unsupported', 'preview_bootstrap_unsupported', 422)
    try:
        loader, tail = source.split(MARKER, 1)
        suffix = SUFFIX.search(tail)
        if suffix is None:
            if b'serviceWorker' not in source:
                return None
            raise ValueError()
        config = json.loads(tail[:suffix.start()])
        expected = {'engineRevision': ENGINE_REVISION, 'builds': [{'compileTarget': 'dart2js',
            'renderer': 'canvaskit', 'mainJsPath': 'main.dart.js'}], 'useLocalCanvasKit': True}
        if (hashlib.sha256(loader).hexdigest() != QUALIFIED_LOADER_SHA256 or config != expected
                or len(tail[:suffix.start()]) > 2048):
            raise ValueError()
        result = loader + MARKER + tail[:suffix.start()] + b';\n\n_flutter.loader.load();\n'
        return BootstrapView(result, hashlib.sha256(source).hexdigest(), hashlib.sha256(result).hexdigest())
    except (ValueError, UnicodeError, TypeError):
        if b'serviceWorker' not in source:
            return None
        raise DomainError('Flutter service-worker bootstrap has not been qualified for isolated preview',
            'preview_bootstrap_unsupported', 422) from None
