"""Exercise real Pydantic AI tools using SDK replies, without paid calls."""
import base64
import hashlib
import json

import httpx
import pytest
from model_fixtures import responses_reply
from openai.types.responses import ResponseFunctionToolCall
from pydantic_ai.exceptions import UsageLimitExceeded

from kleos.cad_render import png_bytes
from kleos.config import Settings
from kleos.models import ModelGateway


class Bridge:
    def __init__(self):
        self.actions, self.observed = [], []

    def assert_live(self, job):
        assert job == 'native-job'

    def remaining_seconds(self, job):
        self.assert_live(job)
        return 900

    def record_usage(self, *args):
        self.observed.append(args[-1])

    record_observed_usage = record_usage

    async def execute(self, attempt, job, key, action, args):
        assert (attempt, job) == ('attempt', 'native-job')
        self.actions.append((key, action, args))
        result = {'sha256': 'a'*64, 'content': 'source'}
        if action == 'render':
            raw = png_bytes(bytes([80, 100, 120] * 4), width=2, height=2)
            result = {'images': [{'contentBase64': base64.b64encode(raw).decode(),
                                  'mediaType': 'image/png', 'sha256': hashlib.sha256(raw).hexdigest()}]}
        return {'status': 'succeeded', 'result': result}


def context(spend=1):
    return {'lane': 'mechanics', 'task': 'project.repair', 'nativeSession': {
        'jobId': 'native-job', 'attemptId': 'attempt', 'maxSteps': 8, 'maxSpendUsd': spend}}


@pytest.fixture
def gateway(monkeypatch):
    async def no_network(*args, **kwargs):
        raise AssertionError('No paid calls allowed')
    monkeypatch.setattr(httpx.AsyncClient, 'send', no_network)
    result = ModelGateway(Settings(_env_file=None, engineering_streaming=False, meta_api_key='fixture', gemini_api_key=None,
                                  modal_llm_url=None, modal_llm_key=None))
    result.session_bridge = Bridge()
    return result


def tool_reply(model, action, arguments):
    response = responses_reply(model, {}, input_tokens=100, output_tokens=50)
    return response.model_copy(update={'output': [ResponseFunctionToolCall(
        type='function_call', id='fc-'+action, call_id='call-'+action,
        name=action, arguments=json.dumps(arguments))]})


async def test_sdk_executes_bounded_tools_and_returns_real_image_content(gateway, monkeypatch):
    model = gateway.settings.meta_model
    requests = []
    async def reply(**kwargs):
        requests.append(kwargs)
        if len(requests) == 1:
            return tool_reply(model, 'read', {'path': 'enclosure.py'})
        if len(requests) == 2:
            return tool_reply(model, 'render', {})
        return responses_reply(model, {'summary': 'Candidate only', 'files': []})
    monkeypatch.setattr(gateway.session_agents[('meta', model)].model.client.responses, 'create', reply)
    output, usage, _ = await gateway.generate('meta', model, context())
    assert output.summary == 'Candidate only' and output.files == []
    assert [row[1] for row in gateway.session_bridge.actions] == ['read', 'render']
    assert gateway.session_bridge.actions[0][0] == 'call-read'
    assert usage['requests'] == 3 and usage['estimatedUsd'] > 0
    assert 'input_image' in json.dumps(requests[-1]['input'])
    assert all('fixture' not in json.dumps(row) for row in gateway.session_bridge.actions)
    assert gateway.session_bridge.observed[-1]['outputTokens'] == 150


async def test_estimated_spend_stops_before_tools_or_another_request(gateway, monkeypatch):
    model = gateway.settings.meta_model
    requests = []
    async def reply(**kwargs):
        requests.append(kwargs)
        return tool_reply(model, 'build', {})
    monkeypatch.setattr(gateway.session_agents[('meta', model)].model.client.responses, 'create', reply)
    with pytest.raises(UsageLimitExceeded, match='spend limit'):
        await gateway.generate('meta', model, context(spend=.000001))
    assert len(requests) == 1 and not gateway.session_bridge.actions
    assert gateway.session_bridge.observed[-1]['outputTokens'] == 50


async def test_unpriced_session_cannot_keep_spending(gateway, monkeypatch):
    model = gateway.settings.meta_model
    gateway.settings.meta_token_prices = {}
    monkeypatch.setattr(gateway, 'estimate_cost', lambda *a, **kw: (0, 0.0, None))
    async def reply(**kwargs):
        return tool_reply(model, 'build', {})
    monkeypatch.setattr(gateway.session_agents[('meta', model)].model.client.responses, 'create', reply)
    with pytest.raises(UsageLimitExceeded, match='pricing is unavailable'):
        await gateway.generate('meta', model, context())
    assert not gateway.session_bridge.actions
