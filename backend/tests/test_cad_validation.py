import hashlib
import json
import struct
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from kleos.artifacts.storage import BlobStore
from kleos.contracts.models import ArtifactRef, DomainError
from kleos.execution.docker import DockerClient
from kleos.validation.cad import (
    CadChecker,
    CadRequirements,
    IsolatedCadProcess,
    checker_spec,
    decode_logs,
    install_cad_checker,
)
from kleos.validation.native_check import (
    PROTOCOL,
    InvalidExport,
    inspect_request,
    step_measurements,
    stl_measurements,
)
from kleos.validation.service import CheckerRegistry, ValidationInput


def tetrahedron():
    a, b, c, d = (0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1)
    triangles = [(a, c, b), (a, b, d), (a, d, c), (b, c, d)]
    return b'\0'*80 + struct.pack('<I', 4) + b''.join(
        struct.pack('<12fH', 0, 0, 0, *(v for point in tri for v in point), 0) for tri in triangles)


def request_fixture(tmp_path):
    files = []
    for kind, data in [('step', b'qualified mock STEP'), ('stl', tetrahedron())]:
        digest = hashlib.sha256(data).hexdigest()
        (tmp_path/digest).write_bytes(data)
        files.append({'kind': kind, 'part': 'base', 'sha256': digest, 'size': len(data)})
    return {'protocol': PROTOCOL, 'units': 'mm', 'files': files, 'constraints': [], 'unsupportedRequirements': []}


def step_reader(path, data):
    return {'boundsMinMm': [0, 0, 0], 'boundsMaxMm': [1, 1, 1], 'volumeMm3': 1/6,
            'solidCount': 1, 'solidValid': True, 'units': 'mm'}


def test_mesh_reads_real_triangles_and_rejects_open_or_nonfinite():
    data = tetrahedron()
    assert stl_measurements(data)['volumeMm3'] == pytest.approx(1/6)
    broken = bytearray(data)
    struct.pack_into('<f', broken, 96, float('nan'))
    with pytest.raises(InvalidExport, match='Nonfinite'):
        stl_measurements(broken)
    broken = data[:80]+struct.pack('<I', 5)+data[84:]+data[84:134]
    with pytest.raises(InvalidExport, match='closed mesh'):
        stl_measurements(broken)


def test_parser_dimension_digest_mesh_and_unknown_boundaries(tmp_path):
    request = request_fixture(tmp_path)
    request['constraints'] = [{'part': 'base', 'metric': 'extent_x_mm', 'expected': 1, 'tolerance': .001}]
    assert inspect_request(request, tmp_path, step_reader=step_reader)['status'] == 'PASS'
    request['constraints'][0]['expected'] = 2
    with pytest.raises(InvalidExport, match='numeric constraint'):
        inspect_request(request, tmp_path, step_reader=step_reader)
    request['constraints'] = []
    request['unsupportedRequirements'] = ['Fatigue strength is not supported']
    assert inspect_request(request, tmp_path, step_reader=step_reader)['status'] == 'UNKNOWN'
    request['unsupportedRequirements'] = []
    assert inspect_request(request, tmp_path, step_reader=lambda *_: None)['status'] == 'UNKNOWN'
    with pytest.raises(InvalidExport, match='bounds mismatch'):
        inspect_request(request, tmp_path, step_reader=lambda *_: step_reader(None, None) | {'boundsMaxMm': [2, 1, 1]})
    (tmp_path/request['files'][0]['sha256']).write_bytes(b'tampered')
    with pytest.raises(InvalidExport, match='digest'):
        inspect_request(request, tmp_path, step_reader=step_reader)


def test_specs_are_fixed_credential_free_and_readonly():
    spec = checker_spec('sha256:'+'a'*64, '/trusted/input', 'separate-job', ['DATABASE_URL', 'AWS_SECRET_ACCESS_KEY'])
    assert 'DATABASE_URL=' in spec['Env'] and 'AWS_SECRET_ACCESS_KEY=' in spec['Env']
    assert spec['User'] == '10001:10001'
    assert spec['HostConfig']['CapDrop'] == ['ALL']
    assert spec['HostConfig']['ReadonlyRootfs']
    assert spec['HostConfig']['Mounts'][0]['ReadOnly']
    assert spec['HostConfig']['NetworkMode'] == 'none'
    assert spec['Cmd'] == []
    assert 'seccomp=' in spec['HostConfig']['SecurityOpt'][1]
    with pytest.raises(ValueError, match='local'):
        IsolatedCadProcess(None, 'sha256:'+'a'*64, deployment='ecs')


def test_constraints_reject_nan_and_no_log_partial_records():
    with pytest.raises(ValueError):
        CadRequirements(constraints=[{'part': 'base', 'metric': 'extent_x_mm', 'expected': float('nan'), 'tolerance': .1}])
    with pytest.raises(DomainError):
        decode_logs(b'garbage')


def test_mixed_or_missing_step_units_are_unknown_without_loading_geometry(tmp_path):
    assert step_measurements(tmp_path/'unused', b'ISO-10303-21;') is None
    mixed = b'LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.MILLI.,.METRE.); LENGTH_UNIT() NAMED_UNIT(*) CONVERSION_BASED_UNIT("inch",#1);'
    assert step_measurements(tmp_path/'unused', mixed) is None


@pytest.mark.asyncio
async def test_adapter_exact_refs_only_exports_and_postexecution_cancel(tmp_path):
    blobs = BlobStore(tmp_path/'blobs')
    refs = []
    for name, data in [('model.py', b'raise RuntimeError("never mount or execute")'), ('base.step', b'STEP'), ('base.stl', tetrahedron())]:
        refs.append(ArtifactRef(id=name, path=name, sha256=blobs.put(data), size=len(data), revision=1))
    subject = ValidationInput(candidate_id='candidate', project_id='p', revision=1, subject_hash='a'*64,
                              sources=refs[:1], outputs=refs[1:])
    repo = SimpleNamespace(resolve_artifacts=AsyncMock(return_value=refs), assert_active=AsyncMock())
    scope = SimpleNamespace(project_id='p', revision=1)
    calls = []

    async def run(directory, execution_id):
        request = json.loads((directory/'request.json').read_text())
        assert not (directory/refs[0].sha256).exists()
        assert request['files'][0]['artifactId'] == 'base.step'
        calls.append(execution_id)
        return {'protocol': PROTOCOL, 'subjectHash': subject.subject_hash, 'executionId': execution_id,
                'status': 'UNKNOWN', 'measurements': {}, 'unsupportedRequirements': ['strength']}

    adapter = CadChecker(repo, blobs, SimpleNamespace(run=run), tmp_path/'control', AsyncMock(return_value=CadRequirements()))
    report = await adapter.invoke(scope, subject)
    assert report.status.value == 'UNKNOWN' and report.execution_id == calls[0]
    assert report.measurements['physicalAcceptance'] is False
    assert not list((tmp_path/'control').iterdir())
    repo.assert_active.side_effect = DomainError('Cancelled', 'run_inactive')
    with pytest.raises(DomainError, match='Cancelled'):
        await adapter.invoke(scope, subject)
    assert len(set(calls)) == 2


@pytest.mark.asyncio
async def test_adapter_rejects_changed_digest_before_process(tmp_path):
    original = ArtifactRef(id='x', path='base.step', sha256='a'*64, size=1, revision=1)
    repo = SimpleNamespace(resolve_artifacts=AsyncMock(return_value=[original.model_copy(update={'sha256': 'b'*64})]))
    process = SimpleNamespace(run=AsyncMock())
    adapter = CadChecker(repo, None, process, tmp_path, AsyncMock())
    subject = ValidationInput(candidate_id='c', project_id='p', revision=1, subject_hash='c'*64, sources=[], outputs=[original])
    with pytest.raises(DomainError, match='binding changed'):
        await adapter.invoke(SimpleNamespace(project_id='p', revision=1), subject)
    process.run.assert_not_called()


@pytest.mark.asyncio
async def test_docker_http_protocol_cleanup_and_binding(tmp_path):
    image = 'sha256:'+'a'*64
    result = json.dumps({'status': 'PASS'}).encode()
    requests, removed = [], []

    def handler(request):
        requests.append(request)
        path = request.url.path
        if path.endswith('/info'):
            return httpx.Response(200, json={'OperatingSystem': 'Docker Desktop'})
        if '/images/' in path:
            return httpx.Response(200, json={'Id': image, 'Config': {'Labels': {'kleos.checker.protocol': PROTOCOL}, 'Env': ['API_KEY=secret']}})
        if path.endswith('/create'):
            spec = json.loads(request.content)
            assert 'API_KEY=' in spec['Env'] and 'API_KEY=secret' not in spec['Env']
            return httpx.Response(201, json={'Id': 'checker'})
        if request.method == 'DELETE':
            removed.append(True)
            return httpx.Response(204)
        if path.endswith('/json'):
            if removed:
                return httpx.Response(404)
            return httpx.Response(200, json={'Id': 'c'*64, 'Image': image, 'Config': {'Labels': {
                'kleos.checker.protocol': PROTOCOL, 'kleos.checker.execution': 'checker'}},
                'State': {'Running': False, 'ExitCode': 0}})
        if path.endswith('/logs'):
            return httpx.Response(200, content=b'\x01\0\0\0'+len(result).to_bytes(4, 'big')+result)
        return httpx.Response(204)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url='http://docker/v1.45') as client:
        process = IsolatedCadProcess(DockerClient(client=client), image)
        assert await process.run(tmp_path, 'checker') == {'status': 'PASS'}
    assert requests[-1].method == 'GET' and requests[-2].method == 'GET' and requests[-3].method == 'DELETE'


def test_registration_requires_matching_exact_image_and_real_negative_evidence(tmp_path):
    evidence = Path(__file__).parents[1]/'src/kleos/validation/qualification/cad-check-exact-image.json'
    record = json.loads(evidence.read_text())
    adapter = SimpleNamespace(process=SimpleNamespace(image=record['imageId'], timeout=120), invoke=AsyncMock())
    registry = CheckerRegistry()
    installed = install_cad_checker(registry, adapter, evidence)
    assert installed.key == 'cad.export-integrity'
    with pytest.raises(DomainError, match='Release policy is unavailable'):
        registry.snapshot('full-device')
    record['cases'][1]['result']['status'] = 'PASS'
    altered = tmp_path/'changed-report.json'
    altered.write_text(json.dumps(record))
    with pytest.raises(DomainError, match='qualification'):
        install_cad_checker(CheckerRegistry(), adapter, altered)
    adapter.process.image = 'sha256:'+'0'*64
    with pytest.raises(DomainError, match='qualification'):
        install_cad_checker(CheckerRegistry(), adapter, evidence)


@pytest.mark.asyncio
async def test_nonzero_checker_exit_is_error_and_container_is_removed(tmp_path):
    image = 'sha256:'+'a'*64
    removed = []

    def handler(request):
        path = request.url.path
        if path.endswith('/info'):
            return httpx.Response(200, json={'OperatingSystem': 'Docker Desktop'})
        if '/images/' in path:
            return httpx.Response(200, json={'Id': image, 'Config': {'Labels': {'kleos.checker.protocol': PROTOCOL}}})
        if path.endswith('/create'):
            return httpx.Response(201, json={'Id': 'checker'})
        if path.endswith('/json'):
            if removed:
                return httpx.Response(404)
            return httpx.Response(200, json={'Id': 'c'*64, 'Image': image, 'Config': {'Labels': {
                'kleos.checker.protocol': PROTOCOL, 'kleos.checker.execution': 'failed-checker'}},
                'State': {'Running': False, 'ExitCode': 137, 'OOMKilled': True}})
        if request.method == 'DELETE':
            removed.append(request.url)
        return httpx.Response(204)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url='http://docker/v1.45') as client:
        with pytest.raises(DomainError, match='process failed'):
            await IsolatedCadProcess(DockerClient(client=client), image).run(tmp_path, 'failed-checker')
    assert len(removed) == 1


@pytest.mark.parametrize('failure', ['none', 'busy', 'lost-create-present', 'lost-create-absent', 'failed-delete', 'still-present', 'renamed-still-present'])
async def test_checker_capacity_requires_verified_cleanup_even_after_lost_create(tmp_path, failure):
    from kleos.execution.capacity import CapacityService
    from kleos.infrastructure.schema import metadata
    from kleos.projects.store import ProjectStore
    store = ProjectStore('sqlite:///'+str(tmp_path/'capacity.db'))
    metadata().create_all(store.engine)
    capacity = CapacityService(store)
    image, execution_id = 'sha256:'+'a'*64, 'capacity-check'
    name = 'kleos-cad-check-'+execution_id
    state = {'present': False, 'renamed': False}
    result = json.dumps({'status': 'PASS'}).encode()
    calls = []
    authority_checks = []
    if failure == 'busy':
        assert capacity.reserve('native:other', 'project', 'f'*64,
            {'cpus': 3, 'memoryBytes': 1024**3, 'scratchBytes': 1024**3})
    async def assert_active():
        authority_checks.append(True)
        if failure == 'busy' and len(authority_checks) == 2:
            capacity.release('native:other', 'f'*64, {'kind': 'container-removed', 'identity': 'other'})
    def handler(request):
        calls.append((request.method, request.url.path))
        path = request.url.path
        if path.endswith('/info'):
            return httpx.Response(200, json={'OperatingSystem': 'Docker Desktop'})
        if '/images/' in path:
            return httpx.Response(200, json={'Id': image, 'Config': {'Labels': {'kleos.checker.protocol': PROTOCOL}}})
        if path.endswith('/create'):
            assert capacity.usage()['jobs'] == 1
            state['present'] = failure != 'lost-create-absent'
            if failure.startswith('lost-create'):
                raise httpx.ReadError('Fixture lost response')
            return httpx.Response(201, json={'Id': name})
        if path.endswith('/json'):
            if not state['present'] or (state['renamed'] and name in path):
                return httpx.Response(404)
            return httpx.Response(200, json={'Id': 'c'*64, 'Image': image, 'Config': {'Labels': {
                'kleos.checker.protocol': PROTOCOL, 'kleos.checker.execution': execution_id}},
                'State': {'Running': False, 'ExitCode': 0}})
        if request.method == 'DELETE':
            if failure == 'failed-delete':
                return httpx.Response(500)
            state['present'] = failure in {'still-present', 'renamed-still-present'}
            state['renamed'] = failure == 'renamed-still-present'
            return httpx.Response(204)
        if path.endswith('/logs'):
            return httpx.Response(200, content=b'\x01\0\0\0'+len(result).to_bytes(4, 'big')+result)
        return httpx.Response(204)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url='http://docker/v1.45') as client:
        process = IsolatedCadProcess(DockerClient(client=client), image, capacity=capacity)
        if failure in {'none', 'busy'}:
            assert await process.run(tmp_path, execution_id, project_id='project', assert_active=assert_active) == {'status': 'PASS'}
        else:
            with pytest.raises((httpx.HTTPError, DomainError)):
                await process.run(tmp_path, execution_id, project_id='project')
    if failure == 'busy':
        assert len(authority_checks) >= 3
    assert capacity.usage()['jobs'] == (1 if failure in {'lost-create-absent', 'failed-delete', 'still-present', 'renamed-still-present'} else 0)
    store.engine.dispose()


async def test_sustained_capacity_contention_defers_before_any_container_create(tmp_path):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock

    from kleos.validation.service import CheckerAdmissionDeferred
    image = 'sha256:'+'a'*64
    requests = []
    def handler(request):
        requests.append(request)
        if request.url.path.endswith('/info'):
            return httpx.Response(200, json={'OperatingSystem': 'Docker Desktop'})
        if '/images/' in request.url.path:
            return httpx.Response(200, json={'Id': image, 'Config': {'Labels': {'kleos.checker.protocol': PROTOCOL}}})
        pytest.fail('Full pool cannot create or delete a container')
    capacity = SimpleNamespace(reserve=Mock(return_value=False), release=Mock())
    authority = AsyncMock()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url='http://docker/v1.45') as client:
        process = IsolatedCadProcess(DockerClient(client=client), image, capacity=capacity)
        with pytest.raises(CheckerAdmissionDeferred):
            await process.run(tmp_path, 'waiting', project_id='project', assert_active=authority)
    assert authority.await_count >= 2 and all(r.method == 'GET' for r in requests)
    capacity.release.assert_not_called()
