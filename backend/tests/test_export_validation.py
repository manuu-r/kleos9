import hashlib
import io
import json
import stat
import struct
import zipfile
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from kleos.contracts.models import DomainError
from kleos.execution.docker import DockerClient
from kleos.execution.security import daemon_socket_profile
from kleos.projects.store import fingerprint
from kleos.validation.export_check import (
    BOARD,
    PROTOCOL,
    InvalidExport,
    UnsupportedExport,
    firmware_measurements,
    inspect_request,
    web_measurements,
)
from kleos.validation.export_process import CHECKER_RESOURCES, IsolatedExportProcess, checker_spec
from kleos.validation.exports import EXPECTED_CASES, ExportChecker, install_export_checker, source_hashes
from kleos.validation.service import CheckerRegistry, ValidationInput


def firmware():
    binary = struct.pack('<II', 0x20040000, 9)+b'\0'*8
    elf = bytearray(256+len(binary))
    elf[:16] = b'\x7fELF\x01\x01\x01'+b'\0'*9
    struct.pack_into('<HHIIIIIHHHHHH', elf, 16, 2, 40, 1, 9, 52, 84, 0, 52, 32, 1, 40, 2, 0)
    struct.pack_into('<8I', elf, 52, 1, 256, 0, 0, len(binary), len(binary), 5, 4)
    struct.pack_into('<10I', elf, 124, 0, 1, 2, 0, 256, len(binary), 0, 0, 4, 0)
    elf[256:] = binary
    return bytes(elf), binary


def web_files():
    return {'index.html': b'<html><base href="/"><script src="flutter_bootstrap.js"></script></html>',
        'flutter_bootstrap.js': b'var buildConfig={mainJsPath:"main.dart.js",renderer:"canvaskit"};',
        'main.dart.js': b'// static fixture; never execute', 'canvaskit/canvaskit.js': b'// fixture',
        'canvaskit/canvaskit.wasm': b'\0asm\x01\0\0\0',
        'assets/FontManifest.json': b'[{"family":"Roboto","fonts":[{"asset":"fonts/regular.ttf"}]}]',
        'assets/fonts/regular.ttf': b'\x00\x01\x00\x00fixture-font'}


def archive(files, additions=()):
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as zipped:
        for name, content in [*files.items(), *additions]:
            zipped.writestr(name, content)
    return out.getvalue()


def test_firmware_bytes_board_memory_and_independent_binary_reconstruction():
    elf, binary = firmware()
    result = firmware_measurements(elf, binary, {'board': BOARD})
    assert result['binaryMatchesElf'] and result['firmwareExecuted'] is False
    assert result['flashBytes'] == len(binary)


@pytest.mark.parametrize('mutation', ['machine', 'truncated', 'binary', 'memory', 'vectors', 'section', 'board', 'overflow', 'entry'])
def test_firmware_invalid_and_unsupported_are_distinct(mutation):
    elf, binary = firmware()
    elf = bytearray(elf)
    target = {'board': BOARD}
    if mutation == 'machine':
        struct.pack_into('<H', elf, 18, 62)
    elif mutation == 'truncated':
        elf = elf[:100]
    elif mutation == 'binary':
        binary += b'other'
    elif mutation == 'memory':
        struct.pack_into('<I', elf, 52+8, 0x30000000)
    elif mutation == 'vectors':
        binary = b'\0'*8+binary[8:]
        elf[256:] = binary
    elif mutation == 'section':
        struct.pack_into('<I', elf, 128, 0x70000002)
    elif mutation == 'board':
        target = {'board': 'native_sim'}
    elif mutation == 'overflow':
        struct.pack_into('<I', elf, 52+16, 0xffffffff)
    else:
        struct.pack_into('<I', elf, 24, 0xfffffffe)
    expected = UnsupportedExport if mutation in {'section', 'board'} else InvalidExport
    with pytest.raises(expected):
        firmware_measurements(bytes(elf), binary, target)


def test_web_is_bounded_data_check_and_never_browser_or_security_evidence():
    result = web_measurements(archive(web_files()))
    assert result['requiredLocalResourcesPresent']
    assert not result['browserExecuted'] and not result['networkBehaviorVerified'] and not result['applicationSecurityVerified']


@pytest.mark.parametrize('mutation', ['bootstrap', 'font', 'escape', 'symlink', 'case', 'external', 'missing_ref', 'font_json', 'remote_renderer', 'zip'])
def test_web_missing_unsafe_and_unsupported_exports(mutation):
    files, extra = web_files(), []
    if mutation == 'bootstrap':
        del files['flutter_bootstrap.js']
    elif mutation == 'font':
        del files['assets/fonts/regular.ttf']
    elif mutation == 'escape':
        extra = [('../escape', b'x')]
    elif mutation == 'symlink':
        link = zipfile.ZipInfo('link')
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        extra = [(link, b'index.html')]
    elif mutation == 'case':
        files['INDEX.html'] = b'different'
    elif mutation == 'external':
        files['index.html'] = b'<script src="https://example.invalid/script.js"></script>'
    elif mutation == 'missing_ref':
        files['index.html'] = b'<script src="absent.js"></script>'
    elif mutation == 'font_json':
        files['assets/FontManifest.json'] = b'[{"family":"Roboto","family":"other","fonts":[]}]'
    elif mutation == 'remote_renderer':
        files['flutter_bootstrap.js'] += b'var config={canvasKitBaseUrl:"https://example.invalid/"};'
    data = b'not a zip' if mutation == 'zip' else archive(files, extra)
    with pytest.raises(UnsupportedExport if mutation in {'external', 'remote_renderer'} else InvalidExport):
        web_measurements(data)


def test_web_archive_crc_and_expansion_limits():
    zipped = bytearray(archive(web_files()))
    # Matching local/central claims, but wrong actual decompressed-payload CRC.
    offset = zipped.index(b'PK\x01\x02')
    zipped[offset+16:offset+20] = b'\xff'*4
    local = struct.unpack_from('<I', zipped, offset+42)[0]
    zipped[local+14:local+18] = b'\xff'*4
    with pytest.raises(InvalidExport):
        web_measurements(bytes(zipped))
    files = web_files()
    files['bomb'] = b'x'*(2*1024**2)
    with pytest.raises(InvalidExport, match='expansion'):
        web_measurements(archive(files))


@pytest.mark.parametrize('lane', ['firmware', 'application'])
def test_reader_requires_exact_hash_and_complete_outputs(tmp_path, lane):
    if lane == 'firmware':
        elf, binary = firmware()
        files = {'outputs/firmware.elf': elf, 'outputs/firmware.bin': binary}
    else:
        files = {'outputs/companion-web.zip': archive(web_files())}
    req = {'lane': lane, 'protocol': PROTOCOL, 'firmwareTarget': {'board': BOARD}, 'files': []}
    for name, data in files.items():
        digest = hashlib.sha256(data).hexdigest()
        (tmp_path/digest).write_bytes(data)
        req['files'].append({'path': name, 'sha256': digest, 'size': len(data)})
    assert inspect_request(req, tmp_path)['status'] == 'PASS'
    (tmp_path/req['files'][0]['sha256']).write_bytes(b'tampered')
    with pytest.raises(InvalidExport, match='binding'):
        inspect_request(req, tmp_path)
    req['files'].pop()
    with pytest.raises(InvalidExport):
        inspect_request(req, tmp_path)


def qualification(lane):
    value = {'schemaVersion': 'kleos-export-check-qualification-v1', 'qualified': True, 'lane': lane,
        'imageId': 'sha256:'+'a'*64, 'protocol': PROTOCOL, 'sourceOverride': False,
        'deployment': 'local', 'containerChecksPassed': True, 'sourceHashes': source_hashes(),
        'caseStatuses': EXPECTED_CASES[lane], 'qualifiedBy': 'fixture-runner',
        'networkPolicy': 'docker-desktop-daemon-seccomp', 'resources': CHECKER_RESOURCES, 'watchdogSeconds': 175,
        'daemonSeccompSha256': fingerprint(daemon_socket_profile())}
    value['independentReview'] = {'reviewer': 'fixture-reviewer', 'reviewedAt': datetime.now(UTC).isoformat(),
        'approved': True, 'evidenceSha256': fingerprint(value)}
    return value


@pytest.mark.parametrize('lane', ['firmware', 'application'])
@pytest.mark.parametrize('mutation', ['none', 'review', 'source', 'lane', 'symlink', 'cases'])
def test_installation_requires_reviewed_exact_source_image_lane_and_cases(tmp_path, lane, mutation):
    evidence = qualification(lane)
    path = tmp_path/'report.json'
    if mutation == 'review':
        evidence.pop('independentReview')
    elif mutation == 'source':
        evidence['sourceHashes']['export_check.py'] = 'b'*64
    elif mutation == 'lane':
        evidence['lane'] = 'pcb'
    elif mutation == 'cases':
        evidence['caseStatuses'] = {'valid_firmware': 'PASS'}
    path.write_text(json.dumps(evidence))
    if mutation == 'symlink':
        link = tmp_path/'link'
        link.symlink_to(path)
        path = link
    adapter = SimpleNamespace(lane=lane, process=SimpleNamespace(image=evidence['imageId'], timeout=180), invoke=AsyncMock())
    if mutation == 'none':
        assert install_export_checker(CheckerRegistry(), adapter, path).qualified
    else:
        with pytest.raises(DomainError, match='independent review'):
            install_export_checker(CheckerRegistry(), adapter, path)


def test_fixed_export_process_has_no_candidate_entrypoint_or_privileged_mount():
    spec = checker_spec('sha256:'+'a'*64, '/input-root', 'test', ['SECRET'])
    assert spec['Entrypoint'][-1] == '/opt/kleos-export-checker/export_check.py'
    assert spec['Entrypoint'][2] == '175' and 'SECRET=' in spec['Env']
    assert spec['HostConfig']['ReadonlyRootfs'] and spec['HostConfig']['Mounts'][0]['ReadOnly']
    assert spec['HostConfig']['NetworkMode'] == 'none' and spec['HostConfig']['CapDrop'] == ['ALL']


@pytest.mark.parametrize('mode', ['normal', 'lost_create', 'renamed_live'])
async def test_capacity_release_requires_immutable_id_absence(tmp_path, mode):
    image, container = 'sha256:'+'a'*64, 'b'*64
    present, polls, released = False, 0, []
    def respond(req):
        nonlocal present, polls
        path = req.url.path
        if path == '/info':
            return httpx.Response(200, json={'OperatingSystem': 'Docker Desktop'})
        if '/images/' in path:
            return httpx.Response(200, json={'Id': image, 'Config': {'Labels': {'kleos.checker.protocol': PROTOCOL}}})
        if path.endswith('/create'):
            if mode == 'lost_create':
                raise httpx.ReadTimeout('lost', request=req)
            present = True
            return httpx.Response(201, json={'Id': container})
        if path.endswith('/start'):
            return httpx.Response(204)
        if path.endswith('/logs'):
            raw = b'{"status":"PASS"}'
            return httpx.Response(200, content=b'\x01\0\0\0'+len(raw).to_bytes(4, 'big')+raw)
        if req.method == 'DELETE':
            assert path == '/containers/'+container
            present = mode == 'renamed_live'
            return httpx.Response(204)
        if '/containers/' in path:
            polls += 1
            if not present or (mode == 'renamed_live' and polls > 1 and container not in path):
                return httpx.Response(404)
            return httpx.Response(200, json={'Id': container, 'Image': image,
                'Config': {'Labels': {'kleos.checker.protocol': PROTOCOL, 'kleos.checker.execution': 'test'}},
                'State': {'Running': False, 'ExitCode': 0}})
        raise AssertionError(path)
    capacity = SimpleNamespace(reserve=lambda *a, **k: True, release=lambda *a: released.append(a))
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond), base_url='http://docker') as client:
        process = IsolatedExportProcess(DockerClient(client=client), image, lane='application', capacity=capacity)
        if mode == 'normal':
            assert await process.run(tmp_path, 'test', project_id='project') == {'status': 'PASS'}
            assert len(released) == 1
        else:
            with pytest.raises((httpx.HTTPError, DomainError)):
                await process.run(tmp_path, 'test', project_id='project')
            assert released == []


@pytest.mark.parametrize('lane', ['firmware', 'application'])
@pytest.mark.parametrize('mutation', ['none', 'board', 'job_hash', 'source', 'output_subset', 'result_binding', 'context_after'])
async def test_authorized_native_exports_are_checked_without_running_sources(tmp_path, lane, mutation):
    from kleos.artifacts.service import ArtifactService
    from kleos.artifacts.storage import BlobStore
    from kleos.contracts.models import Actor, TaskSpec
    from kleos.execution.gateway import NativeJob
    from kleos.orchestration.engine import RunScope
    from kleos.projects.graph_repository import GraphRepository
    from kleos.projects.store import Objective, Project, ProjectStore

    store = ProjectStore(f'sqlite:///{tmp_path}/exports.db')
    store.initialize_for_tests()
    actor = Actor(tenant_id='tenant', user_id='owner')
    created = store.create(actor, 'Export fixture', 'create')
    target = {'board': BOARD}
    with store.transaction() as session:
        project = session.get(Project, created['projectId'])
        project.data = {**project.data, 'interfaces': {'firmware': target}}
    submitted = store.submit(actor, created['projectId'], 'Build', 1, 'build')
    scope = RunScope(actor=actor, project_id=created['projectId'], run_id=submitted['runId'],
        revision=submitted['revision'], deadline_at=datetime.now(UTC)+timedelta(minutes=5))
    task = TaskSpec(id=lane, capability=lane, objective=lane, allowed_paths=['source.txt'])
    repo = GraphRepository(store)
    await repo.publish_plan(scope, tasks=[task], superseded={}, turn=0, summary='Fixture', question=None)
    pins = {'lane': lane, 'imageDigest': 'sha256:'+'a'*64, 'board': BOARD,
            'firmwareTarget': target, 'firmwareTargetHash': fingerprint(target)}
    with store.transaction() as session:
        session.add(NativeJob(id='producer', project_id=scope.project_id, run_id=scope.run_id, task_id=lane,
            task_hash='b'*64, request_hash=fingerprint(pins), request=pins, status='finished', source_manifest={}))
    blobs = BlobStore(tmp_path/'blobs')
    if lane == 'firmware':
        elf, binary = firmware()
        outputs = {'outputs/firmware.elf': elf, 'outputs/firmware.bin': binary}
    else:
        outputs = {'outputs/companion-web.zip': archive(web_files())}
    refs = await ArtifactService(store, blobs).publish_native(scope, task,
        [{'path': 'source.txt', 'role': 'source', 'content': b'Never execute this candidate', 'expectedSha256': None}]
        +[{'path': name, 'role': 'output', 'content': raw} for name, raw in outputs.items()],
        {'nativeJobId': 'producer', 'imageDigest': pins['imageDigest'], 'requestHash': fingerprint(pins)})
    with store.transaction() as session:
        job = session.get(NativeJob, 'producer')
        job.source_manifest = {refs[0].path: refs[0].sha256}
        objective = session.get(Objective, scope.run_id)
        objective.data = {**objective.data, 'results': {lane: {'artifacts': [ref.model_dump(mode='json') for ref in refs]}}}
        if mutation == 'job_hash':
            job.request = {**job.request, 'tampered': True}
        elif mutation == 'source':
            job.source_manifest = {'source.txt': 'f'*64}
        elif mutation == 'board':
            if lane == 'firmware':
                project = session.get(Project, scope.project_id)
                project.data = {**project.data, 'interfaces': {'firmware': {'board': 'other'}}}
            else:
                job.project_id = 'other'
    subject = ValidationInput(candidate_id='candidate', subject_hash='b'*64, project_id=scope.project_id,
                              revision=scope.revision, sources=refs[:1], outputs=refs[1:])
    if mutation == 'output_subset':
        subject = subject.model_copy(update={'outputs': subject.outputs[:-1]})
    calls = []
    async def run(directory, ident, **kwargs):
        request = json.loads((directory/'request.json').read_text())
        assert {value['path'] for value in request['files']} == set(outputs)
        assert not (directory/refs[0].sha256).exists()  # Candidate source is never staged or executed.
        calls.append(ident)
        result = inspect_request(request, directory)
        if mutation == 'context_after':
            with store.transaction() as session:
                session.get(Project, scope.project_id).advance_revision()
        return {**result, 'protocol': PROTOCOL, 'lane': lane,
            'executionId': 'other' if mutation == 'result_binding' else ident, 'subjectHash': subject.subject_hash,
            'checkerSourceSha256': source_hashes()['export_check.py']}
    adapter = ExportChecker(repo, blobs, SimpleNamespace(lane=lane, run=run), tmp_path/'control')
    if mutation == 'none':
        report = await adapter.invoke(scope, subject)
        assert report.status.value == 'PASS'
        assert report.measurements['sourceBuildReproduced'] is False
        assert len(calls) == 1
    else:
        with pytest.raises(DomainError):
            await adapter.invoke(scope, subject)
        assert bool(calls) == (mutation in {'result_binding', 'context_after'})
    assert not list((tmp_path/'control').iterdir())
    store.engine.dispose()


async def test_runtime_composes_all_fifteen_policies_only_with_explicit_reviewed_installations(tmp_path, monkeypatch):
    import os
    from pathlib import Path

    from pydantic import SecretStr
    from test_pcb_validation import qualification as pcb_qualification
    from test_runtime import test_persistence

    from kleos.infrastructure.runtime import Runtime
    from kleos.infrastructure.settings import Settings
    from kleos.validation import cad
    from kleos.validation.policies import POLICY_LANES

    for name in list(os.environ):
        if name.startswith('KLEOS_'):
            monkeypatch.delenv(name)
    configs = {}
    for lane in ('pcb', 'firmware', 'application'):
        evidence = pcb_qualification() if lane == 'pcb' else qualification(lane)
        path = tmp_path/(lane+'.json')
        path.write_text(json.dumps(evidence))
        configs[lane+'_checker_installation'] = {'imageDigest': evidence['imageId'], 'qualificationReference': str(path)}
    configs['cad_checker_installation'] = {
        'imageDigest': 'sha256:e3c5823091b7dde83d1043bd455ef96567939424ab447a3efed1149dc1030e69',
        'qualificationReference': str(Path(cad.__file__).parent/'qualification/cad-check-exact-image.json')}
    common = dict(_env_file=None, environment='test', database_url=f'sqlite:///{tmp_path}/runtime.db',
        artifact_root=tmp_path/'artifacts', native_control_root=tmp_path/'native', checker_control_root=tmp_path/'checkers',
        slicer_control_root=tmp_path/'slicing', session_secret=SecretStr('s'*40))
    runtime = Runtime(Settings(**common, **configs), persistence_factory=test_persistence, worker_locks=object())
    try:
        for key, lanes in POLICY_LANES.items():
            assert len(runtime.checker_registry.snapshot(key)['checkers']) == len(lanes)
        for key in ('firmware.export-integrity', 'application.web-bundle-integrity'):
            adapter = runtime.checker_registry.checker(key).invoke.__self__
            assert adapter.process.capacity is runtime.capacity
            assert adapter.process.docker is runtime.docker
        assert runtime.services.engineering.verification.registry is runtime.checker_registry
    finally:
        await runtime.docker.client.aclose()
        runtime.projects.engine.dispose()
    # Source/image evidence is deployment input, never inferred from generation qualification.
    (tmp_path/'application.json').write_text('{}')
    with pytest.raises(DomainError, match='independent review'):
        Runtime(Settings(**common, **configs), persistence_factory=test_persistence, worker_locks=object())


@pytest.mark.parametrize('mutation', ['nul_name', 'file_directory', 'case_prefix', 'local_name', 'unicode_extra', 'entry_count'])
def test_raw_zip_identity_cannot_alias_reader_or_preview(mutation):
    files = web_files()
    if mutation == 'nul_name':
        files['index.htmlXevil'] = files.pop('index.html')
    elif mutation == 'file_directory':
        files['assets'] = b'not a directory'
    elif mutation == 'case_prefix':
        files['Assets/other.txt'] = b'case alias'
    raw = bytearray(archive(files))
    if mutation == 'nul_name':
        raw = raw.replace(b'index.htmlXevil', b'index.html\0evil')
    elif mutation == 'local_name':
        raw[30] = ord('X')
    elif mutation == 'unicode_extra':
        entry = zipfile.ZipInfo('extra.txt')
        entry.extra = struct.pack('<HH', 0x7075, 0)
        raw = archive(files, [(entry, b'unsupported identity')])
    elif mutation == 'entry_count':
        offset = raw.rfind(b'PK\x05\x06')
        struct.pack_into('<HH', raw, offset+8, 4097, 4097)
    with pytest.raises(InvalidExport):
        web_measurements(bytes(raw))


@pytest.mark.parametrize('html', [
    b'<script src="https://example.invalid/remote.js" src="flutter_bootstrap.js"></script>',
    b'<base href="https://example.invalid/" href="/"><script src="flutter_bootstrap.js"></script>',
])
def test_html_duplicate_attributes_cannot_hide_browser_first_value(html):
    files = web_files()
    files['index.html'] = html
    with pytest.raises(InvalidExport, match='Duplicate HTML'):
        web_measurements(archive(files))


def test_arm_exception_index_is_checked_as_bounded_file_backed_data():
    elf, binary = firmware()
    elf = bytearray(elf)
    struct.pack_into('<II', elf, 128, 0x70000001, 0x82)
    assert firmware_measurements(elf, binary, {'board': BOARD})['binaryMatchesElf']
    struct.pack_into('<I', elf, 124+20, 15)
    with pytest.raises(InvalidExport, match='exception-index'):
        firmware_measurements(elf, binary, {'board': BOARD})


def test_firmware_gap_fill_is_explicit_and_not_inferred_from_candidate():
    elf, binary = firmware()
    elf = bytearray(elf)
    # Two allocated sections with a bounded four-byte flash gap.
    struct.pack_into('<H', elf, 48, 3)
    struct.pack_into('<I', elf, 124+20, 8)
    struct.pack_into('<10I', elf, 164, 0, 1, 2, 12, 268, 4, 0, 0, 4, 0)
    good = binary[:8]+b'\xff'*4+binary[12:]
    assert firmware_measurements(elf, good, {'board': BOARD})['binaryGapFill'] == 255
    with pytest.raises(InvalidExport, match='differs'):
        firmware_measurements(elf, binary, {'board': BOARD})
