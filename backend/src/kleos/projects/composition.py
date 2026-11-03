"""Same-run immutable source grants; no execution, scheduling or release authority."""
import copy
import json
import re

from sqlalchemy import select

from kleos.artifacts.service import Artifact, NativePublication
from kleos.contracts.models import ArtifactRef, DomainError, TaskResult, TaskSpec
from kleos.execution.gateway import NativeAction, NativeJob, original_duration
from kleos.execution.protocol import LANES, MAX_SOURCES, PROTOCOL, source_path
from kleos.projects.graph_repository import resolve_dependencies
from kleos.projects.store import fingerprint
from kleos.validation.policies import authored_native_rows, complete_outputs, covered_output

KEY = 'sourceComposition'
HISTORY = 'sourceCompositions'
PROTOCOL_NAME = 'kleos-source-composition-v1'


def fail(message, code='composition_conflict'):
    raise DomainError(message, code)


def reference(row):
    return ArtifactRef(id=row.id, path=row.name, sha256=row.sha256, size=row.size,
        revision=row.revision, media_type=row.media_type).model_dump(mode='json', by_alias=True)


def authoring_plan(run):
    plans = [TaskSpec.model_validate(value) for value in run.data.get('tasks', [])]
    superseded = run.data.get('superseded', {})
    known = {task.id for task in plans}
    active = {task.id: task.model_copy(update={'depends_on': resolve_dependencies(known, superseded, task.depends_on)})
              for task in plans if task.id not in superseded and task.capability.value in LANES}
    return {'tasks': [task.model_dump(mode='json') for _, task in sorted(active.items())],
        'authoringTasks': [task.model_dump(mode='json') for task in plans if task.capability.value in LANES],
        'superseded': {key: value for key, value in superseded.items()
                      if any(task.id == key and task.capability.value in LANES for task in plans)}}


def source_successors(session, run, rows):
    """Exact declared read-before-write chains, with no unordered source overlay."""
    plans = {value['id']: TaskSpec.model_validate(value) for value in run.data.get('tasks', [])}
    groups = {}
    for row in rows:
        if row.details.get('role') == 'source':
            groups.setdefault(row.name, {})[row.id] = row
    winners, lineages = {}, {}
    for path, versions in groups.items():
        parents = {}
        for ident, row in versions.items():
            task = plans.get(row.details.get('taskId'))
            job = session.get(NativeJob, row.details.get('nativeJobId'))
            if task is None or job is None or path not in task.allowed_paths:
                fail('Source ownership is outside its producer task')
            reads = [value for value in job.request.get('sources', []) if value.get('path') == path]
            if len(reads) > 1:
                fail('Source ownership has ambiguous read ancestry')
            if reads and reads[0].get('artifactId') in versions:
                parent = versions[reads[0]['artifactId']]
                dependencies = resolve_dependencies(plans, run.data.get('superseded', {}), task.depends_on)
                if (parent.id == ident or parent.lane != row.lane or reads[0].get('sha256') != parent.sha256
                        or parent.details.get('taskId') not in dependencies):
                    fail('Source ownership successor needs an exact declared same-lane dependency')
                parents[ident] = parent.id
        def lineage(ident):
            result = [ident]
            while ident in parents:
                ident = parents[ident]
                if ident in result:
                    fail('Source ownership ancestry contains a cycle')
                result.append(ident)
            return list(reversed(result))
        leaves = [ident for ident in versions if set(lineage(ident)) == set(versions)]
        if len(leaves) != 1:
            fail('Competing source ownership needs one exact successor chain')
        winners[path], lineages[path] = versions[leaves[0]], lineage(leaves[0])
    return winners, lineages


def compilation_targets(authors, rows, winners, lineages):
    """Retire a target only when one successor owns all its sources and exports."""
    source_rows = {row.id: row for row in rows if row.details.get('role') == 'source'}
    owned = {author['taskId']: [row for row in source_rows.values() if row.details['taskId'] == author['taskId']]
             for author in authors}
    replaced = {}
    for author in authors:
        paths = owned[author['taskId']]
        successors = {winners[row.name].details['taskId'] for row in paths}
        if paths and author['taskId'] not in successors and len(successors) == 1:
            successor = next(value for value in authors if value['taskId'] == next(iter(successors)))
            old_exports = {ref['path'] for ref in author['coveredOutputs']}
            new_exports = {ref['path'] for ref in successor['coveredOutputs']}
            if (author['lane'] == successor['lane'] and old_exports == new_exports
                    and all(row.id in lineages[row.name][:-1] for row in paths)):
                replaced[author['taskId']] = successor['taskId']
    targets = [value for value in authors if value['taskId'] not in replaced]
    output_owners = set()
    for target in targets:
        for ref in target['coveredOutputs']:
            key = target['lane'], ref['path']
            if key in output_owners:
                fail('Producer output ownership is ambiguous or only partially replaced')
            output_owners.add(key)
    return targets, replaced


class CompositionService:
    """Trusted worker admission only. The gateway is used for read-only history checks.

    Compilation must later revalidate this grant before every dispatch/publication.
    Its targets may build/render/finish only; source edits, model calls and release
    acceptance are never granted. Changed authoring receives a new bounded grant;
    old grants are retained and can never be edited or retargeted.
    """
    def __init__(self, repository, artifacts, gateway, *, max_seconds_per_target=600):
        if type(max_seconds_per_target) is not int or not 1 <= max_seconds_per_target <= 600:
            raise ValueError('Composition duration bound is invalid')
        self.repository, self.artifacts, self.gateway = repository, artifacts, gateway
        self.max_seconds = max_seconds_per_target

    def freeze(self, scope):
        with self.repository.store.transaction() as session:
            project, run = self.repository._check(session, scope)
            history, prior = self._history(run)
            if prior is not None and prior.get('scopeHash') != fingerprint(scope.model_dump(mode='json')):
                fail('Composition cannot replace its original scope or budget', 'composition_scope_conflict')
            bound = prior['maxSecondsPerTarget'] if prior else self.max_seconds
            snapshot = self._snapshot(session, project, run, scope, bound)
            ident = fingerprint(snapshot)
            if prior is not None and prior['id'] == ident:
                self._match(prior, snapshot)
                return copy.deepcopy(prior)
            if len(history) >= scope.max_plan_calls:
                fail('Source composition grant allowance is exhausted', 'composition_budget_exhausted')
            grant = {**snapshot, 'id': ident, 'snapshotHash': ident}
            run.data = {**run.data, KEY: grant, HISTORY: {**history, ident: grant}}
            self.repository.store._emit(session, project, 'sources.composition_frozen', {
                'runId': run.id, 'compositionId': ident, 'sourceClosureHash': snapshot['sourceClosureHash'],
                'targetCount': len(snapshot['targets']), 'modelCallsAllowed': 0, 'accepted': False})
            return copy.deepcopy(grant)

    def validate(self, scope, grant_id):
        with self.repository.store.transaction() as session:
            return self.validate_in_session(session, scope, grant_id)

    def validate_in_session(self, session, scope, grant_id):
        """Revalidate inside a caller's Project-first publication transaction."""
        project, run = self.repository._check(session, scope)
        _, prior = self._history(run)
        if not isinstance(prior, dict) or prior.get('id') != grant_id:
            fail('Source composition grant is missing or changed')
        self._match(prior, self._snapshot(session, project, run, scope, prior['maxSecondsPerTarget']))
        return copy.deepcopy(prior)

    @staticmethod
    def _history(run):
        history, current = run.data.get(HISTORY, {}), run.data.get(KEY)
        if not isinstance(history, dict) or len(history) > 64:
            fail('Composition history is invalid')
        for ident, grant in history.items():
            if not isinstance(grant, dict):
                fail('Composition history is invalid')
            snapshot = {key: value for key, value in grant.items() if key not in {'id', 'snapshotHash'}}
            if (ident != fingerprint(snapshot) or grant.get('id') != ident or grant.get('snapshotHash') != ident
                    or type(grant.get('maxSecondsPerTarget')) is not int
                    or not 1 <= grant['maxSecondsPerTarget'] <= 600):
                fail('Immutable composition history changed')
        if current is not None and (not isinstance(current, dict) or history.get(current.get('id')) != current):
            fail('Current composition pointer differs from its immutable history')
        if history and current is None:
            fail('Current composition pointer is missing')
        return history, current

    @staticmethod
    def _match(prior, snapshot):
        checksum = fingerprint(snapshot)
        if prior != {**snapshot, 'id': checksum, 'snapshotHash': checksum}:
            fail('Frozen composition no longer matches its original source and producer authority')

    def _snapshot(self, session, project, run, scope, max_seconds):
        plans = [TaskSpec.model_validate(value) for value in run.data.get('tasks', [])]
        if len(plans) > scope.max_tasks or len({task.id for task in plans}) != len(plans):
            fail('Composition plan exceeds its pinned task bound')
        superseded = run.data.get('superseded', {})
        planned = {task.id: task for task in plans}
        tasks = {task.id: task.model_copy(update={'depends_on': resolve_dependencies(planned, superseded, task.depends_on)})
            for task in plans if task.id not in superseded and task.capability.value in LANES}
        if not tasks:
            fail('Composition needs published native producers', 'composition_incomplete')
        results = run.data.get('results', {})
        for task in tasks.values():
            result = TaskResult.model_validate(results[task.id]) if task.id in results else None
            if result is None or result.task_id != task.id or result.status != 'succeeded' or result.blockers:
                fail('Every current native task must finish before composition', 'composition_incomplete')
        rows = authored_native_rows(session, run)
        grouped = {task_id: [] for task_id in tasks}
        for row in rows:
            task_id = row.details.get('taskId')
            if task_id not in tasks or row.lane != tasks[task_id].capability.value:
                fail('Published native producer is outside the current plan')
            grouped[task_id].append(row)
        baseline = dict(project.data.get('sourceManifest', {}))
        closure = dict(baseline)
        sources, targets = {}, []
        for path, ident in baseline.items():
            source_path(path)
            row = session.get(Artifact, ident)
            if (row is None or row.project_id != project.id or row.name != path
                    or row.details.get('role') == 'output' or row.details.get('nativeInput') is False):
                fail('Authoritative source baseline is invalid')
            sources[path] = row
        repairs = self.repository._repair_refs(session, project, run)
        if repairs and run.data.get('repairBaseline') != baseline:
            fail('Pinned repair baseline differs from current source authority')
        for ident, ref in repairs.items():
            row = session.get(Artifact, ident)
            job = session.get(NativeJob, row.details.get('nativeJobId'))
            publication = session.scalar(select(NativePublication).where(
                NativePublication.run_id == run.data.get('repairSourceRunId'),
                NativePublication.task_id == row.details.get('taskId'),
                NativePublication.job_id == row.details.get('nativeJobId')))
            if (row.details.get('role') != 'source' or row.status != 'candidate' or job is None
                    or job.project_id != project.id or job.run_id != run.data.get('repairSourceRunId')
                    or job.task_id != row.details.get('taskId') or job.status != 'finished'
                    or job.request_hash != row.details.get('requestHash')
                    or fingerprint(job.request) != job.request_hash
                    or job.request.get('imageDigest') != row.details.get('imageDigest')
                    or job.source_manifest.get(row.name) != row.sha256
                    or publication is None
                    or ref not in [ArtifactRef.model_validate(value) for value in publication.references]):
                fail('Retained repair source lacks exact producer provenance')
            self.gateway.rebuild_snapshot(session, scope.actor, job)
            source_path(row.name)
            sources[row.name], closure[row.name] = row, row.id
        for task_id, task in sorted(tasks.items()):
            produced = grouped[task_id]
            if not produced or not complete_outputs(task.capability.value, produced):
                fail('A producer is missing its covered lane outputs', 'composition_incomplete')
            job_ids = {row.details.get('nativeJobId') for row in produced}
            if len(job_ids) != 1:
                fail('A producer task must identify one exact native job')
            job = session.get(NativeJob, next(iter(job_ids)))
            if (job is None or job.project_id != project.id or job.run_id != run.id or job.task_id != task_id
                    or job.status != 'finished' or job.active_action is not None
                    or job.request_hash != fingerprint(job.request) or job.request.get('protocol') != PROTOCOL
                    or job.request.get('revision') != scope.revision or job.request.get('lane') != task.capability.value
                    or not re.fullmatch(r'sha256:[a-f0-9]{64}', job.request.get('imageDigest', ''))):
                fail('Native producer history is incomplete or inconsistent', 'composition_producer_invalid')
            # Includes exact source bytes, closed action history and observed termination.
            snapshot = self.gateway.rebuild_snapshot(session, scope.actor, job)
            if (snapshot['sourceRequestHash'] != job.request_hash or snapshot['sourceManifest'] != job.source_manifest
                    or snapshot['originalRequest'] != job.request):
                fail('Native producer snapshot differs from its history')
            if task.capability.value == 'firmware':
                target = project.data.get('interfaces', {}).get('firmware')
                if (not target or job.request.get('firmwareTarget') != target
                        or job.request.get('firmwareTargetHash') != fingerprint(target)
                        or job.request.get('board') != target.get('board')):
                    fail('Firmware producer differs from current accepted target')
            if task.capability.value == 'pcb':
                from kleos.execution.pcb_contract import circuit_contract
                from kleos.projects.interfaces import accepted_circuit_identity
                identity = accepted_circuit_identity(session, project)
                circuit = circuit_contract(project.data.get('interfaces', {}).get('circuit'), identity['peripheralConceptIds'])
                if (job.request.get('acceptedCircuitIdentity') != identity or job.request.get('circuitContract') != circuit
                        or job.request.get('circuitContractHash') != fingerprint(circuit)):
                    fail('PCB producer differs from current accepted circuit')
            inputs = job.request.get('sources', [])
            input_ids = [item.get('artifactId') for item in inputs]
            if len(input_ids) != len(set(input_ids)) or any(not ident for ident in input_ids):
                fail('Native input identities are missing or ambiguous')
            resolved_task = task.model_copy(update={'input_artifact_ids': input_ids})
            if (fingerprint(resolved_task.model_dump(mode='json')) != job.task_hash
                    or set(job.request.get('allowedPaths', [])) != set(task.allowed_paths)):
                fail('Native producer task differs from its authorized plan')
            permitted = set(task.input_artifact_ids)
            for dependency in task.depends_on:
                if dependency not in results or TaskResult.model_validate(results[dependency]).status != 'succeeded':
                    fail('Native producer dependency is incomplete')
                permitted.update(ref.id for ref in TaskResult.model_validate(results[dependency]).artifacts)
            for item in inputs:
                source = session.get(Artifact, item['artifactId'])
                if source is not None and source.details.get('role') == 'output':
                    fail('Derived output inputs need an explicit compilation dependency contract', 'composition_derived_input_unsupported')
                if (source is None or source.project_id != project.id or source.id not in permitted
                        or source.name != item.get('path') or source.sha256 != item.get('sha256')):
                    fail('Native producer input provenance changed')
            publication = session.scalar(select(NativePublication).where(NativePublication.run_id == run.id,
                NativePublication.task_id == task_id, NativePublication.job_id == job.id))
            if (publication is None or {ref.id for ref in map(ArtifactRef.model_validate, publication.references)}
                    != {row.id for row in produced}):
                fail('Native publication is incomplete or differs from its producer result')
            exported = []
            for row in produced:
                if (row.details.get('requestHash') != job.request_hash
                        or row.details.get('imageDigest') != job.request['imageDigest']
                        or ArtifactRef.model_validate(reference(row)) not in [ArtifactRef.model_validate(value)
                                                                               for value in publication.references]):
                    fail('Candidate differs from its native publication')
                if row.details.get('role') == 'source':
                    path = source_path(row.name)
                    if (path not in task.allowed_paths
                            or job.source_manifest.get(path) != row.sha256
                            or row.details.get('sourceClosure', {}).get(path) != row.id
                            or job.request.get('baselines', {}).get(path) !=
                               (session.get(Artifact, baseline[path]).sha256 if path in baseline else None)):
                        fail('Candidate source ownership is ambiguous or outside its original edit scope')
                elif row.details.get('role') == 'output' and covered_output(row.lane, row.name):
                    exported.append(reference(row))
            # An observed finish receipt must carry these exact publication refs.
            actions = list(session.scalars(select(NativeAction).where(NativeAction.job_id == job.id)))
            if not any(action.status == 'completed' and action.result.get('artifacts')
                    and {value['id'] for value in action.result['artifacts']} == {row.id for row in produced}
                    and any(value.get('name') == 'native.finish' and value.get('details', {}).get('executionSucceeded') is True
                            for value in action.result.get('observations', [])) for action in actions):
                fail('Producer has no completed finish publication receipt')
            actions_allowed = ['build', 'render', 'finish'] if task.capability.value == 'cad' else ['build', 'finish']
            if job.request.get('maxActions', 0) < len(actions_allowed):
                fail('Original producer action allowance cannot support compilation')
            contract = {key: copy.deepcopy(value) for key, value in job.request.items()
                        if key not in {'sources', 'deadline'}}
            targets.append({'taskId': task_id, 'jobId': job.id, 'lane': task.capability.value,
                'requestHash': job.request_hash, 'taskHash': job.task_hash,
                'sourceManifestHash': fingerprint(job.source_manifest), 'publicationHash': publication.fingerprint,
                'contract': contract, 'coveredOutputs': sorted(exported, key=lambda value: value['path']),
                'actions': actions_allowed, 'maxActions': len(actions_allowed),
                'maxSeconds': min(max_seconds, original_duration(job.request))})
        winners, lineages = source_successors(session, run, rows)
        sources.update(winners)
        closure.update({path: row.id for path, row in winners.items()})
        authors = targets
        targets, replaced = compilation_targets(authors, rows, winners, lineages)
        if not sources or len(sources) > 50 or sum(row.size for row in sources.values()) > MAX_SOURCES:
            fail('Composed native source closure exceeds its existing bounds', 'artifact_limit')
        for row in sources.values():
            if len(self.artifacts.blobs.get(row.sha256)) != row.size:
                fail('Composed source byte length changed')
        refs = [reference(row) for _, row in sorted(sources.items())]
        authority = {key: copy.deepcopy(project.data.get(key)) for key in (
            'requirements', 'interfaces', 'acceptedInterfaces', 'acceptedCircuitIdentity', 'approvedDesign')}
        value = {'schemaVersion': 1, 'protocol': PROTOCOL_NAME, 'projectId': project.id, 'runId': run.id,
            'actor': scope.actor.model_dump(mode='json', by_alias=True), 'revision': scope.revision,
            'deadlineAt': scope.deadline_at.isoformat(), 'computeBudgetSeconds': scope.compute_budget_seconds,
            'scopeHash': fingerprint(scope.model_dump(mode='json')),
            'runtimePinsHash': fingerprint(run.data.get('runtimePins')),
            'toolPolicySnapshot': copy.deepcopy(run.data.get('toolPolicySnapshot', {})),
            'baseline': baseline, 'closure': closure, 'sources': refs, 'sourceClosureHash': fingerprint(refs),
            'repairSourceRunId': run.data.get('repairSourceRunId'),
            'repairSources': [ref.model_dump(mode='json', by_alias=True) for ref in repairs.values()],
            'authority': authority, 'planHash': fingerprint(authoring_plan(run)), 'authoringPlan': authoring_plan(run),
            'maxSecondsPerTarget': max_seconds,
            'authors': authors, 'targets': targets, 'sourceLineage': lineages, 'replacedTargets': replaced,
            'modelCallsAllowed': 0, 'retrievalCallsAllowed': 0,
            'sourceEditsAllowed': False, 'releaseAcceptanceAllowed': False}
        if len(json.dumps(value, allow_nan=False).encode()) > 512 * 1024:
            fail('Composition grant exceeds its metadata bound', 'artifact_limit')
        return value
