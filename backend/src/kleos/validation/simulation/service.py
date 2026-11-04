"""Advisory simulation evidence: compile accepted contracts, run through an isolated executor, store.

Evidence is an immutable artifact (`status=evidence`, outside every candidate/release file set) bound to
the exact accepted circuit/simulation hashes and engineering revision. Reads never start compute.
"""
import hashlib
import json
from datetime import UTC, datetime
from uuid import uuid4

from kleos.artifacts.service import Artifact
from kleos.contracts.models import DomainError
from kleos.projects.store import fingerprint
from kleos.validation.simulation.plan import plan_simulation
from kleos.validation.simulation.results import DEFAULT_VERSIONS
from kleos.validation.simulation.run import simulate

KIND = 'circuitSimulation'
MAX_EVIDENCE_BYTES = 4 * 1024 * 1024
NO_CONTRACT = 'No accepted simulation bindings exist for this project.'


def authority(project):
    interfaces = project.data.get('interfaces', {})
    return interfaces.get('circuit'), interfaces.get('simulation')


def binding_hash(circuit, simulation):
    return fingerprint({'circuit': circuit, 'simulation': simulation, 'policy': 'ngspice-ideal-v1'})


def _header(project):
    return {'revision': project.revision, 'engineeringRevision': project.context_revision}


def _unavailable(project, reason):
    return {**_header(project), 'status': 'UNKNOWN', 'artifactId': None, 'artifactSha256': None,
            'result': None, 'reason': reason}


def find_evidence(session, project, bound):
    from sqlalchemy import select
    rows = [row for row in session.scalars(select(Artifact).where(Artifact.project_id == project.id,
            Artifact.status == 'evidence', Artifact.revision == project.context_revision))
            if row.details.get('kind') == KIND and row.details.get('bindingHash') == bound]
    return max(rows, key=lambda row: (row.details.get('createdAt', ''), row.id), default=None)


def current_view(session, project, blobs, *, installed):
    """Authorized, side-effect-free read of the current evidence, verifying every stored hash."""
    circuit, simulation = authority(project)
    if simulation is None:
        return _unavailable(project, NO_CONTRACT)
    plan = plan_simulation(circuit or {}, simulation)
    if not plan.ready:
        return _unavailable(project, 'Simulation is not supported for these bindings: ' + ' '.join(plan.reasons))
    row = find_evidence(session, project, binding_hash(circuit, simulation))
    if row is None:
        return _unavailable(project, 'Simulation evidence has not been produced for the accepted contract.'
                            if installed else 'No isolated circuit simulator is installed.')
    try:
        raw = blobs.get(row.sha256)
        result = json.loads(raw)
        valid = (hashlib.sha256(raw).hexdigest() == row.sha256 and len(raw) == row.size
                 and result.get('resultsSha256') == row.details.get('resultsSha256')
                 and result['resultsSha256'] == fingerprint({k: v for k, v in result.items() if k != 'resultsSha256'})
                 and result['metadata']['circuitSha256'] == plan.metadata['circuitSha256']
                 and result['metadata']['simulationSha256'] == plan.metadata['simulationSha256'])
    except (DomainError, ValueError, KeyError, TypeError):
        valid = False
    if not valid:
        return _unavailable(project, 'Stored simulation evidence failed hash or binding verification.')
    return {**_header(project), 'status': 'available', 'artifactId': row.id, 'artifactSha256': row.sha256,
            'result': result, 'reason': None}


class SimulationService:
    def __init__(self, projects, artifacts, executor=None, *, allowed_versions=DEFAULT_VERSIONS):
        self.projects, self.artifacts, self.executor = projects, artifacts, executor
        self.allowed_versions = allowed_versions

    @property
    def installed(self):
        return self.executor is not None

    def current(self, session, project):
        return current_view(session, project, self.artifacts.blobs, installed=self.installed)

    async def run(self, actor, project_id):
        """Idempotent for one accepted binding. Returns the outcome status; never accepts a release."""
        with self.projects.transaction() as session:
            project = self.projects.authorized(session, actor, project_id)
            circuit, simulation = authority(project)
            revision = project.context_revision
            if simulation is None:
                return 'NOT_APPLICABLE'
            bound = binding_hash(circuit, simulation)
            if find_evidence(session, project, bound) is not None:
                return 'available'
        plan = plan_simulation(circuit or {}, simulation)
        outcome = await simulate(plan, self.executor, allowed_versions=self.allowed_versions, project_id=project_id)
        with self.projects.transaction() as session:
            project = self.projects.authorized(session, actor, project_id, write=True)
            # Fence: the accepted authority must be unchanged while the solver ran.
            if project.context_revision != revision or binding_hash(*authority(project)) != bound:
                return 'stale'
            if outcome.status != 'available':
                self.projects._emit(session, project, 'simulation.unavailable',
                    {'status': outcome.status, 'reason': outcome.reason[:500]})
                return outcome.status
            raw = json.dumps(outcome.result, sort_keys=True, separators=(',', ':')).encode()
            if len(raw) > MAX_EVIDENCE_BYTES:
                self.projects._emit(session, project, 'simulation.unavailable',
                    {'status': 'ERROR', 'reason': 'Evidence exceeds its size bound'})
                return 'ERROR'
            self.artifacts.blobs.put(raw)
            row = Artifact(id=str(uuid4()), project_id=project.id, revision=revision,
                name='evidence/circuit-simulation.json', sha256=hashlib.sha256(raw).hexdigest(), size=len(raw),
                media_type='application/json', status='evidence', lane='pcb', details={
                    'role': 'validationEvidence', 'kind': KIND, 'origin': 'independent_simulation',
                    'bindingHash': bound, 'engineeringRevision': revision,
                    'circuitSha256': outcome.result['metadata']['circuitSha256'],
                    'simulationSha256': outcome.result['metadata']['simulationSha256'],
                    'resultsSha256': outcome.result['resultsSha256'], 'toolVersion': outcome.result['toolVersion'],
                    'createdAt': datetime.now(UTC).isoformat()})
            session.add(row)
            self.projects._emit(session, project, 'simulation.completed',
                {'artifactId': row.id, 'artifactSha256': row.sha256,
                 'checkStatuses': {c['name']: c['status'] for c in outcome.result['checks']}})
            return 'available'
