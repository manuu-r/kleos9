"""Opt-in: real isolated container (KLEOS_TEST_SIM_IMAGE=sha256:<image id>, local Docker daemon).

Build: docker build -f src/kleos/validation/simulation/Dockerfile src/kleos/validation/simulation
(see the Dockerfile for the optional restricted-network build inputs).
"""
import asyncio
import math
import os
import subprocess

import pytest
from simulation_fixtures import RC_CIRCUIT, RC_SIMULATION, ready_plan, variant
from test_simulation_results import viewer_parse

from kleos.execution.docker import DockerClient
from kleos.validation.simulation.plan import Deck
from kleos.validation.simulation.process import PROTOCOL, IsolatedNgspice
from kleos.validation.simulation.run import ExecutorUnavailable, simulate

IMAGE = os.environ.get('KLEOS_TEST_SIM_IMAGE')
pytestmark = pytest.mark.skipif(not IMAGE, reason='Set KLEOS_TEST_SIM_IMAGE to the built simulation image ID')


def leftovers():
    out = subprocess.run(['docker', 'ps', '-a', '--filter', 'label=kleos.checker.protocol=' + PROTOCOL, '-q'],
                         capture_output=True, text=True, check=True).stdout
    return out.split()


async def with_executor(tmp_path, action):
    docker = DockerClient()
    try:
        return await action(IsolatedNgspice(docker, IMAGE, tmp_path / 'control'))
    finally:
        await docker.client.aclose()


def test_real_container_runs_every_corner_and_matches_the_closed_form_response(tmp_path):
    circuit, simulation = variant(RC_CIRCUIT, RC_SIMULATION)
    plan = ready_plan(circuit, simulation)

    async def action(executor):
        return await simulate(plan, executor)
    outcome = asyncio.run(with_executor(tmp_path, action))
    assert outcome.status == 'available', outcome.reason
    assert outcome.result['toolVersion'] == 'ngspice-39' and len(outcome.result['decks']) == 9
    viewer_parse(outcome.result)  # the unchanged Flutter viewer's own acceptance rules
    rails = next(c for c in outcome.result['checks'] if c['name'] == 'circuit_dc_rails')['details']['measurements']
    nominal = next(m for m in rails if m['corner'] == [0, 0, 0])
    assert nominal['voltageV'] == pytest.approx(2.2, rel=1e-6)
    load = next(s for s in outcome.result['waveform']['signals'] if s['name'] == 'load')
    tau = (1000 * 2000 / 3000) * 100e-9
    assert load['values'][2] == pytest.approx(2.2 * (1 - math.exp(-4 * 100e-6 / tau)), abs=.02)
    assert load['values'][100] == pytest.approx(2.2, abs=.01)
    assert not leftovers() and not list((tmp_path / 'control').iterdir())


def test_real_container_led_circuit_is_reproducible_and_leaves_no_container(tmp_path):
    plan = ready_plan()

    async def action(executor):
        return [await simulate(plan, executor) for _ in range(2)]
    first, second = asyncio.run(with_executor(tmp_path, action))
    assert first.status == second.status == 'available'
    assert [d['outputSha256'] for d in first.result['decks']] == [d['outputSha256'] for d in second.result['decks']]
    assert [c['status'] for c in first.result['checks']] == ['PASS'] * 4
    assert not leftovers()


def test_hostile_deck_cannot_escape_the_container_or_change_the_host(tmp_path):
    # Bypasses the compiler on purpose: even a malicious deck is confined to a read-only, networkless sandbox.
    hostile = Deck((0,), '* hostile\n.control\nshell touch /etc/pwned /opt/pwned /input/pwned\n'
                         'shell cat /etc/passwd > /tmp/work/op.txt\n.endc\n.end\n', 'f' * 64)

    async def action(executor):
        try:
            await executor.run(hostile)
        except Exception as error:  # solver exits non-zero or output is rejected: never usable evidence
            return type(error).__name__
        return 'ran'
    asyncio.run(with_executor(tmp_path, action))
    assert not os.path.exists('/etc/pwned') and not os.path.exists('/opt/pwned')
    assert not leftovers() and not list((tmp_path / 'control').iterdir())


def test_unpinned_or_missing_image_is_unavailable_not_a_pass(tmp_path):
    async def action(_):
        docker = DockerClient()
        try:
            with pytest.raises(ExecutorUnavailable):
                await IsolatedNgspice(docker, 'sha256:' + '0' * 64, tmp_path / 'c2').run(ready_plan().decks[0])
        finally:
            await docker.client.aclose()
    asyncio.run(with_executor(tmp_path, action))
    with pytest.raises(ValueError):
        IsolatedNgspice(DockerClient(), 'kleos-sim:latest', tmp_path / 'c3')


def test_configured_installation_runs_through_the_service_against_the_real_container(tmp_path):
    from pydantic import SecretStr
    from simulation_fixtures import CIRCUIT, SIMULATION

    from kleos.artifacts.service import ArtifactService
    from kleos.artifacts.storage import BlobStore
    from kleos.contracts.models import Actor
    from kleos.infrastructure.settings import Settings
    from kleos.infrastructure.validation import configure_simulation
    from kleos.projects.store import Project, ProjectStore

    settings = Settings(environment='test', database_url=f'sqlite:///{tmp_path / "s.db"}', artifact_root=tmp_path / 'a',
        session_secret=SecretStr('s' * 40), development_no_login=True, simulation_control_root=tmp_path / 'sim',
        simulation_installation={'imageDigest': IMAGE, 'qualificationReference': 'local container run'})
    projects = ProjectStore(settings.database_url)
    projects.initialize_for_tests()
    actor = Actor(tenant_id='tenant', user_id='owner')
    ident = projects.create(actor, 'Real simulation', 'create')['projectId']
    with projects.transaction() as session:
        project = session.get(Project, ident)
        project.data = {**project.data, 'interfaces': {'circuit': CIRCUIT, 'simulation': SIMULATION}}
        project.advance_revision()
    artifacts = ArtifactService(projects, BlobStore(tmp_path / 'files'))
    service, docker = configure_simulation(settings, projects, artifacts)

    async def action():
        try:
            return await service.run(actor, ident)
        finally:
            await docker.close()
    assert asyncio.run(action()) == 'available'
    with projects.transaction() as session:
        served = service.current(session, projects.authorized(session, actor, ident))
    assert served['status'] == 'available' and served['result']['toolVersion'] == 'ngspice-39'
    assert [c['status'] for c in served['result']['checks']] == ['PASS'] * 4
    assert not leftovers()
    projects.engine.dispose()
