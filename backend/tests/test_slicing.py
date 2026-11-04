"""Preparation authority, geometry and native protocol; no hardware operations."""
import base64
import io
import json
import struct
import zipfile

import pytest

from kleos.artifacts.service import Artifact
from kleos.artifacts.storage import BlobStore
from kleos.capabilities.fabrication.native_slicer import mesh
from kleos.capabilities.fabrication.slicing import (
    BUNDLES,
    PROTOCOL,
    PrintPreparation,
    PrintPreparations,
    SlicingSettings,
    digest,
    validate_package,
)
from kleos.contracts.models import Actor, DomainError
from kleos.projects.store import Project, ProjectStore
from kleos.validation.service import Release

ACTOR = Actor(tenant_id='tenant', user_id='owner')


def account_authority(service):
    from kleos.access.service import Account, OrganizationMember
    with service.projects.transaction() as session:
        session.add(Account(id=ACTOR.user_id, email='slicing@example.invalid', password_hash='fixture'))
        session.add(OrganizationMember(tenant_id=ACTOR.tenant_id, user_id=ACTOR.user_id, role='admin'))
    service.projects.require_account_authority = True


def revoke_preparation_authority(service, project, change):
    from kleos.access.service import Account, OrganizationMember
    from kleos.projects.store import Membership
    with service.projects.transaction() as session:
        if change == 'account':
            session.delete(session.get(Account, ACTOR.user_id))
        elif change == 'project':
            session.delete(session.get(Membership, (project, ACTOR.user_id)))
        else:
            member = session.get(OrganizationMember, (ACTOR.tenant_id, ACTOR.user_id))
            if change == 'organization':
                session.delete(member)
            else:
                member.role = change


def cube(size=20):
    points = [(0, 0, 0), (size, 0, 0), (size, size, 0), (0, size, 0),
              (0, 0, size), (size, 0, size), (size, size, size), (0, size, size)]
    faces = [(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4),
             (1, 2, 6), (1, 6, 5), (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7)]
    return ('\n'.join('v %s %s %s' % point for point in points) + '\n' +
            '\n'.join('f %s %s %s' % tuple(i + 1 for i in face) for face in faces)).encode()


def package(**members):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as archive:
        for name, value in {'3D/3dmodel.model': '<model/>', 'Metadata/plate_1.gcode': 'G1 X2 Y2 E1\n', **members}.items():
            archive.writestr(name, value)
    return output.getvalue()


class Driver:
    def __init__(self):
        self.started, self.results, self.cleaned = [], {}, []
    async def start(self, job, source, extension, profile):
        self.started.append((job, source, extension, profile))
        raw = package()
        self.results[job] = {'protocol': PROTOCOL, 'jobId': job, 'sourceSha256': digest(source),
            'bundleSha256': profile['bundleSha256'], 'image': profile['image'], 'status': 'ready', 'package': base64.b64encode(raw).decode(),
            'preparedSha256': digest(raw), 'measurements': {'watertight': True}}
    async def result(self, job):
        return self.results.get(job)
    async def cleanup(self, job, *, acknowledged=False, observed_id=None):
        self.cleaned.append(job)


@pytest.fixture
def prepared(tmp_path):
    store = ProjectStore(f"sqlite:///{tmp_path / 'slicing.db'}")
    store.initialize_for_tests()
    project = store.create(ACTOR, 'Replacement bracket', 'create')['projectId']
    blobs = BlobStore(tmp_path / 'blobs')
    raw = cube()
    with store.transaction() as session:
        from kleos.projects.store import Project
        record = session.get(Project, project)
        record.data = {**record.data, 'sourceManifest': {'part.obj': 'source'}}
        session.add(Artifact(id='source', project_id=project, revision=1, name='part.obj', sha256=blobs.put(raw),
            size=len(raw), media_type='model/obj', status='input', lane='cad', details={}))
    driver = Driver()
    service = PrintPreparations(store, blobs, SlicingSettings('sha256:' + 'a' * 64, tmp_path / 'control',
                                                            tuple(BUNDLES)), driver)
    body = {'artifactId': 'source', 'profileId': 'a1-mini-pla-04', 'expectedEngineeringRevision': 1}
    yield service, project, body, driver
    store.engine.dispose()


def test_geometry_conversion_repair_and_bounds():
    stl, measured = mesh(cube(), 'obj', 180)
    assert measured['volumeMm3'] == 8000 and measured['triangleCount'] == 12
    assert measured['watertight'] and measured['selfIntersections'] == 'UNKNOWN'
    repaired, again = mesh(stl + b'', 'stl', 180)
    assert again['volumeMm3'] == 8000 and repaired == stl
    duplicate = stl[:80] + struct.pack('<I', 13) + stl[84:] + stl[84:134]
    _, repaired_metrics = mesh(duplicate, 'stl', 180)
    assert repaired_metrics['removedFaces'] == 1
    with pytest.raises(ValueError, match='watertight'):
        mesh(stl[:80] + struct.pack('<I', 11) + stl[84:-50], 'stl', 180)
    with pytest.raises(ValueError, match='envelope'):
        mesh(cube(179), 'obj', 180)
    with pytest.raises(ValueError):
        mesh(cube().replace(b'v 0 0 0', b'v nan 0 0'), 'obj', 180)
    with pytest.raises(ValueError):
        mesh(b'print("bad")', 'py', 180)


@pytest.mark.parametrize('extra', [{'../escape': 'bad'}, {'Metadata/plate_2.gcode': 'G1 E2'},
                                   {'Metadata/plate_1.gcode': 'no extrusion'}, {'/absolute': ''}])
def test_untrusted_package_rejected(extra):
    with pytest.raises(ValueError):
        validate_package(package(**extra))


@pytest.mark.asyncio
async def test_durable_candidate_and_restart(prepared):
    service, project, body, driver = prepared
    result = service.create(ACTOR, project, body, 'prepare')
    assert result == service.create(ACTOR, project, body, 'prepare')
    assert result['preparation']['status'] == 'queued'
    assert await service.process_next()
    assert not await service.process_next()
    restarted = PrintPreparations(service.projects, service.blobs, service.settings, driver)
    assert await restarted.reconcile() == 1
    view = restarted.get(ACTOR, project, result['preparation']['id'])
    assert view['status'] == 'ready_for_operator' and not view['physicalAcceptance']
    assert view['measurements']['archiveCheck'] == 'PASS'
    with service.projects.transaction() as session:
        artifact = session.get(Artifact, view['preparedArtifactId'])
        assert artifact.status == 'candidate' and artifact.details['sourceSha256'] == view['sourceSha256']
    await restarted.reconcile()
    assert len(driver.cleaned) == 1 and len(driver.started) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['cancel', 'revision', 'provenance', 'hash'])
async def test_publication_fences_and_failed_result(prepared, change):
    service, project, body, driver = prepared
    ident = service.create(ACTOR, project, body, 'prepare')['preparation']['id']
    await service.process_next()
    job = driver.started[0][0]
    if change == 'cancel':
        service.cancel(ACTOR, project, ident, 'cancel')
    elif change == 'revision':
        with service.projects.transaction() as session:
            row = session.get(Project, project)
            row.data = {**row.data, 'engineeringRevision': 2}
    elif change == 'provenance':
        driver.results[job]['sourceSha256'] = 'b' * 64
    else:
        driver.results[job]['preparedSha256'] = 'b' * 64
    assert await service.reconcile() == 0
    view = service.get(ACTOR, project, ident)
    assert view['status'] == {'cancel': 'cancelled', 'revision': 'failed'}.get(change, 'failed')
    assert view['preparedArtifactId'] is None


@pytest.mark.asyncio
async def test_dispatch_persistently_blocked_even_with_authorization(prepared):
    service, project, body, driver = prepared
    ident = service.create(ACTOR, project, body, 'prepare')['preparation']['id']
    await service.process_next()
    await service.reconcile()
    ready = service.get(ACTOR, project, ident)
    body = {'preparationId': ident, 'preparedSha256': ready['preparedSha256'], 'expectedEngineeringRevision': 1,
            'authorizePrint': True, 'externalSpoolConfirmed': True}
    dispatch = service.dispatch(ACTOR, project, body, 'dispatch')['dispatch']
    assert dispatch['status'] == 'blocked' and not dispatch['physicalAcceptance']
    assert service.get(ACTOR, project, dispatch['id'], dispatch=True) == dispatch
    assert len(driver.started) == 1


def test_scope_revision_configuration_and_profile_gates(prepared):
    service, project, body, _ = prepared
    with pytest.raises(DomainError):
        service.create(Actor(tenant_id='other', user_id='owner'), project, body, 'cross-tenant')
    with pytest.raises(DomainError):
        service.create(ACTOR, project, {**body, 'expectedEngineeringRevision': 2}, 'stale')
    with pytest.raises(DomainError):
        service.create(ACTOR, project, {**body, 'profileId': 'unqualified'}, 'unknown')
    service.settings = None
    with pytest.raises(DomainError, match='No qualified'):
        service.create(ACTOR, project, body, 'unconfigured')
    with pytest.raises(ValueError):
        SlicingSettings('mutable:latest', service.blobs.root)
    with pytest.raises(ValueError):
        SlicingSettings('sha256:' + 'a'*64, service.blobs.root, docker_desktop=True, deployment='ecs')


@pytest.mark.asyncio
async def test_missing_or_dead_supervisor_never_restarts(prepared):
    service, project, body, driver = prepared
    ident = service.create(ACTOR, project, body, 'prepare')['preparation']['id']
    await service.process_next()
    driver.results.clear()
    assert await service.reconcile() == 0
    assert not await service.process_next()
    assert service.get(ACTOR, project, ident)['status'] == 'running'
    with service.projects.transaction() as session:
        session.get(PrintPreparation, ident).created_at = 1
    await service.reconcile()
    assert service.get(ACTOR, project, ident)['status'] == 'failed'
    assert len(driver.started) == 1


def test_profile_is_fixed_and_client_cannot_supply_flags(prepared):
    service, _, _, _ = prepared
    profile = service.settings.profile('a1-mini-pla-04')
    assert profile['slicer'] == 'orca' and profile['slicerVersion'] == '2.4.2'
    assert profile['printerModelId'] == 'N1' and profile['nozzleMm'] == .4
    assert json.dumps(profile).count('physicalAcceptance') == 0


def test_exact_image_qualification_gate_and_mismatches(tmp_path):
    from pathlib import Path

    from kleos.capabilities.fabrication import slicing
    # The committed evidence is bound to the runner bytes it was recorded with. Adding a profile changes those bytes,
    # so this logic test rebinds a copy to the current runner; production still needs the image requalified.
    recorded = Path(slicing.__file__).parent / 'qualification/slicer-qualified-2026-11-02.json'
    report = {**json.loads(recorded.read_text()),
              'runnerSha256': slicing.digest(Path(slicing.__file__).with_name('native_slicer.py').read_bytes())}
    report_path = tmp_path / 'rebound.json'
    report_path.write_text(json.dumps(report))
    installation = {'image': report['image'], 'report': str(report_path), 'dockerDesktop': True}
    settings = slicing.qualify_slicing(installation, tmp_path / 'control')
    # Only profiles with recorded PASS evidence qualify; the X1 Carbon profile awaits its own exact-image run.
    assert set(settings.qualified_profiles) == set(report['profiles']) == set(BUNDLES) - {'x1c-pla-04'}
    for changes in ({'image': 'sha256:' + 'b'*64}, {'dockerDesktop': False}, {'deployment': 'ecs'}):
        with pytest.raises(ValueError):
            slicing.qualify_slicing({**installation, **changes}, tmp_path)
    for key in ('sourceOverride', 'runnerSha256', 'securityBaselineSha256'):
        bad = {**report, key: True if key == 'sourceOverride' else '0'*64}
        target = tmp_path / (key + '.json')
        target.write_text(json.dumps(bad))
        with pytest.raises(ValueError):
            slicing.qualify_slicing({**installation, 'report': str(target)}, tmp_path)


@pytest.mark.asyncio
async def test_pending_capacity_does_not_start_more_work(prepared):
    service, project, body, _ = prepared
    for number in range(16):
        service.create(ACTOR, project, body, str(number))
    with pytest.raises(DomainError, match='Too many'):
        service.create(ACTOR, project, body, 'over-capacity')


@pytest.mark.asyncio
async def test_native_context_revision_is_not_ui_engineering_epoch(prepared):
    from kleos.projects.store import Objective
    service, project, body, _ = prepared
    with service.projects.transaction() as session:
        record = session.get(Project, project)
        record.revision = 3
        record.data = {**record.data, 'contextRevision': 3, 'sourceManifest': {}}
        session.add(Objective(id='native-run', project_id=project, revision=3, status='completed', data={}))
        source = session.get(Artifact, 'source')
        source.revision = 3
        source.details = {'origin': 'native_candidate', 'runId': 'native-run'}
    ident = service.create(ACTOR, project, body, 'native')['preparation']['id']
    await service.process_next()
    assert await service.reconcile() == 1
    assert service.get(ACTOR, project, ident)['status'] == 'ready_for_operator'


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['queued', 'running', 'ready'])
async def test_replaced_source_fenced_at_every_boundary(prepared, stage):
    service, project, body, driver = prepared
    ident = service.create(ACTOR, project, body, 'prepare')['preparation']['id']
    if stage in {'running', 'ready'}:
        await service.process_next()
    if stage == 'ready':
        await service.reconcile()
    with service.projects.transaction() as session:
        record = session.get(Project, project)
        record.data = {**record.data, 'sourceManifest': {}}
    if stage == 'queued':
        await service.process_next()
        assert not driver.started
    elif stage == 'running':
        assert await service.reconcile() == 0
    else:
        ready = service.get(ACTOR, project, ident)
        with pytest.raises(DomainError, match='not current'):
            service.dispatch(ACTOR, project, {'preparationId': ident, 'preparedSha256': ready['preparedSha256'],
                'expectedEngineeringRevision': 1, 'authorizePrint': True, 'externalSpoolConfirmed': True}, 'dispatch')
    if stage != 'ready':
        assert service.get(ACTOR, project, ident)['status'] == 'failed'


def test_exact_accepted_release_can_supply_mesh(prepared):
    service, project, body, _ = prepared
    with service.projects.transaction() as session:
        record = session.get(Project, project)
        record.data = {**record.data, 'acceptedReleaseId': 'release', 'sourceManifest': {}}
        source = session.get(Artifact, 'source')
        source.revision = 8
        session.add(Release(id='release', project_id=project, candidate_id='candidate', revision=9,
            data={'sources': [], 'outputs': [{'id': source.id, 'sha256': source.sha256}]}))
    view = service.create(ACTOR, project, {**body, 'releaseId': 'release'}, 'released')['preparation']
    assert view['status'] == 'queued'


async def test_shared_capacity_busy_keeps_slicer_queued_and_holds_slot_until_proven_cleanup(prepared):
    from kleos.execution.capacity import CapacityService
    from kleos.infrastructure.schema import metadata
    service, project, body, driver = prepared
    metadata().create_all(service.projects.engine)
    capacity = CapacityService(service.projects)
    service.capacity = capacity
    assert capacity.reserve('checker:other', project, 'e'*64,
        {'cpus': 3, 'memoryBytes': 3*1024**3, 'scratchBytes': 1024**3})
    queued = service.create(ACTOR, project, body, 'capacity')['preparation']
    assert not await service.process_next() and not driver.started
    capacity.release('checker:other', 'e'*64, {'kind': 'container-removed', 'identity': 'other'})
    assert await service.process_next()
    assert capacity.usage()['jobs'] == 1
    await service.reconcile()  # Result collected; process capacity remains until removal.
    assert capacity.usage()['jobs'] == 1
    await service.reconcile()  # Fixture returning no proof cannot free the reservation.
    assert capacity.usage()['jobs'] == 1
    async def cleanup(job, *, acknowledged=False, observed_id=None):
        return {'kind': 'container-removed', 'identity': 'kleos-slicer-'+job}
    driver.cleanup = cleanup
    await service.reconcile()
    assert capacity.usage()['jobs'] == 0
    with service.projects.transaction() as session:
        assert session.get(PrintPreparation, queued['id']).container_id is None


@pytest.mark.parametrize('renamed_still_present', [False, True])
async def test_lost_slicer_create_then_absence_does_not_release_capacity(prepared, renamed_still_present):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    import httpx

    from kleos.capabilities.fabrication.slicing import DockerSlicingDriver
    from kleos.execution.capacity import CapacityService
    from kleos.infrastructure.schema import metadata
    service, project, body, _ = prepared
    metadata().create_all(service.projects.engine)
    service.capacity = CapacityService(service.projects)
    deleted = []
    def handler(request):
        assert request.method == 'DELETE'
        deleted.append(request.url.path)
        return httpx.Response(204)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url='http://docker') as client:
        docker = SimpleNamespace(inspect=AsyncMock(return_value=None), client=client)
        driver = DockerSlicingDriver(docker, service.settings)
        driver.start = AsyncMock(side_effect=httpx.ReadError('Lost create response'))
        service.driver = driver
        created = service.create(ACTOR, project, body, 'lost')['preparation']
        await service.process_next()
        await service.reconcile()
        assert 'capacity remains reserved' in service.get(ACTOR, project, created['id'])['measurements']['error']
        assert service.capacity.usage()['jobs'] == 1 and not deleted
        with service.projects.transaction() as session:
            row = session.get(PrintPreparation, created['id'])
            assert row.measurements['_dispatchAcknowledged'] is False
            job_id = row.container_id
        assert '_dispatchAcknowledged' not in service.get(ACTOR, project, created['id'])['measurements']
        # Later recovery observes the exact owned container and removes its immutable ID.
        driver._check = lambda info, selected: selected == job_id
        docker.inspect.side_effect = [{'Id': 'c'*64}, {'Id': 'c'*64} if renamed_still_present else None, None]
        await service.reconcile()
        assert deleted == ['/containers/'+'c'*64]
        assert docker.inspect.call_args_list[-2].args == ('c'*64,)
        assert docker.inspect.call_args_list[-1].args == ('kleos-slicer-'+job_id,)
        assert service.capacity.usage()['jobs'] == (1 if renamed_still_present else 0)


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['account', 'organization', 'viewer', 'project'])
async def test_revoked_creator_cannot_dispatch_queued_preparation(prepared, change):
    service, project, body, driver = prepared
    account_authority(service)
    ident = service.create(ACTOR, project, body, 'prepare')['preparation']['id']
    revoke_preparation_authority(service, project, change)
    assert await service.process_next()
    assert not driver.started
    with service.projects.transaction() as session:
        row = session.get(PrintPreparation, ident)
        assert row.status == 'cancelled' and row.container_id is None
        assert row.prepared_artifact_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize('internal', [False, True])
@pytest.mark.parametrize('change', ['account', 'organization', 'viewer', 'project'])
async def test_revoked_creator_cannot_publish_but_cleanup_releases_capacity(prepared, internal, change):
    from kleos.execution.capacity import CapacityService
    service, project, body, driver = prepared
    account_authority(service)
    ident = service.create(ACTOR, project, body, 'prepare')['preparation']['id']
    if internal:
        with service.projects.transaction() as session:
            row = session.get(Project, project)
            row.data = {**row.data, 'internalPurpose': 'bambu_test_print'}
    service.capacity = CapacityService(service.projects)
    assert await service.process_next()
    assert len(driver.started) == 1 and service.capacity.usage()['jobs'] == 1
    revoke_preparation_authority(service, project, change)
    assert await service.reconcile() == 0
    with service.projects.transaction() as session:
        row = session.get(PrintPreparation, ident)
        assert row.status == 'failed' and row.container_id is not None
        assert row.prepared_artifact_id is None
    assert service.capacity.usage()['jobs'] == 1
    async def cleanup(job, *, acknowledged=False, observed_id=None):
        driver.cleaned.append(job)
        return {'kind': 'container-removed', 'identity': 'kleos-slicer-'+job}
    driver.cleanup = cleanup
    assert await service.reconcile() == 0
    assert len(driver.cleaned) == 1 and service.capacity.usage()['jobs'] == 0
    with service.projects.transaction() as session:
        assert session.get(PrintPreparation, ident).container_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize('when', ['queued', 'running'])
async def test_internal_test_preparation_requires_current_administrator(prepared, when):
    service, project, body, driver = prepared
    account_authority(service)
    ident = service.create(ACTOR, project, body, 'prepare')['preparation']['id']
    with service.projects.transaction() as session:
        row = session.get(Project, project)
        row.data = {**row.data, 'internalPurpose': 'bambu_test_print'}
    if when == 'running':
        assert await service.process_next()
    revoke_preparation_authority(service, project, 'member')
    if when == 'queued':
        assert await service.process_next()
        assert not driver.started
    else:
        assert await service.reconcile() == 0
    with service.projects.transaction() as session:
        row = session.get(PrintPreparation, ident)
        assert row.status == ('cancelled' if when == 'queued' else 'failed')
        assert row.prepared_artifact_id is None
