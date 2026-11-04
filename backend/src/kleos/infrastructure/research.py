"""Reviewed, explicitly installed web research; no public default endpoint."""
import hashlib
import json
from datetime import datetime
from pathlib import Path

from kleos.capabilities.web_research import SearxngGateway, register_searxng
from kleos.contracts.models import DomainError
from kleos.projects.store import fingerprint

REQUIRED_CHECKS = frozenset({'fixed_endpoint', 'query_selectors', 'response_bounds', 'authorization_fences',
    'durable_replay', 'request_budget', 'candidate_evidence', 'runtime_source_flow'})
SOURCE_PATHS = ('capabilities/web_research.py', 'capabilities/research.py',
    'projects/research.py', 'infrastructure/research.py')


def source_hashes():
    root = Path(__file__).parents[1]
    return {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in SOURCE_PATHS}


def configure_web_research(settings, registry, repository, *, client=None):
    raw = settings.web_search_installation
    if raw is None:
        return []
    if not isinstance(raw, dict) or set(raw) != {'provider', 'config', 'qualificationReference'} or raw['provider'] != 'searxng':
        raise DomainError('Unsupported web research installation', 'web_research_unavailable', 503)
    try:
        path = Path(raw['qualificationReference'])
        if path.is_symlink():
            raise ValueError('Report symlinks are not accepted')
        with path.open('rb') as stream:
            raw_report = stream.read(131073)
        if len(raw_report) > 131072:
            raise ValueError('Report too large')
        report = json.loads(raw_report)
        gateway = SearxngGateway(raw['config'], repository, client=client)
        test_only = report.get('testOnly')
        review = report.get('review', {})
        reviewed_at = datetime.fromisoformat(review.get('reviewedAt', ''))
        if (report.get('schemaVersion') != 1 or report.get('provider') != 'searxng'
                or report.get('qualified') is not True or type(test_only) is not bool
                or report.get('sourceSha256') != source_hashes()
                or review.get('independent') is not True
                or not isinstance(review.get('reviewer'), str) or not review['reviewer'].strip()
                or not isinstance(review.get('reference'), str) or not review['reference'].strip()
                or reviewed_at.tzinfo is None or reviewed_at.utcoffset() is None
                or review.get('sourceHash') != fingerprint(report.get('sourceSha256'))
                or not REQUIRED_CHECKS.issubset({entry['name'] for entry in report.get('checks', [])
                    if entry.get('status') == 'PASS'})):
            raise ValueError('Report does not qualify the current source')
        if test_only:
            if settings.environment != 'test' or report.get('scope') != 'http-mocked-read-only':
                raise ValueError('Mock evidence does not qualify a live instance')
        elif (report.get('scope') != 'operator-instance-read-only'
                or report.get('installationConfigHash') != fingerprint(gateway.installation.model_dump(mode='json'))):
            raise ValueError('Live installation identity is not qualified')
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        raise DomainError('Web research needs matching reviewed qualification evidence', 'web_research_unqualified', 503) from None
    return register_searxng(registry, gateway)
