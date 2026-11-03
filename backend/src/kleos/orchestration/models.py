"""Explicit model routing and durable spend reservations for LangChain chat calls."""
import asyncio
import base64
import hashlib
import json
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any, Literal
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, convert_to_openai_messages
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import ConfigDict, Field, SecretStr, model_validator
from sqlalchemy import JSON, BigInteger, Integer, String, select
from sqlalchemy.orm import Mapped, mapped_column

from kleos.contracts.models import DomainError, WireModel
from kleos.orchestration.observability import model_task
from kleos.projects.graph_repository import GraphRepository
from kleos.projects.store import Base, Objective, fingerprint


class ModelConfiguration(WireModel):
    model_config = ConfigDict(**(WireModel.model_config | {'frozen': True}))
    key: str = Field(pattern=r'^[a-z][a-z0-9_.-]{0,79}$')
    provider: Literal['meta', 'modal']
    protocol: Literal['openai_chat']
    model: str = Field(min_length=1, max_length=200)
    version: str = Field(min_length=1, max_length=100)
    base_url: str
    vision: bool = False
    tool_calls: bool = True
    qualified: bool = False
    max_input_tokens: int = Field(ge=1024, le=1000000)
    max_output_tokens: int = Field(default=4096, ge=256, le=32768)
    max_request_bytes: int = Field(default=12000000, ge=4000, le=20000000)
    max_response_bytes: int = Field(default=1000000, ge=4000, le=4000000)
    timeout_seconds: int = Field(default=180, ge=1, le=600)
    input_price_micro_usd_per_million: int = Field(ge=0)
    output_price_micro_usd_per_million: int = Field(ge=0)

    @model_validator(mode='after')
    def endpoint(self):
        parsed = urlsplit(self.base_url)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment):
            raise ValueError('Model endpoints require explicit HTTPS URLs without credentials or query strings')
        return self


class ModelPolicy(WireModel):
    model_config = ConfigDict(**(WireModel.model_config | {'frozen': True}))
    text_model_key: str
    vision_model_key: str | None = None
    modal_rate_limit_fallback_key: str | None = None
    max_calls: int = Field(default=64, ge=1, le=1000)
    max_cost_micro_usd: int = Field(default=1000000, ge=1, le=1000000000)


class ModelBudget(Base):
    __tablename__ = 'kleos2_model_budgets'
    run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    policy_hash: Mapped[str] = mapped_column(String(64))
    max_calls: Mapped[int] = mapped_column(Integer)
    max_cost: Mapped[int] = mapped_column(BigInteger)
    calls: Mapped[int] = mapped_column(Integer, default=0)
    committed_cost: Mapped[int] = mapped_column(BigInteger, default=0)


class ModelAttempt(Base):
    __tablename__ = 'kleos2_model_attempts'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    status: Mapped[str] = mapped_column(String(30))
    data: Mapped[dict] = mapped_column(JSON)


def _price(config, input_tokens, output_tokens):
    amount = (input_tokens * config.input_price_micro_usd_per_million
              + output_tokens * config.output_price_micro_usd_per_million)
    return (amount + 999999) // 1000000


class ModelLedger:
    """Reservations survive errors, restarts and missing usage; never silently reset."""
    def __init__(self, store):
        self.store = store

    def _active(self, session, scope):
        GraphRepository(self.store)._check(session, scope)

    def reserve(self, scope, policy, policy_hash, config, request_hash, output_tokens, fallback_of=None):
        amount = _price(config, config.max_input_tokens, output_tokens)
        with self.store.transaction() as session:
            self._active(session, scope)  # Project row lock serializes parallel task reservations.
            budget = session.get(ModelBudget, scope.run_id, with_for_update=True)
            if budget is None:
                budget = ModelBudget(run_id=scope.run_id, policy_hash=policy_hash, max_calls=policy.max_calls,
                    max_cost=policy.max_cost_micro_usd, calls=0, committed_cost=0)
                session.add(budget)
            if budget.policy_hash != policy_hash:
                raise DomainError('Objective model policy is pinned', 'model_policy_conflict')
            if budget.calls >= budget.max_calls or budget.committed_cost + amount > budget.max_cost:
                raise DomainError('Model allowance exhausted', 'model_budget_exhausted')
            budget.calls += 1
            budget.committed_cost += amount
            attempt_id = str(uuid4())
            attribution = model_task.get()
            session.add(ModelAttempt(id=attempt_id, run_id=scope.run_id, status='reserved', data={
                'model': config.model_dump(mode='json', by_alias=True), 'requestHash': request_hash,
                'reservedMicroUsd': amount, 'committedMicroUsd': amount,
                'createdAt': datetime.now(UTC).isoformat(), 'fallbackOf': fallback_of,
                'taskId': attribution[1] if attribution and attribution[0] == scope.run_id else None}))
            return attempt_id

    def settle(self, attempt_id, status, *, usage=None, request_id=None):
        with self.store.transaction() as session:
            attempt = session.scalar(select(ModelAttempt).where(ModelAttempt.id == attempt_id).with_for_update())
            if attempt is None:
                raise DomainError('Model attempt missing', 'not_found', 404)
            if attempt.status != 'reserved':
                return
            budget = session.scalar(select(ModelBudget).where(ModelBudget.run_id == attempt.run_id).with_for_update())
            config = ModelConfiguration.model_validate(attempt.data['model'])
            cost = attempt.data['reservedMicroUsd']
            if usage is not None:
                cost = _price(config, usage['input_tokens'], usage['output_tokens'])
            elif status == 'rate_limited':
                cost = 0
            budget.committed_cost += cost - attempt.data['committedMicroUsd']
            attempt.status = status
            attempt.data = {**attempt.data, 'committedMicroUsd': cost, 'usage': usage,
                'requestId': request_id, 'finishedAt': datetime.now(UTC).isoformat()}

    def assert_active(self, scope):
        with self.store.transaction() as session:
            self._active(session, scope)

    def records(self, actor, project_id, run_id):
        with self.store.transaction() as session:
            self.store.authorized(session, actor, project_id)
            run = session.get(Objective, run_id)
            if run is None or run.project_id != project_id:
                raise DomainError('Objective not found', 'not_found', 404)
            return [{'id': item.id, 'status': item.status, **item.data} for item in session.scalars(
                select(ModelAttempt).where(ModelAttempt.run_id == run_id))]


class ModelGateway:
    def __init__(self, configurations: list[ModelConfiguration], policy: ModelPolicy, ledger: ModelLedger,
                 credentials: dict[str, SecretStr], *, client: httpx.AsyncClient | None = None):
        if len({item.key for item in configurations}) != len(configurations):
            raise ValueError('Duplicate model configuration key')
        self.catalog = MappingProxyType({item.key: item for item in configurations})
        self.policy, self.ledger, self._credentials, self._client = policy, ledger, MappingProxyType(dict(credentials)), client
        self.policy_hash = fingerprint({'policy': policy.model_dump(mode='json'),
            'models': [item.model_dump(mode='json') for item in sorted(configurations, key=lambda item: item.key)]})

    def select(self, *, images=False, model_key=None):
        key = model_key or (self.policy.vision_model_key if images else self.policy.text_model_key)
        config = self.catalog.get(key)
        if (config is None or not config.qualified or config.provider not in self._credentials
                or not self._credentials[config.provider].get_secret_value()):
            raise DomainError('Selected model is not configured and qualified', 'model_unavailable')
        if images and not config.vision:
            raise DomainError('Selected model cannot process images', 'vision_model_required')
        return config

    def chat_model(self, scope, *, images=False, model_key=None):
        selected = self.select(images=images, model_key=model_key)
        return ScopedChatModel(gateway=self, scope=scope, model_key=selected.key)

    async def generate(self, scope, model_key, messages, *, tools=None, tool_choice=None, max_tokens=None, stop=None):
        wire = convert_to_openai_messages(messages)
        images = self._images(wire)
        config = self.select(images=bool(images), model_key=model_key)
        if tools and not config.tool_calls:
            raise DomainError('Selected model cannot return typed tool output', 'model_capability_missing')
        limit = max_tokens or config.max_output_tokens
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= config.max_output_tokens:
            raise DomainError('Model output exceeds the configured allowance', 'model_budget_exhausted')
        body = {'model': config.model, 'messages': wire, 'max_tokens': limit, 'stream': False}
        if tools:
            body.update(tools=tools, tool_choice=tool_choice or 'auto')
        if stop:
            body['stop'] = stop
        return await self._request(scope, config, body, images=bool(images))

    @staticmethod
    def _images(messages):
        count = 0
        for message in messages:
            content = message.get('content')
            if not isinstance(content, list):
                continue
            for part in content:
                if part.get('type') == 'text':
                    continue
                if part.get('type') != 'image_url' or message['role'] != 'user':
                    raise DomainError('Unsupported model content part', 'invalid_model_input')
                url = part.get('image_url', {}).get('url', '')
                if not url.startswith(('data:image/png;base64,', 'data:image/jpeg;base64,', 'data:image/webp;base64,')):
                    raise DomainError('Use authorized inline image content', 'invalid_model_input')
                try:
                    base64.b64decode(url.split(',', 1)[1], validate=True)
                except (ValueError, TypeError):
                    raise DomainError('Invalid inline image', 'invalid_model_input') from None
                count += 1
                if count > 8:
                    raise DomainError('Too many reference images', 'invalid_model_input')
        return count

    async def _request(self, scope, config, body, *, images, fallback_of=None):
        encoded = json.dumps(body).encode()
        if len(encoded) > config.max_request_bytes:
            raise DomainError('Model request exceeds its bound', 'model_input_too_large')
        # UTF-8 bytes conservatively bound text tokens; inline vision reserves the full configured context.
        if not images and len(encoded) > config.max_input_tokens:
            raise DomainError('Model context exceeds its token bound', 'model_input_too_large')
        attempt = self.ledger.reserve(scope, self.policy, self.policy_hash, config,
            hashlib.sha256(encoded).hexdigest(), body['max_tokens'], fallback_of)
        timeout = min(config.timeout_seconds, (scope.deadline_at - datetime.now(UTC)).total_seconds())
        owned = self._client is None
        client = self._client or httpx.AsyncClient(follow_redirects=False)
        try:
            async with asyncio.timeout(max(0, timeout)):
                async with client.stream('POST', config.base_url.rstrip('/') + '/chat/completions',
                    headers={'Authorization': 'Bearer ' + self._credentials[config.provider].get_secret_value(),
                             'Content-Type': 'application/json'}, content=encoded, timeout=timeout,
                    follow_redirects=False) as response:
                    if response.status_code == 429:
                        self.ledger.settle(attempt, 'rate_limited')
                        fallback = self.policy.modal_rate_limit_fallback_key
                        if config.provider == 'modal' and fallback and fallback != config.key and fallback_of is None:
                            target = self.select(images=images, model_key=fallback)
                            if body.get('tools') and not target.tool_calls:
                                raise DomainError('Fallback lacks typed output support', 'model_capability_missing')
                            if body['max_tokens'] > target.max_output_tokens:
                                raise DomainError('Fallback output allowance is insufficient', 'model_budget_exhausted')
                            return await self._request(scope, target, {**body, 'model': target.model},
                                                       images=images, fallback_of=attempt)
                        raise DomainError('Model rate limited; explicit retry is required', 'model_rate_limited', 429)
                    if not 200 <= response.status_code < 300:
                        self.ledger.settle(attempt, 'provider_error')
                        raise DomainError('Model provider rejected the request', 'model_provider_error', 502)
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > config.max_response_bytes:
                            raise DomainError('Model response exceeds its bound', 'model_response_invalid', 502)
                    payload = json.loads(content)
                    result, usage = self._response(payload)
                    self.ledger.settle(attempt, 'completed' if usage else 'usage_unknown', usage=usage,
                        request_id=str(payload.get('id', ''))[:200])
                    self.ledger.assert_active(scope)
                    return result
        except (httpx.HTTPError, TimeoutError):
            self.ledger.settle(attempt, 'uncertain')
            raise DomainError('Model request outcome is uncertain; reconcile before retry', 'model_uncertain', 502) from None
        except asyncio.CancelledError:
            self.ledger.settle(attempt, 'uncertain')
            raise
        except DomainError:
            self.ledger.settle(attempt, 'uncertain')
            raise
        except (ValueError, KeyError, TypeError, IndexError, AttributeError):
            self.ledger.settle(attempt, 'invalid_response')
            raise DomainError('Model returned an invalid response', 'model_response_invalid', 502) from None
        finally:
            if owned:
                await client.aclose()

    @staticmethod
    def _response(payload):
        choice = payload['choices'][0]
        if choice.get('finish_reason') not in {'stop', 'tool_calls'}:
            raise ValueError('Incomplete generation')
        message = choice['message']
        if message.get('content') is not None and not isinstance(message['content'], str):
            raise ValueError('Unsupported provider message content')
        calls = []
        for call in message.get('tool_calls', []):
            calls.append({'id': call['id'], 'name': call['function']['name'],
                          'args': json.loads(call['function']['arguments']), 'type': 'tool_call'})
        reported = payload.get('usage', {})
        usage = None
        if all(type(reported.get(key)) is int and 0 <= reported[key] <= 1000000000
               for key in ('prompt_tokens', 'completion_tokens')):
            usage = {'input_tokens': reported['prompt_tokens'], 'output_tokens': reported['completion_tokens'],
                     'total_tokens': reported['prompt_tokens'] + reported['completion_tokens']}
        # Do not retain provider reasoning fields or raw response objects.
        result = AIMessage(content=message.get('content') or '', tool_calls=calls, usage_metadata=usage,
                           response_metadata={'finish_reason': choice['finish_reason']})
        return ChatResult(generations=[ChatGeneration(message=result)]), usage


class ScopedChatModel(BaseChatModel):
    gateway: Any = Field(exclude=True, repr=False)
    scope: Any = Field(exclude=True, repr=False)
    model_key: str

    @property
    def _llm_type(self):
        return 'kleos-scoped-model'

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        return self.bind(tools=[convert_to_openai_tool(tool) for tool in tools],
                         tool_choice='required' if tool_choice == 'any' else tool_choice)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        raise NotImplementedError('Use asynchronous model calls')

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        if set(kwargs) - {'tools', 'tool_choice', 'max_tokens', 'ls_structured_output_format'}:
            raise DomainError('Unsupported model request option', 'invalid_model_input')
        kwargs.pop('ls_structured_output_format', None)
        return await self.gateway.generate(self.scope, self.model_key, messages, stop=stop, **kwargs)
