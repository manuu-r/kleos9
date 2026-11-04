"""Public GitHub commit archives, with fixed hosts and DNS-pinned HTTPS connections."""
import asyncio
import ipaddress
import re
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from kleos.artifacts.import_archive import MAX_ARCHIVE_BYTES
from kleos.contracts.models import DomainError


def invalid(message='Use a public github.com repository and a full commit SHA'):
    return DomainError(message, 'repository_input_invalid', 422)


@dataclass(frozen=True)
class RepositorySource:
    owner: str
    repository: str
    commit: str

    @property
    def url(self):
        return f'https://github.com/{self.owner}/{self.repository}'

    @property
    def archive_url(self):
        return f'https://api.github.com/repos/{self.owner}/{self.repository}/zipball/{self.commit}'


def source_descriptor(repository_url, commit):
    if (not isinstance(repository_url, str) or len(repository_url) > 300
            or not isinstance(commit, str) or not re.fullmatch('[a-f0-9]{40}', commit)):
        raise invalid()
    match = re.fullmatch(r'https://github\.com/([A-Za-z0-9][A-Za-z0-9-]{0,38})/([A-Za-z0-9_.-]{1,100})/?', repository_url)
    if not match or match[2] in {'.', '..'} or match[2].endswith('.git'):
        raise invalid()
    return RepositorySource(match[1], match[2], commit)


async def resolve_public(host):
    rows = await asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    return [row[4][0] for row in rows]


def public_addresses(values):
    if not isinstance(values, (list, tuple)) or not 1 <= len(values) <= 64:
        raise DomainError('Repository hostname could not be safely resolved', 'repository_network_blocked', 502)
    addresses = []
    for value in values:
        try:
            address = ipaddress.ip_address(value)
            if '%' in value or not address.is_global or address.is_multicast or address.is_reserved:
                raise ValueError()
            if isinstance(address, ipaddress.IPv6Address) and (address.ipv4_mapped or address.sixtofour or address.teredo):
                raise ValueError()
        except (ValueError, TypeError):
            raise DomainError('Repository DNS includes a forbidden address', 'repository_network_blocked', 502) from None
        addresses.append(str(address))
    return sorted(set(addresses), key=lambda value: (':' in value, value))


class RepositoryDownloader:
    """No arbitrary URLs, credentials, retries, proxy inheritance or automatic redirects.

    A fresh client per hop connects to a validated literal IP. HTTPCore's documented
    sni_hostname extension preserves certificate verification against the fixed host.
    Resolver and transport injections are only composed by tests, never user payloads.
    """
    def __init__(self, *, resolver=resolve_public, transport=None):
        self.resolver, self.transport = resolver, transport

    async def download(self, source):
        try:
            async with asyncio.timeout(30):
                result = await self._hop(source.archive_url, redirect=True)
                if isinstance(result, bytes):
                    return result
                allowed = {
                    f'https://codeload.github.com/{source.owner}/{source.repository}/legacy.zip/{source.commit}',
                    f'https://codeload.github.com/{source.owner}/{source.repository}/zip/{source.commit}',
                }
                if result not in allowed:
                    raise DomainError('Repository redirect is outside the pinned commit', 'repository_redirect_blocked', 502)
                return await self._hop(result, redirect=False)
        except (httpx.HTTPError, TimeoutError, OSError):
            raise DomainError('Repository download outcome is unknown; this request will not automatically retry',
                              'repository_download_uncertain', 502) from None

    async def _hop(self, url, *, redirect):
        parsed = urlsplit(url)
        if parsed.hostname not in {'api.github.com', 'codeload.github.com'}:
            raise DomainError('Repository host is not supported', 'repository_network_blocked', 502)
        addresses = public_addresses(await self.resolver(parsed.hostname))
        address = addresses[0]
        authority = '['+address+']' if ':' in address else address
        pinned = 'https://'+authority+parsed.path
        async with httpx.AsyncClient(transport=self.transport, trust_env=False, follow_redirects=False,
                timeout=httpx.Timeout(10, connect=5), limits=httpx.Limits(max_connections=1, max_keepalive_connections=0)) as client:
            async with client.stream('GET', pinned, headers={'Host': parsed.hostname,
                    'Accept': 'application/vnd.github+json' if redirect else 'application/zip',
                    'Accept-Encoding': 'identity', 'User-Agent': 'Kleos-repository-import/1'},
                    extensions={'sni_hostname': parsed.hostname}) as response:
                if redirect and response.status_code == 302:
                    location = response.headers.get('Location', '')
                    if len(location) > 500:
                        raise DomainError('Repository redirect exceeds its bound', 'repository_redirect_blocked', 502)
                    return location
                if response.status_code != 200:
                    raise DomainError('Public repository archive is unavailable', 'repository_download_failed', 502)
                if response.headers.get('Content-Encoding', 'identity').strip().lower() != 'identity':
                    raise DomainError('Encoded repository responses are unsupported', 'repository_response_invalid', 502)
                length = response.headers.get('Content-Length')
                if length is not None and (not length.isascii() or not length.isdecimal()
                        or len(length) > 12 or int(length) > MAX_ARCHIVE_BYTES):
                    raise DomainError('Repository archive exceeds its download bound', 'archive_limit', 422)
                raw = bytearray()
                async for chunk in response.aiter_raw():
                    raw.extend(chunk)
                    if len(raw) > MAX_ARCHIVE_BYTES:
                        raise DomainError('Repository archive exceeds its download bound', 'archive_limit', 422)
                if length is not None and len(raw) != int(length):
                    raise DomainError('Repository response was incomplete', 'repository_download_uncertain', 502)
                return bytes(raw)
