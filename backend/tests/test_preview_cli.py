from types import SimpleNamespace

import uvicorn

from kleos import cli


def test_preview_command_requires_explicit_enablement(monkeypatch, capsys):
    monkeypatch.setattr(cli, 'Settings', lambda: SimpleNamespace(companion_preview_enabled=False))
    def forbidden(*args, **kwargs):
        raise AssertionError('Disabled preview must not construct a runtime or start a server')
    monkeypatch.setattr(cli, 'Runtime', forbidden)
    monkeypatch.setattr(uvicorn, 'run', forbidden)
    assert cli.main(['preview-serve']) == 2
    assert 'configuration_required' in capsys.readouterr().err


def test_preview_process_uses_only_preview_app_without_token_access_logs(monkeypatch):
    settings = SimpleNamespace(companion_preview_enabled=True)
    preview_app, api_app, calls = object(), object(), []
    monkeypatch.setattr(cli, 'Settings', lambda: settings)
    monkeypatch.setattr(cli, 'Runtime', lambda value: SimpleNamespace(preview_app=preview_app, app=api_app))
    monkeypatch.setattr(uvicorn, 'run', lambda app, **options: calls.append((app, options)))
    assert cli.main(['preview-serve']) == 0
    assert calls == [(preview_app, {'host': '127.0.0.1', 'port': 8004,
                                  'proxy_headers': False, 'access_log': False, 'limit_concurrency': 16})]
