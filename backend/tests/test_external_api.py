"""Existing external-work HTTP contracts, using real authority and mocked provider HTTP."""
import json
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from kleos.access.service import AccessService, AccessSettings, Account, OrganizationMember, Session, sha
from kleos.api import create_app
from kleos.api.external import ExternalFacade
from kleos.api.services import ApplicationServices
from kleos.artifacts.service import Artifact
from kleos.capabilities.external import ExternalJobs
from kleos.capabilities.human_work import RentAHumanDiscovery
from kleos.capabilities.sourcing import DigiKeyCatalog
from kleos.projects.store import Membership, Objective, Project, ProjectStore

BASE = "/api/v1"
EDITOR = {"Authorization": "Bearer editor-token"}


def draft_body(**changes):
    body = {"expectedEngineeringRevision": 1, "task": {
        "title": "Inspect connector fit", "description": "Inspect the supplied connector against the drawing.",
        "completionCriteria": "Provide original measured photos and a written fit report.",
        "price": 25.5, "estimatedHours": 1, "priceType": "fixed", "category": "labor-gigs",
        "skillsNeeded": ["prototype assembly"], "evidenceTypes": ["photo"],
        "location": {"city": "", "country": "", "isRemoteAllowed": True}}}
    return {**body, **changes}


@pytest.fixture
def api(tmp_path):
    store = ProjectStore(f"sqlite:///{tmp_path / 'external-api.db'}")
    store.initialize_for_tests()
    access = AccessService(store, AccessSettings(secret_key="x" * 32, environment="test", development_no_login=True))
    actor = access.authenticate({}, {}, "GET")
    first = store.create(actor, "Repair a missing connector", "project")["projectId"]
    other = store.create(actor, "Other project", "other")["projectId"]
    with store.transaction() as session:
        session.add(Account(id="editor", email="editor@example.invalid", name="Editor", password_hash="unused"))
        session.add(OrganizationMember(tenant_id="development", user_id="editor", role="member"))
        session.add(Session(token_hash=sha("editor-token"), user_id="editor", expires=time.time() + 3600, browser=False))
        session.add(Membership(project_id=first, user_id="editor", role="editor"))
        project = session.get(Project, first)
        project.data = {**project.data, "sourceManifest": {"drawing.txt": "specimen"}}
        for ident, digest in [("specimen", "a"), ("reading", "b")]:
            session.add(Artifact(id=ident, project_id=first, revision=1, name=ident + ".txt", sha256=digest * 64,
                size=20, media_type="text/plain", status="input", lane="research", details={"origin": "user_upload"}))
    calls = []
    def respond(request):
        calls.append(request)
        assert request.method == "GET"
        if request.url.host == "api.digikey.com":
            return httpx.Response(200, json={"SearchLocaleUsed": {"Currency": "USD"}, "Product": {
                "ManufacturerProductNumber": "ABC", "ProductVariations": [{"DigiKeyProductNumber": "ABC-ND",
                    "QuantityAvailableforPackageType": 100, "MinimumOrderQuantity": 1,
                    "StandardPricing": [{"BreakQuantity": 1, "UnitPrice": 2}]}]}})
        if request.url.path == "/api/humans":
            return httpx.Response(200, json={"success": True, "humans": [{"id": "h1", "name": "Engineer",
                "headline": "Prototype testing", "hourlyRate": 25, "currency": "USD",
                "location": {"city": "Pune", "country": "IN"}, "privateEmail": "hidden@example.invalid"}],
                "hasMore": True, "nextCursor": "next-page"})
        if request.url.path == "/api/bounties":
            assert request.url.params["mine"] == "true"
            return httpx.Response(200, json={"success": True, "bounties": [
                {"id": "b_1", "title": "Owned work", "status": "open", "privateAccount": "hidden"}],
                "hasMore": False, "nextCursor": None})
        if request.url.path == "/api/bounties/b_1/submissions":
            return httpx.Response(200, json={"success": True, "submissions": [{"id": "s1", "status": "approved"}]})
        return httpx.Response(403, json={"error": "private provider details"})
    human = RentAHumanDiscovery(api_key=SecretStr("fixture-key"), transport=httpx.MockTransport(respond))
    catalog = DigiKeyCatalog(client_id=SecretStr("fixture-id"), access_token=SecretStr("fixture-token"),
                            transport=httpx.MockTransport(respond))
    jobs = ExternalJobs(store, {("development", "development", "rentahuman"): human,
                               ("development", "development", "digikey"): catalog})
    facade = ExternalFacade(access, jobs)
    with TestClient(create_app(ApplicationServices(store, access, external=facade))) as client:
        yield client, facade, first, other, calls, store
    store.engine.dispose()


def keys(name, value):
    frozen = json.loads((Path(__file__).parents[1] / "contracts/openapi.json").read_text())
    schema = frozen["components"]["schemas"][name]
    assert set(schema.get("required", [])) <= set(value)
    assert set(value) <= set(schema["properties"])


def save(api, *, body=None, key="draft"):
    response = api[0].post(f"{BASE}/projects/{api[2]}/human-task-drafts", json=body or draft_body(),
                          headers={"Idempotency-Key": key})
    assert response.status_code == 200, response.text
    return response.json()


def test_status_search_owned_bounties_preserve_frozen_contract_and_account_scope(api):
    client, _, _, _, calls, _ = api
    status = client.get(BASE + "/rentahuman/status")
    keys("RentAHumanStatusView", status.json())
    assert status.json()["accountConfigured"] and status.json()["livePostingEnabled"] is False
    result = client.get(BASE + "/rentahuman/humans", params={"skill": "assembly", "city": "Pune", "maxRate": 30,
                                                           "limit": 10, "cursor": "cursor-1"})
    assert result.status_code == 200
    keys("HumanSearchView", result.json())
    assert result.json()["nextCursor"] == "next-page" and result.json()["humans"][0]["city"] == "Pune"
    assert "hidden" not in result.text
    assert calls[-1].url.params["maxRate"] == "30.0" and calls[-1].url.params["cursor"] == "cursor-1"
    owned = client.get(BASE + "/rentahuman/bounties", params={"mine": "false"})
    keys("HumanBountiesView", owned.json())
    assert owned.json()["bounties"][0]["id"] == "b_1" and "hidden" not in owned.text
    assert client.get(BASE + "/rentahuman/bounties", headers=EDITOR).status_code == 503


def test_unconfigured_provider_is_explicit_while_local_drafting_still_works(api):
    client, facade, _, _, calls, _ = api
    facade.jobs.providers.clear()
    status = client.get(BASE + "/rentahuman/status").json()
    assert status["capabilities"] == ["local_draft"] and status["accountConfigured"] is False
    assert client.get(BASE + "/rentahuman/humans").status_code == 503
    draft = save(api)
    assert draft["status"] == "draft" and draft["physicalAcceptance"] is False
    assert not calls


def test_draft_is_exact_idempotent_local_context_without_release_prerequisite(api):
    client, facade, project, _, calls, _ = api
    draft = save(api)
    keys("HumanTaskDraftView", draft)
    keys("HumanTaskSpec", draft["task"])
    assert draft["releaseId"] == "" and draft["task"]["price"] == 25.5
    assert save(api) == draft and not calls
    listed = client.get(f"{BASE}/projects/{project}/human-task-drafts").json()
    keys("HumanTaskDraftsView", listed)
    assert listed["drafts"] == [draft]
    actor = facade.access.authenticate({}, {}, "GET")
    job = facade.jobs.get(actor, draft["id"])
    assert job["kind"] == "human_work" and job["request"]["ceilingMinorUnits"] == 2550
    assert job["request"]["artifactBindings"][0]["artifactId"] == "specimen"


def test_draft_rejects_forged_release_revision_qualification_and_fractional_money(api):
    client, _, project, _, calls, _ = api
    for body in [draft_body(releaseId="another-release"), draft_body(expectedEngineeringRevision=2),
                 draft_body(qualified=True), draft_body(task={**draft_body()["task"], "price": 1.234}),
                 draft_body(task={**draft_body()["task"], "priceType": "hourly"})]:
        response = client.post(f"{BASE}/projects/{project}/human-task-drafts", json=body,
                               headers={"Idempotency-Key": "bad"})
        assert response.status_code in {409, 422}
    assert not calls


def test_project_path_binding_prevents_cross_project_job_or_draft_access(api):
    client, _, project, other, calls, _ = api
    draft = save(api)
    wrong = f"{BASE}/projects/{other}"
    assert client.get(wrong + "/external-jobs/" + draft["id"]).status_code == 404
    assert client.get(wrong + "/external-jobs/" + draft["id"] + "/events").status_code == 404
    response = client.post(wrong + "/human-task-drafts/" + draft["id"] + "/link",
                           json={"draftSha256": draft["draftSha256"], "bountyId": "b_1"})
    assert response.status_code == 404 and not calls
    response = client.post(wrong + "/human-jobs/" + draft["id"] + "/submissions",
                           json={}, headers={"Idempotency-Key": "wrong"})
    assert response.status_code == 404
    assert client.get(f"{BASE}/projects/{project}/external-jobs/{draft['id']}?projectId={other}").status_code == 200


def test_link_sync_cancel_observe_without_posting_or_claiming_completion(api):
    client, _, project, _, calls, _ = api
    draft = save(api)
    path = f"{BASE}/projects/{project}/human-task-drafts/{draft['id']}"
    bad = client.post(path + "/link", json={"draftSha256": "0" * 64, "bountyId": "b_1"})
    assert bad.status_code == 409 and not calls
    foreign = client.post(path + "/link", json={"draftSha256": draft["draftSha256"], "bountyId": "b_other"})
    assert foreign.status_code == 502
    linked = client.post(path + "/link", json={"draftSha256": draft["draftSha256"], "bountyId": "b_1"})
    assert linked.status_code == 200
    keys("HumanTaskDraftView", linked.json())
    assert linked.json()["status"] == "linked" and linked.json()["physicalAcceptance"] is False
    assert linked.json()["observation"]["state"] == "unknown"
    before = len(calls)
    sync = client.post(path + "/sync", json={"draftSha256": draft["draftSha256"]})
    assert sync.status_code == 200 and len(calls) == before
    cancelled = client.delete(path)
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
    assert len(calls) == before


def test_provider_preview_is_explicitly_unavailable_and_sends_nothing(api):
    draft = save(api)
    response = api[0].post(f"{BASE}/projects/{api[2]}/human-task-drafts/{draft['id']}/preview",
                           json={"draftSha256": draft["draftSha256"]})
    assert response.status_code == 501 and response.json()["code"] == "operation_unavailable"
    assert not api[4]


def test_external_job_and_event_projections_are_real_scoped_records(api):
    client, _, project, _, _, _ = api
    draft = save(api)
    listed = client.get(f"{BASE}/projects/{project}/external-jobs").json()
    keys("ExternalJobsPage", listed)
    keys("ExternalJobSummary", listed["externalJobs"][0])
    assert listed["externalJobs"][0]["state"] == "draft"
    assert listed["externalJobs"][0]["settlementStatus"] == "not_reserved"
    events = client.get(f"{BASE}/projects/{project}/external-jobs/{draft['id']}/events").json()
    keys("ExternalJobEventsPage", events)
    keys("ExternalJobEventSummary", events["events"][0])
    assert events["events"][0]["disposition"] == "external.created"


def evidence_body(api, draft):
    actor = api[1].access.authenticate({}, {}, "GET")
    job = api[1].jobs.get(actor, draft["id"])
    return {"expectedEngineeringRevision": 1, "eventKey": "measurement-1", "specimen": {
        "id": job["request"]["workOrder"]["specimen"], "artifactBindings": job["request"]["artifactBindings"]},
        "measurements": [], "files": [{"criterionId": "photo", "artifactId": "reading", "sha256": "b" * 64}]}


def test_old_evidence_contract_is_forced_synthetic_and_event_key_is_deduplicated(api):
    client, _, project, _, _, _ = api
    draft = save(api)
    body = evidence_body(api, draft)
    path = f"{BASE}/projects/{project}/human-jobs/{draft['id']}"
    response = client.post(path + "/submissions", json=body, headers={**EDITOR, "Idempotency-Key": "evidence-1"})
    assert response.status_code == 200, response.text
    keys("HumanSubmissionAccepted", response.json())
    assert response.json()["evidenceKind"] == "synthetic" and response.json()["physicalAcceptance"] is False
    again = client.post(path + "/submissions", json=body, headers={**EDITOR, "Idempotency-Key": "evidence-2"})
    assert again.json()["eventId"] == response.json()["eventId"]
    review = {"expectedEngineeringRevision": 1, "submissionEventId": response.json()["eventId"],
              "decision": "ACCEPTED", "notes": "Reviewed carefully"}
    rejected = client.post(path + "/reviews", json=review, headers={"Idempotency-Key": "review-1"})
    assert rejected.status_code == 409 and rejected.json()["code"] == "evidence_not_passed"
    review["decision"] = "REVISION_REQUESTED"
    result = client.post(path + "/reviews", json=review, headers={"Idempotency-Key": "review-2"})
    assert result.status_code == 200
    keys("HumanReviewAccepted", result.json())
    assert result.json()["physicalAcceptance"] is False


def test_evidence_rejects_forged_live_kind_and_missing_provenance(api):
    draft = save(api)
    body = evidence_body(api, draft)
    body["specimen"]["physicalAcceptance"] = True
    response = api[0].post(f"{BASE}/projects/{api[2]}/human-jobs/{draft['id']}/submissions", json=body,
                           headers={**EDITOR, "Idempotency-Key": "bad-evidence"})
    assert response.status_code == 422


@pytest.mark.parametrize("command", ["project.placeOrders", "project.acceptPhysical", "project.acceptKit",
                                     "project.ship", "project.activate"])
def test_physical_commands_stay_blocked_even_with_client_qualification(api, command):
    response = api[0].post(BASE + "/commands", json={"type": command,
        "payload": {"projectId": api[2], "qualified": True, "authorize": True, "simulate": False},
        "expectedRevision": 1}, headers={"Idempotency-Key": command})
    assert response.status_code == 403 and response.json()["code"] == "physical_policy"
    assert not api[4]


@pytest.mark.parametrize("command", ["project.requestQuotes", "project.createPhysicalTasks", "project.prepareKit"])
def test_old_preparation_commands_admit_real_objectives_not_fake_stage_success(api, command):
    client, _, project, _, calls, store = api
    body = {"type": command, "payload": {"projectId": project}, "expectedRevision": 1}
    response = client.post(BASE + "/commands", json=body, headers={"Idempotency-Key": "objective"})
    assert response.status_code == 202, response.text
    result = response.json()
    assert result["runId"]
    replay = client.post(BASE + "/commands", json=body, headers={"Idempotency-Key": "objective"})
    assert replay.status_code == 202 and replay.json() == result
    with store.transaction() as session:
        objective = session.get(Objective, result["runId"])
        assert objective.status == "pending"
        assert session.get(Project, project).data.get("state", {}) == {}
    assert not calls


def test_supplied_bom_uses_server_selected_catalog_and_never_places_order(api):
    body = {"type": "project.requestQuotes", "payload": {"projectId": api[2], "provider": "attacker",
        "bom": [{"reference": "J1", "supplierPart": "ABC-ND", "quantity": 2}]}, "expectedRevision": 1}
    response = api[0].post(BASE + "/commands", json=body, headers={"Idempotency-Key": "quote"})
    assert response.status_code == 202, response.text
    assert len(api[4]) == 1 and api[4][0].url.host == "api.digikey.com"
    jobs = api[0].get(f"{BASE}/projects/{api[2]}/external-jobs").json()["externalJobs"]
    assert jobs[0]["pluginId"] == "digikey" and jobs[0]["settlementStatus"] == "not_reserved"


def test_draft_receipt_replays_after_project_changes_but_rejects_changed_task(api):
    client, _, project, _, _, store = api
    saved = save(api)
    with store.transaction() as session:
        row = session.get(Project, project)
        row.revision += 1
        row.data = {**row.data, "engineeringRevision": 2}
    assert save(api) == saved
    changed = draft_body(task={**draft_body()["task"], "price": 26})
    response = client.post(f"{BASE}/projects/{project}/human-task-drafts", json=changed,
                           headers={"Idempotency-Key": "draft"})
    assert response.status_code == 409 and response.json()["code"] == "idempotency_conflict"


def test_optional_review_notes_preserve_contract_and_do_not_promote_synthetic_evidence(api):
    draft = save(api)
    path = f"{BASE}/projects/{api[2]}/human-jobs/{draft['id']}"
    response = api[0].post(path + "/submissions", json=evidence_body(api, draft),
                           headers={**EDITOR, "Idempotency-Key": "submission"})
    review = {"expectedEngineeringRevision": 1, "submissionEventId": response.json()["eventId"],
              "decision": "REVISION_REQUESTED"}
    result = api[0].post(path + "/reviews", json=review, headers={"Idempotency-Key": "review"})
    assert result.status_code == 200 and result.json()["physicalAcceptance"] is False


def test_summary_historical_tracks_context_not_ui_engineering_epoch(api):
    view = save(api)
    endpoint = f'{BASE}/projects/{api[2]}/external-jobs'
    jobs = api[0].get(endpoint).json()
    assert not jobs['externalJobs'][0]['historical']
    with api[5].transaction() as session:
        project = session.get(Project, api[2])
        project.advance_revision(context=False)
        assert project.data.get('engineeringRevision', 1) == view['engineeringRevision']
    assert not api[0].get(endpoint).json()['externalJobs'][0]['historical']
    with api[5].transaction() as session:
        project = session.get(Project, api[2])
        project.advance_revision(context=True)
        assert project.data['engineeringRevision'] == project.context_revision
        assert project.data['engineeringRevision'] > view['engineeringRevision']
    assert api[0].get(endpoint).json()['externalJobs'][0]['historical']
