"""The concept agent stays small and never returns executable artifacts."""
import httpx
import httpx2
import pytest
from model_fixtures import patch_replies, responses_reply

from kleos.anatomy_skill import seed_concept
from kleos.config import Settings
from kleos.models import ModelGateway, is_planning_context


@pytest.mark.parametrize('task', ['project.capture','project.research','project.requestReview'])
async def test_compact_planner_uses_scene_contract_and_token_ceiling(monkeypatch, task):
    async def no_network(*args, **kwargs):
        raise AssertionError('No external API calls in model contract tests')
    monkeypatch.setattr(httpx.AsyncClient, 'send', no_network)
    monkeypatch.setattr(httpx2.AsyncClient, 'send', no_network)
    gateway = ModelGateway(Settings(_env_file=None, meta_api_key='fixture'))
    model = gateway.registry.models['meta'][0]
    requests = patch_replies(monkeypatch, gateway, 'meta', model, lambda _n: responses_reply(model, {
        'summary':'I kept the requested handheld shape.', 'requirements':{'form':'handheld'},
        'conceptScene':seed_concept('Handheld analyser')}, output_tokens=200), planning=True)
    output, usage, _ = await gateway.generate('meta', model,
        {'task':task,'lane':'research' if task=='project.research' else 'coordination',
         'planning':{'automatic':True},'brief':'A handheld analyser'})
    assert output.concept_scene.device_kind=='handheld'
    assert output.files==[] and output.interfaces=={}
    assert len(requests)==1
    assert usage['agentMode']=='concept_planning'
    assert usage['outputTokenCeiling']==5000
    assert requests[0]['max_output_tokens']==5000


def test_manual_engineering_keeps_its_existing_agent():
    assert not is_planning_context({'task':'project.generate','planning':{'automatic':True}})
    assert not is_planning_context({'task':'project.research'})
    assert is_planning_context({'task':'project.capture'})


def scripted_gateway(monkeypatch, scenes):
    async def no_network(*args, **kwargs):
        raise AssertionError('No external API calls in model contract tests')
    monkeypatch.setattr(httpx.AsyncClient, 'send', no_network)
    monkeypatch.setattr(httpx2.AsyncClient, 'send', no_network)
    gateway = ModelGateway(Settings(_env_file=None, meta_api_key='fixture'))
    model = gateway.registry.models['meta'][0]
    requests = patch_replies(monkeypatch, gateway, 'meta', model, lambda n: responses_reply(model, {
        'summary':'Updated the watch.', 'conceptScene':scenes[min(n - 1, len(scenes) - 1)]}, output_tokens=200),
        planning=True)
    return gateway, requests


def broken_watch():
    scene = seed_concept('A smart watch')
    scene['parts'][1]['position'] = {'x': 30, 'y': 30, 'z': 30}
    return scene


async def generate(gateway):
    return await gateway.generate('meta', gateway.registry.models['meta'][0],
        {'task': 'project.capture', 'lane': 'coordination', 'planning': {'automatic': True}, 'brief': 'A smart watch'})


async def test_broken_scene_is_sent_back_once_and_the_repair_is_published(monkeypatch):
    gateway, requests = scripted_gateway(monkeypatch, [broken_watch(), seed_concept('A smart watch')])
    output, usage, _ = await generate(gateway)
    assert len(requests) == 2 and usage['requests'] == 2
    assert not any(part.id == 'battery' and part.position.x == 30 for part in output.concept_scene.parts)
    assert output.limitations == []


async def test_scene_that_stays_broken_is_published_with_the_fault_disclosed(monkeypatch):
    gateway, requests = scripted_gateway(monkeypatch, [broken_watch()])
    output, _, _ = await generate(gateway)
    assert len(requests) == 2  # one repair request, then the turn still completes
    assert 'battery' in output.limitations[-1] and output.limitations[-1].startswith('Preview layout is approximate')
