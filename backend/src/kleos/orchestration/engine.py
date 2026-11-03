"""Dynamic objective graph. All effects pass through an authoritative scoped repository."""
import asyncio
import hashlib
from datetime import UTC, datetime
from typing import Any, Awaitable, Callable, Protocol, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from pydantic import Field

from kleos.contracts.models import (
    Actor,
    ArtifactRef,
    DomainError,
    ProjectContext,
    TaskResult,
    TaskSpec,
    TaskStatus,
    WireModel,
)
from kleos.orchestration.observability import model_task


class RunScope(WireModel):
    actor: Actor
    project_id: str
    run_id: str
    revision: int = Field(ge=1)
    deadline_at: datetime
    max_plan_calls: int = Field(default=8, ge=1, le=32)
    max_tasks: int = Field(default=32, ge=1, le=128)
    compute_budget_seconds: int | None = Field(default=None, ge=1, le=7200)

    @property
    def thread_id(self):
        return hashlib.sha256(f'{self.actor.tenant_id}/{self.project_id}/{self.run_id}'.encode()).hexdigest()


class PlanDecision(WireModel):
    tasks: list[TaskSpec] = Field(default_factory=list, max_length=32)
    supersedes: dict[str, str] = Field(default_factory=dict, max_length=32)
    question: str | None = Field(default=None, max_length=4000)
    done: bool = False
    summary: str = Field(default='', max_length=4000)


class Repository(Protocol):
    async def context(self, scope: RunScope) -> ProjectContext: ...
    async def assert_active(self, scope: RunScope) -> None: ...
    async def run_once(self, scope: RunScope, key: str,
                       operation: Callable[[], Awaitable[dict]]) -> dict: ...
    async def publish(self, scope: RunScope, result: TaskResult) -> None: ...
    async def publish_plan(self, scope: RunScope, *, tasks: list[TaskSpec], superseded: dict[str, str],
                           turn: int, summary: str, question: str | None) -> None: ...
    async def resolve_artifacts(self, scope: RunScope, artifact_ids: list[str]) -> list[ArtifactRef]: ...
    async def select_dependency_sources(self, scope, task, references, context_refs, dependencies): ...


class Planner(Protocol):
    async def plan(self, scope: RunScope, objective: str, context: ProjectContext,
                   tasks: list[TaskSpec], results: list[TaskResult], answer: Any, *,
                   superseded: dict[str, str], artifact_context: dict | None = None) -> PlanDecision: ...


class Specialist(Protocol):
    async def execute(self, scope: RunScope, task: TaskSpec, context: ProjectContext) -> TaskResult: ...


class GraphState(TypedDict, total=False):
    scope: dict
    objective: str
    tasks: dict[str, dict]
    results: dict[str, dict]
    turns: int
    superseded: dict[str, str]
    question: str | None
    answer: Any
    done: bool
    summary: str
    task_inputs: dict[str, dict]
    compilation_requested: bool
    prerequisite: dict | None
    prerequisite_payload: dict | None


class ObjectiveEngine:
    def __init__(self, repository: Repository, planner: Planner, specialist: Specialist, *,
                 checkpointer, store=None, concurrency=4, external_waits=None, composition=None, prerequisites=None):
        self.repository, self.planner, self.specialist = repository, planner, specialist
        self.external_waits = external_waits
        self.composition = composition
        self.prerequisites = prerequisites
        self.concurrency = max(1, min(concurrency, 8))
        graph = StateGraph(GraphState)
        graph.add_node('plan', self._plan)
        graph.add_node('execute', self._execute)
        graph.add_node('wait', self._wait)
        graph.add_node('external_wait', self._external_wait)
        graph.add_node('compose', self._compose)
        graph.add_node('prerequisite_prepare', self._prerequisite_prepare)
        graph.add_node('prerequisite_wait', self._prerequisite_wait)
        graph.add_edge(START, 'plan')
        graph.add_conditional_edges('plan', self._route,
            {'wait': 'wait', 'execute': 'execute', 'external_wait': 'external_wait', 'compose': 'compose', 'prerequisite': 'prerequisite_prepare', 'end': END})
        graph.add_conditional_edges('compose', self._route,
            {'wait': 'wait', 'execute': 'execute', 'external_wait': 'external_wait', 'end': END})
        graph.add_edge('prerequisite_prepare', 'prerequisite_wait')
        graph.add_conditional_edges('prerequisite_wait', self._route,
            {'prerequisite': 'prerequisite_prepare', 'execute': 'execute', 'wait': 'wait',
             'external_wait': 'external_wait', 'compose': 'compose', 'end': END})
        graph.add_edge('wait', 'plan')
        graph.add_conditional_edges('execute', self._after_execute,
            {'plan': 'plan', 'external_wait': 'external_wait'})
        graph.add_edge('external_wait', 'plan')
        self.graph = graph.compile(checkpointer=checkpointer, store=store)

    async def _guard(self, scope):
        if scope.deadline_at.tzinfo is None or datetime.now(UTC) >= scope.deadline_at:
            raise DomainError('Objective deadline exceeded', 'deadline_exceeded')
        await self.repository.assert_active(scope)

    async def _context(self, scope):
        await self._guard(scope)
        context = await self.repository.context(scope)
        if (context.tenant_id != scope.actor.tenant_id or context.project_id != scope.project_id
                or context.revision != scope.revision):
            raise DomainError('Project context changed or is unauthorized', 'stale_context')
        return context

    async def _plan(self, state):
        scope = RunScope.model_validate(state['scope'])
        context = await self._context(scope)
        turn = state.get('turns', 0)
        if turn >= scope.max_plan_calls:
            raise DomainError('Objective planning allowance exhausted', 'budget_exhausted')
        async def call():
            results = [TaskResult.model_validate(r) for r in state.get('results', {}).values()]
            projection = getattr(self.repository, 'planning_inputs', None)
            options = {}
            if projection is not None:
                packet = await projection(scope, results)
                results = packet['results']
                if packet['artifact_context'] is not None:
                    options['artifact_context'] = packet['artifact_context']
            result = await self.planner.plan(scope, state['objective'], context,
                [TaskSpec.model_validate(t) for t in state.get('tasks', {}).values()],
                results, state.get('answer'),
                superseded=dict(state.get('superseded', {})), **options)
            return result.model_dump(mode='json')
        decision = PlanDecision.model_validate(await self.repository.run_once(scope, f'plan:{turn}', call))
        await self._guard(scope)
        tasks = dict(state.get('tasks', {}))
        for task in decision.tasks:
            encoded = task.model_dump(mode='json')
            if task.id in tasks and tasks[task.id] != encoded:
                raise DomainError('A task identity cannot be rewritten; create a dependent repair task', 'task_conflict')
            tasks[task.id] = encoded
        results = state.get('results', {})
        superseded = dict(state.get('superseded', {}))
        for old, replacement in decision.supersedes.items():
            if old in superseded and superseded[old] != replacement:
                raise DomainError('A supersession cannot be rewritten', 'task_conflict')
            if (old not in tasks or replacement not in tasks or old == replacement
                    or results.get(old, {}).get('status') not in {'failed', 'blocked'}
                    or replacement in results):
                raise DomainError('Only failed or blocked tasks may have a new unexecuted replacement', 'invalid_plan')
            original, repair = tasks[old], tasks[replacement]
            if (original['capability'] != repair['capability']
                    or not set(repair['allowed_paths']).issubset(original['allowed_paths'])
                    or not set(original['acceptance_criteria']).issubset(repair['acceptance_criteria'])):
                raise DomainError('Repair cannot broaden edit scope or remove acceptance criteria', 'invalid_plan')
            superseded[old] = replacement
        self._validate_tasks(tasks, scope.max_tasks)
        effective = self._effective_tasks(tasks, superseded)
        self._validate_tasks(effective, scope.max_tasks)
        if decision.done and (not effective or any(k not in results or results[k]['status'] != 'succeeded' for k in effective)):
            raise DomainError('Objective cannot finish without completed task results', 'incomplete_objective')
        if decision.done and hasattr(self.repository, 'assert_complete'):
            await self.repository.assert_complete(scope,
                {key: results[key] for key in effective})
        if (not decision.done and not decision.question and not self._ready(effective, results)
                and not self._waiting_ids(effective, results)):
            raise DomainError('No runnable tasks; clarify or report the unresolved blocker', 'objective_blocked')
        task_inputs = dict(state.get('task_inputs', {}))
        ready = self._ready(effective, results)
        await self.repository.publish_plan(scope, tasks=[TaskSpec.model_validate(raw) for raw in tasks.values()],
            superseded=superseded, turn=turn, summary=decision.summary, question=decision.question)
        prerequisite = None
        if self.prerequisites is not None and not decision.question and not decision.done:
            prerequisite = await self.prerequisites.inspect(scope,
                [TaskSpec.model_validate(raw) for raw in effective.values()], results, task_inputs)
        compilation_requested = False
        if (prerequisite is None and self.composition is not None and not decision.question
                and (decision.done or any(task['capability'] == 'validation' for task in ready))):
            compilation_requested = await self.composition.needed(scope, effective, results)
        for raw in ([] if compilation_requested or prerequisite else ready):
            if raw['id'] not in task_inputs:
                task_inputs[raw['id']] = await self._prepare_inputs(scope, raw, context, results)
        return {'tasks': tasks, 'superseded': superseded, 'turns': turn + 1, 'done': decision.done, 'question': decision.question,
                'summary': decision.summary, 'answer': None, 'task_inputs': task_inputs,
                'compilation_requested': compilation_requested, 'prerequisite': prerequisite}

    async def _prerequisite_prepare(self, state):
        scope = RunScope.model_validate(state['scope'])
        await self._guard(scope)
        if self.prerequisites is None or not state.get('prerequisite'):
            raise DomainError('Interface prerequisite is not configured', 'prerequisite_conflict')
        payload = await self.prerequisites.proposal(scope, state['prerequisite'])
        await self._guard(scope)
        return {'prerequisite_payload': payload}

    async def _prerequisite_wait(self, state):
        scope = RunScope.model_validate(state['scope'])
        await self._guard(scope)
        if self.prerequisites is None or not state.get('prerequisite_payload'):
            raise DomainError('Interface prerequisite is missing', 'prerequisite_conflict')
        # Always replay the same interrupt before reading the durable decision.
        # Proposal/model work belongs to the preceding checkpointed node.
        answer = interrupt(state['prerequisite_payload'])
        await self._guard(scope)
        decision = await self.prerequisites.resolve(scope, state['prerequisite_payload'], answer)
        if decision['status'] == 'revise':
            return {'prerequisite': decision['pending'], 'prerequisite_payload': None}
        if decision['status'] != 'approved':
            raise DomainError('Interface prerequisite has no approved continuation', 'prerequisite_conflict')
        context = await self._context(scope)
        inputs = dict(state.get('task_inputs', {}))
        tasks = self._effective_tasks(state['tasks'], state.get('superseded', {}))
        for raw in self._ready(tasks, state.get('results', {})):
            inputs[raw['id']] = await self._prepare_inputs(scope, raw, context, state.get('results', {}))
        return {'prerequisite': None, 'prerequisite_payload': None, 'task_inputs': inputs, 'answer': None}

    async def _compose(self, state):
        scope = RunScope.model_validate(state['scope'])
        await self._guard(scope)
        if self.composition is None or not state.get('compilation_requested'):
            raise DomainError('Final compilation is not configured or requested', 'composition_conflict')
        await self.composition.run(scope)
        context = await self._context(scope)
        inputs = dict(state.get('task_inputs', {}))
        effective = self._effective_tasks(state['tasks'], state.get('superseded', {}))
        for raw in self._ready(effective, state.get('results', {})):
            # These tasks have not dispatched. Rebind only after the complete
            # compiled set is published, then checkpoint the exact references.
            inputs[raw['id']] = await self._prepare_inputs(scope, raw, context, state.get('results', {}))
        return {'compilation_requested': False, 'task_inputs': inputs}

    async def _prepare_inputs(self, scope, raw, context, results):
        """Freeze authorized source references before a replayable specialist begins."""
        expected, dependencies = {}, {}
        ids = list(raw['input_artifact_ids'])
        resolver = getattr(self.repository, 'dependency_artifacts', None)
        if resolver is not None and raw['depends_on']:
            dependencies = await resolver(scope, raw['depends_on'])
        for dependency in raw['depends_on']:
            if dependency not in dependencies:
                dependencies[dependency] = TaskResult.model_validate(results[dependency]).artifacts
            for artifact in dependencies[dependency]:
                if artifact.id in expected and expected[artifact.id] != artifact:
                    raise DomainError('Dependency artifacts disagree', 'artifact_conflict')
                expected[artifact.id] = artifact
                ids.append(artifact.id)
        ids = list(dict.fromkeys(ids))
        if len(ids) > 256:
            raise DomainError('Task inputs exceed the artifact bound', 'artifact_limit')
        references = await self.repository.resolve_artifacts(scope, ids) if ids else []
        if [item.id for item in references] != ids:
            raise DomainError('Task artifact resolution differs from requested inputs', 'artifact_conflict')
        for item in references:
            if item.id in expected and item != expected[item.id]:
                raise DomainError('Dependency artifact version changed', 'artifact_conflict')
        combined = {item.id: item for item in [*context.artifacts, *references]}
        paths = {}
        for item in combined.values():
            paths.setdefault(item.path, set()).add(item.id)
        shadowed = set()
        if dependencies and any(len(values) > 1 for values in paths.values()):
            selector = getattr(self.repository, 'select_dependency_sources', None)
            if selector is None:
                raise DomainError('Dependency source precedence requires authoritative provenance', 'source_conflict')
            references, shadowed = await selector(scope, TaskSpec.model_validate(raw), references,
                context.artifacts, dependencies)
            ids = [item.id for item in references]
        merged = {item.id: item for item in context.artifacts if item.id not in shadowed}
        merged.update({item.id: item for item in references})
        pinned = context.model_copy(update={'artifacts': list(merged.values())})
        # Keep exports in read context, but do not automatically stage them as
        # native build sources. An explicitly requested derived input stays exact.
        from kleos.execution.protocol import LANES
        if raw['capability'] in LANES:
            explicit = set(raw['input_artifact_ids'])
            ids = [ref.id for ref in references if not ref.path.startswith('outputs/') or ref.id in explicit]
        # Read dependencies never enlarge the task's editable source paths.
        task = TaskSpec.model_validate({**raw, 'input_artifact_ids': ids})
        return {'task': task.model_dump(mode='json'), 'context': pinned.model_dump(mode='json')}

    @staticmethod
    def _validate_tasks(tasks, limit):
        if len(tasks) > limit:
            raise DomainError('Objective task allowance exhausted', 'budget_exhausted')
        visiting, visited = set(), set()
        def visit(key):
            if key not in tasks:
                raise DomainError('Task dependency does not exist', 'invalid_plan')
            if key in visiting:
                raise DomainError('Task dependency cycle', 'invalid_plan')
            if key in visited:
                return
            visiting.add(key)
            for dependency in tasks[key]['depends_on']:
                visit(dependency)
            visiting.remove(key)
            visited.add(key)
        for key in tasks:
            visit(key)

    @staticmethod
    def _effective_tasks(tasks, superseded):
        def resolve(key):
            seen = set()
            while key in superseded:
                if key in seen:
                    raise DomainError('Supersession cycle', 'invalid_plan')
                seen.add(key)
                key = superseded[key]
            return key
        return {key: {**task, 'depends_on': [resolve(d) for d in task['depends_on']]}
                for key, task in tasks.items() if key not in superseded}

    @staticmethod
    def _ready(tasks, results):
        return [task for key, task in tasks.items() if key not in results and all(
            dependency in results and results[dependency]['status'] == 'succeeded'
            for dependency in task['depends_on'])]

    @staticmethod
    def _waiting_ids(tasks, results):
        return list(dict.fromkeys(ident for key, raw in results.items() if key in tasks
            and raw['status'] == 'waiting' for ident in TaskResult.model_validate(raw).wait_ids))

    def _route(self, state):
        if state.get('prerequisite'):
            return 'prerequisite'
        if state.get('compilation_requested'):
            return 'compose'
        if state.get('done'):
            return 'end'
        if state.get('question'):
            return 'wait'
        tasks = self._effective_tasks(state['tasks'], state.get('superseded', {}))
        return 'execute' if self._ready(tasks, state.get('results', {})) else 'external_wait'

    def _after_execute(self, state):
        tasks = self._effective_tasks(state['tasks'], state.get('superseded', {}))
        results = state.get('results', {})
        return 'external_wait' if self._waiting_ids(tasks, results) and not self._ready(tasks, results) else 'plan'

    async def _execute(self, state):
        scope = RunScope.model_validate(state['scope'])
        await self._context(scope)
        semaphore = asyncio.Semaphore(self.concurrency)
        async def work(raw):
            pinned = state.get('task_inputs', {}).get(raw['id'])
            if pinned is None:
                raise DomainError('Task inputs were not checkpointed', 'task_input_missing')
            task = TaskSpec.model_validate(pinned['task'])
            context = ProjectContext.model_validate(pinned['context'])
            if (task.id != raw['id'] or task.allowed_paths != raw['allowed_paths']
                    or context.project_id != scope.project_id or context.tenant_id != scope.actor.tenant_id
                    or context.revision != scope.revision):
                raise DomainError('Checkpointed task inputs disagree with scope', 'task_conflict')
            async with semaphore:
                await self._guard(scope)
                async def call():
                    token = model_task.set((scope.run_id, task.id))
                    try:
                        result = await self.specialist.execute(scope, task, context)
                    finally:
                        model_task.reset(token)
                    if result.task_id != task.id:
                        raise DomainError('Specialist returned a different task identity', 'task_conflict')
                    if result.status not in {TaskStatus.SUCCEEDED, TaskStatus.WAITING, TaskStatus.BLOCKED,
                                             TaskStatus.FAILED, TaskStatus.CANCELLED}:
                        raise DomainError('Specialist did not return a terminal or waiting observation', 'invalid_result')
                    return result.model_dump(mode='json')
                from kleos.capabilities.design.runner import DesignRunner
                from kleos.infrastructure.rebuild import NativeRebuildSpecialist
                from kleos.orchestration.specialist import ModelSpecialist
                from kleos.projects.interface_runner import InterfaceRunner
                # This known coordinator journals every external action separately. Reentry
                # rebuilds its local history from those receipts; an unknown receipt blocks.
                # Other specialist implementations retain the conservative outer IO receipt.
                result = TaskResult.model_validate(await call() if type(self.specialist) in {
                    ModelSpecialist, DesignRunner, NativeRebuildSpecialist, InterfaceRunner}
                    else await self.repository.run_once(scope, f'task:{task.id}', call))
                if result.status == TaskStatus.WAITING and (self.external_waits is None or not result.wait_ids):
                    raise DomainError('Waiting tasks require bound external jobs', 'wait_scope')
                if result.status != TaskStatus.WAITING and result.wait_ids:
                    raise DomainError('Only waiting tasks can carry external waits', 'wait_scope')
                await self._guard(scope)
                await self.repository.publish(scope, result)
                return task.id, result.model_dump(mode='json')
        ready = self._ready(self._effective_tasks(state['tasks'], state.get('superseded', {})),
                            state.get('results', {}))
        completed = await asyncio.gather(*(work(task) for task in ready))
        return {'results': {**state.get('results', {}), **dict(completed)}}

    async def _external_wait(self, state):
        scope = RunScope.model_validate(state['scope'])
        await self._guard(scope)
        tasks = self._effective_tasks(state['tasks'], state.get('superseded', {}))
        ids = self._waiting_ids(tasks, state.get('results', {}))
        if self.external_waits is None or not ids:
            raise DomainError('External waiting is not configured or bound', 'wait_scope')
        await self.external_waits.validate(scope, ids)
        answer = interrupt({'kind': 'external_wait', 'waitIds': ids, 'runId': scope.run_id})
        await self._guard(scope)
        resolved = await self.external_waits.resolve(scope, ids, answer)
        results = dict(state['results'])
        for result in resolved:
            prior = TaskResult.model_validate(results[result.task_id])
            if prior.status != TaskStatus.WAITING or result.status != TaskStatus.BLOCKED:
                raise DomainError('External events cannot approve task results', 'wait_scope')
            results[result.task_id] = result.model_dump(mode='json')
        return {'results': results, 'answer': None}

    async def _wait(self, state):
        scope = RunScope.model_validate(state['scope'])
        await self._guard(scope)
        answer = interrupt({'question': state['question'], 'runId': scope.run_id})
        await self._guard(scope)
        return {'answer': answer, 'question': None}

    @staticmethod
    def _config(scope):
        return {'configurable': {'thread_id': scope.thread_id}, 'recursion_limit': 128}

    async def start(self, scope: RunScope, objective: str):
        await self._guard(scope)
        config = self._config(scope)
        existing = await self.graph.aget_state(config)
        if existing.values:
            raise DomainError('Run already started; use resume', 'run_exists')
        return await self.graph.ainvoke({'scope': scope.model_dump(mode='json'), 'objective': objective,
            'tasks': {}, 'results': {}, 'turns': 0, 'done': False}, config, durability='sync')

    async def resume(self, scope: RunScope, answer=None):
        await self._guard(scope)
        config = self._config(scope)
        existing = await self.graph.aget_state(config)
        if not existing.values:
            raise DomainError('Run is missing or its scope changed', 'run_conflict')
        previous = RunScope.model_validate(existing.values['scope'])
        if previous != scope:
            if self.prerequisites is None:
                raise DomainError('Run is missing or its scope changed', 'run_conflict')
            if await self.prerequisites.validate_resume(previous, scope, answer, existing.interrupts) is not True:
                raise DomainError('Scope amendment was not verified', 'prerequisite_conflict')
            command = Command(update={'scope': scope.model_dump(mode='json')}, resume=answer)
        else:
            command = Command(resume=answer) if answer is not None else None
        return await self.graph.ainvoke(command, config, durability='sync')
