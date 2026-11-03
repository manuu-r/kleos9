"""Host-only interface approval bridge for the existing objective graph.

This adapter has no approval authority or workflow storage. The project service
owns request identities and explicit user decisions; LangGraph owns the wait.
Proposal inference uses the existing scoped receipt and model budget.
"""
from kleos.contracts.models import DomainError, TaskSpec
from kleos.orchestration.observability import model_task
from kleos.projects.interface_runner import InterfaceRunner


class _BoundRequest:
    def __init__(self, service, request_id):
        self.service, self.request_id = service, request_id

    def request(self, scope):
        return self.service.request(scope, self.request_id)


class InterfacePrerequisites:
    """A server-composed adapter, never registered as a model-invokable tool.

    ``model_resolver(scope, context)`` must return the original attempt's pinned
    coordination/vision model. Runtime supplies that resolver; this module never
    reads current model settings or chooses another provider. Each request round
    has one deterministic inference receipt, including across worker restarts.
    """
    def __init__(self, repository, service, model_resolver, image_loader=None, *,
                 output_tokens=8192, max_context_chars=60000):
        self.repository, self.service = repository, service
        self.model_resolver, self.image_loader = model_resolver, image_loader
        self.output_tokens, self.max_context_chars = output_tokens, max_context_chars

    async def inspect(self, scope, tasks, results=None, task_inputs=None):
        # Called after the authoritative plan is published, before preparing any
        # native inputs. The service checks that the whole run is quiescent.
        return self.service.inspect(scope, tasks, results, task_inputs)

    async def proposal(self, scope, pending):
        request_id = pending.get('requestId') if isinstance(pending, dict) else None
        if (not isinstance(request_id, str) or not 1 <= len(request_id) <= 80
                or pending.get('runId') != scope.run_id):
            raise DomainError('Prerequisite request identity is invalid', 'prerequisite_conflict')
        # Resolve the request from authoritative storage before model selection;
        # checkpoint/resume data cannot supply a replacement model prompt.
        self.service.request(scope, request_id)
        context = await self.repository.context(scope)
        if (context.tenant_id != scope.actor.tenant_id or context.project_id != scope.project_id
                or context.revision != scope.revision):
            raise DomainError('Prerequisite context changed', 'stale_context')
        model = self.model_resolver(scope, context)
        runner = InterfaceRunner(model, self.repository, _BoundRequest(self.service, request_id),
            self.image_loader, output_tokens=self.output_tokens, max_context_chars=self.max_context_chars)
        task = TaskSpec(id='prerequisite:' + request_id, capability='research',
            objective='Propose only the missing shared interfaces for the existing objective.', max_actions=1)
        token = model_task.set((scope.run_id, task.id))
        try:
            result = await runner.execute(scope, task, context)
        finally:
            model_task.reset(token)
        # This is a candidate publication only. It is deliberately absent from
        # ordinary specialist task results and cannot count as task completion.
        return self.service.publish(scope, request_id, result)

    async def resolve(self, scope, payload, resume_value):
        # The service looks up the exact user-command receipt. No interpretation
        # of an "approve" string, model observation or arbitrary resume payload.
        return self.service.resolve(scope, payload, resume_value)

    async def validate_resume(self, old_scope, new_scope, answer, interrupts):
        # The old scope is intentionally no longer current after approval. Only
        # the service's canonical, revision-only amendment may bridge this fence.
        return self.service.validate_resume(old_scope, new_scope, answer, interrupts)
