"""The concept agent stays small and never returns executable artifacts."""
import json

import httpx
import httpx2
import pytest
from google.genai.types import GenerateContentResponse

from kleos.anatomy_skill import seed_concept
from kleos.config import Settings
from kleos.models import ModelGateway, is_planning_context


@pytest.mark.parametrize('task', ['project.capture','project.research','project.requestReview'])
async def test_compact_planner_uses_scene_contract_and_token_ceiling(monkeypatch, task):
    async def no_network(*args, **kwargs):
        raise AssertionError('No external API calls in model contract tests')
    monkeypatch.setattr(httpx.AsyncClient, 'send', no_network)
    monkeypatch.setattr(httpx2.AsyncClient, 'send', no_network)
    gateway = ModelGateway(Settings(_env_file=None, gemini_api_key='fixture'))
    requests = []
    async def respond(**kwargs):
        requests.append(kwargs)
        return GenerateContentResponse.model_validate({'candidates':[{'content':{'role':'model','parts':[
            {'text':json.dumps({'summary':'I kept the requested handheld shape.',
                'requirements':{'form':'handheld'}, 'conceptScene':seed_concept('Handheld analyser')})}]},
            'finishReason':'STOP'}], 'usageMetadata':{'promptTokenCount':100,'candidatesTokenCount':200,'totalTokenCount':300}})
    monkeypatch.setattr(gateway.planning_agents['google'].model.client.aio.models,'generate_content',respond)
    output, usage, _ = await gateway.generate('google',gateway.registry.models['google'],
        {'task':task,'lane':'research' if task=='project.research' else 'coordination',
         'planning':{'automatic':True},'brief':'A handheld analyser'})
    assert output.concept_scene.device_kind=='handheld'
    assert output.files==[] and output.interfaces=={}
    assert len(requests)==1
    assert usage['agentMode']=='concept_planning'
    assert usage['outputTokenCeiling']==5000
    assert requests[0]['config']['max_output_tokens']==5000


def test_manual_engineering_keeps_its_existing_agent():
    assert not is_planning_context({'task':'project.generate','planning':{'automatic':True}})
    assert not is_planning_context({'task':'project.research'})
    assert is_planning_context({'task':'project.capture'})
