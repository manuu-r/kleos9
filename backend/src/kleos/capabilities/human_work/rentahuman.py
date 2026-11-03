"""Official rentahuman.ai GET API. Never posts, messages, funds or hires."""
import re

from pydantic import Field, SecretStr

from kleos.capabilities.external import ProviderObservation, ReadOnlyHTTP, ReadOnlyProvider
from kleos.capabilities.sourcing.digikey import price
from kleos.contracts.models import DomainError, WireModel


class HumanSearch(WireModel):
    skill: str = Field(default="", max_length=200)
    city: str = Field(default="", max_length=100)
    country: str = Field(default="", max_length=100)
    max_rate: float | None = Field(default=None, ge=0, le=10000, allow_inf_nan=False)
    limit: int = Field(default=10, ge=1, le=20)
    cursor: str | None = Field(default=None, min_length=1, max_length=2048)


def pagination(raw):
    cursor = raw.get("nextCursor")
    if cursor is not None and (not isinstance(cursor, str) or not 1 <= len(cursor) <= 2048):
        raise DomainError("Invalid provider pagination", "provider_response", 502)
    return {"hasMore": raw.get("hasMore") is True, "nextCursor": cursor}


class RentAHumanDiscovery(ReadOnlyProvider):
    id, version, kind = "rentahuman", "rest-read-2026-11-02", "human_work"

    def __init__(self, *, api_key: SecretStr | None = None, transport=None):
        self.authenticated = bool(api_key and api_key.get_secret_value())
        headers = {"X-API-Key": api_key.get_secret_value()} if self.authenticated else {}
        self.http = ReadOnlyHTTP("https://rentahuman.ai/api/", headers, transport=transport)

    async def discover(self, request):
        work = request.work_order
        if work is None:
            raise DomainError("A work order is required", "invalid_request", 422)
        result = await self.search(HumanSearch(skill=work.skill, city=work.city, country=work.country, limit=20))
        return {"people": result["humans"], "hasMore": result["hasMore"], "nextCursor": result["nextCursor"],
                "hired": False, "profilesAreUntrusted": True}

    async def search(self, filters: HumanSearch):
        params = filters.model_dump(mode="json", by_alias=True, exclude_none=True)
        raw = await self.http.get("humans", params)
        humans = raw.get("humans")
        if not isinstance(humans, list) or len(humans) > 100:
            raise DomainError("Malformed human discovery response", "provider_response", 502)
        people = []
        for person in humans[:filters.limit]:
            if not isinstance(person, dict) or not isinstance(person.get("id"), str):
                raise DomainError("Malformed human profile", "provider_response", 502)
            location = person.get("location", {})
            location = location if isinstance(location, dict) else {}
            people.append({"id": person["id"][:160], "name": str(person.get("name", ""))[:200],
                "headline": str(person.get("headline", ""))[:400],
                "city": str(location.get("city", ""))[:100], "country": str(location.get("country", ""))[:100],
                "hourlyRate": price(person["hourlyRate"]) if person.get("hourlyRate") is not None else None,
                "currency": str(person.get("currency", ""))[:3],
                "verifiedByProvider": person.get("isVerified") is True})
        return {"humans": people, **pagination(raw), "source": "rentahuman.ai"}

    async def owned_bounties(self, cursor=None):
        if not self.authenticated:
            raise DomainError("Owner credentials are needed to list jobs", "provider_unavailable", 503)
        if cursor is not None and (not isinstance(cursor, str) or not 1 <= len(cursor) <= 2048):
            raise DomainError("Invalid cursor", "invalid_request", 422)
        params = {"mine": "true", "limit": 20}
        if cursor:
            params["cursor"] = cursor
        raw = await self.http.get("bounties", params)
        rows = raw.get("bounties")
        if not isinstance(rows, list) or len(rows) > 100:
            raise DomainError("Malformed owned job listing", "provider_response", 502)
        result = []
        for row in rows[:20]:
            if not isinstance(row, dict) or not isinstance(row.get("id"), str):
                raise DomainError("Malformed owned job listing", "provider_response", 502)
            result.append({"id": row["id"][:100], "title": str(row.get("title", ""))[:200],
                           "status": str(row.get("status", "unknown"))[:100]})
        return {"bounties": result, **pagination(raw)}

    async def quote(self, request):
        # Listed rates are not negotiated prices. A local scope cannot invent a funding quote.
        raise DomainError("No binding human-work quote is available through discovery",
                          "operation_unavailable", 503)

    async def status(self, external_id):
        if not self.authenticated:
            raise DomainError("Owner credentials are needed to observe human work", "provider_unavailable", 503)
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", external_id):
            raise DomainError("Invalid external job identity", "invalid_request", 422)
        # The owner-protected endpoint rejects jobs not owned by the injected account.
        raw = await self.http.get(f"bounties/{external_id}/submissions")
        submissions = raw.get("submissions")
        if not isinstance(submissions, list) or len(submissions) > 100:
            raise DomainError("Malformed submission observation", "provider_response", 502)
        summaries = []
        for item in submissions:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                raise DomainError("Malformed submission observation", "provider_response", 502)
            status = item.get("status")
            summaries.append({"id": item["id"][:160], "status": status if status in {
                "pending_review", "approved", "rejected", "redo_requested"} else "unknown"})
        return ProviderObservation(external_id=external_id, state="unknown", ownership_verified=True, details={
            "submissions": summaries, "physicalAcceptance": False,
            "reason": "Submission observations do not establish bounty completion or acceptance"})

    async def close(self):
        await self.http.close()
