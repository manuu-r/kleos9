"""Exact operator installation and independent qualification binding."""
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import ConfigDict, Field

from kleos.contracts.models import DomainError, WireModel
from kleos.projects.store import fingerprint

from .transport import PROTOCOL_VERSION, SDK_VERSION

REQUIRED_CHECKS = frozenset({'protocol', 'scope', 'bounds', 'replay', 'isolation', 'artifact_inspection'})


class Installation(WireModel):
    model_config = ConfigDict(**(WireModel.model_config | {'frozen': True}))
    id: str = Field(pattern=r'^[a-z][a-z0-9_-]{0,49}$')
    version: str = Field(pattern=r'^[0-9]+\.[0-9]+\.[0-9]+$')
    image: str = Field(pattern=r'^sha256:[a-f0-9]{64}$')
    package_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    server_name: str = Field(min_length=1, max_length=100)
    server_version: str = Field(min_length=1, max_length=100)
    tool_name: str = Field(pattern=r'^[A-Za-z0-9_.-]{1,100}$')
    protocol: Literal['2025-11-25'] = PROTOCOL_VERSION
    sdk_version: Literal['1.26.0'] = SDK_VERSION
    network_policy: Literal['unix-only-no-egress'] = 'unix-only-no-egress'
    timeout_seconds: int = Field(default=30, ge=1, le=120)
    max_input_bytes: int = Field(default=1048576, ge=1, le=8388608)
    media_types: tuple[Literal['text/plain', 'image/png', 'image/jpeg'], ...] = Field(
        default=('text/plain',), min_length=1, max_length=3)


def source_hashes():
    root = Path(__file__).parent
    result = {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
            for name in ('transport.py', 'installation.py', 'bridge.py', 'runner.py', 'docker.py',
                'isolated_server.py', 'Dockerfile', 'Dockerfile.dockerignore', 'tool-schema.json',
                'image.pyproject.toml', 'image.uv.lock', 'vendor/source.json',
                'vendor/qwen-mm-core-read-image.tar.gz', 'vendor/LICENSE.qwen-mm-plugins')}
    for name in ('security.py', 'security/docker-default-seccomp.json'):
        result['execution/' + name] = hashlib.sha256((root.parents[1] / 'execution' / name).read_bytes()).hexdigest()
    for name in ('infrastructure/mcp.py', 'execution/capacity.py', 'capabilities/registry.py'):
        result[name] = hashlib.sha256((root.parents[1] / name).read_bytes()).hexdigest()
    return result


def qualified_hash(installation: Installation, report: dict, *, environment: str):
    """The caller reads operator-owned report bytes, never a project upload.

    Fixture qualification is accepted only in the test environment. This bridge
    ships no production report and cannot enable a desktop plugin by name.
    """
    try:
        review = report['review']
        reviewed = datetime.fromisoformat(review['reviewedAt'])
        checks = report['checks']
        valid = (report['qualified'] is True and report['installation'] == installation.model_dump(mode='json')
            and report['sourceSha256'] == source_hashes() and review['independent'] is True
            and isinstance(review['reviewer'], str) and bool(review['reviewer'].strip())
            and isinstance(review['reference'], str) and bool(review['reference'].strip())
            and reviewed.tzinfo is not None and review['sourceHash'] == fingerprint(report['sourceSha256'])
            and isinstance(checks, dict) and set(checks) == REQUIRED_CHECKS
            and all(status == 'PASS' for status in checks.values())
            and isinstance(report['testOnly'], bool)
            and (not report['testOnly'] or environment == 'test'))
    except (KeyError, TypeError, ValueError, AttributeError):
        valid = False
    if not valid:
        raise DomainError('MCP installation lacks matching independent qualification', 'mcp_unqualified')
    return fingerprint({'installation': installation.model_dump(mode='json'), 'report': report})
