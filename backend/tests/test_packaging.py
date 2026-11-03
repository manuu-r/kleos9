"""Local deployment shape and portable source-layout checks; no Docker/provider calls."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]


def test_compose_defaults_do_not_start_workers_or_expose_private_database():
    document = yaml.safe_load((ROOT / 'compose.yaml').read_text())
    services = document['services']
    assert {name for name, value in services.items() if not value.get('profiles')} == {'api', 'postgres'}
    assert services['migrate']['command'] == ['migrate'] and services['migrate']['profiles'] == ['setup']
    assert services['worker']['profiles'] == ['worker'] and services['worker']['command'] == ['worker']
    assert services['preview']['profiles'] == ['preview']
    assert services['preview']['command'][0] == 'preview-serve'
    assert 'ports' not in services['postgres']
    assert all(value.startswith('127.0.0.1:') for name in ('api', 'preview') for value in services[name]['ports'])
    for name in ('api', 'worker', 'preview', 'migrate'):
        config = services[name]
        assert config['pull_policy'] == 'never' and config['read_only'] is True
        assert config['user'] == '10001:10001' and config['cap_drop'] == ['ALL']
        assert config['tmpfs'] == ['/tmp:rw,noexec,nosuid,nodev,size=64m']
        assert not any('docker.sock' in str(value) for value in config['volumes'])
    assert services['preview']['volumes'] == ['artifacts:/var/lib/kleos/artifacts:ro']
    for key in ('KLEOS2_META_API_KEY', 'KLEOS2_MODAL_API_KEY', 'KLEOS2_SETUP_TOKEN'):
        assert services['preview']['environment'][key] == ''
    assert set(document['volumes']) == {'postgres', 'artifacts'}
    assert 'build' not in services['api']


def test_control_build_uses_fresh_explicit_inputs_and_no_legacy_entrypoint():
    source = (ROOT / 'Dockerfile').read_text()
    assert source.count('@sha256:') == 2
    assert 'uv sync --locked --no-dev --no-install-project' in source
    assert 'PYTHONPATH=/app/src' in source
    assert 'COPY src ./src' in source and 'COPY migrations ./migrations' in source
    assert 'COPY alembic.ini ./alembic.ini' in source
    assert 'ENTRYPOINT ["python", "-m", "kleos.cli"]' in source
    assert 'requirements.lock' not in source and 'kleos.kernel' not in source
    assert 'COPY . ' not in source and 'ADD ' not in source
    rules = (ROOT / 'Dockerfile.dockerignore').read_text().splitlines()
    assert '**' in rules and '**/.env*' in rules and '**/.data/**' in rules
    assert '!src/**' in rules and '!migrations/**' in rules
    assert not any(rule in {'!.env', '!.data/**', '!kleos/**'} for rule in rules)


def test_packaged_source_layout_finds_real_migrations_and_cli_without_project_install(tmp_path):
    for name in ('src', 'migrations'):
        shutil.copytree(ROOT / name, tmp_path / name, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    shutil.copyfile(ROOT / 'alembic.ini', tmp_path / 'alembic.ini')
    env = {**os.environ, 'PYTHONPATH': str(tmp_path / 'src'), 'PYTHONDONTWRITEBYTECODE': '1'}
    code = '''import json
from kleos.infrastructure.runtime import BACKEND_ROOT,migration_config,runtime_contracts
from alembic.script import ScriptDirectory
print(json.dumps({'root':str(BACKEND_ROOT),'heads':ScriptDirectory.from_config(migration_config()).get_heads(),
 'ready':'/api/v1/ready' in runtime_contracts()['paths']}))'''
    result = subprocess.run([sys.executable, '-c', code], cwd=tmp_path, env=env, capture_output=True, text=True, check=True)
    data = json.loads(result.stdout)
    assert Path(data['root']) == tmp_path and data['heads'] and data['ready']
    help_result = subprocess.run([sys.executable, '-m', 'kleos.cli', '--help'], cwd=tmp_path, env=env,
        capture_output=True, text=True, check=True)
    assert 'preview-serve' in help_result.stdout and 'export-runtime-contracts' in help_result.stdout


def test_actual_compose_interpolation_preserves_existing_provider_names(tmp_path):
    import pytest
    if not shutil.which('docker'):
        pytest.skip('Docker Compose CLI unavailable; no daemon needed for this check')
    probe = subprocess.run(['docker', 'compose', 'version'], capture_output=True)
    if probe.returncode:
        pytest.skip('Docker Compose plugin unavailable')
    path = tmp_path / 'private-fixture.env'
    path.write_text('KLEOS2_DATABASE_URL=postgresql+psycopg://kleos:fixture@postgres/kleos2_local\n'
        'KLEOS2_POSTGRES_PASSWORD=fixture\nKLEOS2_SESSION_SECRET=' + 's'*40 + '\nKLEOS2_SETUP_TOKEN=' + 't'*32 + '\n'
        'KLEOS_META_API_KEY=fixture-existing-meta\nKLEOS_MODAL_LLM_KEY=fixture-existing-modal\n'
        'KLEOS_MODAL_LLM_URL=https://fixture.modal.example/v1\n')
    path.chmod(0o600)
    environment = {key: value for key, value in os.environ.items() if not key.startswith(('KLEOS_', 'KLEOS2_'))}
    command = ['docker', 'compose', '--env-file', str(path), '-f', str(ROOT / 'compose.yaml'),
        '--profile', 'worker', '--profile', 'preview', 'config', '--format', 'json']
    result = subprocess.run(command, env=environment, capture_output=True, text=True, check=True)
    document = json.loads(result.stdout)
    for service in ('api', 'worker'):
        configured = document['services'][service]['environment']
        assert configured['KLEOS2_META_API_KEY'] == configured['KLEOS_META_API_KEY'] == 'fixture-existing-meta'
        assert configured['KLEOS2_MODAL_API_KEY'] == configured['KLEOS_MODAL_LLM_KEY'] == 'fixture-existing-modal'
        assert configured['KLEOS2_MODAL_BASE_URL'] == configured['KLEOS_MODAL_LLM_URL'] == 'https://fixture.modal.example/v1'
    assert document['services']['api']['environment']['KLEOS2_MODEL_CATALOG'] is None
    preview = document['services']['preview']['environment']
    assert all(preview[key] == '' for key in ('KLEOS2_META_API_KEY', 'KLEOS_META_API_KEY',
        'KLEOS2_MODAL_API_KEY', 'KLEOS_MODAL_LLM_KEY', 'KLEOS2_MODAL_BASE_URL', 'KLEOS_MODAL_LLM_URL'))
    result = subprocess.run(command, env={**environment, 'KLEOS2_META_API_KEY': 'fixture-new-meta',
        'KLEOS2_MODAL_RATE_LIMIT_FALLBACK_KEY': 'fixture-explicit-fallback'},
        capture_output=True, text=True, check=True)
    assert json.loads(result.stdout)['services']['api']['environment']['KLEOS2_META_API_KEY'] == 'fixture-new-meta'

    assert json.loads(result.stdout)['services']['worker']['environment']['KLEOS2_MODAL_RATE_LIMIT_FALLBACK_KEY'] == 'fixture-explicit-fallback'
