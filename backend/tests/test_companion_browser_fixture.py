"""Fixture setup/observability only; these tests make no browser isolation claim."""
import io
import zipfile

import httpx
from companion_browser_fixture import PREVIEW, WORKBENCH, applications, trusted_bundle


def test_negative_scripts_target_only_loopback_and_preserve_relative_assets():
    with zipfile.ZipFile(io.BytesIO(trusted_bundle(adversarial=True))) as archive:
        script = archive.read('main.dart.js').decode()
        assert 'http://127.0.0.1:18791/attempt/' in script
        assert 'ws://127.0.0.1:18791/attempt/websocket' in script
        assert 'https://' not in script
        assert "fetch('assets/data.json'" in script
        assert 'worker.js' in archive.namelist()


async def test_local_trap_observes_attempts_without_retaining_sensitive_input(tmp_path):
    store, workbench, preview = await applications(tmp_path, adversarial=True)
    try:
        async with (httpx.AsyncClient(transport=httpx.ASGITransport(app=workbench), base_url=WORKBENCH) as ui,
                httpx.AsyncClient(transport=httpx.ASGITransport(app=workbench.state.trap_app), base_url='http://127.0.0.1:18791') as trap,
                httpx.AsyncClient(transport=httpx.ASGITransport(app=preview), base_url=PREVIEW) as files):
            assert (await ui.get('/observations')).json()['received'] == []
            page = await ui.get('/')
            url = page.text.split('src="', 1)[1].split('"', 1)[0]
            script = await files.get(url + 'files/main.dart.js')
            assert script.status_code == 200 and 'Attempt local external navigation' in (await files.get(url + 'files/index.html')).text
            assert "connect-src " + url + 'files/' in script.headers['Content-Security-Policy']
            await trap.post('/attempt/fixture?secret=not-recorded', content=b'private-not-recorded', headers={'Cookie': 'private=fixture'})
            observed = (await ui.get('/observations')).json()
            assert observed['received'] == [{'kind': 'fixture', 'method': 'POST'}]
            assert 'private' not in str(observed) and 'secret' not in str(observed)
    finally:
        store.engine.dispose()
