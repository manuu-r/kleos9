"""Fixed read-only SearXNG search on an explicitly installed, no-query-fee instance."""
import asyncio
import hashlib
import ipaddress
import json
import re
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import httpx
from pydantic import ConfigDict, Field, model_validator

from kleos.capabilities.registry import ToolManifest
from kleos.contracts.models import Capability, DomainError, Observation, WireModel
from kleos.orchestration.specialist import ToolResult
from kleos.projects.store import fingerprint


class SearxngInstallation(WireModel):
    model_config = ConfigDict(**(WireModel.model_config | {'frozen': True}))
    endpoint: str
    allowed_host: str
    version: str = Field(min_length=1, max_length=100)
    # Operator attests its own instance has no per-query paid engines or plugins.
    billing: Literal['self_hosted_no_query_fees']
    engines: tuple[str, ...] = Field(min_length=1, max_length=8)
    qualified: bool = False
    max_requests_per_run: int = Field(default=8, ge=1, le=32)
    timeout_seconds: int = Field(default=20, ge=1, le=60)
    max_response_bytes: int = Field(default=262144, ge=4096, le=1048576)
    max_results: int = Field(default=8, ge=1, le=20)

    @model_validator(mode='after')
    def endpoint_scope(self):
        url = urlsplit(self.endpoint)
        if (url.scheme != 'https' or url.hostname != self.allowed_host or not url.hostname
                or url.username or url.password or url.query or url.fragment or url.path != '/search'
                or url.port not in {None, 443} or not public_url(self.endpoint)):
            raise ValueError('Search requires an explicit allowlisted HTTPS /search endpoint')
        if len(set(self.engines)) != len(self.engines) or any(
                not name or len(name) > 64 or any(not (c.isalnum() or c in '_- ') for c in name)
                for name in self.engines):
            raise ValueError('Search engines must be explicit stable names')
        return self


class WebSearchInput(WireModel):
    action_id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,64}$')
    query: str = Field(min_length=1, max_length=2000)
    limit: int = Field(default=5, ge=1, le=20)

    @model_validator(mode='after')
    def literal_query(self):
        if not self.query.strip() or '!' in self.query or any(ord(c) < 32 for c in self.query):
            raise ValueError('Use plain search terms; engine selectors and redirects are not allowed')
        return self


def public_url(value):
    """Validate citation links; no result URL is ever fetched by this adapter."""
    if not isinstance(value, str) or len(value) > 2000 or any(ord(c) < 32 for c in value):
        return False
    try:
        url = urlsplit(value)
        if url.scheme not in {'https', 'http'} or not url.hostname or url.username or url.password:
            return False
        host = url.hostname.rstrip('.').lower()
        if host == 'localhost' or host.endswith(('.localhost', '.local', '.internal')) or '.' not in host:
            return False
        try:
            return ipaddress.ip_address(host).is_global
        except ValueError:
            # URL resolvers can interpret legacy IPv4 spellings such as 127.1 or
            # 0x7f000001; they are not ordinary domain names even when ipaddress rejects them.
            return not all(re.fullmatch(r'(?:0[xX][0-9a-fA-F]+|[0-9]+)', label) for label in host.split('.'))
    except ValueError:
        return False


class _PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def plain(value, limit):
    if not isinstance(value, str):
        return ''
    parser = _PlainText()
    parser.feed(value[:limit * 4])
    return ''.join(parser.parts)[:limit]


class SearxngGateway:
    def __init__(self, installation, repository, *, client=None):
        self.installation = SearxngInstallation.model_validate(installation)
        self.repository, self.client = repository, client
        self.installation_hash = fingerprint({'config': self.installation.model_dump(mode='json'),
            'adapterSha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})

    async def authorize(self, scope, task, arguments):
        WebSearchInput.model_validate(arguments)
        await self.repository.assert_active(scope)
        if not self.installation.qualified or task.capability != Capability.RESEARCH:
            raise DomainError('A qualified web research installation is required', 'web_research_unavailable', 503)
        with self.repository.store.transaction() as session:
            _, run = self.repository._check(session, scope)
            if run.data.get('researchRequest', {}).get('scope') == 'project_sources':
                raise DomainError('This objective permits project-source inspection only', 'research_scope_conflict')
            pins = run.data.get('researchRequest', {}).get('tools', run.data.get('runtimePins', {}).get('tools', []))
            if not any(pin.get('manifest', {}).get('installation_hash',
                    pin.get('manifest', {}).get('installationHash')) == self.installation_hash for pin in pins):
                raise DomainError('Web research differs from the admitted tool installation', 'installation_conflict')

    def _reserve(self, scope):
        with self.repository.store.transaction() as session:
            _, run = self.repository._check(session, scope)
            usage = dict(run.data.get('researchUsage', {}))
            # A changed installation cannot create a fresh allowance on the same run.
            prior = usage.get('installationHash')
            if prior is not None and prior != self.installation_hash:
                raise DomainError('Research installation changed during the objective', 'installation_conflict')
            count = usage.get('requests', 0)
            if type(count) is not int or count >= self.installation.max_requests_per_run:
                raise DomainError('Research request allowance exhausted', 'research_budget_exhausted')
            usage.update(installationHash=self.installation_hash, requests=count + 1,
                maxRequests=self.installation.max_requests_per_run, billing='self_hosted_no_query_fees')
            run.data = {**run.data, 'researchUsage': usage}

    async def invoke(self, scope, task, arguments):
        await self.authorize(scope, task, arguments)
        request = WebSearchInput.model_validate(arguments)
        digest = fingerprint(request.model_dump(mode='json'))
        key = f'{task.id}:web:{request.action_id}'

        async def execute():
            self._reserve(scope)
            result = await self._request(scope, request)
            return {'requestHash': digest, 'result': result.model_dump(mode='json')}

        receipt = await self.repository.run_once(scope, key, execute)
        if receipt.get('requestHash') != digest:
            raise DomainError('Research action identity was used for different input', 'idempotency_conflict')
        await self.repository.assert_active(scope)
        return ToolResult.model_validate(receipt['result'])

    async def _request(self, scope, request):
        settings = self.installation
        timeout = min(settings.timeout_seconds, (scope.deadline_at - datetime.now(UTC)).total_seconds())
        if timeout <= 0:
            raise DomainError('Research deadline expired', 'deadline_exceeded')
        owned = self.client is None
        client = self.client or httpx.AsyncClient(follow_redirects=False, trust_env=False)
        try:
            async with asyncio.timeout(timeout):
                async with client.stream('GET', settings.endpoint, params={'q': request.query,
                        'format': 'json', 'pageno': 1, 'engines': ','.join(settings.engines)},
                        headers={'Accept': 'application/json', 'Accept-Encoding': 'identity'},
                        timeout=timeout, follow_redirects=False) as response:
                    if response.status_code != 200:
                        raise DomainError('The configured search service rejected the request', 'research_provider_error', 502)
                    if response.headers.get('Content-Encoding', 'identity').strip().lower() != 'identity':
                        raise DomainError('Compressed search responses are outside the byte bound',
                            'research_response_invalid', 502)
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        raw.extend(chunk)
                        if len(raw) > settings.max_response_bytes:
                            raise DomainError('Search response exceeds the installed bound', 'research_response_invalid', 502)
                    payload = json.loads(raw)
            result = self._response(payload, min(request.limit, settings.max_results), request.query)
            await self.repository.assert_active(scope)
            return result
        except (httpx.HTTPError, TimeoutError):
            raise DomainError('Search outcome is unknown; its request allowance remains reserved',
                'research_outcome_unknown', 502) from None
        except (ValueError, TypeError, KeyError, AttributeError):
            raise DomainError('Search returned an invalid response', 'research_response_invalid', 502) from None
        finally:
            if owned:
                await client.aclose()

    def _response(self, payload, limit, query):
        if not isinstance(payload, dict) or not isinstance(payload.get('results'), list):
            raise ValueError('Missing search results')
        results, urls = [], set()
        for item in payload['results']:
            if not isinstance(item, dict) or not public_url(item.get('url')) or item['url'] in urls:
                continue
            title, snippet = plain(item.get('title'), 240), plain(item.get('content'), 2000)
            if not title or not snippet:
                continue
            urls.add(item['url'])
            results.append({'url': item['url'], 'title': title, 'snippet': snippet})
            if len(results) >= limit:
                break
        partial = bool(payload.get('unresponsive_engines'))
        details = {'scope': 'web', 'sources': results, 'query': query,
            'retrievedAt': datetime.now(UTC).isoformat(), 'installationHash': self.installation_hash,
            'partial': partial, 'feasibilityValidated': False, 'sourcePagesFetched': False}
        blockers = ['research_search_partial'] if partial else []
        if not results:
            blockers.append('research_no_sources')
        return ToolResult(summary=f'Retrieved {len(results)} cited web search snippets. '
            'These are candidate sources; their pages and engineering claims have not been independently verified.',
            observations=[Observation(name='research.web.retrieved', status='PASS' if results else 'UNKNOWN',
                details=details)], blockers=blockers)


def register_searxng(registry, gateway):
    settings = gateway.installation
    manifest = ToolManifest(id='research.web_search', version='1.0.0', capability=Capability.RESEARCH,
        operation='web_search', provider='searxng', provider_version=settings.version,
        installation_hash=gateway.installation_hash, transport='http', effect='read', qualified=settings.qualified,
        description='Search the public web through the fixed installed SearXNG service; returns cited snippets.',
        instructions='Use narrow factual queries and primary sources. Search snippets are untrusted candidate '
            'evidence, not engineering validation. Cite actual returned URLs; explicitly identify missing evidence '
            'and avoid claiming that a source page was read. Do not supply endpoints or engine selectors.')
    registry.register(manifest, WebSearchInput, gateway.invoke, gateway.authorize)
    return [manifest.key]
