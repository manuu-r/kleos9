"""Aggregate admission boundary; no native generated code or live provider calls."""
import multiprocessing
from dataclasses import replace

import pytest
from sqlalchemy import select
from test_postgres_runtime import database  # noqa: F401 - shared opt-in disposable PostgreSQL fixture

from kleos.contracts.models import DomainError
from kleos.execution.capacity import CapacityLimits, CapacityPool, CapacityReservation, CapacityService
from kleos.execution.docker import Resources
from kleos.infrastructure.schema import metadata
from kleos.projects.store import ProjectStore

HASH = 'a' * 64
GIB = 1024**3


@pytest.fixture
def capacity(tmp_path):
    metadata()
    store = ProjectStore(f'sqlite:///{tmp_path}/capacity.db')
    store.initialize_for_tests()
    yield CapacityService(store)
    store.engine.dispose()


def claim(service, key='native:first', *, memory=4*GIB, cpus=2, scratch=2*GIB):
    return service.reserve(key, 'project', HASH, {'cpus': cpus, 'memoryBytes': memory, 'scratchBytes': scratch})


def free(service, key='native:first'):
    return service.release(key, HASH, {'kind': 'container-removed', 'identity': key})


def test_aggregate_native_slicer_checker_usage_and_verified_release(capacity):
    assert claim(capacity)
    assert not claim(capacity, 'slicer:busy')
    assert claim(capacity, 'checker:fits', memory=2*GIB, scratch=GIB//4)
    assert capacity.usage() == {'poolId': 'local-docker', 'limits': CapacityLimits().wire(), 'jobs': 2,
                                'cpus': 4, 'memoryBytes': 6*GIB, 'scratchBytes': 2*GIB+GIB//4}
    assert not claim(capacity, 'native:another', memory=GIB, cpus=1, scratch=GIB//4)
    assert free(capacity) and free(capacity)
    assert claim(capacity, 'slicer:busy')
    assert capacity.usage()['jobs'] == 2


def test_mcp_shares_native_limits_and_requires_matching_cleanup(capacity):
    assert claim(capacity, 'mcp:inspection')
    assert not claim(capacity, 'native:busy')
    assert claim(capacity, 'checker:fits', memory=2*GIB, scratch=GIB//4)
    assert not claim(capacity, 'slicer:busy', memory=GIB, cpus=1, scratch=GIB//4)
    with pytest.raises(DomainError, match='identity changed'):
        capacity.release('mcp:inspection', 'b'*64,
            {'kind': 'container-removed', 'identity': 'mcp:inspection'})
    assert capacity.usage()['jobs'] == 2
    assert free(capacity, 'mcp:inspection')
    assert claim(capacity, 'native:busy')


@pytest.mark.parametrize(('limit','resource'), [('cpus','cpus'), ('memory_bytes','memoryBytes'),
                                               ('scratch_bytes','scratchBytes')])
def test_each_resource_is_an_independent_aggregate_bound(capacity, limit, resource):
    limits = replace(CapacityLimits(), **{limit: {'cpus': 3, 'memory_bytes': 5*GIB,
                                                'scratch_bytes': 3*GIB}[limit]})
    service = CapacityService(capacity.projects, limits)
    assert claim(service)
    assert not claim(service, 'slicer:second')
    oversized = Resources().wire()
    oversized[resource] = limits.wire()[resource] + 1
    with pytest.raises(DomainError, match='exceeds'):
        service.reserve('checker:oversized', 'project', HASH, oversized)


def test_reservation_replay_retains_original_contract_and_never_expires(capacity):
    assert claim(capacity)
    restored = CapacityService(capacity.projects, clock=lambda: 10**12)
    assert claim(restored)
    assert restored.usage()['jobs'] == 1
    assert not claim(restored, 'slicer:later')
    with pytest.raises(DomainError, match='another contract'):
        claim(restored, memory=2*GIB)
    with pytest.raises(DomainError, match='another contract'):
        restored.reserve('native:first', 'other-project', HASH, {'cpus': 2, 'memoryBytes': 4*GIB, 'scratchBytes': 2*GIB})
    assert free(restored)
    with pytest.raises(DomainError, match='already closed'):
        claim(restored)
    with restored.projects.transaction() as session:
        row = session.get(CapacityReservation, 'native:first')
        assert row.reserved_at < row.released_at and row.resources['memoryBytes'] == 4*GIB


def test_mismatched_pool_limits_and_unproven_release_fail_closed(capacity):
    assert claim(capacity)
    changed = CapacityService(capacity.projects, replace(capacity.limits, memory_bytes=8*GIB))
    with pytest.raises(DomainError, match='differs across workers'):
        claim(changed, 'checker:changed')
    for proof in ({}, {'kind': 'timeout', 'identity': 'native:first'}, {'kind': 'container-removed', 'identity': ''}):
        with pytest.raises(DomainError, match='verified container-removal'):
            capacity.release('native:first', HASH, proof)
    assert capacity.usage()['jobs'] == 1
    with pytest.raises(DomainError, match='identity changed'):
        capacity.release('native:first', 'b'*64, {'kind': 'container-removed', 'identity': 'native:first'})


def test_reservation_can_join_and_rollback_dispatch_transaction(capacity):
    with pytest.raises(RuntimeError, match='rollback'):
        with capacity.projects.transaction() as session:
            assert capacity.reserve('slicer:atomic', 'project', HASH, Resources().wire(), session=session)
            raise RuntimeError('rollback')
    assert capacity.usage()['jobs'] == 0
    with capacity.projects.transaction() as session:
        assert session.get(CapacityReservation, 'slicer:atomic') is None


def test_job_count_and_removal_identity_are_independent_limits(capacity):
    small = {'cpus': 1, 'memoryBytes': GIB, 'scratchBytes': GIB//4}
    assert capacity.reserve('checker:one', 'project', HASH, small, details={'containerName': 'owned-one'})
    assert capacity.reserve('checker:two', 'project', HASH, small)
    assert not capacity.reserve('checker:three', 'project', HASH, small)
    with pytest.raises(DomainError, match='another container'):
        capacity.release('checker:one', HASH, {'kind':'container-removed','identity':'wrong-name'})
    assert capacity.usage()['jobs'] == 2


def test_qualified_web_footprint_fits_and_cannot_share_memory(capacity):
    from kleos.execution.gateway import capacity_resources

    original = {'resources': Resources(memory_bytes=6*GIB, scratch_bytes=4*GIB, pids=256).wire()}
    footprint = capacity_resources(original)
    assert footprint['scratchBytes'] == 4*GIB + 128*1024**2
    assert original['resources']['scratchBytes'] == 4*GIB
    assert capacity.reserve('native:web', 'project', HASH, footprint)
    assert not claim(capacity, 'checker:small', memory=GIB, cpus=1, scratch=GIB//4)


def test_rollout_refuses_unreserved_legacy_native_compute(capacity):
    from kleos.execution.gateway import NativeAction, NativeJob

    with capacity.projects.transaction() as session:
        session.add(NativeJob(id='old', project_id='project', run_id='old-run', task_id='old-task', task_hash=HASH,
            request_hash=HASH, request={'resources': Resources().wire()}, status='finished', source_manifest={}, active_action=None))
    with pytest.raises(DomainError, match='Reconcile existing native'):
        claim(capacity)
    with capacity.projects.transaction() as session:
        session.add(NativeAction(job_id='old', action_id='_cleanup', request_hash=HASH, status='completed',
            result={'kind':'cleanup-proof', 'jobId':'old', 'containerRemoved': True}))
    assert claim(capacity)


def _parallel_reserve(url, key, barrier, result):
    store = ProjectStore(url)
    try:
        barrier.wait(timeout=10)
        result.put((key, claim(CapacityService(store), key)))
    except Exception as error:
        result.put((key, type(error).__name__))  # Never expose connection credentials in diagnostics.
    finally:
        store.engine.dispose()


def test_postgres_crossprocess_admission_serializes_initial_pool_and_competing_jobs(database):  # noqa: F811 - pytest fixture
    store, _ = database
    context = multiprocessing.get_context('spawn')
    barrier, result = context.Barrier(2), context.Queue()
    url = store.engine.url.render_as_string(hide_password=False)
    children = [context.Process(target=_parallel_reserve, args=(url, 'native:'+name, barrier, result))
                for name in ('one', 'two')]
    try:
        for child in children:
            child.start()
        answers = [result.get(timeout=20) for _ in children]
        for child in children:
            child.join(timeout=10)
            assert child.exitcode == 0
        assert sorted(value for _, value in answers) == [False, True]
        service = CapacityService(store)
        assert service.usage()['jobs'] == 1
        winner = next(key for key, success in answers if success)
        assert free(service, winner)
        loser = next(key for key, success in answers if not success)
        assert claim(service, loser)
        with store.transaction() as session:
            assert len(list(session.scalars(select(CapacityPool)))) == 1
    finally:
        for child in children:
            if child.is_alive():
                child.terminate()
            child.join(timeout=5)
