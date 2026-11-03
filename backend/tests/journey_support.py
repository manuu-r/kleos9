"""Shared pieces for composed journeys over the REAL runtime and REAL native containers.

The model is the only swappable part: `Script` replays fixed tool calls over a mocked HTTP provider, while live
mode (`KLEOS_TEST_LIVE_MODEL=1` plus credentials and an explicit spend cap) lets a real model decide. Nothing
here fabricates native output: CAD/PCB source runs inside the qualified container images.
"""
import json
import os
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from alembic import command
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from pydantic import SecretStr

import kleos
from kleos.infrastructure.runtime import Runtime, migration_config
from kleos.infrastructure.settings import Settings

QUALIFICATION = Path(kleos.__file__).parent / 'execution/qualification'
EVIDENCE = {'cad': QUALIFICATION / 'cad-linux-strict-2026-11-03.json',
            'pcb': QUALIFICATION / 'pcb-linux-strict-2026-11-03.json'}
LIVE = os.environ.get('KLEOS_TEST_LIVE_MODEL') == '1'


@asynccontextmanager
async def persistence(url, *, setup):
    assert not setup
    yield InMemorySaver(), InMemoryStore()


class Locks:
    def execute(self, _):
        pass

    @asynccontextmanager
    async def hold(self, _):
        yield self


def native_tools(*lanes):
    """Operator configuration for the exact locally built images named by the committed evidence."""
    tools = []
    for lane in lanes:
        report = json.loads(EVIDENCE[lane].read_text())
        found = subprocess.run(['docker', 'image', 'inspect', report['imageId']], capture_output=True)
        if found.returncode != 0:
            pytest.skip(f'{lane} native image {report["imageId"][:19]} is not present in the local Docker daemon')
        tools.append({'capability': lane, 'imageDigest': report['imageId'], 'providerVersion': 'local-qualified',
                      'qualified': True, 'qualificationReference': str(EVIDENCE[lane])})
    return tools


def live_cap_micro_usd():
    """Live runs never start without an explicit, bounded spend ceiling."""
    value = os.environ.get('KLEOS_LIVE_MAX_COST_USD')
    if not value or not 0 < float(value) <= 20:
        pytest.skip('Set KLEOS_LIVE_MAX_COST_USD (0 < USD <= 20) to authorize a live-model run')
    return int(float(value) * 1_000_000)


def real_runtime(tmp_path, lanes, upstream):
    common = dict(environment='test', database_url=f'sqlite:///{tmp_path / "runtime.db"}',
        artifact_root=tmp_path / 'artifacts', native_control_root=tmp_path / 'native',
        slicer_control_root=tmp_path / 'slicer', checker_control_root=tmp_path / 'checker',
        simulation_control_root=tmp_path / 'sim', session_secret=SecretStr('s' * 40), development_no_login=True,
        native_tools=native_tools(*lanes))
    if LIVE:
        key = os.environ.get('KLEOS2_META_API_KEY') or os.environ.get('KLEOS_META_API_KEY')
        if not key:
            pytest.skip('Live mode needs KLEOS2_META_API_KEY')
        settings = Settings(**common, meta_api_key=SecretStr(key), max_model_cost_micro_usd=live_cap_micro_usd(),
                            max_model_calls=int(os.environ.get('KLEOS_LIVE_MAX_CALLS', '60')))
        client = None
    else:
        from test_interface_runtime import model
        settings = Settings(**common, model_catalog=[model('text'), model('vision', True)], text_model_key='text',
                            vision_model_key='vision', meta_api_key=SecretStr('fixture-not-a-real-key'))
        client = upstream
    runtime = Runtime(settings, persistence_factory=persistence, worker_locks=Locks(), model_client=client)
    with runtime.projects.engine.begin() as connection:
        config = migration_config()
        config.attributes['connection'] = connection
        command.upgrade(config, 'head')
    return runtime


class Script:
    """Scripted provider: payloads are consumed per typed tool name; anything else is the memory extractor."""

    def __init__(self, **payloads):
        self.payloads = {name: list(items) for name, items in payloads.items()}
        self.calls = []

    def __call__(self, request):
        body = json.loads(request.content)
        name = body.get('tools', [{}])[0].get('function', {}).get('name')
        self.calls.append(name)
        queue = self.payloads.get(name)
        if not queue:
            return httpx.Response(200, json={'id': 'memory', 'choices': [{'finish_reason': 'stop', 'message': {
                'role': 'assistant', 'content': 'No reusable memory facts.'}}],
                'usage': {'prompt_tokens': 10, 'completion_tokens': 5}})
        payload = queue.pop(0) if len(queue) > 1 or name == 'Action' else queue[0]
        return httpx.Response(200, json={'id': 'fixture', 'choices': [{'finish_reason': 'tool_calls', 'message': {
            'role': 'assistant', 'tool_calls': [{'id': 'typed', 'type': 'function',
                'function': {'name': name, 'arguments': json.dumps(payload)}}]}}],
            'usage': {'prompt_tokens': 100, 'completion_tokens': 25}})

    def count(self, name):
        return self.calls.count(name)
