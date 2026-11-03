"""Existing provider names remain usable without inheriting legacy runtime authority."""
from pathlib import Path

import pytest
from pydantic import SecretStr

from kleos.infrastructure.settings import Settings


@pytest.fixture(autouse=True)
def clear_provider_environment(monkeypatch):
    for key in ('KLEOS_META_API_KEY', 'KLEOS_MODAL_LLM_KEY', 'KLEOS_MODAL_LLM_URL',
                'KLEOS2_META_API_KEY', 'KLEOS2_MODAL_API_KEY', 'KLEOS2_MODAL_BASE_URL',
                'KLEOS2_MODEL_CATALOG', 'KLEOS2_TEXT_MODEL_KEY', 'KLEOS2_VISION_MODEL_KEY'):
        monkeypatch.delenv(key, raising=False)


def test_existing_names_load_only_providers_from_file(tmp_path, monkeypatch):
    monkeypatch.delenv('KLEOS2_DATABASE_URL', raising=False)  # CI exports it for the PostgreSQL tests
    path = tmp_path / '.env'
    path.write_text('KLEOS_META_API_KEY=private-meta\nKLEOS_MODAL_LLM_KEY=private-modal\n'
        'KLEOS_MODAL_LLM_URL=https://fixture.modal.run\nKLEOS_DATABASE_URL=postgresql://old/data\n'
        'KLEOS_DEVELOPMENT_NO_LOGIN=true\nKLEOS2_DATABASE_URL=postgresql://also-ignored/data\n'
        'KLEOS2_PHYSICAL_ACTIONS_ENABLED=true\n')
    configured = Settings(_env_file=path)
    assert configured.meta_api_key.get_secret_value() == 'private-meta'
    assert configured.modal_api_key.get_secret_value() == 'private-modal'
    assert configured.database_url.endswith('/kleos_rewrite')
    assert configured.development_no_login is False and configured.physical_actions_enabled is False
    assert {m['provider'] for m in configured.model_catalog} == {'meta', 'modal'}
    assert configured.text_model_key == configured.vision_model_key == 'meta-muse-spark'
    modal = next(m for m in configured.model_catalog if m['provider'] == 'modal')
    assert modal['base_url'] == 'https://fixture.modal.run/v1'
    assert modal['model'] == 'meta-models/Muse-Spark-Open-816B-A42B'
    assert 'private-' not in repr(configured)
    assert configured.modal_rate_limit_fallback_key is None


def test_existing_process_names_and_override_precedence(tmp_path, monkeypatch):
    path = tmp_path / '.env'
    path.write_text('KLEOS_META_API_KEY=file-meta\nKLEOS_MODAL_LLM_KEY=file-modal\n')
    monkeypatch.setenv('KLEOS_META_API_KEY', 'process-meta')
    monkeypatch.setenv('KLEOS_MODAL_LLM_KEY', 'process-modal')
    monkeypatch.setenv('KLEOS_MODAL_LLM_URL', 'https://fixture.modal.run/v1/')
    configured = Settings(_env_file=path)
    assert configured.meta_api_key.get_secret_value() == 'process-meta'
    assert configured.modal_api_key.get_secret_value() == 'process-modal'
    assert configured.model_catalog[1]['base_url'] == 'https://fixture.modal.run/v1'
    monkeypatch.setenv('KLEOS2_META_API_KEY', 'explicit-new-name')
    assert Settings(_env_file=path).meta_api_key.get_secret_value() == 'explicit-new-name'
    explicit = Settings(_env_file=path, meta_api_key=SecretStr('constructor'))
    assert explicit.meta_api_key.get_secret_value() == 'constructor'


def test_tests_and_explicit_disable_never_load_private_file(tmp_path):
    path = tmp_path / '.env'
    path.write_text('KLEOS_META_API_KEY=must-not-be-used\n')
    assert Settings(environment='test', _env_file=path).meta_api_key is None
    assert Settings(_env_file=None).meta_api_key is None
    assert Settings(_env_file=None).model_catalog == []
    assert Path(Settings.model_config['env_file']).name == '.env'


def test_explicit_catalog_and_selection_are_not_overwritten():
    configured = Settings(_env_file=None, meta_api_key='fixture', model_catalog=[],
        text_model_key=None, vision_model_key=None)
    assert configured.model_catalog == []
    assert configured.text_model_key is configured.vision_model_key is None


def test_modal_only_requires_existing_endpoint_and_key():
    incomplete = Settings(_env_file=None, modal_api_key='fixture')
    assert incomplete.model_catalog == [] and incomplete.text_model_key is None
    configured = Settings(_env_file=None, modal_api_key='fixture', modal_base_url='https://fixture.modal.run')
    assert configured.text_model_key == configured.vision_model_key == 'modal-muse-spark-open'
    assert len(configured.model_catalog) == 1
