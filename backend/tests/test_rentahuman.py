import json
import socket

import pytest
from pydantic import SecretStr, ValidationError
from sqlalchemy import select
from test_control_plane import create

from kleos.auth import Principal
from kleos.connectors import FetchResponse, RestrictedFetcher
from kleos.contracts import DomainError
from kleos.db import Artifact, ExternalIntent, Membership, Project, Release, Validation, uid
from kleos.rentahuman import HumanTaskService, RentAHumanClient
from kleos.rentahuman_contracts import HumanTaskDraftRequest, HumanTaskSpec


def specification():
    return {"title": "Check the prototype case fit", "description": "Assemble the supplied prototype and record the fit.",
            "completionCriteria": "Photograph each joint and report measured gaps in millimetres.",
            "price": 40, "estimatedHours": 1, "evidenceTypes": ["text", "photo"], "skillsNeeded": ["assembly"],
            "location": {"city": "Bengaluru", "country": "IN", "isRemoteAllowed": False}}


def accepted_project(platform):
    ident = create(platform)["projectId"]
    with platform.db.transaction() as session:
        project = session.get(Project, ident)
        source = Artifact(id=uid(), tenant_id=project.tenant_id, project_id=ident, revision=1,
            lane="mechanics", name="fixture.step", key="fixture", sha256="a" * 64, size=1, status="accepted")
        session.add(source)
        release = Release(id=uid(), tenant_id=project.tenant_id, project_id=ident, revision=1,
            mode="digital", approved_by="fixture", manifest={"requirements": project.requirements, "interfaces": project.interfaces,
                                                               "artifacts": {source.id: source.sha256}})
        session.add(release)
        project.accepted_release_id = release.id
        return ident, {"releaseId": release.id, "expectedEngineeringRevision": 1, "task": specification()}


def draft(platform):
    ident, body = accepted_project(platform)
    response = platform.client.post(f"/api/v1/projects/{ident}/human-task-drafts", json=body,
                                    headers={"Idempotency-Key": "fixture-draft"})
    assert response.status_code == 200, response.text
    return ident, body, response.json()


class Fetcher:
    def __init__(self, data=None, status=200):
        self.data, self.status, self.calls = data, status, []

    async def request(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.data is not None:
            data = self.data
        elif url.endswith("/submissions"):
            data = {"success": True, "submissions": [{"id": "sub_fixture", "status": "approved", "files": ["private"]}]}
        elif url.endswith("/b_fixture"):
            data = {"success": True, "bounty": {"id": "b_fixture", "title": "Operator-created work", "status": "completed"}}
        elif kwargs.get("method") == "POST":
            data = {"success": True, "dryRun": True, "fundingTotal": 47.2,
                    "perSeat": {"baseCents": 4000, "feeCents": 720, "totalCents": 4720}}
        else:
            data = {"success": True, "bounties": [], "hasMore": False}
        return FetchResponse(url, self.status, {}, json.dumps(data).encode())


def install(platform, fetcher):
    platform.settings.rentahuman_api_keys = {"org-a": SecretStr("rah_fixture_key_never_live")}
    service = platform.client.app.state.integrations.human_tasks
    service.client_factory = lambda settings, tenant: RentAHumanClient(settings, tenant, fetcher)
    return service


def test_draft_is_durable_idempotent_and_has_no_physical_authority(platform):
    ident, body, result = draft(platform)
    path = f"/api/v1/projects/{ident}/human-task-drafts"
    replay = platform.client.post(path, json=body, headers={"Idempotency-Key": "fixture-draft"})
    assert replay.json() == result and result["physicalAcceptance"] is False
    conflict = platform.client.post(path, json={**body, "task": {**body["task"], "price": 50}},
                                    headers={"Idempotency-Key": "fixture-draft"})
    assert conflict.status_code == 409
    assert platform.client.get(path).json()["drafts"] == [result]
    with platform.db.transaction() as session:
        intent = session.scalar(select(ExternalIntent).where(ExternalIntent.id == result["id"]))
        assert intent.mode == "human_preparation" and intent.status == "draft"
        assert session.scalar(select(Validation)) is None


def test_missing_key_is_explicit_and_simulated_portal_stays_available(platform):
    ident, _, job = draft(platform)
    status = platform.client.get("/api/v1/rentahuman/status").json()
    assert status["accountConfigured"] is False and status["livePostingEnabled"] is False
    response = platform.client.post(f"/api/v1/projects/{ident}/human-task-drafts/{job['id']}/preview",
                                    json={"draftSha256": job["draftSha256"]})
    assert response.status_code == 503 and response.json()["code"] == "rentahuman_unconfigured"
    assert platform.client.get(f"/api/v1/projects/{ident}/portal/tasks").json()["mode"] == "simulated"


async def test_public_search_is_bounded_and_strips_private_fields(platform):
    fetcher = Fetcher({"success": True, "humans": [{"id": "h_fixture", "name": "Fixture worker",
        "headline": "assembly", "skills": ["CAD"], "email": "private", "wallet": "private",
        "hourlyRate": float("nan"), "location": {"city": "City", "address": "private"}}],
        "hasMore": True, "nextCursor": "opaque-cursor"})
    result = await RentAHumanClient(platform.settings, "org-a", fetcher).search({"skill": "PCB & assembly", "limit": 1})
    url, request = fetcher.calls[0]
    assert "PCB+%26+assembly" in url and request["method"] == "GET" and "X-API-Key" not in request["headers"]
    assert result["humans"][0]["hourlyRate"] is None and "private" not in json.dumps(result)
    assert result["nextCursor"] == "opaque-cursor"


def test_preview_sends_only_reviewed_fields_and_hardcodes_dry_run(platform):
    fetcher = Fetcher()
    install(platform, fetcher)
    ident, _, job = draft(platform)
    result = platform.client.post(f"/api/v1/projects/{ident}/human-task-drafts/{job['id']}/preview",
                                  json={"draftSha256": job["draftSha256"]})
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "previewed" and result.json()["preview"]["fundingTotalUsd"] == 47.2
    assert len(fetcher.calls) == 1
    url, request = fetcher.calls[0]
    sent = json.loads(request["body"])
    assert url == "https://rentahuman.ai/api/bounties" and request["method"] == "POST"
    assert sent == {**job["task"], "dryRun": True} and "releaseId" not in sent
    assert "rah_fixture" not in result.text
    rejected = platform.client.post(f"/api/v1/projects/{ident}/human-task-drafts/{job['id']}/preview",
                                    json={"draftSha256": job["draftSha256"], "dryRun": False})
    assert rejected.status_code == 422 and len(fetcher.calls) == 1


def test_link_and_sync_are_owned_observations_not_physical_acceptance(platform):
    fetcher = Fetcher()
    install(platform, fetcher)
    ident, _, job = draft(platform)
    path = f"/api/v1/projects/{ident}/human-task-drafts/{job['id']}"
    result = platform.client.post(path + "/link", json={"draftSha256": job["draftSha256"], "bountyId": "b_fixture"})
    assert result.status_code == 200, result.text
    data = result.json()
    assert data["status"] == "linked" and data["observation"]["status"] == "completed"
    assert data["physicalAcceptance"] is False and "private" not in result.text
    assert all(call[1]["method"] == "GET" for call in fetcher.calls)
    assert fetcher.calls[0][0].endswith("/b_fixture/submissions")
    assert platform.client.post(path + "/sync", json={"draftSha256": job["draftSha256"]}).status_code == 200
    assert platform.client.post(path + "/link", json={"draftSha256": job["draftSha256"], "bountyId": "different"}).status_code == 409
    with platform.db.transaction() as session:
        assert session.scalar(select(Validation)) is None
        assert "physical_acceptance" not in session.get(Project, ident).state


@pytest.mark.parametrize("change", ["revision", "release", "contract", "artifact", "membership", "cancel"])
def test_changed_or_cancelled_drafts_cannot_publish_provider_response(platform, change):
    ident, _, job = draft(platform)
    class ChangingFetcher(Fetcher):
        async def request(self, url, **kwargs):
            with platform.db.transaction() as session:
                project = session.get(Project, ident)
                if change == "revision":
                    project.engineering_revision += 1
                elif change == "release":
                    project.accepted_release_id = None
                elif change == "contract":
                    project.interfaces = {"changed": True}
                elif change == "artifact":
                    session.scalar(select(Artifact).where(Artifact.project_id == ident)).status = "stale"
                elif change == "membership":
                    session.scalar(select(Membership).where(Membership.subject == "development")).role = "viewer"
                else:
                    session.get(ExternalIntent, job["id"]).status = "cancelled"
            return await super().request(url, **kwargs)
    fetcher = ChangingFetcher()
    install(platform, fetcher)
    response = platform.client.post(f"/api/v1/projects/{ident}/human-task-drafts/{job['id']}/preview",
                                    json={"draftSha256": job["draftSha256"]})
    assert response.status_code in {403, 404, 409}
    with platform.db.transaction() as session:
        assert not session.get(ExternalIntent, job["id"]).receipt


def test_project_tenant_permissions_exact_hash_and_accepted_release(platform):
    ident, body, job = draft(platform)
    service = HumanTaskService(platform.db, platform.settings)
    for user in [Principal("other", "org-b", "owner"), Principal("other", "org-a", "viewer")]:
        with pytest.raises(DomainError):
            service.create(user, ident, HumanTaskDraftRequest.model_validate(body), "bad-access")
    path = f"/api/v1/projects/{ident}/human-task-drafts/{job['id']}/preview"
    assert platform.client.post(path, json={"draftSha256": "0" * 64}).status_code == 409
    with platform.db.transaction() as session:
        session.get(Project, ident).accepted_release_id = None
    assert platform.client.post(f"/api/v1/projects/{ident}/human-task-drafts", json=body,
                                headers={"Idempotency-Key": "stale"}).status_code == 409


async def test_account_keys_never_cross_organizations_and_owned_filter_is_fixed(platform):
    fetcher = Fetcher()
    platform.settings.rentahuman_api_keys = {"org-a": SecretStr("rah_fixture_key_never_live")}
    await RentAHumanClient(platform.settings, "org-a", fetcher).bounties()
    assert "mine=true" in fetcher.calls[0][0]
    assert fetcher.calls[0][1]["headers"]["X-API-Key"] == "rah_fixture_key_never_live"
    with pytest.raises(DomainError, match="organization"):
        await RentAHumanClient(platform.settings, "org-b", fetcher).bounties()
    assert len(fetcher.calls) == 1


@pytest.mark.parametrize("status,code", [(401, "rentahuman_auth_failed"), (403, "rentahuman_auth_failed"),
                                       (429, "rentahuman_rate_limited"), (500, "rentahuman_unavailable")])
async def test_provider_failures_are_not_fake_success_or_secret_echoes(platform, status, code):
    client = RentAHumanClient(platform.settings, "org-a", Fetcher({"error": "private details"}, status))
    with pytest.raises(DomainError) as error:
        await client.search({"skill": "assembly", "limit": 1})
    assert error.value.code == code and "private" not in str(error.value)


async def test_non_dry_preview_or_public_listing_cannot_confirm_owned_work(platform):
    platform.settings.rentahuman_api_keys = {"org-a": SecretStr("rah_fixture_key_never_live")}
    with pytest.raises(DomainError, match="noncommitting"):
        await RentAHumanClient(platform.settings, "org-a", Fetcher({"success": True})).preview(specification())
    fetcher = Fetcher({"success": False}, 403)
    with pytest.raises(DomainError):
        await RentAHumanClient(platform.settings, "org-a", fetcher).observe("b_public")
    assert len(fetcher.calls) == 1 and fetcher.calls[0][0].endswith("/submissions")


async def test_authenticated_fetch_never_follows_redirects():
    calls = []
    class Response:
        status = 302
        def getheaders(self):
            return [("Location", "https://other.example/steal")]
    class Connection:
        def __init__(self, host, *_):
            calls.append(host)
        def request(self, *_args, **_kwargs):
            pass
        def getresponse(self):
            return Response()
        def close(self):
            pass
    def resolve(*_args, **_kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))]
    with pytest.raises(DomainError, match="redirect"):
        await RestrictedFetcher(resolve, Connection, allow_redirects=False).request("https://rentahuman.ai/api/humans",
                                                                                  headers={"X-API-Key": "fixture"})
    assert calls == ["rentahuman.ai"]


@pytest.mark.parametrize("changes", [{"price": float("nan")}, {"price": -1}, {"price": 10001},
                                    {"estimatedHours": 0}, {"dryRun": False},
                                    {"location": {"isRemoteAllowed": False}}])
def test_unsafe_or_incomplete_task_contracts_are_rejected(changes):
    with pytest.raises(ValidationError):
        HumanTaskSpec.model_validate({**specification(), **changes})


def test_local_cancel_never_contacts_provider(platform):
    fetcher = Fetcher()
    install(platform, fetcher)
    ident, _, job = draft(platform)
    path = f"/api/v1/projects/{ident}/human-task-drafts/{job['id']}"
    assert platform.client.delete(path).json()["status"] == "cancelled"
    assert platform.client.post(path + "/preview", json={"draftSha256": job["draftSha256"]}).status_code == 409
    assert fetcher.calls == []
