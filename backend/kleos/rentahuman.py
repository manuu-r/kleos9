"""RentAHuman REST discovery, noncommitting previews and scoped provider observations.

Only the fixed dry-run endpoint may receive POST. Hiring, messages, escrow,
payments and provider-side cancellation remain outside this adapter.
"""

import hashlib
import json
import math
import re
from datetime import UTC, datetime
from urllib.parse import urlencode

from sqlalchemy import select

from .auth import resolve_workspace_access
from .connectors import RestrictedFetcher
from .contracts import DomainError
from .db import Artifact, ExternalIntent, Release, uid
from .domain import contract_hash, emit, get_project
from .telemetry import redact

BASE = "https://rentahuman.ai/api"


def checksum(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def text(value, limit=200):
    return redact(value[:limit]) if isinstance(value, str) else ""


def number(value, ceiling=1000000):
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and 0 <= value <= ceiling:
        return value
    return None


def bounty_summary(item):
    return {"id": text(item.get("id"), 100), "title": text(item.get("title")),
            "status": text(item.get("status"), 80), "priceUsd": number(item.get("price")),
            "applicationCount": number(item.get("applicationCount"))}


def valid_id(value):
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value) is not None


class RentAHumanClient:
    def __init__(self, settings, tenant_id, fetcher=None):
        self.key = settings.rentahuman_api_keys.get(tenant_id)
        self.fetcher = fetcher or RestrictedFetcher(max_bytes=500000, timeout=12, allow_redirects=False)

    async def request(self, path, *, params=None, preview=None, authenticated=False):
        if authenticated and not self.key:
            raise DomainError("Add a RentAHuman API key for this organization first", 503, "rentahuman_unconfigured")
        secret = self.key.get_secret_value() if self.key else None
        headers = {"Accept": "application/json"}
        if secret:
            headers["X-API-Key"] = secret
        body = None
        if preview is not None:
            if path != "/bounties":
                raise DomainError("Live RentAHuman actions are disabled", 403)
            body = json.dumps({**preview, "dryRun": True}, allow_nan=False).encode()
            headers["Content-Type"] = "application/json"
        url = BASE + path + ("?" + urlencode(params) if params else "")
        response = await self.fetcher.request(url, method="POST" if body else "GET", body=body, headers=headers)
        if response.status == 429:
            raise DomainError("RentAHuman is rate limiting requests; retry later", 429, "rentahuman_rate_limited")
        if response.status in {401, 403}:
            raise DomainError("RentAHuman account access was refused; check its API key and permissions", 503, "rentahuman_auth_failed")
        if response.status == 422:
            raise DomainError("RentAHuman could not preview these task details; review its task and lead-time rules", 422, "rentahuman_task_refused")
        if response.status != 200 or len(response.body) > 500000:
            raise DomainError("RentAHuman is unavailable or its response exceeded the limit", 502, "rentahuman_unavailable")
        try:
            raw = response.body.decode("utf-8")
            data = json.loads(raw.replace(secret, "[redacted]") if secret else raw)
            if not isinstance(data, dict) or data.get("success") is not True:
                raise ValueError("Unexpected provider response")
        except (ValueError, RecursionError) as exc:
            raise DomainError("RentAHuman returned an unsupported response", 502, "rentahuman_contract_changed") from exc
        return data

    async def search(self, params):
        data = await self.request("/humans", params=params)
        if not isinstance(data.get("humans"), list) or len(data["humans"]) > 100:
            raise DomainError("RentAHuman returned an unsupported human directory", 502)
        humans = []
        for item in data["humans"][:params["limit"]]:
            if not isinstance(item, dict):
                continue
            location = item.get("location") if isinstance(item.get("location"), dict) else {}
            humans.append({"id": text(item.get("id"), 100), "name": text(item.get("name"), 100),
                "headline": text(item.get("headline")), "skills": [text(s, 80) for s in item.get("skills", [])[:12]]
                if isinstance(item.get("skills"), list) else [], "city": text(location.get("city"), 100),
                "country": text(location.get("country"), 100), "hourlyRate": number(item.get("hourlyRate")),
                "currency": text(item.get("currency"), 8), "verified": item.get("isVerified") is True})
        return {"humans": humans, **self.pagination(data)}

    @staticmethod
    def pagination(data):
        return {"hasMore": data.get("hasMore") is True,
                "nextCursor": data.get("nextCursor") if isinstance(data.get("nextCursor"), str)
                and len(data["nextCursor"]) <= 1000 else None}

    async def bounties(self, cursor=None):
        data = await self.request("/bounties", params={"mine": "true", "limit": 20,
            **({"cursor": cursor} if cursor else {})}, authenticated=True)
        if not isinstance(data.get("bounties"), list) or len(data["bounties"]) > 100:
            raise DomainError("RentAHuman returned an unsupported task list", 502)
        unique = {b["id"]: bounty_summary(b) for b in data["bounties"][:20]
                  if isinstance(b, dict) and valid_id(b.get("id"))}
        return {"bounties": list(unique.values()), **self.pagination(data)}

    async def preview(self, task):
        data = await self.request("/bounties", preview=task, authenticated=True)
        if data.get("dryRun") is not True:
            raise DomainError("RentAHuman did not confirm a noncommitting preview", 502, "rentahuman_contract_changed")
        return {"dryRun": True, "fundingTotalUsd": number(data.get("fundingTotal")),
                "perSeatCents": {k: number(v) for k, v in data.get("perSeat", {}).items()
                                  if k in {"baseCents", "bonusesCents", "feeCents", "totalCents"}}
                if isinstance(data.get("perSeat"), dict) else {}, "observedAt": datetime.now(UTC).isoformat()}

    async def observe(self, ident):
        if not valid_id(ident):
            raise DomainError("Invalid RentAHuman task identity", 422)
        # This owner-only endpoint is the authorization proof; a public listing is insufficient.
        submissions = await self.request(f"/bounties/{ident}/submissions", authenticated=True)
        if not isinstance(submissions.get("submissions"), list) or len(submissions["submissions"]) > 1000:
            raise DomainError("RentAHuman returned unsupported submission metadata", 502)
        data = await self.request(f"/bounties/{ident}", authenticated=True)
        item = data.get("bounty")
        if not isinstance(item, dict) or item.get("id") != ident:
            raise DomainError("RentAHuman returned a different task", 502)
        return {**bounty_summary(item), "submissions": [{"id": text(s.get("id"), 100),
            "status": text(s.get("status"), 80)} for s in submissions["submissions"][:100] if isinstance(s, dict)],
            "submissionsTruncated": len(submissions["submissions"]) > 100,
            "observedAt": datetime.now(UTC).isoformat(), "authority": "provider_observation", "physicalAcceptance": False}


class HumanTaskService:
    def __init__(self, db, settings, client_factory=RentAHumanClient):
        self.db, self.settings, self.client_factory = db, settings, client_factory

    def status(self, principal):
        return {"accountConfigured": bool(self.settings.rentahuman_api_keys.get(principal.tenant_id)),
                "capabilities": ["public_search", "local_drafts", "dry_run_preview", "owned_task_observations"],
                "limitations": ["Hiring, messages and payment happen on RentAHuman; Kleos live posting stays blocked.",
                                "Provider completion is an observation, never physical acceptance."]}

    def client(self, principal):
        return self.client_factory(self.settings, principal.tenant_id)

    @staticmethod
    def current(session, project, payload):
        release = session.get(Release, payload["releaseId"])
        if (not release or release.tenant_id != project.tenant_id or release.project_id != project.id
                or release.mode != "digital" or project.accepted_release_id != release.id
                or release.revision != project.engineering_revision
                or contract_hash(release.manifest.get("requirements", {}), release.manifest.get("interfaces", {}))
                != contract_hash(project.requirements, project.interfaces)):
            raise DomainError("Prepare human work against the current accepted digital release", 409, "human_task_release_required")
        hashes = release.manifest.get("artifacts", {})
        artifacts = session.scalars(select(Artifact).where(Artifact.project_id == project.id,
            Artifact.tenant_id == project.tenant_id, Artifact.revision == project.engineering_revision,
            Artifact.status == "accepted", Artifact.id.in_(list(hashes)))).all()
        if not hashes or {a.id: a.sha256 for a in artifacts} != hashes:
            raise DomainError("Accepted release files changed; review a new release before preparing work", 409, "human_task_release_required")

    def create(self, principal, project_id, body, key):
        payload = body.model_dump(mode="json", by_alias=True)
        fingerprint = checksum(payload)
        with self.db.transaction() as session:
            project = get_project(session, project_id, principal, write=True)
            if body.expected_engineering_revision != project.engineering_revision:
                raise DomainError("This human task belongs to an outdated revision", 409)
            self.current(session, project, payload)
            operation = "rentahuman.draft:" + checksum(key)[:40]
            previous = session.scalar(select(ExternalIntent).where(ExternalIntent.project_id == project.id,
                ExternalIntent.revision == project.engineering_revision, ExternalIntent.operation == operation))
            if previous:
                if previous.payload["fingerprint"] != fingerprint:
                    raise DomainError("Idempotency key was reused with different task details", 409)
                return self.view(previous)
            intent = ExternalIntent(id=uid(), tenant_id=principal.tenant_id, project_id=project.id,
                revision=project.engineering_revision, operation=operation, mode="human_preparation", status="draft",
                payload={**payload, "fingerprint": fingerprint}, receipt={})
            session.add(intent)
            emit(session, project, "humanTask.drafted", {"draftId": intent.id})
            session.flush()
            return self.view(intent)

    def get(self, session, principal, project_id, ident, *, write=False, action=None):
        project = get_project(session, project_id, principal, write=write)
        intent = session.scalar(select(ExternalIntent).where(ExternalIntent.id == ident,
            ExternalIntent.project_id == project_id, ExternalIntent.tenant_id == principal.tenant_id,
            ExternalIntent.mode == "human_preparation", ExternalIntent.operation.like("rentahuman.draft:%")).with_for_update())
        if not intent or intent.revision != project.engineering_revision:
            raise DomainError("Current human-task draft not found", 404)
        self.current(session, project, intent.payload)
        if action and action.draft_sha256 != intent.payload["fingerprint"]:
            raise DomainError("Review the exact saved task before sharing it with RentAHuman", 409)
        return project, intent

    def list(self, principal, project_id):
        with self.db.transaction() as session:
            project = get_project(session, project_id, principal)
            intents = session.scalars(select(ExternalIntent).where(ExternalIntent.tenant_id == principal.tenant_id,
                ExternalIntent.project_id == project_id, ExternalIntent.revision == project.engineering_revision,
                ExternalIntent.mode == "human_preparation").order_by(ExternalIntent.created_at.desc()).limit(30)).all()
            drafts = []
            for intent in intents:
                try:
                    self.current(session, project, intent.payload)
                    drafts.append(self.view(intent))
                except DomainError:
                    continue
            return {"drafts": drafts}

    def cancel(self, principal, project_id, ident):
        with self.db.transaction() as session:
            project, intent = self.get(session, principal, project_id, ident, write=True)
            if intent.status != "cancelled":
                intent.status = "cancelled"
                emit(session, project, "humanTask.draftCancelled", {"draftId": ident})
            return self.view(intent)

    async def update(self, principal, project_id, ident, action, kind):
        with self.db.transaction() as session:
            _, intent = self.get(session, principal, project_id, ident, write=True, action=action)
            if intent.status == "cancelled":
                raise DomainError("This local draft was cancelled; its provider task was not changed", 409)
            payload, receipt = dict(intent.payload), dict(intent.receipt)
        client = self.client(principal)
        if kind == "preview":
            if receipt.get("bountyId"):
                raise DomainError("This task is already linked; refresh its observation instead", 409)
            value = await client.preview(payload["task"])
        else:
            bounty_id = action.bounty_id if kind == "link" else receipt.get("bountyId")
            if not bounty_id or receipt.get("bountyId") not in {None, bounty_id}:
                raise DomainError("A saved task link cannot be replaced; create a new draft", 409)
            value = await client.observe(bounty_id)
        with self.db.transaction() as session:
            fresh = resolve_workspace_access(session, principal.subject, project_id, principal.tenant_id).principal
            project, intent = self.get(session, fresh, project_id, ident, write=True, action=action)
            if intent.status == "cancelled" or intent.receipt.get("bountyId") != receipt.get("bountyId"):
                raise DomainError("Human-task draft changed while the provider was responding", 409)
            if kind == "preview":
                intent.receipt = {**intent.receipt, "preview": value}
                intent.status = "previewed"
            else:
                intent.receipt = {**intent.receipt, "bountyId": bounty_id, "observation": value}
                intent.status = "linked"
            emit(session, project, "humanTask.providerObserved", {"draftId": ident, "kind": kind})
            return self.view(intent)

    @staticmethod
    def view(intent):
        return {"id": intent.id, "projectId": intent.project_id, "engineeringRevision": intent.revision,
                "releaseId": intent.payload["releaseId"], "status": intent.status,
                "draftSha256": intent.payload["fingerprint"], "task": intent.payload["task"],
                "preview": intent.receipt.get("preview", {}), "observation": intent.receipt.get("observation", {}),
                "bountyId": intent.receipt.get("bountyId"), "physicalAcceptance": False}
