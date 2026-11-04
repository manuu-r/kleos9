"""Simulation evidence service, proposal validation and the unchanged circuit-simulation HTTP route."""
import hashlib
import json

import httpx
import pytest
from simulation_fixtures import CIRCUIT, SIMULATION, analytic_output
from test_interface_runtime import configured_runtime
from test_interfaces import current, proposal, publish, setup  # noqa: F401  (fixture)

from kleos.artifacts.service import Artifact, ArtifactService
from kleos.artifacts.storage import BlobStore
from kleos.contracts.models import Actor, DomainError
from kleos.projects.store import Project, ProjectStore
from kleos.validation.simulation.plan import plan_simulation
from kleos.validation.simulation.service import SimulationService, current_view

BANNER = '** ngspice-39 : Circuit level simulation program'
OWNER = Actor(tenant_id='tenant', user_id='owner')
OTHER = Actor(tenant_id='tenant', user_id='intruder')


class Analytic:
    def __init__(self, plan_source):
        self.circuit, self.simulation, self.calls = plan_source
        self.plan = plan_simulation(self.circuit, self.simulation)
        self.on_run = None

    async def run(self, deck, project_id=None):
        self.calls.append(deck.sha256)
        if self.on_run:
            self.on_run()
        index = [d.sha256 for d in self.plan.decks].index(deck.sha256)
        return analytic_output(self.plan, index)[0], BANNER


@pytest.fixture
def world(tmp_path):
    projects = ProjectStore(f'sqlite:///{tmp_path / "sim.db"}')
    projects.initialize_for_tests()
    ident = projects.create(OWNER, 'Power-on simulation of the divider', 'create')['projectId']
    artifacts = ArtifactService(projects, BlobStore(tmp_path / 'files'))
    yield projects, artifacts, ident
    projects.engine.dispose()


def accept(projects, ident, circuit=CIRCUIT, simulation=SIMULATION):
    with projects.transaction() as session:
        project = session.get(Project, ident)
        project.data = {**project.data, 'interfaces': {'circuit': circuit, 'simulation': simulation}}
        project.advance_revision()


def view(projects, artifacts, ident, service):
    with projects.transaction() as session:
        return service.current(session, projects.authorized(session, OWNER, ident))


async def test_run_stores_immutable_hash_bound_evidence_and_serves_it(world):
    projects, artifacts, ident = world
    accept(projects, ident)
    executor = Analytic(({**CIRCUIT}, {**SIMULATION}, []))
    service = SimulationService(projects, artifacts, executor, allowed_versions=('ngspice-39',))
    assert await service.run(OWNER, ident) == 'available'
    served = view(projects, artifacts, ident, service)
    assert served['status'] == 'available' and served['reason'] is None
    assert served['result']['schemaVersion'] == 'circuit-waveform-v1' and served['revision'] >= 1
    with projects.transaction() as session:
        row = session.get(Artifact, served['artifactId'])
        assert row.status == 'evidence' and row.sha256 == served['artifactSha256']
        assert row.details['role'] == 'validationEvidence' and row.details['origin'] == 'independent_simulation'
        assert hashlib.sha256(artifacts.blobs.get(row.sha256)).hexdigest() == row.sha256
    # Idempotent for the same accepted binding: no second solver run, same evidence.
    runs = len(executor.calls)
    assert await service.run(OWNER, ident) == 'available' and len(executor.calls) == runs
    assert view(projects, artifacts, ident, service)['artifactId'] == served['artifactId']


async def test_authority_change_makes_old_evidence_stale_and_requires_new_evidence(world):
    projects, artifacts, ident = world
    accept(projects, ident)
    service = SimulationService(projects, artifacts, Analytic((CIRCUIT, SIMULATION, [])), allowed_versions=('ngspice-39',))
    await service.run(OWNER, ident)
    changed = {**SIMULATION, 'maxCurrentMa': 40}
    accept(projects, ident, simulation=changed)
    stale = view(projects, artifacts, ident, service)
    assert stale['status'] == 'UNKNOWN' and 'not been produced' in stale['reason'] and stale['result'] is None


async def test_authority_changing_during_the_run_fences_publication(world):
    projects, artifacts, ident = world
    accept(projects, ident)
    executor = Analytic((CIRCUIT, SIMULATION, []))
    executor.on_run = lambda: accept(projects, ident, simulation={**SIMULATION, 'maxCurrentMa': 41})
    service = SimulationService(projects, artifacts, executor, allowed_versions=('ngspice-39',))
    assert await service.run(OWNER, ident) == 'stale'
    with projects.transaction() as session:
        assert not [row for row in session.query(Artifact).filter_by(project_id=ident)]


async def test_tampered_or_replaced_evidence_is_never_served(world):
    projects, artifacts, ident = world
    accept(projects, ident)
    service = SimulationService(projects, artifacts, Analytic((CIRCUIT, SIMULATION, [])), allowed_versions=('ngspice-39',))
    await service.run(OWNER, ident)
    served = view(projects, artifacts, ident, service)
    result = json.loads(artifacts.blobs.get(served['artifactSha256']))
    result['checks'][0]['status'] = 'PASS' if result['checks'][0]['status'] != 'PASS' else 'FAIL'
    forged = json.dumps(result, sort_keys=True, separators=(',', ':')).encode()
    artifacts.blobs.put(forged)
    with projects.transaction() as session:  # point the row at forged bytes with a matching raw hash
        row = session.get(Artifact, served['artifactId'])
        row.sha256, row.size = hashlib.sha256(forged).hexdigest(), len(forged)
    broken = view(projects, artifacts, ident, service)
    assert broken['status'] == 'UNKNOWN' and 'verification' in broken['reason'] and broken['result'] is None


async def test_no_executor_missing_contract_and_unsupported_bindings_are_explicit_unknowns(world):
    projects, artifacts, ident = world
    service = SimulationService(projects, artifacts, None)
    assert view(projects, artifacts, ident, service)['reason'].startswith('No accepted simulation bindings')
    assert await service.run(OWNER, ident) == 'NOT_APPLICABLE'
    accept(projects, ident)
    assert await service.run(OWNER, ident) == 'UNKNOWN'  # no installation: nothing is invented
    assert view(projects, artifacts, ident, service)['reason'] == 'No isolated circuit simulator is installed.'
    broken = {**SIMULATION, 'loads': []}
    accept(projects, ident, simulation=broken)
    assert 'not supported' in view(projects, artifacts, ident, service)['reason']


async def test_tenant_isolation_on_reads_and_runs(world):
    projects, artifacts, ident = world
    accept(projects, ident)
    service = SimulationService(projects, artifacts, Analytic((CIRCUIT, SIMULATION, [])), allowed_versions=('ngspice-39',))
    with pytest.raises(DomainError):
        await service.run(OTHER, ident)
    with projects.transaction() as session, pytest.raises(DomainError):
        current_view(session, projects.authorized(session, OTHER, ident), artifacts.blobs, installed=True)


async def test_unchanged_http_route_serves_available_and_unknown_shapes(tmp_path):
    upstream = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(500)))
    runtime = configured_runtime(tmp_path, upstream)
    executor = Analytic((CIRCUIT, SIMULATION, []))
    runtime.simulation = SimulationService(runtime.projects, runtime.artifacts, executor, allowed_versions=('ngspice-39',))
    runtime.previews.simulation = runtime.simulation
    async with upstream, runtime.start(), httpx.AsyncClient(
            transport=httpx.ASGITransport(app=runtime.app), base_url='http://local') as client:
        created = await client.post('/api/v1/commands', headers={'Idempotency-Key': 'create'},
            json={'type': 'project.create', 'payload': {'brief': 'Power-on check', 'autoStart': False}})
        ident = created.json()['projectId']
        actor = runtime.access.authenticate({}, {}, 'GET')
        route = f'/api/v1/projects/{ident}/circuit-simulation'
        before = (await client.get(route)).json()
        assert before['status'] == 'UNKNOWN' and before['result'] is None and before['reason']
        accept(runtime.projects, ident)
        assert await runtime.simulation.run(actor, ident) == 'available'
        after = (await client.get(route)).json()
        assert after['status'] == 'available' and after['result']['evidenceKind'] == 'digital'
        assert after['result']['physicalAcceptance'] is False and after['revision'] >= 1
        content = await client.get(f"/api/v1/artifacts/{after['artifactId']}/content")
        assert content.status_code == 200 and hashlib.sha256(content.content).hexdigest() == after['artifactSha256']


def simulation_proposal(**changes):
    value = proposal()
    value['interfaces']['simulation'] = {'supplyVoltageV': 3.3, 'maxCurrentMa': 20,
        'source': {'componentId': 'J1', 'positivePin': '1', 'negativePin': '2'},
        'loads': [{'id': 'load', 'minVoltageV': 3.0, 'maxVoltageV': 3.6, 'currentMa': 3.3,
                   'probe': {'componentId': 'R1', 'positivePin': '1', 'negativePin': '2'}}],
        'components': [{'componentId': 'R1', 'kind': 'R', 'pins': ['1', '2']}], **changes}
    return value


def test_valid_simulation_bindings_are_reviewed_and_accepted_only_by_explicit_approval(setup):  # noqa: F811
    service, actor, ident = setup
    publish(setup, simulation_proposal())
    assert 'simulation' not in current(*setup).data['interfaces']  # a candidate, never self-approved
    row = current(*setup)
    from kleos.api.interfaces import InterfaceFacade
    facade = InterfaceFacade(service)
    facade.command(actor, {'type': 'project.message', 'payload': {'projectId': ident, 'text': 'approve interfaces'},
                           'expectedRevision': row.revision}, 'approve')
    accepted = current(*setup).data['interfaces']
    assert accepted['simulation']['supplyVoltageV'] == 3.3 and accepted['circuit']['references'][1]['reference'] == 'R1'
    assert plan_simulation(accepted['circuit'], accepted['simulation']).ready


@pytest.mark.parametrize('kind', ['unbound_probe', 'unclassified_part', 'no_circuit'])
def test_simulation_bindings_that_cannot_compile_are_rejected_at_proposal(setup, kind):  # noqa: F811
    service, _, ident = setup
    candidate = simulation_proposal()
    if kind == 'unbound_probe':
        candidate['interfaces']['simulation']['loads'][0]['probe']['componentId'] = 'J1'
    elif kind == 'unclassified_part':
        candidate['interfaces']['circuit']['references'].append(
            {'reference': 'R2', 'value': '2k', 'footprint': 'Resistor:R_0603', 'pins': {'1': 'VCC', '2': 'GND'}})
    else:
        del candidate['interfaces']['circuit']
    from test_interfaces import queue, result
    _, scope = queue(setup)
    with pytest.raises(DomainError) as error:
        service.publish(scope, result(candidate))
    assert error.value.code in {'interface_mapping_invalid', 'invalid_request'} and ident
