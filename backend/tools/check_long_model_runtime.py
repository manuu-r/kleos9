"""Explicit, bounded streamed-output check; no projects or engineering jobs are created."""
import argparse
import asyncio
import json
import time
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field
from pydantic_ai import Agent, PromptedOutput
from pydantic_ai.usage import UsageLimits

from kleos.config import Settings
from kleos.models import ModelGateway


class Sequence(BaseModel):
    values: list[int] = Field(min_length=600, max_length=600)


async def check():
    settings = Settings()
    gateway = ModelGateway(settings)
    checks = []
    for provider, model in [('meta', settings.meta_wonder_model), ('modal', settings.modal_llm_model)]:
        row = {'provider': provider, 'model': model, 'outputTokenCeiling': 4096,
               'requestCeiling': 1, 'deadlineSeconds': settings.engineering_model_timeout_seconds}
        if not gateway.registry.configured(provider):
            checks.append({**row, 'status': 'unavailable'})
            continue
        started, first, events_seen = time.monotonic(), None, 0

        async def consume(_ctx, events):
            nonlocal first, events_seen
            async for _ in events:
                first = first if first is not None else time.monotonic() - started
                events_seen += 1

        agent = Agent(gateway._build_model(provider, model), output_type=PromptedOutput(Sequence), retries=0,
            instructions='Transport qualification only. Return JSON values containing every integer 1 through 600 '
                         'in order, exactly once. No explanations, tools, search, or code.')
        options = {'max_tokens': 4096, 'timeout': settings.engineering_model_timeout_seconds}
        if provider == 'meta':
            options['openai_reasoning_effort'] = 'minimal'
        try:
            async with asyncio.timeout(settings.engineering_model_timeout_seconds):
                result = await agent.run('Return the complete sequence.', model_settings=options,
                    timeout=settings.engineering_model_timeout_seconds, event_stream_handler=consume,
                    usage_limits=UsageLimits(request_limit=1, output_tokens_limit=4096))
            assert result.output.values == list(range(1, 601))
            _, _, estimate = gateway.estimate_cost(provider, result.usage, result.all_messages(), model=model)
            row.update(status='passed', inputTokens=result.usage.input_tokens,
                       outputTokens=result.usage.output_tokens, requests=result.usage.requests,
                       estimatedUsd=estimate, responseValueCount=len(result.output.values))
        except Exception as exc:
            # Never copy provider bodies, request headers, credentials or partial reasoning.
            row.update(status='failed', errorType=type(exc).__name__, statusCode=getattr(exc, 'status_code', None))
        row.update(elapsedSeconds=round(time.monotonic()-started, 3),
                   firstEventSeconds=round(first, 3) if first is not None else None, streamEvents=events_seen)
        checks.append(row)
        print(json.dumps(row), flush=True)
    report = {'schemaVersion': 'long-stream-runtime-v1', 'checkedAt': datetime.now(UTC).isoformat(),
              'status': 'passed' if all(c['status'] == 'passed' for c in checks) else 'failed',
              'scope': 'Two bounded 600-integer JSON stream requests; no fallback, retries, search, engineering generation, '
                       'or customer project changes. Not a 600-second soak or full-output stress test.',
              'engineeringConfiguredCeiling': settings.engineering_max_output_tokens,
              'checks': checks, 'knownEstimatedUsd': sum(c.get('estimatedUsd') or 0 for c in checks)}
    destination = Path(__file__).resolve().parents[1] / 'verification/long-model-runtime.json'
    destination.write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--live', action='store_true', help='Authorize exactly two bounded paid inference requests')
    if not parser.parse_args().live:
        parser.error('--live is required; this check consumes provider credits')
    asyncio.run(check())
