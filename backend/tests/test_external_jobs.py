import asyncio
import json
from datetime import UTC, datetime

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from kleos.artifacts.service import Artifact
from kleos.capabilities.external import (
    ArtifactBinding,
    BOMItem,
    ExternalJob,
    ExternalJobs,
    ExternalRequest,
    ProviderObservation,
    ReadingRequirement,
    WorkOrder,
)
from kleos.capabilities.human_work import (
    EvidenceReading,
    EvidenceSubmission,
    HumanEvidence,
    RentAHumanDiscovery,
    ReviewRequest,
)
from kleos.capabilities.human_work.evidence import EvidenceAttachment
from kleos.capabilities.sourcing import DigiKeyCatalog
from kleos.contracts.models import Actor, DomainError
from kleos.projects.store import Membership, Project, ProjectStore

OWNER = Actor(tenant_id="tenant", user_id="owner")
EDITOR = Actor(tenant_id="tenant", user_id="editor")
OTHER = Actor(tenant_id="elsewhere", user_id="owner")
NOW = 2000000000


class FixtureProvider:
    """Tests only. Never registered by production startup."""
    id, version, kind, effect_capable = "fixture", "v1", "sourcing", True

    def __init__(self):
        self.submissions, self.polls, self.reconciliations = [], [], []
        self.result = ProviderObservation(external_id="external-1", state="waiting")
        self.error = False
        self.gate = None

    async def discover(self, request):
        return {"items": [], "source": "fixture"}

    async def quote(self, request):
        return {"currency": request.currency, "bindingQuote": False}

    async def draft(self, request):
        return {"submitted": False}

    async def submit(self, request, key):
        self.submissions.append((request, key))
        if self.gate:
            await self.gate.wait()
        if self.error:
            raise TimeoutError("sensitive upstream details")
        return self.result

    async def status(self, external_id):
        self.polls.append(external_id)
        if self.error:
            raise RuntimeError("secret response")
        return self.result

    async def reconcile(self, key, external_id):
        self.reconciliations.append((key, external_id))
        return self.result


@pytest.fixture
def context(tmp_path):
    store = ProjectStore(f"sqlite:///{tmp_path / 'external.db'}")
    store.initialize_for_tests()
    project_id = store.create(OWNER, "Repair the missing power input", "create")["projectId"]
    with store.transaction() as session:
        session.add(Membership(project_id=project_id, user_id=EDITOR.user_id, role="editor"))
        for name in ("specimen", "reading"):
            session.add(Artifact(id=name, project_id=project_id, revision=1, name=f"{name}.txt",
                sha256=("a" if name == "specimen" else "b") * 64, size=20, media_type="text/plain",
                status="input", lane="research", details={"origin": "user_upload"}))
    clock = [NOW]
    provider = FixtureProvider()
    providers = {(OWNER.tenant_id, OWNER.user_id, provider.id): provider}
    jobs = ExternalJobs(store, providers, clock=lambda: clock[0])
    request = ExternalRequest(project_id=project_id, revision=1, kind="sourcing", provider="fixture",
        objective="Source one connector", bom=[BOMItem(reference="J1", supplier_part="ABC-ND", quantity=10)],
        ceiling_minor_units=5000)
    yield store, jobs, provider, request, clock
    store.engine.dispose()


def create(context, *, authorize=False):
    _, jobs, _, request, _ = context
    if authorize:
        jobs.effect_authorizer = lambda actor, req: True
    job = jobs.create(OWNER, request, "job")["job"]
    return job["id"]


def approved(context):
    job_id = create(context, authorize=True)
    context[1].approve(OWNER, job_id, "approve")
    return job_id


def assert_error(code, callable, *args, **kwargs):
    with pytest.raises(DomainError) as error:
        callable(*args, **kwargs)
    assert error.value.code == code


@pytest.mark.asyncio
async def test_unconfigured_provider_is_explicit_and_never_fabricated(context):
    store, _, _, request, _ = context
    jobs = ExternalJobs(store)
    job = jobs.create(OWNER, request, "missing")["job"]
    assert job["status"] == "unavailable" and job["physicalAcceptance"] is False
    with pytest.raises(DomainError, match="No external provider"):
        await jobs.discover(OWNER, job["id"])


def test_scope_and_idempotency_no_release_prerequisite(context):
    _, jobs, _, request, _ = context
    first = jobs.create(OWNER, request, "job")
    assert jobs.create(OWNER, request, "job") == first
    assert_error("idempotency_conflict", jobs.create, OWNER,
                 request.model_copy(update={"ceiling_minor_units": 2}), "job")
    assert_error("not_found", jobs.get, OTHER, first["job"]["id"])
    assert_error("not_found", jobs.list, OTHER, request.project_id)
    assert first["job"]["revision"] == 1


@pytest.mark.asyncio
async def test_prepare_does_not_submit_and_pins_context(context):
    _, jobs, provider, _, _ = context
    job_id = create(context)
    assert (await jobs.draft(OWNER, job_id))["committed"] is False
    assert (await jobs.quote(OWNER, job_id))["result"]["bindingQuote"] is False
    assert (await jobs.discover(OWNER, job_id))["result"]["source"] == "fixture"
    assert not provider.submissions


@pytest.mark.asyncio
async def test_owner_approval_and_server_policy_both_required(context):
    _, jobs, provider, _, _ = context
    job_id = create(context)
    with pytest.raises(DomainError) as error:
        await jobs.dispatch(OWNER, job_id)
    assert error.value.code == "approval_required"
    assert_error("forbidden", jobs.approve, EDITOR, job_id, "editor-approval")
    jobs.approve(OWNER, job_id, "approve")
    with pytest.raises(DomainError) as error:
        await jobs.dispatch(OWNER, job_id)
    assert error.value.code == "physical_policy"
    assert not provider.submissions


@pytest.mark.asyncio
async def test_effect_is_claimed_durably_and_never_repeated(context):
    store, jobs, provider, _, _ = context
    job_id = approved(context)
    provider.error = True
    result = await jobs.dispatch(OWNER, job_id)
    assert result["status"] == result["effectState"] == "uncertain"
    restarted = ExternalJobs(store, jobs.providers, clock=jobs.clock, effect_authorizer=lambda a, r: True)
    assert (await restarted.dispatch(OWNER, job_id))["status"] == "uncertain"
    assert len(provider.submissions) == 1
    provider.error = False
    result = await restarted.reconcile(OWNER, job_id)
    assert result["status"] == "waiting"
    assert provider.reconciliations[0][0] == provider.submissions[0][1]
    assert len(provider.submissions) == 1


@pytest.mark.asyncio
async def test_dispatch_crash_claim_reconciles_without_resubmit(context):
    store, jobs, provider, _, _ = context
    job_id = approved(context)
    with store.transaction() as session:
        row = session.get(ExternalJob, job_id)
        row.status, row.effect_state = "dispatching", "claimed"
    assert (await jobs.dispatch(OWNER, job_id))["effectState"] == "claimed"
    assert (await jobs.reconcile(OWNER, job_id))["externalId"] == "external-1"
    assert not provider.submissions


@pytest.mark.asyncio
async def test_concurrent_dispatch_does_not_repeat(context):
    _, jobs, provider, _, _ = context
    job_id = approved(context)
    provider.gate = asyncio.Event()
    first = asyncio.create_task(jobs.dispatch(OWNER, job_id))
    await asyncio.sleep(0)
    assert (await jobs.dispatch(OWNER, job_id))["status"] == "dispatching"
    provider.gate.set()
    await first
    assert len(provider.submissions) == 1


@pytest.mark.asyncio
async def test_missing_external_identity_is_uncertain(context):
    _, jobs, provider, _, clock = context
    job_id = approved(context)
    provider.result = ProviderObservation(state="completed")
    assert (await jobs.dispatch(OWNER, job_id))["status"] == "uncertain"
    clock[0] += 61
    with pytest.raises(DomainError) as error:
        await jobs.poll(OWNER, job_id)
    assert error.value.code == "reconciliation_required"


@pytest.mark.asyncio
async def test_poll_waits_and_reports_only_actual_observations(context):
    _, jobs, provider, _, clock = context
    job_id = approved(context)
    await jobs.dispatch(OWNER, job_id)
    assert (await jobs.poll(OWNER, job_id))["status"] == "waiting"
    assert not provider.polls
    clock[0] += 61
    provider.result = ProviderObservation(external_id="external-1", state="completed")
    result = await jobs.poll(OWNER, job_id)
    assert result["status"] == "completed" and result["physicalAcceptance"] is False
    assert provider.polls == ["external-1"]


@pytest.mark.asyncio
async def test_poll_error_retains_observation_and_throttles(context):
    _, jobs, provider, _, clock = context
    job_id = approved(context)
    original = await jobs.dispatch(OWNER, job_id)
    clock[0] += 61
    provider.error = True
    with pytest.raises(DomainError) as error:
        await jobs.poll(OWNER, job_id)
    assert "secret" not in str(error.value)
    current = jobs.get(OWNER, job_id)
    assert current["observation"] == original["observation"]
    await jobs.poll(OWNER, job_id)
    assert len(provider.polls) == 1


@pytest.mark.asyncio
async def test_revision_or_provider_change_blocks_effect(context):
    store, jobs, provider, request, _ = context
    job_id = approved(context)
    provider.version = "v2"
    with pytest.raises(DomainError) as error:
        await jobs.dispatch(OWNER, job_id)
    assert error.value.code == "provider_changed"
    provider.version = "v1"
    with store.transaction() as session:
        session.get(Project, request.project_id).revision += 1
    with pytest.raises(DomainError) as error:
        await jobs.dispatch(OWNER, job_id)
    assert error.value.code == "revision_conflict"
    assert not provider.submissions


@pytest.mark.asyncio
async def test_cancel_fences_inflight_result_without_claiming_remote_cancellation(context):
    _, jobs, provider, _, _ = context
    job_id = approved(context)
    provider.gate = asyncio.Event()
    pending = asyncio.create_task(jobs.dispatch(OWNER, job_id))
    await asyncio.sleep(0)
    result = jobs.cancel(OWNER, job_id, "cancel")
    assert result["externalCancellationConfirmed"] is False
    provider.gate.set()
    result = await pending
    assert result["status"] == "cancelled"
    assert result["effectState"] == "observed" and result["externalId"] == "external-1"
    assert len(provider.submissions) == 1


@pytest.mark.asyncio
async def test_deadline_ends_polling_without_fabricated_remote_failure(context):
    _, jobs, provider, _, clock = context
    job_id = approved(context)
    await jobs.dispatch(OWNER, job_id)
    clock[0] += 86401
    result = await jobs.poll(OWNER, job_id)
    assert result["status"] == "expired"
    assert result["observation"]["state"] == "waiting"
    assert not provider.polls


def human_context(context):
    _, jobs, provider, request, _ = context
    provider.kind = "human_work"
    request = ExternalRequest(project_id=request.project_id, revision=1, kind="human_work", provider="fixture",
        objective="Measure the connector", artifact_bindings=[ArtifactBinding(
            artifact_id="specimen", sha256="a" * 64, revision=1)],
        work_order=WorkOrder(title="Check fit", procedure="Measure width with calipers", specimen="connector-A",
            evidence_required=["caliper reading"], readings=[ReadingRequirement(
                name="width", unit="mm", minimum=9.9, maximum=10.1)]))
    job_id = jobs.create(OWNER, request, "human")["job"]["id"]
    submission = EvidenceSubmission(revision=1, specimen="connector-A",
        specimen_bindings=request.artifact_bindings,
        attachments=[EvidenceAttachment(artifact_id="reading", sha256="b" * 64, revision=1,
                                        requirement="caliper reading")],
        readings=[EvidenceReading(name="width", unit="mm", value=10, instrument="caliper-3",
            method="Outside jaws across connector", artifact_id="reading", observed_at=datetime.fromtimestamp(NOW, UTC))])
    return HumanEvidence(jobs), job_id, submission


def test_evidence_submission_and_independent_review(context):
    evidence, job_id, submission = human_context(context)
    result = evidence.submit(EDITOR, job_id, submission, "submit")
    assert evidence.submit(EDITOR, job_id, submission, "submit") == result
    record = result["evidence"]
    assert record["checks"][0]["status"] == "PASS"
    assert record["checks"][0]["independent"] is False
    assert_error("independent_review", evidence.review, EDITOR, job_id, record["id"],
                 ReviewRequest(decision="accept", rationale="Looks good"), "self-review")
    reviewed = evidence.review(OWNER, job_id, record["id"],
                              ReviewRequest(decision="accept", rationale="Compared reading and photo"), "review")
    assert reviewed["evidence"]["review"]["independent"] is True
    assert reviewed["evidence"]["physicalAcceptance"] is False
    assert_error("not_found", evidence.list, OTHER, job_id)


@pytest.mark.parametrize("kind", ["synthetic", "out_of_range"])
def test_failed_or_synthetic_evidence_cannot_be_accepted(context, kind):
    evidence, job_id, submission = human_context(context)
    if kind == "synthetic":
        submission.synthetic = True
    else:
        submission.readings[0].value = 12
    record = evidence.submit(EDITOR, job_id, submission, "submit")["evidence"]
    assert_error("evidence_not_passed", evidence.review, OWNER, job_id, record["id"],
                 ReviewRequest(decision="accept", rationale="Approve"), "review")
    evidence.review(OWNER, job_id, record["id"],
                    ReviewRequest(decision="request_changes", rationale="Repeat measurement"), "changes")


@pytest.mark.parametrize("change", ["hash", "specimen", "unit", "source", "future", "missing"])
def test_evidence_provenance_is_checked(context, change):
    evidence, job_id, submission = human_context(context)
    if change == "hash":
        submission.attachments[0].sha256 = "c" * 64
    elif change == "specimen":
        submission.specimen = "another connector"
    elif change == "unit":
        submission.readings[0].unit = "inch"
    elif change == "source":
        submission.readings[0].artifact_id = "specimen"
    elif change == "future":
        submission.readings[0].observed_at = datetime.fromtimestamp(NOW + 86400, UTC)
    else:
        submission.readings = []
    with pytest.raises(DomainError):
        evidence.submit(EDITOR, job_id, submission, "submit")


def test_numeric_models_reject_nonfinite_values():
    with pytest.raises(ValidationError):
        ReadingRequirement(name="x", unit="mm", minimum=float("nan"))
    with pytest.raises(ValidationError):
        ReadingRequirement(name="x", unit="mm", minimum=2, maximum=1)


@pytest.mark.asyncio
async def test_rentahuman_real_get_protocol_allowlists_fields(context):
    _, _, _, request, _ = context
    _, _, _ = human_context(context)
    request = ExternalRequest.model_validate(context[1].list(OWNER, request.project_id)[0]["request"])
    requests = []
    def respond(req):
        requests.append(req)
        return httpx.Response(200, json={"success": True, "humans": [{"id": "human-1", "name": "Person",
            "hourlyRate": 45, "currency": "USD", "isVerified": True, "privateEmail": "private@example.invalid"}],
            "hasMore": True})
    adapter = RentAHumanDiscovery(api_key=SecretStr("test-only-key"), transport=httpx.MockTransport(respond))
    result = await adapter.discover(request)
    assert requests[0].method == "GET"
    assert requests[0].url.host == "rentahuman.ai" and requests[0].url.path == "/api/humans"
    assert requests[0].headers["X-API-Key"] == "test-only-key"
    assert result["people"][0]["hourlyRate"] == "45"
    assert "private" not in json.dumps(result) and result["hired"] is False
    await adapter.close()


@pytest.mark.asyncio
async def test_rentahuman_owner_status_is_only_candidate_evidence():
    requests = []
    def respond(req):
        requests.append(req)
        return httpx.Response(200, json={"success": True, "submissions": [{"id": "s1", "status": "approved"}]})
    adapter = RentAHumanDiscovery(api_key=SecretStr("fixture"), transport=httpx.MockTransport(respond))
    result = await adapter.status("b_123")
    assert requests[0].url.path == "/api/bounties/b_123/submissions"
    assert result.state == "unknown" and result.details["physicalAcceptance"] is False
    with pytest.raises(DomainError):
        await adapter.status("../other")
    with pytest.raises(DomainError):
        await adapter.submit(None, "no")
    assert len(requests) == 1
    await adapter.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [httpx.Response(302, headers={"Location": "https://evil.invalid"}),
    httpx.Response(200, content=b"x" * 256001), httpx.Response(200, json={"success": False, "error": "secret"}),
    httpx.Response(200, content=b"not-json")])
async def test_http_failures_are_bounded_redacted_and_not_redirected(response):
    calls = []
    def respond(req):
        calls.append(req)
        return response
    adapter = RentAHumanDiscovery(api_key=SecretStr("private-secret"), transport=httpx.MockTransport(respond))
    with pytest.raises(DomainError) as error:
        await adapter.status("b1")
    assert "secret" not in str(error.value)
    assert len(calls) == 1
    await adapter.close()


@pytest.mark.asyncio
async def test_digikey_exact_package_quantity_price_and_auth(context):
    _, _, _, request, _ = context
    calls = []
    def respond(req):
        calls.append(req)
        return httpx.Response(200, json={"SearchLocaleUsed": {"Currency": "USD"}, "Product": {
            "ManufacturerProductNumber": "connector", "ProductVariations": [{
                "DigiKeyProductNumber": "ABC-ND", "QuantityAvailableforPackageType": 11,
                "MinimumOrderQuantity": 1, "StandardPricing": [
                    {"BreakQuantity": 1, "UnitPrice": 2}, {"BreakQuantity": 10, "UnitPrice": 1.25}]}]}})
    adapter = DigiKeyCatalog(client_id=SecretStr("fixture-id"), access_token=SecretStr("fixture-token"),
                            transport=httpx.MockTransport(respond))
    result = await adapter.quote(request)
    assert calls[0].url.path == "/products/v4/search/ABC-ND/productdetails"
    assert calls[0].method == "GET" and calls[0].headers["Authorization"] == "Bearer fixture-token"
    assert result["items"][0]["unitPrice"] == "1.25"
    assert result["items"][0]["quantityAvailable"] is True
    assert result["bindingQuote"] is False and result["reserved"] is False
    with pytest.raises(DomainError):
        await adapter.submit(request, "no")
    assert len(calls) == 1
    await adapter.close()


def test_provider_installations_require_tenant_and_principal_scope(context):
    store, _, provider, _, _ = context
    with pytest.raises(ValueError, match="tenant_id"):
        ExternalJobs(store, {"fixture": provider})


@pytest.mark.asyncio
async def test_existing_job_link_requires_provider_ownership_and_is_idempotent(context):
    _, jobs, provider, _, _ = context
    job_id = create(context)
    with pytest.raises(DomainError) as error:
        await jobs.observe_existing(OWNER, job_id, "external-1", "link")
    assert error.value.code == "external_ownership"
    provider.result.ownership_verified = True
    linked = await jobs.observe_existing(OWNER, job_id, "external-1", "link")
    assert (await jobs.observe_existing(OWNER, job_id, "external-1", "link")) == linked
    assert linked["job"]["effectState"] == "observed" and not provider.submissions
    assert linked["job"]["physicalAcceptance"] is False


@pytest.mark.asyncio
async def test_cancelled_uncertain_effect_can_reconcile_without_reopening(context):
    _, jobs, provider, _, clock = context
    job_id = approved(context)
    provider.error = True
    await jobs.dispatch(OWNER, job_id)
    jobs.cancel(OWNER, job_id, "cancel")
    clock[0] += 86500
    provider.error = False
    result = await jobs.reconcile(OWNER, job_id)
    assert result["status"] == "cancelled" and result["externalId"] == "external-1"
    assert len(provider.submissions) == 1


@pytest.mark.asyncio
async def test_preparation_results_are_durable_and_credentials_stay_creator_scoped(context):
    store, jobs, provider, request, _ = context
    job_id = create(context)
    another = FixtureProvider()
    another.result = ProviderObservation(external_id="wrong-account", state="completed")
    jobs.providers[(EDITOR.tenant_id, EDITOR.user_id, "fixture")] = another
    await jobs.quote(EDITOR, job_id)
    restarted = ExternalJobs(store, jobs.providers, clock=jobs.clock, effect_authorizer=lambda a, r: True)
    assert restarted.get(OWNER, job_id)["preparations"]["quote"]["result"]["currency"] == request.currency
    restarted.approve(OWNER, job_id, "approve")
    result = await restarted.dispatch(EDITOR, job_id)
    assert result["externalId"] == "external-1" and len(provider.submissions) == 1
    assert not another.submissions


def test_model_policy_revision_preserves_pinned_external_context(context):
    store, jobs, _, request, _ = context
    job_id = create(context)
    with store.transaction() as session:
        project = session.get(Project, request.project_id)
        project.advance_revision(context=False)
        assert project.revision == 2 and project.context_revision == 1
    jobs.approve(OWNER, job_id, 'approve-after-policy')
    with store.transaction() as session:
        project = session.get(Project, request.project_id)
        project.advance_revision(context=True)
    with pytest.raises(DomainError, match='Project changed'):
        jobs.approve(OWNER, job_id, 'approve-after-context')


@pytest.mark.parametrize('value', ['1e-10000', '0e-999999999', '1e999999999', '0.' + '0'*10000 + '1'])
def test_price_exponent_and_encoded_size_are_bounded_before_formatting(value):
    from kleos.capabilities.sourcing.digikey import price
    with pytest.raises(DomainError, match='Malformed catalog pricing'):
        price(value)


@pytest.mark.asyncio
async def test_link_rechecks_owner_after_provider_lookup(context):
    store, jobs, provider, request, _ = context
    ident = create(context)
    async def status(external_id):
        with store.transaction() as session:
            session.get(Membership, (request.project_id, OWNER.user_id)).role = 'editor'
        return ProviderObservation(external_id=external_id, ownership_verified=True)
    provider.status = status
    with pytest.raises(DomainError) as error:
        await jobs.observe_existing(OWNER, ident, 'provider-job', 'link')
    assert error.value.code == 'forbidden'
    job = jobs.get(OWNER, ident)
    assert job['externalId'] is None and job['effectState'] == 'none'
