import httpx
import pytest

from kleos.artifacts.repository_download import RepositoryDownloader, public_addresses, source_descriptor
from kleos.contracts.models import DomainError

COMMIT = 'a'*40
SOURCE = source_descriptor('https://github.com/example/device', COMMIT)


class Stream(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk


async def resolver(host):
    return ['140.82.112.3'] if host == 'api.github.com' else ['140.82.113.4']


async def test_fixed_hosts_pinned_ip_tls_name_and_credential_free_redirect(monkeypatch):
    monkeypatch.setenv('HTTPS_PROXY', 'http://127.0.0.1:1')
    calls = []
    def respond(request):
        calls.append(request)
        assert request.url.scheme == 'https' and request.extensions['sni_hostname'] == request.headers['host']
        assert 'authorization' not in request.headers and 'cookie' not in request.headers
        assert request.headers['accept-encoding'] == 'identity'
        if len(calls) == 1:
            assert request.url.host == '140.82.112.3'
            return httpx.Response(302, headers={'Location': f'https://codeload.github.com/example/device/legacy.zip/{COMMIT}',
                'Set-Cookie': 'secret=value; Domain=.github.com'})
        assert request.url.host == '140.82.113.4'
        return httpx.Response(200, stream=Stream([b'archive']))
    downloader = RepositoryDownloader(resolver=resolver, transport=httpx.MockTransport(respond))
    assert await downloader.download(SOURCE) == b'archive'
    assert len(calls) == 2


@pytest.mark.parametrize('addresses', [['127.0.0.1'], ['169.254.169.254'], ['::1'], ['::ffff:127.0.0.1'],
    ['140.82.112.3', '10.0.0.1'], ['fe80::1%en0'], ['2002:7f00:1::'], ['224.0.0.1'], []])
async def test_forbidden_dns_answers_never_connect(addresses):
    async def lookup(host):
        return addresses
    def respond(request):
        pytest.fail('DNS validation must precede any connection')
    with pytest.raises(DomainError) as error:
        await RepositoryDownloader(resolver=lookup, transport=httpx.MockTransport(respond)).download(SOURCE)
    assert error.value.code == 'repository_network_blocked'


def test_dns_resolution_pin_is_literal_and_mixed_answers_rejected():
    assert public_addresses(['2606:50c0:8000::154', '140.82.112.3']) == ['140.82.112.3', '2606:50c0:8000::154']


@pytest.mark.parametrize('location', ['http://codeload.github.com/example/device/zip/'+COMMIT,
    'https://127.0.0.1/archive.zip', 'https://codeload.github.com.evil.test/archive.zip',
    'https://codeload.github.com/other/device/zip/'+COMMIT,
    'https://codeload.github.com/example/device/zip/main',
    'https://codeload.github.com/example/device/zip/'+COMMIT+'?token=private',
    'https://user:secret@codeload.github.com/example/device/zip/'+COMMIT])
async def test_redirect_cannot_change_authority_repository_or_commit(location):
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(302, headers={'Location': location})
    with pytest.raises(DomainError) as error:
        await RepositoryDownloader(resolver=resolver, transport=httpx.MockTransport(respond)).download(SOURCE)
    assert error.value.code == 'repository_redirect_blocked' and len(calls) == 1


@pytest.mark.parametrize('url,commit', [('https://github.com/example/device', 'main'),
    ('https://github.com:443/example/device', COMMIT), ('https://github.com./example/device', COMMIT),
    ('https://github.com/example/device.git', COMMIT), ('https://github.com/example/%2e%2e', COMMIT),
    ('https://github.com/example/device?access_token=x', COMMIT), ('ssh://github.com/example/device', COMMIT)])
def test_user_url_is_descriptor_not_fetch_target(url, commit):
    with pytest.raises(DomainError):
        source_descriptor(url, commit)


@pytest.mark.parametrize('variant', ['encoding', 'declared_size', 'stream_size', 'incomplete', 'second_redirect', 'timeout'])
async def test_transport_bounds_and_no_implicit_retry(monkeypatch, variant):
    import kleos.artifacts.repository_download as module
    monkeypatch.setattr(module, 'MAX_ARCHIVE_BYTES', 8)
    calls = []
    def respond(request):
        calls.append(request)
        if variant == 'timeout':
            raise httpx.ReadTimeout('contains no trusted diagnostic', request=request)
        if variant == 'second_redirect':
            return httpx.Response(302, headers={'Location': f'https://codeload.github.com/example/device/zip/{COMMIT}'})
        if variant == 'encoding':
            return httpx.Response(200, headers={'Content-Encoding': 'gzip'}, stream=Stream([b'never decompress']))
        headers = {'Content-Length': '9'} if variant == 'declared_size' else {'Content-Length': '8'} if variant == 'incomplete' else {}
        return httpx.Response(200, headers=headers, stream=Stream([b'12345', b'6789'] if variant == 'stream_size' else [b'x']))
    with pytest.raises(DomainError):
        await RepositoryDownloader(resolver=resolver, transport=httpx.MockTransport(respond)).download(SOURCE)
    assert len(calls) == (2 if variant == 'second_redirect' else 1)


async def test_installed_httpcore_preserves_tls_verification_for_ip_connection():
    """Exercise actual installed HTTPX/HTTPCore transport; network backend is inert."""
    import ssl

    import httpcore
    calls = []
    class NetworkStream(httpcore.AsyncNetworkStream):
        async def read(self, max_bytes, timeout=None):
            return b'HTTP/1.1 200 OK\r\nContent-Length: 3\r\n\r\nzip'
        async def write(self, buffer, timeout=None):
            assert b'Host: api.github.com' in buffer or buffer in {b'', b'0\r\n\r\n'}
        async def aclose(self):
            pass
        async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
            assert ssl_context.verify_mode == ssl.CERT_REQUIRED and ssl_context.check_hostname
            calls.append(('tls', server_hostname))
            return self
        def get_extra_info(self, info):
            return None
    class Backend(httpcore.AsyncNetworkBackend):
        async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
            calls.append(('connect', host, port))
            return NetworkStream()
    transport = httpx.AsyncHTTPTransport(retries=0, trust_env=False)
    await transport._pool.aclose()
    transport._pool = httpcore.AsyncConnectionPool(network_backend=Backend())
    assert await RepositoryDownloader(resolver=resolver, transport=transport).download(SOURCE) == b'zip'
    assert calls == [('connect', '140.82.112.3', 443), ('tls', 'api.github.com')]
