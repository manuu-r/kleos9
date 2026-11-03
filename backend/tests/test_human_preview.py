"""Local draft preview authority and frozen HTTP contracts; no provider sends."""
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
import test_external_api
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select
from test_external_api import BASE, EDITOR, draft_body, keys
from test_runtime import migrate, settings, test_persistence

from kleos.api import create_app
from kleos.api.human_preview import RECORD_KEY, HumanPreviewFacade
from kleos.api.services import ApplicationServices
from kleos.artifacts.service import Artifact
from kleos.capabilities.external import ExternalJob
from kleos.infrastructure.runtime import Runtime
from kleos.projects.store import Event, Membership, Project

api = test_external_api.api


@pytest.fixture
def preview(api):
    _, external, project, other, calls, store = api
    external.jobs.installation_pins[('development', 'development', 'rentahuman')] = 'a'*64
    facade = HumanPreviewFacade(external)
    with TestClient(create_app(ApplicationServices(store, external.access, external=external, human_preview=facade))) as client:
        saved = client.post(f'{BASE}/projects/{project}/human-task-drafts', json=draft_body(), headers={'Idempotency-Key': 'draft'})
        assert saved.status_code == 200, saved.text
        yield client, facade, saved.json(), project, other, calls, store


def call(preview, *, headers=None, body=None, project=None):
    client, _, draft, real_project, _, _, _ = preview
    return client.post(f"{BASE}/projects/{project or real_project}/human-task-drafts/{draft['id']}/preview",
        json=body or {'draftSha256': draft['draftSha256']}, headers=headers or {'Idempotency-Key': 'preview'})


def test_local_preview_is_honest_durable_idempotent_and_unchanged_frozen_contract(preview):
    client, facade, draft, project, _, calls, store = preview
    result = call(preview)
    assert result.status_code == 200, result.text
    view = result.json()
    keys('HumanTaskDraftView', view)
    assert view['status'] == 'local_preview' and view['task'] == draft['task']
    assert view['preview']['mode'] == 'local' and view['preview']['workerBudgetUsd'] == 25.5
    assert not view['preview']['providerContacted'] and not view['preview']['submitted'] and not view['preview']['funded']
    assert view['preview']['feeQuoteAvailable'] is False and 'fundingTotalUsd' not in view['preview']
    assert view['physicalAcceptance'] is False and view['bountyId'] is None
    assert call(preview).json() == view
    assert call(preview, headers={'Idempotency-Key': 'another-preview-key'}).json() == view
    # Recreated adapter reads the same record; no fee quote or preview is recomputed.
    reloaded = HumanPreviewFacade(facade.external)
    with TestClient(create_app(ApplicationServices(store, facade.access, external=facade.external, human_preview=reloaded))) as restarted:
        response = restarted.get(f'{BASE}/projects/{project}/human-task-drafts')
        keys('HumanTaskDraftsView', response.json())
        assert response.json()['drafts'] == [view]
    assert not calls
    with store.transaction() as session:
        job = session.get(ExternalJob, draft['id'])
        assert job.status == 'draft' and job.effect_state == 'none' and job.external_id is None
        assert job.approval is None and RECORD_KEY in job.preparations
        assert session.get(Project, project).revision == 1
        events = list(session.scalars(select(Event).where(Event.project_id == project)))
        assert len([e for e in events if e.data['type'] == 'human.draft.local_previewed']) == 1


@pytest.mark.parametrize('change,code', [('context', 'stale_context'), ('artifact', 'artifact_scope'),
    ('closure', 'stale_context'), ('account', 'provider_changed'), ('role', 'forbidden'),
    ('provider', 'provider_changed'), ('cancelled', 'job_closed'), ('linked', 'job_closed')])
def test_replay_rechecks_current_authority_sources_account_and_job_state(preview, change, code):
    _, facade, draft, project, _, calls, store = preview
    assert call(preview).status_code == 200
    with store.transaction() as session:
        if change == 'context':
            session.get(Project, project).advance_revision()
        elif change == 'artifact':
            session.get(Artifact, 'specimen').sha256 = 'f'*64
        elif change == 'closure':
            p = session.get(Project, project)
            p.data = {**p.data, 'sourceManifest': {}}
        elif change == 'account':
            facade.jobs.installation_pins[('development', 'development', 'rentahuman')] = 'b'*64
        elif change == 'role':
            session.get(Membership, (project, 'development')).role = 'viewer'
        elif change == 'provider':
            facade.jobs.providers[('development', 'development', 'rentahuman')].version = 'changed'
        elif change == 'cancelled':
            session.get(ExternalJob, draft['id']).status = 'cancelled'
        else:
            session.get(ExternalJob, draft['id']).external_id = 'owned'
    rejected = call(preview)
    assert rejected.status_code >= 400 and rejected.json()['code'] == code, rejected.text
    assert not calls
    if change != 'role':
        listed = preview[0].get(f'{BASE}/projects/{project}/human-task-drafts').json()['drafts'][0]
        assert listed['preview'] == {} and listed['status'] != 'local_preview'


def test_preview_rejects_wrong_hash_project_creator_and_extra_posting_fields(preview):
    assert call(preview, body={'draftSha256': '0'*64}).json()['code'] == 'draft_conflict'
    assert call(preview, project=preview[4]).status_code == 404
    assert call(preview, headers=EDITOR).status_code == 403
    extra = {'draftSha256': preview[2]['draftSha256'], 'post': True, 'fundingTotalUsd': 1}
    assert call(preview, body=extra).status_code == 422
    assert not preview[5]


async def test_actual_runtime_preview_reload_sends_nothing_even_with_configured_account(tmp_path):
    report = Path(__file__).parents[1]/'src/kleos/capabilities/qualification/rentahuman-readonly.json'
    configured = settings(tmp_path, external_installations=[{'provider': 'rentahuman',
        'version': 'rest-read-2026-11-02', 'tenantId': 'development', 'userId': 'development',
        'qualificationReference': str(report), 'apiKeySecret': 'human'}],
        external_credentials={'human': SecretStr('private-preview-fixture-key')})
    runtime = Runtime(configured, persistence_factory=test_persistence, worker_locks=object())
    migrate(runtime)
    provider = runtime.external_jobs.providers[('development', 'development', 'rentahuman')]
    provider.http.get = AsyncMock(side_effect=AssertionError('Local preview cannot contact provider'))
    provider.draft = AsyncMock(side_effect=AssertionError('No unqualified remote dry-run may be inferred'))
    async with runtime.start():
        actor = runtime.access.authenticate({}, {}, 'GET')
        project = runtime.projects.create(actor, 'Inspect an existing connector', 'create')['projectId']
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=runtime.app), base_url='http://localhost') as client:
            saved = await client.post(f'{BASE}/projects/{project}/human-task-drafts', json=draft_body(), headers={'Idempotency-Key': 'draft'})
            assert saved.status_code == 200, saved.text
            draft = saved.json()
            result = await client.post(f"{BASE}/projects/{project}/human-task-drafts/{draft['id']}/preview", json={'draftSha256': draft['draftSha256']})
            assert result.status_code == 200, result.text
            assert 'private-preview-fixture-key' not in result.text
            assert result.json()['preview']['providerConfigured'] is True
            assert result.json()['preview']['providerContacted'] is False
            listed = await client.get(f'{BASE}/projects/{project}/human-task-drafts')
            assert listed.json()['drafts'] == [result.json()]
    provider.http.get.assert_not_awaited()
    provider.draft.assert_not_awaited()
    restarted = Runtime(configured, persistence_factory=test_persistence, worker_locks=object())
    restarted_provider = restarted.external_jobs.providers[('development', 'development', 'rentahuman')]
    restarted_provider.http.get = AsyncMock(side_effect=AssertionError('Reload cannot contact provider'))
    restarted_provider.draft = AsyncMock(side_effect=AssertionError('Replay cannot contact provider'))
    async with restarted.start():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=restarted.app), base_url='http://localhost') as client:
            listed = await client.get(f'{BASE}/projects/{project}/human-task-drafts')
            assert listed.json()['drafts'] == [result.json()]
            replayed = await client.post(f"{BASE}/projects/{project}/human-task-drafts/{draft['id']}/preview",
                json={'draftSha256': draft['draftSha256']})
            assert replayed.json() == result.json()
    restarted_provider.http.get.assert_not_awaited()
    restarted_provider.draft.assert_not_awaited()


async def test_unconfigured_local_draft_can_be_reviewed_without_inventing_account_access(tmp_path):
    runtime = Runtime(settings(tmp_path), persistence_factory=test_persistence, worker_locks=object())
    migrate(runtime)
    async with runtime.start():
        actor = runtime.access.authenticate({}, {}, 'GET')
        project = runtime.projects.create(actor, 'Local review', 'create')['projectId']
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=runtime.app), base_url='http://localhost') as client:
            draft = (await client.post(f'{BASE}/projects/{project}/human-task-drafts', json=draft_body(), headers={'Idempotency-Key': 'draft'})).json()
            result = await client.post(f"{BASE}/projects/{project}/human-task-drafts/{draft['id']}/preview", json={'draftSha256': draft['draftSha256']})
            assert result.status_code == 200, result.text
            assert result.json()['preview']['providerConfigured'] is False
            assert result.json()['status'] == 'local_preview'
