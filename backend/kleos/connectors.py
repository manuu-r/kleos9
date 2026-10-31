"""Qualified connector boundary. Physical side effects remain disabled in this MVP."""

import asyncio
import hashlib
import hmac
import http.client
import ipaddress
import json
import secrets
import socket
import ssl
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
from sqlalchemy import select

from .contracts import ConnectorManifest, DomainError
from .db import Connector, ExternalIntent, Inbox, Outbox, Source, now, uid
from .domain import emit, get_project
from .telemetry import redact, tracer

MCP_VERSION = "2025-11-25"
READ_ONLY_CAPABILITIES = {
    "parts.search", "research.fetch", "printer.status", "printer.profile", "tools.describe", "electronics.simulate",
}
COMMIT_CAPABILITIES = {
    "orders.commit", "human.post", "payments.settle", "printer.dispatch", "shipping.label",
    "fab.start", "kit.dispatch",
}


@dataclass(frozen=True)
class FetchResponse:
    url: str
    status: int
    headers: dict
    body: bytes


def public_destination(url: str, resolver=socket.getaddrinfo) -> tuple[str, str, int, str]:
    """Resolve once, reject all non-public answers, and return an address to pin."""
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").encode("idna").decode("ascii").lower().rstrip(".")
        port = parsed.port or 443
    except (ValueError, UnicodeError) as exc:
        raise DomainError("Invalid connector URL", 422) from exc
    if parsed.scheme != "https" or not host or port != 443 or parsed.username or parsed.password:
        raise DomainError("Remote connectors require a public HTTPS URL on port 443", 422, "unsafe_destination")
    if parsed.fragment or any(c in url for c in "\r\n\t") or len(url) > 4000:
        raise DomainError("Invalid connector URL", 422, "unsafe_destination")
    try:
        addresses = {item[4][0] for item in resolver(host, port, type=socket.SOCK_STREAM)}
    except socket.gaierror as exc:
        raise DomainError("Connector hostname could not be resolved", 502, "dns_failed") from exc
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise DomainError("Private, loopback, link-local and reserved destinations are blocked", 422,
                          "unsafe_destination")
    return host, sorted(addresses)[0], port, urlunsplit(("", "", parsed.path or "/", parsed.query, ""))


class _PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, address, port, timeout):
        super().__init__(host, port, timeout=timeout, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        # Connecting to the validated numeric address prevents DNS-rebinding between check and use.
        raw = socket.create_connection((self.address, self.port), self.timeout)
        self.sock = self._context.wrap_socket(raw, server_hostname=self.host)


class RestrictedFetcher:
    """Public HTTPS only, DNS-pinned, bounded body, no proxies, cookies, credentials or redirects on POST."""
    def __init__(self, resolver=socket.getaddrinfo, connection_factory=_PinnedHTTPS,
                 max_bytes=2_000_000, timeout=15, allow_redirects=True):
        self.resolver, self.connection_factory = resolver, connection_factory
        self.max_bytes, self.timeout = max_bytes, timeout
        self.allow_redirects = allow_redirects

    async def request(self, url, method="GET", body=None, headers=None):
        with tracer.start_as_current_span("connector.public_fetch", attributes={"http.request.method": method}) as span:
            result = await asyncio.to_thread(self._request, url, method, body, headers or {})
            span.set_attribute("http.response.status_code", result.status)
            span.set_attribute("http.response.body.size", len(result.body))
            return result

    def _request(self, url, method, body, headers):
        if method not in {"GET", "POST"}:
            raise DomainError("Only GET and qualified POST operations are supported", 422)
        safe_headers = {"Accept-Encoding": "identity", "User-Agent": "Kleos9-Research/0.1", **headers}
        deadline = time.monotonic() + self.timeout
        for hop in range(4):
            host, address, port, target = public_destination(url, self.resolver)
            connection = self.connection_factory(host, address, port, self.timeout)
            watchdog = None
            try:
                connection.request(method, target, body=body, headers={**safe_headers, "Host": host})
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise DomainError("Research fetch deadline exceeded", 504)
                # A peer slowly dribbling headers/body cannot reset the wall-clock deadline indefinitely.
                active_socket = getattr(connection, "sock", None)
                if active_socket:
                    def interrupt(active_socket=active_socket):
                        try:
                            active_socket.shutdown(socket.SHUT_RDWR)
                        except OSError:
                            pass
                    watchdog = threading.Timer(remaining, interrupt)
                    watchdog.daemon = True
                    watchdog.start()
                response = connection.getresponse()
                response_headers = {k.lower(): v for k, v in response.getheaders()}
                if response.status in {301, 302, 303, 307, 308}:
                    if not self.allow_redirects or method != "GET" or hop == 3 or not response_headers.get("location"):
                        raise DomainError("Connector redirect is unsupported", 502, "redirect_blocked")
                    url = urljoin(url, response_headers["location"])
                    continue
                if response_headers.get("content-encoding", "identity").lower() != "identity":
                    raise DomainError("Compressed research responses are unsupported", 415)
                length = response_headers.get("content-length")
                if length and int(length) > self.max_bytes:
                    raise DomainError("Research document is too large", 413)
                content = response.read(self.max_bytes + 1)
                if time.monotonic() > deadline:
                    raise DomainError("Research fetch deadline exceeded", 504)
                if len(content) > self.max_bytes:
                    raise DomainError("Research document is too large", 413)
                return FetchResponse(url, response.status, response_headers, content)
            except (OSError, http.client.HTTPException, ValueError) as exc:
                raise DomainError("Remote connector could not be reached", 502, "connector_unavailable") from exc
            finally:
                if watchdog:
                    watchdog.cancel()
                connection.close()
        raise DomainError("Too many redirects", 502)


class _ReadableHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hidden = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript", "template"}:
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript", "template"}:
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden and data.strip():
            self.parts.append(data.strip())


class ResearchService:
    def __init__(self, db, fetcher=None):
        self.db, self.fetcher = db, fetcher or RestrictedFetcher()

    async def collect(self, principal, project_id, url):
        principal.require_write()
        with self.db.transaction() as session:
            project = get_project(session, project_id, principal, write=True)
            revision = project.engineering_revision
        response = await self.fetcher.request(url)
        if not 200 <= response.status < 300:
            raise DomainError(f"Source returned HTTP {response.status}", 502, "source_unavailable")
        media = response.headers.get("content-type", "").split(";")[0].lower()
        if media not in {"text/plain", "text/html", "application/json", "application/pdf"}:
            raise DomainError("Unsupported research document format", 415)
        if media == "application/pdf":
            try:
                from io import BytesIO

                from pypdf import PdfReader
                reader = PdfReader(BytesIO(response.body))
                if len(reader.pages) > 100:
                    raise DomainError("PDF exceeds the 100-page research limit", 413)
                content = "\n".join(page.extract_text() or "" for page in reader.pages)
            except ImportError as exc:
                raise DomainError("PDF extraction worker is not installed", 503, "tool_unconfigured") from exc
            except DomainError:
                raise
            except Exception as exc:
                raise DomainError("PDF could not be safely parsed", 422, "document_invalid") from exc
        else:
            content = response.body.decode("utf-8", errors="replace")
            if media == "text/html":
                parser = _ReadableHTML()
                parser.feed(content)
                content = "\n".join(parser.parts)
        if not content.strip():
            raise DomainError("Document contains no extractable text", 422)
        if len(content) > 2_000_000:
            raise DomainError("Extracted research text exceeds the 2 MB limit", 413)
        # Full source provenance is preserved; optional context selection happens later.
        content = redact(content)
        sha = hashlib.sha256(response.body).hexdigest()
        with self.db.transaction() as session:
            project = get_project(session, project_id, principal, write=True)
            if revision != project.engineering_revision:
                raise DomainError("Project changed while collecting the source", 409, "stale_revision")
            existing = session.scalar(select(Source).where(
                Source.project_id == project_id, Source.sha256 == sha, Source.revoked.is_(False),
                Source.revision == revision))
            if existing:
                return self.view(existing)
            source = Source(id=uid(), tenant_id=principal.tenant_id, project_id=project_id,
                            revision=revision, uri=redact(response.url), sha256=sha, text=content, authority="research")
            session.add(source)
            session.add(Outbox(key=f"index:{source.id}", kind="index_source", payload={"sourceId": source.id}))
            emit(session, project, "research.sourceCollected", {
                "sourceId": source.id, "uri": response.url, "sha256": sha, "authority": "research"})
            session.flush()
            return self.view(source)

    @staticmethod
    def view(source):
        return {"id": source.id, "uri": source.uri, "sha256": source.sha256, "authority": source.authority,
                "revision": source.revision, "observedAt": source.created_at.isoformat(), "characters": len(source.text)}


class DigiKeyCatalog:
    """DigiKey v4 Product Information, client-credentials OAuth; never orders."""
    def __init__(self, settings, client=None):
        self.settings = settings
        self.client = client
        self.token, self.token_expires = None, 0

    async def search(self, query, limit=10):
        if not isinstance(query, str) or not query.strip() or len(query) > 200 or not 1 <= limit <= 50:
            raise DomainError("Provide a short part query and a limit between 1 and 50", 422)
        if not self.settings.digikey_client_id or not self.settings.digikey_client_secret:
            raise DomainError("DigiKey developer credentials are not configured", 503, "connector_unconfigured")
        if self.client:
            return await self._search(self.client, query, limit)
        async with httpx.AsyncClient(timeout=20, follow_redirects=False, trust_env=False) as client:
            return await self._search(client, query, limit)

    async def _search(self, client, query, limit):
        base = "https://sandbox-api.digikey.com" if self.settings.digikey_sandbox else "https://api.digikey.com"
        try:
            if not self.token or time.monotonic() >= self.token_expires:
                response = await client.post(f"{base}/v1/oauth2/token", data={
                    "grant_type": "client_credentials", "client_id": self.settings.digikey_client_id,
                    "client_secret": self.settings.digikey_client_secret.get_secret_value(),
                })
                response.raise_for_status()
                auth = response.json()
                self.token = auth["access_token"]
                self.token_expires = time.monotonic() + max(0, int(auth["expires_in"]) - 30)
            response = await client.post(f"{base}/products/v4/search/keyword", json={
                "Keywords": query, "Limit": limit, "Offset": 0,
            }, headers={"Authorization": f"Bearer {self.token}",
                        "X-DIGIKEY-Client-Id": self.settings.digikey_client_id,
                        "X-DIGIKEY-Locale-Site": "US", "X-DIGIKEY-Locale-Language": "en",
                        "X-DIGIKEY-Locale-Currency": "USD"})
            response.raise_for_status()
            data = response.json()
            return {"provider": "digikey", "mode": "sandbox" if self.settings.digikey_sandbox else "read_only",
                    "observedAt": now().isoformat(), "products": redact(data.get("Products", [])),
                    "total": data.get("ProductsCount", len(data.get("Products", []))),
                    "limitations": ["Availability and price are observations, not reservations or quotes."]}
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            self.token = None
            raise DomainError("DigiKey lookup failed; check provider configuration and quota", 502,
                              "connector_unavailable") from exc


def conformance(manifest: ConnectorManifest):
    failures = []
    if not manifest.capabilities or len(set(manifest.capabilities)) != len(manifest.capabilities):
        failures.append("Capabilities must be nonempty and unique")
    if any(c in {"release.accept", "quality.approve", "policy.change"} for c in manifest.capabilities):
        failures.append("Connectors cannot own release, quality or policy authority")
    if manifest.transport in {"rest", "mcp"} and not manifest.endpoint:
        failures.append("A remote transport requires an endpoint")
    if manifest.mode == "read_only" and any(c not in READ_ONLY_CAPABILITIES for c in manifest.capabilities):
        failures.append("Read-only connectors may declare only read capabilities")
    if manifest.mode == "live":
        failures.append("Live external effects are disabled in the MVP")
    return {"schemaVersion": "1", "passed": not failures, "failures": failures,
            "qualification": "schema_verified" if not failures else "listed",
            "operationallyQualified": False}


class ConnectorService:
    def __init__(self, db, settings):
        self.db, self.settings = db, settings

    def list(self, principal):
        with self.db.transaction() as session:
            connectors = session.scalars(select(Connector).where(Connector.tenant_id == principal.tenant_id)).all()
            return {"connectors": [self.view(c, self.settings.bridge_stale_seconds) for c in connectors], "demoMode": True}

    def register(self, principal, manifest):
        principal.require_admin()
        manifest = ConnectorManifest.model_validate(manifest)
        result = conformance(manifest)
        if not result["passed"]:
            raise DomainError("; ".join(result["failures"]), 422, "conformance_failed")
        if manifest.endpoint:
            # Validation never connects to a private endpoint. Printer LAN URLs stay on the local bridge.
            public_destination(manifest.endpoint)
        with self.db.transaction() as session:
            connector = Connector(id=uid(), tenant_id=principal.tenant_id, name=manifest.name,
                                  manifest=manifest.model_dump(mode="json"), level="schema_verified", enabled=False)
            session.add(connector)
            return self.view(connector)

    @staticmethod
    def view(connector, stale_seconds=120):
        manifest = {k: v for k, v in connector.manifest.items() if k not in {"bridgeTokenHash", "telemetry"}}
        telemetry = connector.manifest.get("telemetry")
        if telemetry:
            try:
                received = datetime.fromisoformat(telemetry["receivedAt"])
                observed = datetime.fromisoformat(telemetry.get("observedAt", telemetry["receivedAt"]))
                stale = max((now() - received.astimezone(UTC)).total_seconds(),
                            (now() - observed.astimezone(UTC)).total_seconds()) > stale_seconds
            except (KeyError, ValueError, TypeError):
                stale = True
            telemetry = {**telemetry, "stale": stale, "connected": telemetry.get("connected", False) and not stale}
        return {"id": connector.id, "name": connector.name, "manifest": redact(manifest),
                "level": connector.level, "enabled": connector.enabled,
                "telemetry": telemetry}

    def webhook(self, connector_id, body, timestamp, signature, event_id):
        secret = self.settings.webhook_secrets.get(connector_id)
        if not secret:
            raise DomainError("Webhook is not configured", 404)
        if len(body) > 1_000_000 or not event_id or len(event_id) > 200:
            raise DomainError("Invalid webhook envelope", 422)
        try:
            observed = int(timestamp)
        except (ValueError, TypeError) as exc:
            raise DomainError("Invalid webhook timestamp", 401) from exc
        if abs(time.time() - observed) > 300:
            raise DomainError("Webhook timestamp is expired", 401)
        signed = f"{timestamp}.{event_id}.".encode() + body
        expected = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature.removeprefix("sha256=")):
            raise DomainError("Invalid webhook signature", 401)
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeDecodeError) as exc:
            raise DomainError("Invalid webhook JSON", 422) from exc
        if not isinstance(payload, dict):
            raise DomainError("Webhook JSON must be an object", 422)
        captured = {**redact(payload), "_webhookBodySha256": hashlib.sha256(body).hexdigest()}
        with self.db.transaction() as session:
            connector = session.scalar(select(Connector).where(Connector.id == connector_id).with_for_update())
            if not connector or not connector.enabled:
                raise DomainError("Connector is unavailable", 404)
            existing = session.scalar(select(Inbox).where(Inbox.connector_id == connector_id,
                                                          Inbox.external_event_id == event_id))
            if existing:
                if existing.payload != captured:
                    raise DomainError("Webhook event ID was reused with a different payload", 409)
                return {"inboxId": existing.id, "duplicate": True}
            project_id = payload.get("projectId")
            if not isinstance(project_id, str):
                raise DomainError("Webhook requires a project identifier", 422)
            project = get_project(session, project_id)
            if project.tenant_id != connector.tenant_id:
                raise DomainError("Webhook cannot access this project", 403)
            inbox = Inbox(id=uid(), connector_id=connector_id, external_event_id=event_id, payload=captured)
            session.add(inbox)
            session.add(Outbox(key=f"webhook:{inbox.id}", kind="webhook", payload={"inboxId": inbox.id}))
            return {"inboxId": inbox.id, "duplicate": False}

    def process_webhook(self, inbox_id):
        with self.db.transaction() as session:
            inbox = session.scalar(select(Inbox).where(Inbox.id == inbox_id).with_for_update())
            if not inbox or inbox.processed_at:
                return
            connector = session.get(Connector, inbox.connector_id)
            if not connector or not connector.enabled:
                raise DomainError("Connector was revoked before processing", 409)
            project = get_project(session, inbox.payload.get("projectId", ""))
            if project.tenant_id != connector.tenant_id:
                raise DomainError("Webhook cannot access this project", 403)
            # Receipt is an untrusted observation; never a release or physical PASS operation.
            emit(session, project, "connector.observation", {
                "connectorId": connector.id, "inboxId": inbox.id, "observation": inbox.payload,
                "authority": "unverified_provider_assertion",
            })
            inbox.processed_at = now()

    def issue_bridge_token(self, principal, connector_id):
        principal.require_admin()
        with self.db.transaction() as session:
            connector = session.scalar(select(Connector).where(
                Connector.id == connector_id, Connector.tenant_id == principal.tenant_id).with_for_update())
            if not connector or connector.manifest.get("transport") != "bridge":
                raise DomainError("Printer bridge connector not found", 404)
            token = secrets.token_urlsafe(32)
            connector.manifest = {**connector.manifest, "bridgeTokenHash": hashlib.sha256(token.encode()).hexdigest()}
            connector.enabled = True
            return {"connectorId": connector.id, "bridgeToken": token, "readOnly": True}

    def bridge_telemetry(self, connector_id, token, payload):
        if not isinstance(payload, dict) or payload.get("kind") not in {"octoprint", "moonraker", "bambu"}:
            raise DomainError("Invalid printer telemetry", 422)
        allowed = {"kind", "state", "profile", "observedAt", "connected"}
        if set(payload) - allowed or len(json.dumps(payload)) > 50_000:
            raise DomainError("Invalid printer telemetry fields", 422)
        if not isinstance(payload.get("connected"), bool) or not isinstance(payload.get("state"), str) or (
                len(payload["state"]) > 500 or not isinstance(payload.get("profile"), dict)):
            raise DomainError("Invalid printer status values", 422)
        try:
            observed = datetime.fromisoformat(payload["observedAt"])
            if observed.tzinfo is None or abs((now() - observed).total_seconds()) > 300:
                raise ValueError("Stale telemetry")
        except (KeyError, ValueError, TypeError) as exc:
            raise DomainError("Printer telemetry timestamp must be current and timezone aware", 422) from exc
        with self.db.transaction() as session:
            connector = session.scalar(select(Connector).where(Connector.id == connector_id).with_for_update())
            expected = connector.manifest.get("bridgeTokenHash", "") if connector else ""
            if not connector or not connector.enabled or not expected or not hmac.compare_digest(
                    expected, hashlib.sha256(token.encode()).hexdigest()):
                raise DomainError("Printer bridge credentials are invalid", 401)
            connector.manifest = {**connector.manifest, "telemetry": {
                **redact(payload), "receivedAt": now().isoformat(), "evidenceKind": "telemetry",
                "physicalAcceptance": False,
            }}
            return {"accepted": True, "readOnly": True}

    def portal_tasks(self, principal, project_id):
        """An organization-scoped portal for the MVP's explicitly simulated physical assignments."""
        with self.db.transaction() as session:
            project = get_project(session, project_id, principal)
            intents = session.scalars(select(ExternalIntent).where(
                ExternalIntent.project_id == project.id, ExternalIntent.tenant_id == principal.tenant_id,
                ExternalIntent.revision == project.engineering_revision, ExternalIntent.mode == "simulated",
            ).order_by(ExternalIntent.created_at)).all()
            return {"projectId": project.id, "engineeringRevision": project.engineering_revision,
                    "mode": "simulated", "tasks": [{
                        "id": i.id, "operation": i.operation, "status": i.receipt.get("portalStatus", "awaiting_review"),
                        "evidenceKind": "synthetic", "receipt": redact(i.receipt),
                    } for i in intents]}

    def portal_observation(self, principal, intent_id, status, note, expected_revision):
        principal.require_write()
        if status not in {"accepted", "in_progress", "completed", "rejected"} or not isinstance(note, str) or (
                not note.strip() or len(note) > 4000):
            raise DomainError("Provide a portal status and a short observation", 422)
        with self.db.transaction() as session:
            intent = session.scalar(select(ExternalIntent).where(
                ExternalIntent.id == intent_id, ExternalIntent.tenant_id == principal.tenant_id,
                ExternalIntent.mode == "simulated").with_for_update())
            if not intent:
                raise DomainError("Simulated provider task not found", 404)
            project = get_project(session, intent.project_id, principal, write=True)
            if project.engineering_revision != expected_revision or intent.revision != expected_revision:
                raise DomainError("Provider task belongs to an outdated engineering revision", 409, "stale_revision")
            observation = {"portalStatus": status, "note": redact(note), "actorId": principal.subject,
                           "evidenceKind": "synthetic", "realExternalEffect": False, "physicalAcceptance": False}
            # Idempotent repeated observations have no additional event or commitment.
            if all(intent.receipt.get(key) == value for key, value in observation.items()):
                return {"id": intent.id, "receipt": intent.receipt}
            intent.receipt = {**intent.receipt, **observation}
            emit(session, project, "portal.simulatedObservation", {"intentId": intent.id, **observation})
            return {"id": intent.id, "receipt": intent.receipt}


class RemoteConnector:
    """Kleos REST v1 and bounded MCP tools, only after independent operational qualification."""
    def __init__(self, manifest, level, enabled, fetcher=None):
        self.manifest = ConnectorManifest.model_validate(manifest)
        self.level, self.enabled = level, enabled
        self.fetcher = fetcher or RestrictedFetcher()

    async def invoke(self, capability, payload, operation_id):
        if not self.enabled or self.level not in {"qualified", "preferred"}:
            raise DomainError("Connector has not been operationally qualified", 409, "connector_unqualified")
        if capability not in self.manifest.capabilities:
            raise DomainError("Connector does not grant this capability", 403)
        if capability not in READ_ONLY_CAPABILITIES:
            raise DomainError("Remote writes and physical actions are disabled in the MVP", 403, "demo_gate")
        if self.manifest.transport == "rest":
            # This path belongs to the documented Kleos adapter contract, not arbitrary vendor APIs.
            endpoint = self.manifest.endpoint.rstrip("/") + "/v1/invoke"
            result = await self.fetcher.request(endpoint, "POST", json.dumps({
                "schemaVersion": "1", "operationId": operation_id, "capability": capability,
                "payload": payload,
            }).encode(), {"Content-Type": "application/json", "Accept": "application/json"})
            return redact(self._json(result))
        if self.manifest.transport == "mcp":
            return await self._mcp(capability, payload)
        raise DomainError("This transport uses its dedicated adapter", 422)

    @staticmethod
    def _json(response):
        if not 200 <= response.status < 300:
            raise DomainError(f"Connector returned HTTP {response.status}", 502)
        try:
            return json.loads(response.body)
        except ValueError as exc:
            raise DomainError("Connector returned invalid JSON", 502) from exc

    async def _mcp(self, capability, payload):
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        async def rpc(method, params, request_id):
            message = {"jsonrpc": "2.0", "method": method, "params": params}
            if request_id is not None:
                message["id"] = request_id
            response = await self.fetcher.request(
                self.manifest.endpoint, "POST", json.dumps(message).encode(), headers)
            if response.headers.get("mcp-session-id"):
                headers["Mcp-Session-Id"] = response.headers["mcp-session-id"]
            if request_id is None and response.status == 202:
                return None
            if "text/event-stream" in response.headers.get("content-type", ""):
                messages = []
                try:
                    for block in response.body.decode().replace("\r\n", "\n").split("\n\n"):
                        data = "\n".join(line[5:].lstrip() for line in block.splitlines() if line.startswith("data:"))
                        if data:
                            messages.append(json.loads(data))
                except (ValueError, UnicodeDecodeError) as exc:
                    raise DomainError("MCP stream contained an invalid message", 502) from exc
                result = next((x for x in messages if isinstance(x, dict) and x.get("id") == request_id), None)
                if result is None:
                    raise DomainError("MCP response requires unsupported stream resumption", 502)
            else:
                result = self._json(response)
            if not isinstance(result, dict) or result.get("id") != request_id or "error" in result:
                raise DomainError("MCP request failed", 502)
            return result.get("result")
        initialized = await rpc("initialize", {
            "protocolVersion": MCP_VERSION, "capabilities": {},
            "clientInfo": {"name": "kleos9", "version": "0.1.0"},
        }, 1)
        if not isinstance(initialized, dict) or initialized.get("protocolVersion") != MCP_VERSION:
            raise DomainError("MCP protocol version has not been qualified", 409)
        headers["MCP-Protocol-Version"] = MCP_VERSION
        await rpc("notifications/initialized", {}, None)
        listed = await rpc("tools/list", {}, 2)
        if not isinstance(listed, dict) or capability not in {
                t.get("name") for t in listed.get("tools", []) if isinstance(t, dict)}:
            raise DomainError("Qualified MCP capability is no longer available", 409)
        return redact(await rpc("tools/call", {"name": capability, "arguments": payload}, 3))
