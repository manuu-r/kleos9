from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import test_validation_tools

from kleos.contracts.models import DomainError
from kleos.validation.policies import (
    LANE_CHECKS,
    POLICY_LANES,
    include_candidate,
    register_digital_policies,
    select_policy,
)
from kleos.validation.service import CheckerConfiguration, CheckerRegistry

cad_fixture = test_validation_tools.fixture


def row(lane, name, role='output'):
    return SimpleNamespace(lane=lane, name=name, details={'role': role})


def registry(*lanes):
    reg = CheckerRegistry()
    for lane in lanes:
        key = LANE_CHECKS[lane]
        reg.register(CheckerConfiguration(key=key, version='1', installation_hash='a'*64,
                                         qualified=True), AsyncMock())
    register_digital_policies(reg)
    return reg


def test_coverage_selects_complete_mixed_policy_not_cad_subset():
    rows = [row('cad', 'outputs/base.step'), row('pcb', 'outputs/routed.kicad_pcb')]
    with pytest.raises(DomainError, match='policy'):
        select_policy(registry('cad'), rows)
    reg = registry('cad', 'pcb')
    assert select_policy(reg, rows) == 'digital.cad-pcb'
    assert len(reg.snapshot('digital.cad-pcb')['policy']['required_checks']) == 2
    reg.revoke('pcb.board-rules')
    with pytest.raises(DomainError, match='unavailable'):
        select_policy(reg, rows)


@pytest.mark.parametrize('lane', ['firmware', 'application', 'unknown'])
def test_unqualified_lane_cannot_be_silently_discarded(lane):
    with pytest.raises(DomainError):
        select_policy(registry('cad'), [row('cad', 'outputs/base.step'), row(lane, 'outputs/file')])


def test_each_lane_requires_real_covered_output():
    with pytest.raises(DomainError, match='covered digital outputs'):
        select_policy(registry('pcb'), [row('pcb', 'outputs/erc.json')])
    assert select_policy(registry('cad'), [row('cad', 'model.py', 'source'),
                                         row('cad', 'outputs/base.step')]) == 'cad.export-integrity'


def test_only_scoped_outputs_enter_releases():
    for name in ['outputs/erc.json', 'outputs/drc.json', 'outputs/routing.json',
                 'outputs/pcb-project.zip', 'outputs/gerbers/board-F_Cu.gbr']:
        assert not include_candidate('pcb.board-rules', row('pcb', name))
    assert include_candidate('pcb.board-rules', row('pcb', 'outputs/routed.kicad_pcb'))
    assert include_candidate('pcb.board-rules', row('pcb', 'circuit.kicad_sch', 'source'))
    assert not include_candidate('cad.export-integrity', row('pcb', 'outputs/routed.kicad_pcb'))


async def test_existing_verify_command_uses_all_lanes_and_pins_complete_policy(tmp_path):
    from datetime import UTC, datetime, timedelta

    from kleos.api.engineering import EngineeringFacade
    from kleos.artifacts.service import Artifact, ArtifactService
    from kleos.artifacts.storage import BlobStore
    from kleos.contracts.models import Actor, TaskSpec
    from kleos.orchestration.engine import RunScope
    from kleos.projects.graph_repository import GraphRepository
    from kleos.projects.store import Objective, ProjectStore
    from kleos.projects.verification import VerificationJob, VerificationService
    from kleos.validation.service import CheckReport

    store = ProjectStore(f'sqlite:///{tmp_path}/coverage.db')
    store.initialize_for_tests()
    actor = Actor(tenant_id='tenant', user_id='owner')
    created = store.create(actor, 'Mixed digital candidates', 'create')
    submitted = store.submit(actor, created['projectId'], 'Build', 1, 'build')
    scope = RunScope(actor=actor, project_id=created['projectId'], run_id=submitted['runId'],
                     revision=submitted['revision'], deadline_at=datetime.now(UTC)+timedelta(minutes=5))
    tasks = [TaskSpec(id=lane, capability=lane, objective=lane, allowed_paths=['model.py'] if lane == 'cad' else [])
             for lane in ('cad', 'pcb')]
    repository = GraphRepository(store)
    await repository.publish_plan(scope, tasks=tasks, superseded={}, turn=0, summary='Build', question=None)
    artifacts = ArtifactService(store, BlobStore(tmp_path/'blobs'))
    cad = await artifacts.publish_native(scope, tasks[0], [
        {'path': 'model.py', 'role': 'source', 'content': b'fixture only', 'expectedSha256': None},
        {'path': 'outputs/base.step', 'role': 'output', 'content': b'STEP fixture'},
        {'path': 'outputs/base.svg', 'role': 'output', 'content': b'preview fixture'}],
        {'nativeJobId': 'cad-job', 'imageDigest': 'a'*64, 'requestHash': 'b'*64})
    pcb = await artifacts.publish_native(scope, tasks[1], [
        {'path': 'outputs/routed.kicad_pcb', 'role': 'output', 'content': b'PCB fixture'},
        {'path': 'outputs/drc.json', 'role': 'output', 'content': b'generator report'}],
        {'nativeJobId': 'pcb-job', 'imageDigest': 'c'*64, 'requestHash': 'd'*64})
    with store.transaction() as session:
        # Deliberately distinct producer closures, as independent parallel outputs have.
        run = session.get(Objective, scope.run_id)
        run.status = 'succeeded'
        run.data = {**run.data, 'results': {lane: {'artifacts': [ref.model_dump(mode='json') for ref in refs]}
                                         for lane, refs in [('cad', cad), ('pcb', pcb)]}}
    request = {'type': 'project.verify', 'expectedRevision': scope.revision,
               'payload': {'projectId': scope.project_id, 'runId': scope.run_id}}
    verify = VerificationService(store, registry('cad'))
    facade = EngineeringFacade(None, verification=verify)
    with pytest.raises(DomainError):
        facade.command(actor, request, 'unavailable')
    with store.transaction() as session:
        from sqlalchemy import select
        assert list(session.scalars(select(VerificationJob))) == []
    reg = registry('cad', 'pcb')
    # Install deterministic fixture callbacks; no image or native claim in this test.
    for key in ('cad.export-integrity', 'pcb.board-rules'):
        async def check(scope, subject, key=key):
            return CheckReport(subject_hash=subject.subject_hash, status='PASS', execution_id='check-'+key,
                               summary='Fixture evidence')
        reg.checker(key).invoke.side_effect = check
    verify = VerificationService(store, reg)
    facade = EngineeringFacade(None, verification=verify)
    receipt = facade.command(actor, request, 'complete-coverage')
    assert receipt['policyKey'] == 'digital.cad-pcb'
    with store.transaction() as session:
        job = session.get(VerificationJob, receipt['verificationId'])
        assert {ref['id'] for ref in job.data['artifacts']} == {cad[0].id, cad[1].id, pcb[0].id}
        assert len(job.data['policy']['checkers']) == 2
    assert await verify.process_next()
    result = verify.get(actor, scope.project_id, receipt['verificationId'])
    assert result['status'] == 'blocked' and result['blocker'] == 'source_closure_conflict'
    # The rejection preserves candidates rather than silently merging producer closures.
    with store.transaction() as session:
        assert session.get(Artifact, pcb[0].id).status == 'candidate'
    store.engine.dispose()


@pytest.mark.parametrize('late_publication', [False, True])
async def test_agent_subset_cannot_accept_around_other_published_lanes(tmp_path, late_publication):
    from datetime import UTC, datetime, timedelta

    from kleos.artifacts.service import ArtifactService
    from kleos.artifacts.storage import BlobStore
    from kleos.contracts.models import Actor, TaskSpec
    from kleos.orchestration.engine import RunScope
    from kleos.projects.graph_repository import GraphRepository
    from kleos.projects.store import Objective, ProjectStore
    from kleos.validation.service import CheckReport, ValidationService

    store = ProjectStore(f'sqlite:///{tmp_path}/subset.db')
    store.initialize_for_tests()
    actor = Actor(tenant_id='tenant', user_id='owner')
    created = store.create(actor, 'Mixed objective', 'create')
    submitted = store.submit(actor, created['projectId'], 'Build', 1, 'build')
    scope = RunScope(actor=actor, project_id=created['projectId'], run_id=submitted['runId'],
                     revision=submitted['revision'], deadline_at=datetime.now(UTC)+timedelta(minutes=5))
    tasks = [TaskSpec(id='cad', capability='cad', objective='CAD', allowed_paths=['model.py']),
             TaskSpec(id='pcb', capability='pcb', objective='PCB')]
    repo = GraphRepository(store)
    await repo.publish_plan(scope, tasks=tasks, superseded={}, turn=0, summary='Build', question=None)
    files = ArtifactService(store, BlobStore(tmp_path/'blobs'))
    cad = await files.publish_native(scope, tasks[0], [
        {'path': 'model.py', 'role': 'source', 'content': b'fixture', 'expectedSha256': None},
        {'path': 'outputs/base.step', 'role': 'output', 'content': b'fixture'}],
        {'nativeJobId': 'cad-generator', 'imageDigest': 'a'*64, 'requestHash': 'b'*64})
    pcb = await files.publish_native(scope, tasks[1], [
        {'path': 'outputs/routed.kicad_pcb', 'role': 'output', 'content': b'fixture'}],
        {'nativeJobId': 'pcb-generator', 'imageDigest': 'c'*64, 'requestHash': 'd'*64})
    def publish(include_pcb=False, finish=False):
        with store.transaction() as session:
            run = session.get(Objective, scope.run_id)
            results = {'cad': {'artifacts': [ref.model_dump(mode='json') for ref in cad]}}
            if include_pcb:
                results['pcb'] = {'artifacts': [ref.model_dump(mode='json') for ref in pcb]}
            run.data = {**run.data, 'results': results}
            if finish:
                run.status = 'succeeded'
    reg = registry('cad', 'pcb')
    async def check(scope, subject):
        return CheckReport(subject_hash=subject.subject_hash, status='PASS', execution_id='checker', summary='Fixture')
    reg.checker('cad.export-integrity').invoke.side_effect = check
    service = ValidationService(store, reg)
    publish(include_pcb=not late_publication)
    if late_publication:
        candidate = service.prepare(scope, [ref.id for ref in cad], 'cad.export-integrity', 'prepare')
        await service.run(scope, candidate)
        publish(include_pcb=True, finish=True)
        with pytest.raises(DomainError, match='omits a current candidate lane'):
            service.accept(actor, scope.project_id, candidate, scope.revision, 'accept')
    else:
        with pytest.raises(DomainError, match='omits a current candidate lane'):
            service.prepare(scope, [ref.id for ref in cad], 'cad.export-integrity', 'prepare')
    store.engine.dispose()


async def test_validation_carried_refs_do_not_break_verify_or_resurrect_replaced_parts(cad_fixture):
    from kleos.contracts.models import TaskResult, TaskSpec
    from kleos.projects.graph_repository import GraphRepository
    from kleos.projects.store import Objective
    from kleos.projects.verification import current_candidates
    from kleos.validation.policies import assert_run_coverage

    store, scope, artifacts, refs, _ = cad_fixture
    repository = GraphRepository(store)
    validation = TaskSpec(id='validation', objective='Check', capability='validation',
                          input_artifact_ids=[ref.id for ref in refs[:3]])
    replacement = TaskSpec(id='replacement', objective='Repair', capability='cad', allowed_paths=['model.py'])
    with store.transaction() as session:
        run = session.get(Objective, scope.run_id)
        run.data = {**run.data, 'tasks': [*run.data['tasks'], validation.model_dump(mode='json'),
                                        replacement.model_dump(mode='json')]}
    await repository.publish(scope, TaskResult(task_id='validation', status='succeeded', summary='Scoped check', artifacts=refs[:3]))
    with store.transaction() as session:
        run = session.get(Objective, scope.run_id)
        assert {item['id'] for item in current_candidates(session, run, 'cad.export-integrity')} == {ref.id for ref in refs[:3]}
    repaired = await artifacts.publish_native(scope, replacement, [
        {'path': 'model.py', 'role': 'source', 'content': b'repaired source', 'expectedSha256': None},
        {'path': 'outputs/base.step', 'role': 'output', 'content': b'repaired STEP'},
        {'path': 'outputs/base.stl', 'role': 'output', 'content': b'repaired STL'}],
        {'nativeJobId': 'repair-job', 'imageDigest': 'a'*64, 'requestHash': 'b'*64})
    await repository.publish(scope, TaskResult(task_id='replacement', status='succeeded', summary='Repair', artifacts=repaired))
    with store.transaction() as session:
        run = session.get(Objective, scope.run_id)
        run.data = {**run.data, 'superseded': {'generator': {'replacement': 'replacement'}}}
        assert {item['id'] for item in current_candidates(session, run, 'cad.export-integrity')} == {ref.id for ref in repaired}
        assert_run_coverage(registry('cad'), session, run, 'cad.export-integrity', [ref.id for ref in repaired])


def test_all_four_lane_combinations_require_every_complete_export_set():
    reg = registry(*LANE_CHECKS)
    assert len(POLICY_LANES) == 15
    all_rows = {'cad': [row('cad', 'outputs/base.step')],
                'pcb': [row('pcb', 'outputs/routed.kicad_pcb')],
                'firmware': [row('firmware', 'outputs/firmware.elf'), row('firmware', 'outputs/firmware.bin')],
                'application': [row('application', 'outputs/companion-web.zip')]}
    for key, lanes in POLICY_LANES.items():
        rows = [item for lane in lanes for item in all_rows[lane]]
        assert select_policy(reg, rows) == key
        assert len(reg.snapshot(key)['checkers']) == len(lanes)
        if 'firmware' in lanes:
            for missing in ('outputs/firmware.elf', 'outputs/firmware.bin'):
                with pytest.raises(DomainError, match='covered digital outputs'):
                    select_policy(reg, [item for item in rows if item.name != missing])
    reg.revoke('application.web-bundle-integrity')
    with pytest.raises(DomainError):
        select_policy(reg, [item for rows in all_rows.values() for item in rows])
