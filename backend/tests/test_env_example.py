"""`.env.example` must describe the real configuration: no invented names, nothing required missing, no secrets."""
import json
import re
from pathlib import Path

import pytest
import yaml

from kleos.infrastructure.settings import Settings

ROOT = Path(__file__).parents[1]
LINE = re.compile(r'^(#\s)?(KLEOS_[A-Z0-9_]+)=(.*)$')
COMPOSE_ONLY = {'KLEOS_POSTGRES_PASSWORD', 'KLEOS_CONTROL_IMAGE', 'KLEOS_LOCAL_API_PORT', 'KLEOS_LOCAL_PREVIEW_PORT'}
TEST_ONLY = re.compile(r'^KLEOS_(TEST_[A-Z_]+|LIVE_[A-Z_]+|E2E_API)$')
ALIASES = {'KLEOS_MODAL_LLM_KEY', 'KLEOS_MODAL_LLM_URL'}


def entries():
    rows = []
    for line in (ROOT / '.env.example').read_text().splitlines():
        match = LINE.match(line)
        if match:
            rows.append((match.group(2), match.group(3), match.group(1) is None))
    return rows


def test_every_name_is_a_real_setting_compose_variable_or_documented_test_flag():
    settings = {'KLEOS_' + name.upper() for name in Settings.model_fields}
    unknown = [name for name, _, _ in entries()
               if name not in settings | COMPOSE_ONLY | ALIASES and not TEST_ONLY.match(name)]
    assert not unknown, f'.env.example names that nothing reads: {unknown}'


def test_every_variable_compose_requires_is_present_and_active():
    compose = (ROOT / 'compose.yaml').read_text()
    required = set(re.findall(r'\$\{(KLEOS_[A-Z0-9_]+):\?', compose))
    active = {name for name, _, is_active in entries() if is_active}
    assert required and required <= active, f'required but not active in the example: {sorted(required - active)}'
    yaml.safe_load(compose)  # Compose itself must still parse after renames


def test_no_name_is_listed_twice_and_no_value_is_a_secret():
    names = [name for name, _, _ in entries()]
    assert len(names) == len(set(names))
    for name, value, _ in entries():
        if name.endswith(('_KEY', '_SECRET', '_TOKEN')) and 'MODEL' not in name:
            assert value.strip() == '', f'{name} must be an empty placeholder'
    assert 'change-me' in (ROOT / '.env.example').read_text()  # placeholders, not real credentials


def test_json_examples_parse_and_have_the_expected_shape():
    for name, value, _ in entries():
        if value.startswith("'") and value.endswith("'"):
            decoded = json.loads(value[1:-1])
            assert isinstance(decoded, (list, dict)), name
    tools = json.loads(next(v for n, v, _ in entries() if n == 'KLEOS_NATIVE_TOOLS')[1:-1])
    assert {'capability', 'imageDigest', 'providerVersion', 'qualified', 'qualificationReference'} <= set(tools[0])


def test_the_active_lines_load_into_settings_once_the_secrets_are_filled(monkeypatch):
    for key in list(Settings.model_fields):
        monkeypatch.delenv('KLEOS_' + key.upper(), raising=False)
    for name, value, is_active in entries():
        if is_active and not name.startswith('KLEOS_POSTGRES'):
            monkeypatch.setenv(name, value)
    monkeypatch.setenv('KLEOS_SESSION_SECRET', 's' * 40)
    monkeypatch.setenv('KLEOS_SETUP_TOKEN', 't' * 32)
    loaded = Settings(_env_file=None)
    assert loaded.environment == 'development' and loaded.physical_actions_enabled is False
    assert loaded.database_url.endswith('/kleos_rewrite') and loaded.model_provider == 'meta'
    assert loaded.max_model_cost_micro_usd == 5_000_000


@pytest.mark.parametrize('flag', ['KLEOS_DEVELOPMENT_NO_LOGIN', 'KLEOS_PHYSICAL_ACTIONS_ENABLED',
                                  'KLEOS_COMPANION_PREVIEW_ENABLED'])
def test_risky_switches_default_to_off_in_the_example(flag):
    assert dict((n, v) for n, v, active in entries() if active)[flag] == 'false'
