"""Trusted worker model tools. The native task receives actions, never model keys."""
import base64
import hashlib
from copy import copy
from dataclasses import dataclass, replace

from pydantic_ai import BinaryContent, ModelRetry, RunContext, Tool, ToolReturn
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import ModelRequest, ToolReturnPart, UserPromptPart


def bounded_session_history(messages):
    """Bound provider history; original observations remain in the private tool-step ledger."""
    tool_parts = [p for m in messages if isinstance(m, ModelRequest) for p in m.parts if isinstance(p, ToolReturnPart)]
    recent = {id(p) for p in tool_parts[-4:]}
    image_parts = [p for m in messages if isinstance(m, ModelRequest) for p in m.parts
                   if isinstance(p, UserPromptPart) and not isinstance(p.content, str)]
    recent_images = {id(p) for p in image_parts[-1:]}
    result = []
    for message in messages:
        if not isinstance(message, ModelRequest):
            result.append(message)
            continue
        parts = []
        for part in message.parts:
            if isinstance(part, ToolReturnPart) and id(part) not in recent:
                part = replace(part, content={'archived': True, 'tool': part.tool_name, 'toolCallId': part.tool_call_id,
                    'note': 'Previous observation is in the run ledger. Read the current file again before patching.'})
            elif isinstance(part, UserPromptPart) and not isinstance(part.content, str) and id(part) not in recent_images:
                part = replace(part, content='Previous rendered views are archived in the run ledger.')
            parts.append(part)
        result.append(replace(message, parts=parts))
    return result


@dataclass
class SessionBudget(AbstractCapability[dict]):
    gateway: object
    provider: str
    model: str

    async def before_model_request(self, ctx, request_context):
        self.check(ctx)
        return request_context

    def check(self, ctx):
        session = ctx.deps.get('nativeSession', {})
        self.gateway.session_bridge.assert_live(session['jobId'])
        _, _, cost = self.gateway.estimate_cost(self.provider, ctx.usage, ctx.messages, model=self.model)
        self.gateway.session_bridge.record_usage(session['attemptId'], session['jobId'], self.provider, self.model,
            {'inputTokens': ctx.usage.input_tokens, 'outputTokens': ctx.usage.output_tokens,
             'estimatedUsd': cost, 'requests': ctx.usage.requests})
        if cost is None and (ctx.usage.input_tokens or ctx.usage.output_tokens):
            # Do not keep spending when the current model's incremental cost is unknown.
            raise UsageLimitExceeded('Session model pricing is unavailable; configure its price before continuing')
        if cost is not None and cost >= session['maxSpendUsd']:
            raise UsageLimitExceeded('Native session model spend limit reached')

    async def after_model_request(self, ctx, *, request_context, response):
        session = ctx.deps['nativeSession']
        _, _, cost = self.gateway.estimate_cost(self.provider, ctx.usage, ctx.messages, model=self.model)
        # A cancellation during an in-flight response must stop further work,
        # but cannot erase the now-observable billable usage of that response.
        self.gateway.session_bridge.record_observed_usage(
            session['attemptId'], session['jobId'], self.provider, self.model,
            {'inputTokens': ctx.usage.input_tokens, 'outputTokens': ctx.usage.output_tokens,
             # The SDK commits its request counter after this hook. This
             # response is already observed even if cancellation rejects it.
             'estimatedUsd': cost, 'requests': ctx.usage.requests + 1})
        self.check(ctx)
        return response


def tools_for(gateway):
    async def action(ctx, name, arguments):
        session = ctx.deps['nativeSession']
        envelope = await gateway.session_bridge.execute(session['attemptId'], session['jobId'], ctx.tool_call_id, name, arguments)
        result = envelope.get('result', {})
        if isinstance(result.get('logs'), str):
            result = {**result, 'logs': result['logs'][-8000:]}
        return {**envelope, 'result': result}

    async def read(ctx: RunContext[dict], path: str, offset: int = 0) -> dict:
        """Read a bounded source file slice and its current SHA-256 before editing."""
        return await action(ctx, 'read', {'path': path, 'offset': offset, 'maxBytes': 12000})

    async def patch(ctx: RunContext[dict], path: str, expectedSha256: str | None, content: str) -> dict:
        """Replace one source file atomically; null hash creates a new file only."""
        return await action(ctx, 'patch', {'path': path, 'expectedSha256': expectedSha256, 'content': content})

    async def build(ctx: RunContext[dict]) -> dict:
        """Build current files with the fixed qualified tool. This cannot approve a release."""
        return await action(ctx, 'build', {})

    async def analyze(ctx: RunContext[dict]) -> dict:
        """Run fixed diagnostic checks and inspect actual error reports."""
        return await action(ctx, 'analyze', {})

    async def render(ctx: RunContext[dict]) -> ToolReturn:
        """See bounded orthographic PNGs imported from the actual generated STEP file."""
        envelope = await action(ctx, 'render', {})
        result = copy(envelope.get('result', {}))
        images = result.pop('images', [])
        content = []
        for view in images[:3]:
            raw = base64.b64decode(view['contentBase64'], validate=True)
            if len(raw) > 512*1024 or view.get('mediaType') != 'image/png' or hashlib.sha256(raw).hexdigest() != view['sha256']:
                raise ModelRetry('The rendered view failed its integrity check')
            content.append(BinaryContent(data=raw, media_type='image/png'))
        return ToolReturn(return_value={**envelope, 'result': result}, content=content or None)

    return [Tool(action, sequential=True) for action in [read, patch, build, analyze, render]]
