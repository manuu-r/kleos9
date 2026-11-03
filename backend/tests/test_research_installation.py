import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from test_web_research import installation

from kleos.capabilities.registry import CapabilityRegistry
from kleos.contracts.models import DomainError
from kleos.infrastructure.research import REQUIRED_CHECKS, configure_web_research, source_hashes
from kleos.projects.store import fingerprint


def settings(tmp_path, *, environment='test', **changes):
    report = {'schemaVersion': 1, 'provider': 'searxng', 'qualified': True,
        'scope': 'http-mocked-read-only', 'testOnly': True, 'sourceSha256': source_hashes(),
        'review': {'independent': True, 'reviewer': 'synthetic test fixture', 'reference': 'fixture-only',
            'reviewedAt': datetime.now(UTC).isoformat(), 'sourceHash': fingerprint(source_hashes())},
        'checks': [{'name': name, 'status': 'PASS'} for name in sorted(REQUIRED_CHECKS)], **changes}
    path = tmp_path / 'report.json'
    path.write_text(json.dumps(report))
    return SimpleNamespace(environment=environment, web_search_installation={
        'provider': 'searxng', 'config': installation().model_dump(mode='json'), 'qualificationReference': str(path)})


def test_default_no_provider_and_exact_mock_qualification_only_in_test(tmp_path):
    registry = CapabilityRegistry()
    assert configure_web_research(SimpleNamespace(web_search_installation=None), registry, None) == []
    assert registry.catalog() == []
    assert configure_web_research(settings(tmp_path), registry, None) == ['research.web_search@1.0.0']
    assert registry.catalog()[0]['manifest']['provider'] == 'searxng'


@pytest.mark.parametrize('changes', [
    {'environment': 'local'}, {'environment': 'production'}, {'sourceSha256': {}}, {'checks': []},
    {'testOnly': False}, {'testOnly': None}, {'qualified': False}, {'scope': 'all-engineering'}, {'review': {}}])
def test_mock_or_incomplete_evidence_cannot_enable_live_provider(tmp_path, changes):
    with pytest.raises(DomainError) as error:
        configure_web_research(settings(tmp_path, **changes), CapabilityRegistry(), None)
    assert error.value.code == 'web_research_unqualified'


def test_report_does_not_supply_endpoint_or_fake_production_qualification(tmp_path):
    selected = settings(tmp_path, testOnly=False, scope='operator-instance-read-only', installationConfigHash='0'*64)
    with pytest.raises(DomainError):
        configure_web_research(selected, CapabilityRegistry(), None)
