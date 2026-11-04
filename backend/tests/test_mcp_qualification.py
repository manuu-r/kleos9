import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from kleos.capabilities.mcp_bridge.installation import Installation, qualified_hash, source_hashes
from kleos.capabilities.registry import CapabilityRegistry
from kleos.contracts.models import DomainError
from kleos.infrastructure.mcp import McpRuntime, read_installations

ROOT = Path(__file__).parents[1] / 'src/kleos/capabilities/mcp_bridge'
REPORT = ROOT / 'qualification/mm-core-read-image.json'


def test_independently_reviewed_report_loads_real_exact_binding_without_daemon_or_default_install(monkeypatch):
    report = json.loads(REPORT.read_bytes())
    value = Installation.model_validate(report['installation'])
    assert report['testOnly'] is False and report['review']['independent'] is True
    assert report['sourceSha256'] == source_hashes()
    assert qualified_hash(value, report, environment='development')

    def forbidden(**kwargs):
        pytest.fail('Report validation and registry construction must not contact Docker')
    monkeypatch.setattr('kleos.infrastructure.mcp.DockerTransport', forbidden)
    settings = SimpleNamespace(environment='development', mcp_installations=[{
        'installation': report['installation'], 'qualificationReference': str(REPORT)}])
    registry = CapabilityRegistry()
    runtime = McpRuntime(settings, registry, object(), object(), object())
    assert runtime.keys == ['research.mcp_mm-core-image-inspector@1.0.0']
    assert runtime.runner is runtime.docker is None
    pin = registry.snapshot(runtime.keys)[0]
    assert pin['manifest']['transport'] == 'mcp' and pin['manifest']['effect'] == 'read'
    assert pin['manifest']['qualified'] is True
    empty = CapabilityRegistry()
    McpRuntime(SimpleNamespace(environment='development', mcp_installations=[]), empty,
               object(), object(), object())
    assert empty.catalog() == []
    settings.mcp_installations[0]['installation'] = value.model_copy(
        update={'image': 'sha256:' + '0' * 64}).model_dump(mode='json')
    with pytest.raises(DomainError) as failure:
        read_installations(settings)
    assert failure.value.code == 'mcp_unqualified'


def test_raw_attempts_are_preserved_and_watchdog_host_delta_is_explicit():
    report = json.loads(REPORT.read_bytes())
    for evidence in report['evidence']:
        raw = (REPORT.parent / evidence['path']).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == evidence['sha256']
    old = json.loads((REPORT.parent / report['hostOnlyDelta']['watchdogEvidence']).read_bytes())
    assert old['qualified'] is False and old['independentReview'] == 'pending'
    current = source_hashes()
    assert [name for name in current if current[name] != old['sourceSha256'][name]] == ['bridge.py']
    assert old['sourceSha256']['bridge.py'] == report['hostOnlyDelta']['oldSha256']
    assert current['bridge.py'] == report['hostOnlyDelta']['currentSha256']
    for path in report['hostOnlyDelta']['currentHostEvidence']:
        evidence = json.loads((REPORT.parent / path).read_bytes())
        assert evidence['sourceSha256'] == current
        assert evidence['installation'] == report['installation']
        assert evidence['fixturePassed'] and not evidence['qualified']
        assert evidence['finalCapacity']['jobs'] == 0
    watchdog = next(case for case in old['cases'] if case['name'] == 'image-watchdog-after-controller-loss')
    assert watchdog['exitCode'] == 137 and watchdog['retainedCapacityUntilRemoval']
