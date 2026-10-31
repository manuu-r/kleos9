"""Isolated real engine/bridge/Docker probe; optional single bounded Gemini call chain."""
import argparse
import asyncio
import base64
import json
import logging
import os
import secrets
import tempfile
import time
from contextlib import ExitStack
from pathlib import Path
from uuid import uuid4

from sqlalchemy import func, select

from kleos.config import Settings
from kleos.db import (
    Artifact,
    Attempt,
    Base,
    Database,
    Invocation,
    NativeJob,
    NativeSessionStep,
    Organization,
    Project,
    Release,
    Run,
    Task,
    Validation,
)
from kleos.engine import EngineeringEngine
from kleos.execution import DockerExecution
from kleos.models import ModelGateway
from kleos.source_closure import reference
from kleos.storage import ArtifactStore, digest
from kleos.telemetry import redact

SOURCE = b'from build123d import Box\nassembly = Box(40,30,10) - Box(38,28,8)\n'
INSTRUCTIONS = '''This is a narrowly bounded integration smoke test of existing trusted CAD, not engineering generation.
Never generate or patch code, research, analyze, change interfaces or approve anything.
Use exactly three tools across at most five model responses:
1. Read(path="enclosure.py"), then build() exactly once. Prefer requesting these in the same response, in that order. The existing source is trusted and must remain unchanged.
2. Request render() exactly once to inspect the actual STEP views.
3. Return JSON {"summary":"Existing CAD candidate built and viewed; independent validation is still required.","files":[]}.
Keep the final answer short. Do not call any tool twice. No physical or release PASS claims.'''


class DryModels(ModelGateway):
    """Real SDK loop/tool contexts with deterministic responses and no provider transport."""
    def __init__(self, settings):
        self.sdk_requests = 0
        super().__init__(settings, durable=False)

    def _build_model(self, provider, model_id):
        from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
        from pydantic_ai.models.function import FunctionModel
        from pydantic_ai.usage import RequestUsage
        async def response(messages, info):
            self.sdk_requests += 1
            if self.sdk_requests == 1:
                # Deterministically let the root supervisor create its directory
                # before the UID1000 controller delivers the first tool action.
                await asyncio.sleep(2)
                parts = [ToolCallPart('read', {'path': 'enclosure.py'}, tool_call_id='sdk-dry-read'),
                         ToolCallPart('build', {}, tool_call_id='sdk-dry-build')]
            elif self.sdk_requests == 2:
                parts = [ToolCallPart('render', {}, tool_call_id='sdk-dry-render')]
            elif self.sdk_requests == 3:
                parts = [TextPart(json.dumps({'summary': 'Mock SDK candidate; independent validation required', 'files': []}))]
            else:
                raise RuntimeError('Deterministic SDK fixture exceeded three requests')
            return ModelResponse(parts, usage=RequestUsage(input_tokens=100, output_tokens=25),
                                 model_name=model_id, provider_name='google')
        return FunctionModel(response, model_name=model_id)

    def estimate_cost(self, *args, **kwargs):
        return 0, 0., 0.  # FunctionModel executes no paid provider requests.


async def run(mode, image, max_model_requests):
    live = mode == 'live'
    key = (os.environ.get('KLEOS_GEMINI_API_KEY') or os.environ.get('GEMINI_API_KEY')) if live else None
    if live and not key:
        return {'status': 'blocked', 'blocker': 'gemini_key_unconfigured', 'providerRequestsAttempted': False, 'modelCalls': 0}
    ids = {name: str(uuid4()) for name in ('org', 'project', 'run', 'task', 'attempt', 'artifact')}
    started = time.monotonic()
    counters = {'providerRequestsAttempted': False}
    with tempfile.TemporaryDirectory(prefix='kleos-isolated-session-') as temporary:
        root = Path(temporary)
        settings = Settings(_env_file=None, environment='test', auth_mode='development', development_unlimited_usage=False,
            database_url=f'sqlite:///{root / "fixture.sqlite"}', local_artifact_dir=root / 'artifacts', storage_backend='local',
            execution_backend='docker', docker_jobs_dir=Path('/native-jobs'), docker_jobs_volume='kleos9_native_jobs',
            docker_native_images={'mechanics': image}, session_key=secrets.token_hex(32),
            native_sessions_enabled=True, native_session_max_steps=8, native_session_max_seconds=240,
            native_session_max_spend_usd=.10, invocation_reservation_usd=.10, native_job_reservation_usd=.10,
            project_budget_usd=.50, daily_budget_usd=.50, max_model_calls=1, max_native_jobs=1, max_native_jobs_per_project=1,
            max_attempts=1, max_model_turns=max_model_requests, model_timeout_seconds=180, engineering_model_timeout_seconds=180,
            engineering_streaming=False, worker_timeout_seconds=300,
            gemini_api_key=key if live else 'fixture-no-provider-transport', meta_api_key=None, modal_llm_key=None, modal_llm_url=None,
            modal_rate_limit_retries=0, modal_fallback_to_meta=False)
        settings.engineering_max_output_tokens = 4096  # Deliberately below normal production generation allowance.
        assert settings.database_url.startswith('sqlite:///') and settings.local_artifact_dir.is_relative_to(root)
        db, store = Database(settings.database_url), ArtifactStore(settings)
        Base.metadata.create_all(db.engine)
        usage_preflight = None
        if live:
            import genai_prices
            try:
                price = genai_prices.calc_price(genai_prices.Usage(input_tokens=1000, output_tokens=1000),
                                               model_ref=settings.gemini_model, provider_id='google')
                usage_preflight = float(price.total_price)
            except Exception as exc:
                db.close()
                return {'status': 'blocked', 'blocker': 'model_pricing_unavailable', 'blockerType': type(exc).__name__,
                        'providerRequestsAttempted': False, 'modelCalls': 0}
            models = ModelGateway(settings, durable=False)
        else:
            models = DryModels(settings)
        execution = DockerExecution(db, settings, store)
        engine = EngineeringEngine(db, settings, store, models, execution=execution)
        engine_errors = []
        class SafeEngineErrors(logging.Handler):
            def emit(self, record):
                if record.name == 'kleos.engine' and isinstance(record.args, tuple) and len(record.args) == 4:
                    engine_errors.append({'type': record.args[1], 'httpStatus': record.args[2], 'frames': record.args[3]})
        recorder = SafeEngineErrors()
        logging.getLogger('kleos.engine').addHandler(recorder)
        tool_counts = {}
        execute_action = engine.session_bridge.execute
        async def bounded_execute(attempt, job, request_key, action, arguments):
            if action not in {'read', 'build', 'render'} or tool_counts.get(action):
                raise RuntimeError('The fixture allows each required tool exactly once')
            if action == 'read' and arguments.get('path') != 'enclosure.py':
                raise RuntimeError('The fixture only reads its synthetic source')
            tool_counts[action] = 1
            return await execute_action(attempt, job, request_key, action, arguments)
        engine.session_bridge.execute = bounded_execute
        source_key = 'fixture/enclosure.py'
        store.put(source_key, SOURCE)
        with db.transaction() as session:
            session.add(Organization(id=ids['org'], name='Disposable native session proof'))
            session.flush()
            session.add(Project(id=ids['project'], tenant_id=ids['org'], owner_id='isolated-proof',
                name='Disposable hollow box proof', brief=INSTRUCTIONS, status='running', budget_usd=.50,
                state={'confirmed': True}, requirements={}, interfaces={'mechanical': {
                    'externalMm': {'x': 40, 'y': 30, 'z': 10}, 'internalMm': {'x': 38, 'y': 28, 'z': 8},
                    'minimumWallMm': 1, 'components': [{'id': 'board', 'sizeMm': {'x': 10, 'y': 10, 'z': 1},
                                                     'positionMm': {'x': 10, 'y': 8, 'z': 2}}]}}))
            session.flush()
            session.add(Run(id=ids['run'], tenant_id=ids['org'], project_id=ids['project'], command_id='synthetic',
                            kind='project.generate', revision=1, status='running'))
            session.flush()
            session.add(Task(id=ids['task'], tenant_id=ids['org'], project_id=ids['project'], run_id=ids['run'],
                            lane='mechanics', kind='project.generate', revision=1, status='running', fence=1, attempt_count=1))
            session.flush()
            source = Artifact(id=ids['artifact'], tenant_id=ids['org'], project_id=ids['project'], task_id=ids['task'],
                attempt_id=ids['attempt'], revision=1, lane='mechanics', name='enclosure.py', key=source_key,
                sha256=digest(SOURCE), size=len(SOURCE), provenance={'origin': 'trustedPublicFixture'})
            session.add(source)
            session.flush()
            project = session.get(Project, ids['project'])
            context = {'projectId': project.id, 'revision': 1, 'task': 'project.generate', 'lane': 'mechanics',
                'brief': INSTRUCTIONS, 'requirements': {}, 'interfaces': project.interfaces,
                'inputArtifacts': [{'id': source.id, 'name': source.name, 'sha256': source.sha256}],
                'sourceArtifacts': [reference(source)], 'acceptanceCriteria': ['Read, build and render once; leave source unchanged; candidate only']}
            session.add(Attempt(id=ids['attempt'], tenant_id=ids['org'], task_id=ids['task'], number=1, fence=1,
                policy_revision=1, provider='google', model=settings.gemini_model, context=context, status='running'))
        completed, poll_error, native_environment = set(), [], []
        async def poll():
            while True:
                try:
                    execution.dispatch_pending()
                    for job_id, result in execution.reconcile():
                        if job_id not in completed:
                            with db.transaction(read_only=True) as session:
                                job = session.get(NativeJob, job_id)
                            observed = execution.ecs.inspect(job.external_id)
                            names = [item.split('=', 1)[0] for item in observed['Config'].get('Env', [])] if observed else []
                            forbidden = {'GEMINI_API_KEY', 'KLEOS_GEMINI_API_KEY', 'META_API_KEY', 'KLEOS_META_API_KEY',
                                         'AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AWS_SESSION_TOKEN', 'MODAL_TOKEN_SECRET'}
                            assert not forbidden.intersection(names)
                            if key and observed:
                                assert key not in json.dumps(observed['Config'].get('Env', []))
                            native_environment.append({'providerKeyNamesAbsent': True, 'inspected': bool(observed)})
                            engine.finish_native(job_id, result)
                            completed.add(job_id)
                except Exception as exc:
                    poll_error.append(type(exc).__name__)
                    raise
                await asyncio.sleep(.2)
        poller = asyncio.create_task(poll())
        evidence = {'status': 'failed'}
        try:
            with ExitStack() as overrides:
                from kleos.session_tools import tools_for
                agent = models.session_agents[('google', settings.gemini_model)]
                overrides.enter_context(agent.override(instructions=INSTRUCTIONS, retries={'tools': 0, 'output': 1},
                    tools=[tool for tool in tools_for(models) if tool.name in {'read', 'build', 'render'}]))
                if live:
                    from pydantic_ai.models.google import GoogleModelSettings
                    thinking = {'include_thoughts': False, **({'thinking_level': 'LOW'} if 'gemini-3' in settings.gemini_model
                                                             else {'thinking_budget': 128})}
                    overrides.enter_context(agent.override(model_settings=GoogleModelSettings(
                        google_thinking_config=thinking, max_tokens=4096, timeout=180)))
                    original_run_agent = models.run_agent
                    async def observe_provider(*arguments, **keywords):
                        counters['providerRequestsAttempted'] = True
                        return await original_run_agent(*arguments, **keywords)
                    models.run_agent = observe_provider
                await asyncio.wait_for(engine.run_task(ids['task']), timeout=230)
                deadline = time.monotonic() + 45
                while time.monotonic() < deadline:
                    with db.transaction(read_only=True) as session:
                        current = session.get(Task, ids['task'])
                        status = current.status
                    if status in {'succeeded', 'blocked', 'failed', 'cancelled'}:
                        break
                    if poller.done():
                        await poller
                    await asyncio.sleep(.2)
            with db.transaction(read_only=True) as session:
                jobs = list(session.scalars(select(NativeJob)))
                calls = list(session.scalars(select(Invocation)))
                steps = list(session.scalars(select(NativeSessionStep).order_by(NativeSessionStep.sequence)))
                rows = list(session.scalars(select(Artifact)))
                current = session.get(Task, ids['task'])
                project = session.get(Project, ids['project'])
                evidence = {'status': 'passed', 'mode': mode, 'model': settings.gemini_model,
                    'sdkMocked': not live, 'fixtureIds': ids, 'engineErrors': engine_errors,
                    'taskStatus': current.status, 'runStatus': session.get(Run, ids['run']).status,
                    'taskBlocker': redact(current.result.get('reason')),
                    'nativeJobStatus': [job.status for job in jobs], 'toolCounts': tool_counts,
                    'nativeDiagnostics': [redact({'limitations': job.result.get('limitations', []),
                        'log': json.loads(store.get(job.result['logKey'])) if job.result.get('logKey') else {}}) for job in jobs],
                    'validationCount': session.scalar(select(func.count()).select_from(Validation)),
                    'releaseCount': session.scalar(select(func.count()).select_from(Release)),
                    'acceptedReleaseId': project.accepted_release_id,
                    'usage': [{key: value for key, value in call.usage.items() if key in {
                        'inputTokens', 'outputTokens', 'requests', 'estimatedUsd', 'searchCalls', 'searchUsd', 'sessionObserved', 'allowanceDebitUsd', 'costBasis'}} for call in calls],
                    'invocationStatus': [call.status for call in calls],
                    'nativeRequestSecretFree': all(not key or key not in json.dumps(job.request) for job in jobs),
                    'nativeEnvironment': native_environment,
                    'sourceSha256': digest(store.get(source_key)), 'originalSourceSha256': digest(SOURCE),
                    'currentSourceHashes': [row.sha256 for row in rows if row.name == 'enclosure.py' and row.status in {'candidate', 'accepted'}],
                    'outputs': [{'path': row.name, 'sha256': row.sha256, 'size': row.size} for row in rows if row.name.startswith('native/')],
                    'toolSteps': [{'action': row.action, 'status': row.status, 'sequence': row.sequence} for row in steps],
                    'renderImages': [{key: item[key] for key in ('name', 'mediaType', 'sha256', 'stepSha256')}
                        for row in steps if row.action == 'render' for item in row.result.get('images', [])],
                    'pricingProbeUsdPer1000InputAndOutput': usage_preflight,
                    'limits': {'maxModelRequests': max_model_requests, 'maxSteps': 8, 'maxSeconds': 240, 'modelDeadlineSeconds': 180,
                               'outputSchemaRetries': 1,
                               'maxOutputTokensPerResponse': 4096, 'maxSpendUsd': .10},
                    'durableModels': False, 'customerStateTouched': False, 'providerFallback': False, 'providerRetries': 0}
                assert len(jobs) == len(calls) == 1, 'Exactly one native job and invocation expected'
                assert current.status == jobs[0].status == 'succeeded', 'Task/native job did not finish successfully'
                assert tool_counts == {'read': 1, 'build': 1, 'render': 1}, 'Required tools were not each used exactly once'
                assert evidence['validationCount'] == evidence['releaseCount'] == 0 and not project.accepted_release_id
                assert evidence['nativeRequestSecretFree'] and evidence['sourceSha256'] == digest(SOURCE)
                assert evidence['currentSourceHashes'] == [digest(SOURCE)]
                assert len(evidence['renderImages']) == 3
                for row in steps:
                    assert row.status == 'succeeded' and not row.result.get('checks')
                    if row.action == 'render':
                        for item in row.result['images']:
                            image = base64.b64decode(item['contentBase64'], validate=True)
                            assert image.startswith(b'\x89PNG\r\n\x1a\n') and digest(image) == item['sha256']
                if live:
                    assert calls[0].usage.get('requests', 0) <= max_model_requests and (calls[0].estimated_usd or 0) <= .10
        except Exception as exc:
            evidence = {**evidence, 'status': 'blocked', 'blockerType': type(exc).__name__,
                        'blockerCode': getattr(exc, 'code', None), 'pollerErrors': poll_error}
        finally:
            poller.cancel()
            await asyncio.gather(poller, return_exceptions=True)
            execution.cancel_run(ids['run'])
            for _ in range(10):
                execution.reconcile()
                with db.transaction(read_only=True) as session:
                    jobs = list(session.scalars(select(NativeJob)))
                if all(job.result.get('localCleaned') for job in jobs):
                    break
                await asyncio.sleep(.2)
            evidence['ownNativeControlsCleaned'] = all(not execution.control_directory(job.id).exists() for job in jobs)
            evidence['nativeImageIds'] = [job.request.get('toolVersion') for job in jobs]
            logging.getLogger('kleos.engine').removeHandler(recorder)
            db.close()
        observed_requests = sum(max(usage.get('requests', 0), usage.get('sessionObserved', {}).get('requests', 0))
                                for usage in evidence.get('usage', []))
        return {**evidence, **counters, 'wallSeconds': time.monotonic() - started,
                'modelCalls': (observed_requests or (None if counters['providerRequestsAttempted'] else 0)) if live else 0}


parser = argparse.ArgumentParser()
parser.add_argument('--mode', choices=['dry', 'live'], required=True)
parser.add_argument('--image', required=True)
parser.add_argument('--max-model-requests', type=int, choices=[3, 5], default=5)
arguments = parser.parse_args()
logging.getLogger('httpx').setLevel(logging.WARNING)
try:
    result = asyncio.run(run(arguments.mode, arguments.image, arguments.max_model_requests))
except Exception as exc:
    result = {'status': 'blocked', 'blockerType': type(exc).__name__, 'blockerCode': getattr(exc, 'code', None),
              'mode': arguments.mode, 'modelCalls': None, 'providerRequestsAttempted': None}
print(json.dumps(result), flush=True)
if result['status'] != 'passed':
    raise SystemExit(1)
