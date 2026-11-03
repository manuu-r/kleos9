"""Offline provenance guards for the opt-in native browser harness; no native execution."""
import json
from pathlib import Path

import pytest
from companion_native_browser_fixture import IMAGE, SOURCES, inspect_evidence

from kleos.artifacts.storage import BlobStore
from kleos.execution.protocol import digest


def fixture_evidence(root):
    source = Path(__file__).parents[1] / 'src/kleos/execution/qualification/web-fixture-sources.json'
    (root / 'fixture-sources.json').write_bytes(source.read_bytes())
    raw = b'trusted-test-only-bytes-not-a-real-native-build'
    (root / 'companion-web.zip').write_bytes(raw)
    blobs = BlobStore(root / 'blobs')
    blobs.put(raw)
    report = {'qualified': True, 'sourceOverride': False, 'imageId': IMAGE,
        'fixtureSourcesSha256': SOURCES, 'finalCapacity': {'jobs': 0},
        'artifacts': [{'id': 'test-only', 'path': 'outputs/companion-web.zip', 'sha256': digest(raw), 'size': len(raw)}]}
    (root / 'evidence.json').write_text(json.dumps(report))
    return report


@pytest.mark.parametrize('field,value', [('qualified', False), ('sourceOverride', True),
    ('imageId', 'sha256:' + 'c'*64), ('fixtureSourcesSha256', 'd'*64), ('finalCapacity', {'jobs': 1})])
def test_native_harness_rejects_unverified_provenance(tmp_path, field, value):
    report = fixture_evidence(tmp_path)
    report[field] = value
    (tmp_path / 'evidence.json').write_text(json.dumps(report))
    with pytest.raises(ValueError, match='provenance'):
        inspect_evidence(tmp_path)


def test_native_harness_rejects_changed_bundle(tmp_path):
    fixture_evidence(tmp_path)
    (tmp_path / 'companion-web.zip').write_bytes(b'different')
    with pytest.raises(ValueError, match='hash/size'):
        inspect_evidence(tmp_path)


def test_native_harness_verifies_sources_and_private_blob(tmp_path):
    report = fixture_evidence(tmp_path)
    assert inspect_evidence(tmp_path)[2] == report['artifacts'][0]
    (tmp_path / 'fixture-sources.json').write_text('{"lib/main.dart":"changed"}')
    with pytest.raises(ValueError, match='provenance'):
        inspect_evidence(tmp_path)
