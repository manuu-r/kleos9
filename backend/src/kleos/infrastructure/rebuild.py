"""Deterministic LangGraph native rebuild: no provider, retrieval or memory interfaces."""
from kleos.contracts.models import DomainError, TaskResult, TaskSpec
from kleos.orchestration.engine import ObjectiveEngine, PlanDecision, RunScope
from kleos.orchestration.specialist import ToolResult
from kleos.projects.graph_repository import GraphRepository
from kleos.projects.store import fingerprint


class NativeRebuildPlanner:
    def __init__(self, task):
        self.task = task

    async def plan(self, scope, objective, context, tasks, results, answer, *, superseded, artifact_context=None):
        if not tasks:
            return PlanDecision(tasks=[self.task], summary='Rebuild the exact saved sources using pinned native execution only')
        if len(results) == 1 and results[0].task_id == self.task.id and results[0].status == 'succeeded':
            return PlanDecision(done=True, summary='Exact-source native rebuild produced candidate files; independent acceptance is separate')
        raise DomainError('Native rebuild did not produce complete candidate exports', 'native_rebuild_failed')


class NativeRebuildSpecialist:
    """Trusted coordinator; NativeAction receipts alone journal each fixed external action."""
    def __init__(self, repository, gateway, snapshot, grant, task):
        self.repository, self.gateway = repository, gateway
        self.snapshot, self.grant, self.task = snapshot, grant, task

    async def execute(self, scope, task, context):
        if task != self.task:
            raise DomainError('Native rebuild task changed', 'task_conflict')
        observations, artifacts = [], []
        for action in self.grant['actions']:
            await self.repository.assert_active(scope)
            arguments = {'actionId': 'rebuild:'+action}
            if action == 'render':
                arguments['view'] = 'isometric'
            result = ToolResult.model_validate(await self.gateway.invoke_rebuild(scope, task, self.snapshot, action, arguments))
            await self.repository.assert_active(scope)
            observations.extend(result.observations)
            artifacts.extend(result.artifacts)
            if (result.blockers or not result.observations
                    or any(item.status in {'FAIL', 'ERROR'} or item.independent
                           or item.details.get('executionSucceeded') is not True for item in result.observations)):
                return TaskResult(task_id=task.id, status='blocked', summary='Exact-source native action did not complete',
                                  artifacts=artifacts, observations=observations,
                                  blockers=result.blockers or ['native_rebuild_failed'])
        if not artifacts or not any(ref.path.startswith('outputs/') for ref in artifacts):
            return TaskResult(task_id=task.id, status='blocked', summary='Native rebuild produced no candidate exports',
                              observations=observations, blockers=['native_exports_missing'])
        return TaskResult(task_id=task.id, status='succeeded', summary='Exact saved sources rebuilt with the original native contract; outputs remain candidates',
                          artifacts=artifacts, observations=observations)


def configure_native_rebuild(projects, gateway, scope, *, checkpointer, store=None):
    repository = GraphRepository(projects)
    with projects.transaction() as session:
        _, run = repository._check(session, scope)
        if run.data.get('kind') != 'project.nativeRebuild':
            raise DomainError('Objective is not a native-only rebuild', 'run_conflict')
        if RunScope.model_validate(run.data['scope']) != scope:
            raise DomainError('Native rebuild must retain its admission scope and deadline', 'scope_conflict')
        rebuild = run.data['nativeRebuild']
        snapshot, grant = rebuild['snapshot'], rebuild['grant']
        if fingerprint(snapshot) != grant['snapshotFingerprint']:
            raise DomainError('Native rebuild snapshot changed', 'native_history_missing')
        request = snapshot['originalRequest']
        expected = ['build', 'render', 'finish'] if request['lane'] == 'cad' else ['build', 'finish']
        if (grant['actions'] != expected or grant['maxActions'] != len(expected)
                or grant['modelCallsAllowed'] != 0 or grant['retrievalCallsAllowed'] != 0):
            raise DomainError('Native-only action grant is invalid', 'scope_conflict')
        task = TaskSpec(id='native-rebuild', objective=run.data['objective'], capability=request['lane'],
                        allowed_paths=request['allowedPaths'], max_actions=grant['maxActions'],
                        acceptance_criteria=['exact-source native candidate exports'])
    return ObjectiveEngine(repository, NativeRebuildPlanner(task),
                           NativeRebuildSpecialist(repository, gateway, snapshot, grant, task),
                           checkpointer=checkpointer, store=store, concurrency=1)
