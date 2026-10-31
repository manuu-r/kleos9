"""Provider adapters through the real command/planning lifecycle, with zero network or billing."""
import json

import httpx
import httpx2
import pytest
from model_fixtures import chat_reply, patch_replies, responses_reply
from pydantic import SecretStr
from pydantic_ai.exceptions import ModelHTTPError
from sqlalchemy import func, select
from test_clarification import question
from test_control_plane import command
from test_planning_concepts import admit, next_run, start, workspace

from kleos.anatomy_skill import seed_concept
from kleos.db import Attempt, Invocation, NativeJob, Run
from kleos.engine import EngineeringEngine
from kleos.models import ModelGateway


@pytest.mark.parametrize('fallback', [False, True])
async def test_legacy_pinned_modal_capture_answer_research_confirmation_and_deletion(platform, monkeypatch, fallback):
    async def forbidden_network(*args, **kwargs):
        raise AssertionError('This end-to-end fixture must not spend provider credits')
    monkeypatch.setattr(httpx.AsyncClient, 'send', forbidden_network)
    monkeypatch.setattr(httpx2.AsyncClient, 'send', forbidden_network)
    async def no_sleep(_seconds):
        return None
    monkeypatch.setattr('kleos.models.asyncio.sleep', no_sleep)
    settings = platform.settings
    settings.modal_llm_url = 'https://fixture.modal.run'
    settings.modal_llm_key = SecretStr('fixture')
    settings.modal_fallback_to_meta = True
    gateway = ModelGateway(settings)
    worker = EngineeringEngine(platform.db, settings, platform.store, gateway)
    modal, spark = settings.modal_llm_model, settings.meta_model
    scene = seed_concept('A small circular desk button')
    capture = {'summary': 'Choose how to power your button.', 'conceptScene': scene,
               'clarificationQuestions': [question()]}
    def modal_reply(_number):
        if fallback:
            raise ModelHTTPError(429, modal, 'Capacity fixture, before any response')
        return chat_reply(modal, capture)
    modal_calls = patch_replies(monkeypatch, gateway, 'modal', modal, modal_reply, planning=True)
    meta_capture = patch_replies(monkeypatch, gateway, 'meta', spark,
                                lambda _n: responses_reply(spark, capture), planning=True)
    ident, capture_run = start(platform, 'A small circular USB desk button')
    task, attempt = admit(platform, worker, capture_run)
    # A persisted pre-upgrade attempt must retain its original provider and tools.
    with platform.db.transaction() as session:
        pinned = session.get(Attempt, attempt)
        pinned.provider, pinned.model = 'modal', modal
        pinned.context = {k: v for k, v in pinned.context.items() if k not in {'captureResearch', 'modelPolicyLane'}}
    await worker.run_task(task)
    worker.finish_run(capture_run)
    assert next_run(platform, ident) is None
    captured = workspace(platform, ident)
    q = captured['state']['clarification']['questions'][0]
    assert captured['modelPolicy']['coordination']['provider'] == 'modal'
    assert len(modal_calls) == (2 if fallback else 1)
    assert len(meta_capture) == (1 if fallback else 0)
    inspected = platform.control.inspect_run(platform.principal, capture_run)['tasks'][0]['attempts'][0]
    assert inspected['provider'] == ('meta' if fallback else 'modal')
    assert ('fallbackFrom' in inspected['calls'][0]['usage']) is fallback

    research_calls = patch_replies(monkeypatch, gateway, 'meta', spark, lambda _n: responses_reply(spark, {
        'summary': 'USB power is recorded. Ready for your confirmation.', 'conceptScene': scene,
        'requirements': {'power': 'usb'}, 'limitations': ['Fixture research; no physical evidence']}, searches=1), planning=True)
    receipt = command(platform, ident, 'project.answer', {
        'questionId': q['id'], 'questionHash': q['questionHash'], 'values': ['usb'], 'autoResume': True})
    task, _ = admit(platform, worker, receipt['runId'])
    await worker.run_task(task)
    worker.finish_run(receipt['runId'])
    reviewed = workspace(platform, ident)
    assert reviewed['state']['planning']['feasibilityComplete']
    assert reviewed['state']['clarification']['readyToConfirm']
    assert len(research_calls) == 1 and 'usb' in json.dumps(research_calls[0], default=str)
    assert next_run(platform, ident) is None
    command(platform, ident, 'project.confirm')
    approved = workspace(platform, ident)
    assert approved['state']['approvedDesign']['sceneHash'] == approved['requirements']['designIntent']['sceneHash']
    events = platform.client.get(f'/api/v1/projects/{ident}/events?once=true')
    assert events.status_code == 200 and ('model.providerFallback' in events.text) is fallback
    with platform.db.transaction() as session:
        assert session.scalar(select(func.count(Invocation.id)).where(Invocation.project_id == ident)) == 2
        assert session.scalar(select(func.count(NativeJob.id))) == 0
        assert session.scalar(select(func.count(Run.id)).where(Run.project_id == ident, Run.kind == 'project.generate')) == 0
    revision = platform.control.workspace(platform.principal, ident)['revision']
    assert platform.client.delete(f'/api/v1/projects/{ident}', params={'expectedRevision': revision}).status_code == 202
    assert platform.client.get(f'/api/v1/projects/{ident}/workspace').status_code == 404
