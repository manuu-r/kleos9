"""Deterministic final compilation inside the objective's LangGraph checkpoint."""
from kleos.contracts.models import DomainError
from kleos.execution.composition import NativeComposition
from kleos.execution.protocol import LANES
from kleos.orchestration.specialist import ToolResult
from kleos.projects.composition import CompositionService, source_successors
from kleos.projects.composition_results import CompositionResults, authoring_plan_hash
from kleos.validation.policies import authored_native_rows, published_native_rows


class FinalCompilation:
    def __init__(self, repository, artifacts, gateway):
        self.repository = repository
        self.grants = CompositionService(repository, artifacts, gateway)
        self.native = NativeComposition(gateway, self.grants)
        self.results = CompositionResults(self.grants)

    async def needed(self, scope, tasks, results):
        native = [key for key, task in tasks.items() if task['capability'] in LANES]
        if not native or any(results.get(key, {}).get('status') != 'succeeded' for key in native):
            return False
        with self.repository.store.transaction() as session:
            project, run = self.repository._check(session, scope)
            rows = authored_native_rows(session, run)
            closure = dict(project.data.get('sourceManifest', {}))
            repairs = self.repository._repair_refs(session, project, run)
            closure.update({ref.path: ref.id for ref in repairs.values() if not ref.path.startswith('outputs/')})
            winners, _ = source_successors(session, run, rows)
            closure.update({path: row.id for path, row in winners.items()})
            grant = run.data.get('sourceComposition', {})
            if (grant.get('closure') == closure and grant.get('planHash') == authoring_plan_hash(run)
                    and grant.get('id') in run.data.get('compositionResults', {})):
                # Validate the complete receipt before treating compilation as current.
                # A corrupt receipt is an error, never permission to silently rebuild.
                rows = published_native_rows(session, run)
            return any(row.details.get('role') == 'output' and row.details.get('sourceClosure') != closure for row in rows)

    async def run(self, scope):
        await self.repository.assert_active(scope)
        # Terminal producer cleanup is independent of generation and may finish
        # the proof required for an exact snapshot. It starts no new native work.
        await self.native.gateway.reconcile()
        grant = self.grants.freeze(scope)
        lanes = [target['lane'] for target in grant['targets']]
        if len(lanes) != len(set(lanes)):
            raise DomainError('Multiple producers in one lane need separately qualified build targets',
                              'composition_target_unqualified')
        completed = {}
        for target in grant['targets']:
            for action in target['actions']:
                await self.repository.assert_active(scope)
                result = ToolResult.model_validate(await self.native.invoke(scope, grant['id'], target['taskId'], action))
                if (result.blockers or not result.observations or any(
                        item.independent or item.status in {'FAIL', 'ERROR'}
                        or item.details.get('executionSucceeded') is not True for item in result.observations)):
                    raise DomainError('Final compilation did not produce a successful fixed action', 'composition_failed')
                if action == 'finish':
                    completed[target['taskId']] = result
        await self.repository.assert_active(scope)
        return self.results.record(scope, grant['id'], completed)
