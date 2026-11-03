"""Server-owned digital coverage; unavailable lanes never disappear from verification."""
from itertools import combinations
from pathlib import PurePosixPath

from kleos.artifacts.service import Artifact
from kleos.contracts.models import ArtifactRef, DomainError
from kleos.validation.service import CheckRequirement, ReleasePolicy, _ref

LANE_CHECKS = {'cad': 'cad.export-integrity', 'pcb': 'pcb.board-rules',
               'firmware': 'firmware.export-integrity', 'application': 'application.web-bundle-integrity'}
POLICY_LANES = {LANE_CHECKS[lanes[0]] if len(lanes) == 1 else 'digital.'+'-'.join(lanes): lanes
                for count in range(1, len(LANE_CHECKS)+1)
                for lanes in combinations(LANE_CHECKS, count)}
REQUIRED_OUTPUTS = {'pcb': {'outputs/routed.kicad_pcb'},
                    'firmware': {'outputs/firmware.elf', 'outputs/firmware.bin'},
                    'application': {'outputs/companion-web.zip'}}


def covered_output(lane, path):
    if lane == 'cad':
        return PurePosixPath(path).suffix.lower() in {'.step', '.stp', '.stl'}
    return path in REQUIRED_OUTPUTS.get(lane, set())


def complete_outputs(lane, rows):
    paths = {row.name for row in rows if row.lane == lane and row.details.get('role') == 'output'}
    return (any(covered_output(lane, path) for path in paths) if lane == 'cad'
            else REQUIRED_OUTPUTS.get(lane, set()) <= paths and lane in REQUIRED_OUTPUTS)


def include_candidate(policy_key, row):
    lanes = POLICY_LANES.get(policy_key)
    if lanes is None:
        raise DomainError('Digital coverage policy is unavailable', 'policy_unavailable')
    return row.lane in lanes and (row.details.get('role') == 'source' or
        (row.details.get('role') == 'output' and covered_output(row.lane, row.name)))


def register_digital_policies(registry):
    """Register only complete installed combinations; no NOT_APPLICABLE exceptions."""
    available = []
    for lane, key in LANE_CHECKS.items():
        try:
            registry.checker(key)
        except DomainError:
            continue
        available.append(lane)
    for count in range(1, len(available)+1):
        for lanes in combinations(available, count):
            key = next(key for key, value in POLICY_LANES.items() if value == lanes)
            registry.register_policy(ReleasePolicy(key=key, version='1', required_checks=tuple(
                CheckRequirement(checker_key=LANE_CHECKS[lane]) for lane in lanes)))


def select_policy(registry, rows):
    """Inspect the whole objective before filtering ancillary candidate exports."""
    lanes = {row.lane for row in rows}
    if not lanes or not lanes <= LANE_CHECKS.keys():
        raise DomainError('Independent checking is unavailable for one or more candidate lanes',
                          'verification_coverage_unavailable')
    if any(not complete_outputs(lane, rows) for lane in lanes):
        raise DomainError('Each candidate lane requires its covered digital outputs', 'verification_inputs_missing')
    key = next(key for key, value in POLICY_LANES.items() if set(value) == lanes)
    registry.snapshot(key)  # Rechecks revoked or absent installations, including composite checks.
    return key


def check_policy_artifacts(repository, scope, artifact_ids, policy_key):
    with repository.store.transaction() as session:
        project, _ = repository._check(session, scope)
        rows = [session.get(Artifact, ident) for ident in artifact_ids]
        if (not rows or len(rows) != len(set(artifact_ids)) or any(row is None or
                row.project_id != project.id or not include_candidate(policy_key, row) for row in rows)):
            raise DomainError('Artifacts are outside the independent policy coverage', 'checker_scope')
        lanes = set(POLICY_LANES[policy_key])
        if any(not complete_outputs(lane, rows) for lane in lanes):
            raise DomainError('Every policy lane requires covered outputs', 'checker_scope')


def authored_native_rows(session, run):
    """Canonical producer results; validation carry-forward refs add no new authority."""
    rows = {}
    repairs = None
    compiled = {}
    results, superseded = run.data.get('results', {}), run.data.get('superseded', {})
    for task_id, result in results.items():
        if task_id in superseded:
            continue
        for value in result.get('artifacts', []):
            ref = ArtifactRef.model_validate(value)
            row = session.get(Artifact, ref.id)
            if row is None or row.project_id != run.project_id or _ref(row) != ref:
                raise DomainError('Published candidate binding changed', 'artifact_conflict')
            if row.details.get('origin') != 'native_candidate':
                continue
            if row.details.get('runId') != run.id:
                # Explicit repair read inputs can be echoed by research/validation.
                # They are retained context, never new native producer authority.
                if repairs is None:
                    from kleos.projects.graph_repository import GraphRepository
                    from kleos.projects.store import Project
                    project = session.get(Project, run.project_id)
                    repairs = GraphRepository._repair_refs(session, project, run) if project else {}
                if repairs.get(ref.id) == ref:
                    continue
            producer = row.details.get('taskId')
            if producer in superseded:
                continue
            if (row.status != 'candidate' or row.details.get('runId') != run.id
                    or row.revision != run.revision):
                raise DomainError('Published native candidate is stale', 'artifact_scope_conflict')
            if producer != task_id:
                if row.details.get('compositionId'):
                    from kleos.projects.composition_results import compiled_carryforward
                    if compiled_carryforward(session, run, row, ref, compiled):
                        continue
                originals = results.get(producer, {}).get('artifacts', [])
                if not any(ArtifactRef.model_validate(value) == ref for value in originals):
                    raise DomainError('Native candidate has no original producer result', 'artifact_scope_conflict')
                continue
            rows[row.id] = row
    if len(rows) > 256:
        raise DomainError('Native candidate count exceeds verification bound', 'verification_inputs_missing')
    return list(rows.values())


def published_native_rows(session, run):
    """Use final compiled outputs only after the complete trusted handoff."""
    from kleos.projects.composition_results import completed_native_rows
    return completed_native_rows(session, run, authored_native_rows(session, run))


def assert_run_coverage(registry, session, run, policy_key, selected_ids):
    """A model cannot narrow a release to avoid another current native candidate lane."""
    if policy_key not in POLICY_LANES:
        return  # Explicit internal fixture/custom policies retain their own coverage contracts.
    rows = published_native_rows(session, run)
    if not rows:
        raise DomainError('Publish native candidates before independent validation', 'verification_inputs_missing')
    if select_policy(registry, rows) != policy_key:
        raise DomainError('Release policy omits a current candidate lane', 'verification_coverage_unavailable')
    required = {row.id for row in rows if row.details.get('role') == 'output'
                and include_candidate(policy_key, row)}
    if not required <= set(selected_ids):
        raise DomainError('Release selection omits current covered outputs', 'verification_coverage_unavailable')
