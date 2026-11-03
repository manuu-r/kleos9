import hashlib
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from kleos.artifacts.storage import BlobStore
from kleos.contracts.models import ArtifactRef, DomainError
from kleos.execution.docker import DockerClient
from kleos.projects.store import fingerprint
from kleos.validation.pcb import EXPECTED_CASES, PcbChecker, install_pcb_checker, source_hashes
from kleos.validation.pcb_check import (
    PROTOCOL,
    InvalidBoard,
    UnsupportedBoard,
    check_correspondence,
    inspect_request,
    netlist_parts,
    stage_inputs,
)
from kleos.validation.pcb_process import CHECKER_RESOURCES, IsolatedPcbProcess, checker_spec
from kleos.validation.service import CheckerRegistry, ValidationInput


def contract():
    return {'references': [{'reference': 'J1', 'value': 'Conn_01x02',
        'footprint': 'Local:Header', 'pins': {'1': 'GND', '2': 'VCC'}}],
        'conceptReferences': {'connector': 'J1'}, 'constraints': {'maxWidthMm': 42,
        'maxHeightMm': 32, 'minTrackWidthMm': .25, 'minClearanceMm': .2, 'copperLayers': ['F.Cu', 'B.Cu']}}


def measurement():
    return {'references': {'J1': contract()['references'][0]}, 'positions': {'J1': [100, 200, 0, 0]},
        'outline': [[[0, 0], [10, 0]], [[10, 0], [10, 10]], [[0, 10], [10, 10]], [[0, 0], [0, 10]]],
        'pads': {'J1': {'1': {'position': [100, 200], 'size': [200, 200], 'layers': [0, 31]}}},
        'widthMm': 40, 'heightMm': 30, 'copperLayers': 2,
        'tracks': [{'widthMm': .25, 'net': 'VCC'}]}


NETLIST = b'''<export><components><comp ref="J1"><value>Conn_01x02</value><footprint>Local:Header</footprint></comp></components><nets><net name="GND"><node ref="J1" pin="1"/></net><net name="VCC"><node ref="J1" pin="2"/></net></nets></export>'''


def request(tmp_path):
    files = []
    for name in ('board.kicad_pcb', 'circuit.kicad_sch', 'outputs/routed.kicad_pcb'):
        data = ('bounded fixture '+name).encode()
        checksum = hashlib.sha256(data).hexdigest()
        (tmp_path/checksum).write_bytes(data)
        files.append({'path': name, 'size': len(data), 'sha256': checksum})
    return {'protocol': PROTOCOL, 'files': files, 'circuitContract': contract(),
            'circuitContractHash': fingerprint(contract())}


def fixed_tools(arguments, stage):
    if 'netlist' in arguments:
        (stage/'netlist.xml').write_bytes(NETLIST)
    elif 'erc' in arguments:
        (stage/'erc.json').write_text(json.dumps({'sheets': [{'violations': []}]}))
    else:
        (stage/'drc.json').write_text(json.dumps({'violations': [], 'unconnected_items': []}))
    return 0


def test_fresh_rule_commands_and_board_comparison_ignore_generator_reports(tmp_path):
    req = request(tmp_path)
    stage = tmp_path/'stage'
    stage.mkdir()
    calls = []
    def execute(args, stage):
        calls.append(args)
        assert not (stage/'outputs/erc.json').exists()
        return fixed_tools(args, stage)
    result = inspect_request(req, tmp_path, stage, execute=execute, read_board=lambda _: measurement())
    assert result['status'] == 'PASS'
    assert result['measurements']['ruleViolationCounts'] == {'erc': 0, 'drc': 0}
    assert len(calls) == 3 and all(args[0] == 'kicad-cli' for args in calls)
    assert '--exit-code-violations' in calls[1]
    rules = json.loads((stage/'routed.kicad_pro').read_text())
    assert 'drc_exclusions' not in rules
    assert '0.2mm' in (stage/'routed.kicad_dru').read_text()


@pytest.mark.parametrize('change', ['reference', 'net', 'position', 'pad_position', 'pad_size', 'pad_layers', 'outline', 'width', 'bounds', 'empty'])
def test_actual_mismatches_fail(change):
    source, routed = measurement(), measurement()
    if change == 'reference':
        routed['references'] = {'R99': routed['references']['J1']}
    elif change == 'net':
        routed['references']['J1']['pins']['1'] = 'OTHER'
    elif change == 'position':
        routed['positions']['J1'][0] += 1
    elif change.startswith('pad_'):
        routed['pads']['J1']['1'][change.removeprefix('pad_')][0] += 1
    elif change == 'outline':
        routed['outline'][0][0][0] += 1
    elif change == 'width':
        routed['tracks'][0]['widthMm'] = .1
    elif change == 'bounds':
        routed['widthMm'] = float('nan')
    else:
        routed['tracks'] = []
    with pytest.raises(InvalidBoard):
        check_correspondence(contract(), netlist_parts(NETLIST), source, routed)


@pytest.mark.parametrize('kind', ['erc', 'drc'])
def test_generated_empty_reports_cannot_hide_independent_rule_failures(tmp_path, kind):
    req = request(tmp_path)
    stage = tmp_path/'stage'
    stage.mkdir()
    def fail(args, stage):
        fixed_tools(args, stage)
        if kind in args:
            return 5
        return 0
    with pytest.raises(InvalidBoard, match=kind.upper()):
        inspect_request(req, tmp_path, stage, execute=fail, read_board=lambda _: measurement())


def test_report_schema_and_unsupported_layers_do_not_become_pass(tmp_path):
    source, routed = measurement(), measurement()
    routed['copperLayers'] = 4
    with pytest.raises(UnsupportedBoard):
        check_correspondence(contract(), netlist_parts(NETLIST), source, routed)
    req = request(tmp_path)
    stage = tmp_path/'stage'
    stage.mkdir()
    def malformed(args, stage):
        fixed_tools(args, stage)
        if 'drc' in args:
            (stage/'drc.json').write_text('{}')
        return 0
    with pytest.raises(UnsupportedBoard, match='DRC'):
        inspect_request(req, tmp_path, stage, execute=malformed, read_board=lambda _: measurement())


@pytest.mark.parametrize('mutation', ['hash', 'duplicate', 'escape', 'report', 'symlink'])
def test_input_closure_rejects_unsafe_or_generator_controlled_files(tmp_path, mutation):
    req = request(tmp_path)
    if mutation == 'hash':
        (tmp_path/req['files'][0]['sha256']).write_bytes(b'tampered')
    elif mutation == 'duplicate':
        req['files'].append(req['files'][0])
    elif mutation == 'escape':
        req['files'][0]['path'] = '../escape'
    elif mutation == 'report':
        req['files'][0]['path'] = 'outputs/drc.json'
    else:
        path = tmp_path/req['files'][0]['sha256']
        path.unlink()
        path.symlink_to(tmp_path/req['files'][1]['sha256'])
    stage = tmp_path/'stage'
    stage.mkdir()
    with pytest.raises(InvalidBoard):
        stage_inputs(req, tmp_path, stage)


def test_netlist_entities_and_duplicated_pins_fail():
    for content in [b'<!DOCTYPE x [<!ENTITY z "x">]><export/>',
                    NETLIST.replace(b'pin="2"', b'pin="1"')]:
        with pytest.raises(InvalidBoard):
            netlist_parts(content)


def test_checker_spec_fixed_and_no_privileged_or_writable_input():
    spec = checker_spec('sha256:'+'a'*64, '/trusted/input', 'check', ['SECRET_KEY'])
    assert spec['Entrypoint'] == ['/usr/bin/timeout', '--signal=KILL', '175', '/usr/bin/python3', '-I', '/opt/kleos-pcb-checker/pcb_check.py']
    assert 'SECRET_KEY=' in spec['Env']
    assert spec['User'] == '10001:10001'
    assert spec['HostConfig']['ReadonlyRootfs'] is True
    assert spec['HostConfig']['Mounts'][0]['ReadOnly'] is True
    assert spec['HostConfig']['NetworkMode'] == 'none'
    assert spec['HostConfig']['CapDrop'] == ['ALL']
    assert CHECKER_RESOURCES['scratchBytes'] == 256*1024**2


def qualification():
    value = {'schemaVersion': 'kleos-pcb-check-qualification-v1', 'qualified': True,
        'imageId': 'sha256:'+'a'*64, 'protocol': PROTOCOL, 'sourceOverride': False,
        'deployment': 'local', 'containerChecksPassed': True, 'sourceHashes': source_hashes(),
        'caseStatuses': EXPECTED_CASES, 'qualifiedBy': 'fixture-runner',
        'networkPolicy': 'docker-desktop-daemon-seccomp', 'resources': CHECKER_RESOURCES, 'watchdogSeconds': 175,
        'daemonSeccompSha256': fingerprint(__import__('kleos.execution.security', fromlist=['daemon_socket_profile']).daemon_socket_profile())}
    value['independentReview'] = {'reviewer': 'separate-reviewer', 'reviewedAt': datetime.now(UTC).isoformat(),
                                'approved': True, 'evidenceSha256': fingerprint(value)}
    return value


@pytest.mark.parametrize('mutation', ['missing_review', 'same_reviewer', 'stale_source', 'missing_case', 'wrong_image', 'override'])
def test_no_install_without_exact_reviewed_qualification(tmp_path, mutation):
    evidence = qualification()
    if mutation == 'missing_review':
        evidence.pop('independentReview')
    elif mutation == 'same_reviewer':
        evidence['independentReview']['reviewer'] = 'fixture-runner'
    elif mutation == 'stale_source':
        evidence['sourceHashes']['pcb_check.py'] = 'b'*64
    elif mutation == 'missing_case':
        evidence['caseStatuses'] = {'valid_board': 'PASS'}
    elif mutation == 'wrong_image':
        evidence['imageId'] = 'sha256:'+'b'*64
    else:
        evidence['sourceOverride'] = True
    path = tmp_path/'report.json'
    path.write_text(json.dumps(evidence))
    adapter = SimpleNamespace(process=SimpleNamespace(image='sha256:'+'a'*64, timeout=180), invoke=AsyncMock())
    with pytest.raises(DomainError, match='evidence'):
        install_pcb_checker(CheckerRegistry(), adapter, path)


def test_report_is_bounded_not_symlink_and_registration_is_explicit(tmp_path):
    path = tmp_path/'report.json'
    path.write_text(json.dumps(qualification()))
    adapter = SimpleNamespace(process=SimpleNamespace(image='sha256:'+'a'*64, timeout=180), invoke=AsyncMock())
    registry = CheckerRegistry()
    config = install_pcb_checker(registry, adapter, path)
    assert config.key == 'pcb.board-rules'
    with pytest.raises(DomainError):
        registry.snapshot('pcb.board-rules')  # Installation alone is not a release policy.
    link = tmp_path/'link'
    link.symlink_to(path)
    with pytest.raises(DomainError):
        install_pcb_checker(CheckerRegistry(), adapter, link)


async def test_adapter_checks_exact_refs_hashes_and_result_identity(tmp_path, monkeypatch):
    blobs = BlobStore(tmp_path/'blobs')
    refs = []
    for name in ('board.kicad_pcb', 'circuit.kicad_sch', 'outputs/routed.kicad_pcb'):
        data = name.encode()
        refs.append(ArtifactRef(id=name, path=name, sha256=blobs.put(data), size=len(data), revision=1))
    from contextlib import contextmanager
    @contextmanager
    def transaction():
        yield SimpleNamespace(get=lambda model, ident: SimpleNamespace(lane='pcb'))
    repo = SimpleNamespace(resolve_artifacts=AsyncMock(return_value=refs), assert_active=AsyncMock(),
                           store=SimpleNamespace(transaction=transaction), _check=lambda *args: None)
    scope = SimpleNamespace(project_id='project', revision=1, deadline_at=datetime.now(UTC)+timedelta(seconds=10))
    subject = ValidationInput(candidate_id='candidate', subject_hash='b'*64, project_id='project', revision=1,
                              sources=refs[:2], outputs=refs[2:])
    async def run(directory, ident, **kwargs):
        packet = json.loads((directory/'request.json').read_text())
        assert {item['path'] for item in packet['files']} == {ref.path for ref in refs}
        assert all((directory/item['sha256']).read_bytes() for item in packet['files'])
        await kwargs['assert_active']()
        return {'protocol': PROTOCOL, 'subjectHash': 'b'*64, 'executionId': ident,
                'checkerSourceSha256': source_hashes()['pcb_check.py'], 'status': 'PASS', 'measurements': {}}
    process = SimpleNamespace(run=run)
    adapter = PcbChecker(repo, blobs, process, tmp_path/'control')
    monkeypatch.setattr(adapter, '_authority', lambda *args: {'circuitContract': contract(),
        'circuitContractHash': fingerprint(contract()), 'acceptedIdentityHash': 'c'*64})
    report = await adapter.invoke(scope, subject)
    assert report.status.value == 'PASS' and report.measurements['physicalAcceptance'] is False
    assert not list((tmp_path/'control').iterdir())
    repo.resolve_artifacts.return_value = refs[::-1]
    with pytest.raises(DomainError, match='binding'):
        await adapter.invoke(scope, subject)


@pytest.mark.parametrize('failure', ['none', 'lost_create', 'renamed_still_live', 'delete_failed'])
async def test_isolated_process_keeps_capacity_until_immutable_removal_proven(tmp_path, failure):
    image, container = 'sha256:'+'a'*64, 'd'*64
    present, created, calls = True, False, []
    def respond(request):
        nonlocal present, created
        path = request.url.path
        calls.append((request.method, path))
        if path == '/info':
            return httpx.Response(200, json={'OperatingSystem': 'Docker Desktop'})
        if path.startswith('/images/'):
            return httpx.Response(200, json={'Id': image, 'Config': {'Labels': {'kleos.checker.protocol': PROTOCOL}}})
        if path == '/containers/create':
            created = True
            if failure == 'lost_create':
                raise httpx.ReadError('lost response')
            return httpx.Response(201, json={'Id': container})
        if path.endswith('/start'):
            return httpx.Response(204)
        if path.endswith('/logs'):
            raw = json.dumps({'status': 'PASS'}).encode()
            return httpx.Response(200, content=b'\x01\0\0\0'+len(raw).to_bytes(4, 'big')+raw)
        if request.method == 'DELETE':
            assert path == '/containers/'+container
            if failure == 'delete_failed':
                return httpx.Response(500)
            present = failure == 'renamed_still_live'
            return httpx.Response(204)
        if path.endswith('/json') and '/containers/' in path:
            if failure == 'lost_create' or not present:
                return httpx.Response(404)
            if failure == 'renamed_still_live' and created and 'kleos-pcb-check-' in path:
                # First polling request succeeds; cleanup later sees only immutable ID.
                prior = sum(1 for _, value in calls if value == path)
                if prior > 1:
                    return httpx.Response(404)
            return httpx.Response(200, json={'Id': container, 'Image': image, 'State': {'Running': False, 'ExitCode': 0},
                'Config': {'Labels': {'kleos.checker.protocol': PROTOCOL, 'kleos.checker.execution': 'example'}}})
        raise AssertionError((request.method, path))
    capacity = SimpleNamespace(reserve=lambda *args, **kwargs: True, release=lambda *args: calls.append(('RELEASE', 'pool')))
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond), base_url='http://docker') as client:
        process = IsolatedPcbProcess(DockerClient(client=client), image, capacity=capacity)
        if failure == 'none':
            assert await process.run(tmp_path, 'example', project_id='project') == {'status': 'PASS'}
        else:
            with pytest.raises((httpx.HTTPError, DomainError)):
                await process.run(tmp_path, 'example', project_id='project')
    assert ('RELEASE', 'pool') in calls if failure == 'none' else ('RELEASE', 'pool') not in calls


@pytest.mark.parametrize('mutation', ['none', 'contract', 'job', 'source', 'other_project'])
async def test_real_accepted_authority_and_native_provenance_are_required(tmp_path, mutation):
    from kleos.artifacts.service import ArtifactService
    from kleos.contracts.models import Actor, TaskSpec
    from kleos.execution.gateway import NativeJob
    from kleos.orchestration.engine import RunScope
    from kleos.projects.graph_repository import GraphRepository
    from kleos.projects.interfaces import accepted_circuit_identity
    from kleos.projects.store import Project, ProjectStore

    store = ProjectStore(f'sqlite:///{tmp_path}/authority.db')
    store.initialize_for_tests()
    actor = Actor(tenant_id='tenant', user_id='owner')
    created = store.create(actor, 'Check circuit', 'create')
    with store.transaction() as session:
        project = session.get(Project, created['projectId'])
        project.data = {**project.data, 'interfaces': {'circuit': contract()},
                        'approvedDesign': {'peripheralConceptIds': ['connector']}}
    submitted = store.submit(actor, created['projectId'], 'Build', 1, 'build')
    scope = RunScope(actor=actor, project_id=created['projectId'], run_id=submitted['runId'],
                     revision=submitted['revision'], deadline_at=datetime.now(UTC)+timedelta(minutes=5))
    task = TaskSpec(id='pcb', capability='pcb', objective='PCB', allowed_paths=['board.kicad_pcb', 'circuit.kicad_sch'])
    repo = GraphRepository(store)
    await repo.publish_plan(scope, tasks=[task], superseded={}, turn=0, summary='PCB', question=None)
    with store.transaction() as session:
        project = session.get(Project, scope.project_id)
        pins = {'imageDigest': 'sha256:'+'a'*64, 'circuitContract': contract(),
                'circuitContractHash': fingerprint(contract()), 'acceptedCircuitIdentity': accepted_circuit_identity(session, project)}
        session.add(NativeJob(id='producer', project_id=scope.project_id, run_id=scope.run_id, task_id='pcb',
            task_hash='b'*64, request_hash=fingerprint(pins), request=pins, status='finished', source_manifest={}, active_action=None))
    blobs = BlobStore(tmp_path/'blobs')
    artifact_service = ArtifactService(store, blobs)
    refs = await artifact_service.publish_native(scope, task, [
        {'path': name, 'role': 'source' if name != 'outputs/routed.kicad_pcb' else 'output',
         'content': name.encode(), 'expectedSha256': None}
        for name in ['board.kicad_pcb', 'circuit.kicad_sch', 'outputs/routed.kicad_pcb']],
        {'nativeJobId': 'producer', 'imageDigest': pins['imageDigest'], 'requestHash': fingerprint(pins)})
    subject = ValidationInput(candidate_id='candidate', subject_hash='b'*64, project_id=scope.project_id,
                              revision=scope.revision, sources=refs[:2], outputs=refs[2:])
    adapter = PcbChecker(repo, blobs, SimpleNamespace(), tmp_path/'control')
    with store.transaction() as session:
        job = session.get(NativeJob, 'producer')
        job.source_manifest = {ref.path: ref.sha256 for ref in refs[:2]}
        if mutation == 'contract':
            project = session.get(Project, scope.project_id)
            value = contract()
            value['references'][0]['value'] = 'OTHER'
            project.data = {**project.data, 'interfaces': {'circuit': value}}
        elif mutation == 'job':
            job.request = {**job.request, 'untracked': True}
        elif mutation == 'source':
            job.source_manifest = {refs[0].path: 'f'*64}
        elif mutation == 'other_project':
            job.project_id = 'different-project'
    if mutation == 'none':
        assert adapter._authority(scope, subject, refs[2])['circuitContract'] == contract()
    else:
        with pytest.raises(DomainError):
            adapter._authority(scope, subject, refs[2])
    store.engine.dispose()


async def test_actual_runtime_installs_only_reviewed_checker_and_composes_policies(tmp_path, monkeypatch):
    import os

    from pydantic import SecretStr
    from test_runtime import test_persistence

    from kleos.infrastructure.runtime import Runtime
    from kleos.infrastructure.settings import Settings
    from kleos.validation import cad

    for name in list(os.environ):
        if name.startswith('KLEOS_'):
            monkeypatch.delenv(name)
    evidence = tmp_path/'pcb-report.json'
    evidence.write_text(json.dumps(qualification()))
    settings = Settings(_env_file=None, environment='test', database_url=f'sqlite:///{tmp_path}/runtime.db',
        artifact_root=tmp_path/'artifacts', native_control_root=tmp_path/'native', checker_control_root=tmp_path/'checkers',
        slicer_control_root=tmp_path/'slicing', session_secret=SecretStr('s'*40),
        cad_checker_installation={'imageDigest': 'sha256:e3c5823091b7dde83d1043bd455ef96567939424ab447a3efed1149dc1030e69',
            'qualificationReference': str(__import__('pathlib').Path(cad.__file__).parent/'qualification/cad-check-exact-image.json')},
        pcb_checker_installation={'imageDigest': 'sha256:'+'a'*64, 'qualificationReference': str(evidence)})
    runtime = Runtime(settings, persistence_factory=test_persistence, worker_locks=object())
    try:
        assert set(runtime.checker_registry.snapshot('digital.cad-pcb')['checkers']) == {'cad.export-integrity', 'pcb.board-rules'}
        assert runtime.services.engineering.verification is runtime.verification
        assert runtime.verification.registry is runtime.checker_registry
    finally:
        await runtime.docker.client.aclose()
        runtime.projects.engine.dispose()
    bad = qualification()
    bad.pop('independentReview')
    evidence.write_text(json.dumps(bad))
    with pytest.raises(DomainError, match='independent review'):
        Runtime(settings, persistence_factory=test_persistence, worker_locks=object())


def test_reviewed_host_source_change_repins_even_with_same_checker_image(tmp_path, monkeypatch):
    import kleos.validation.pcb as module

    path = tmp_path/'report.json'
    first = qualification()
    path.write_text(json.dumps(first))
    adapter = SimpleNamespace(process=SimpleNamespace(image=first['imageId'], timeout=180), invoke=AsyncMock())
    old = install_pcb_checker(CheckerRegistry(), adapter, path)
    new_hashes = {**source_hashes(), 'pcb_process.py': 'f'*64}
    monkeypatch.setattr(module, 'source_hashes', lambda: new_hashes)
    second = qualification()
    second['sourceHashes'] = new_hashes
    payload = {key: value for key, value in second.items() if key != 'independentReview'}
    second['independentReview']['evidenceSha256'] = fingerprint(payload)
    path.write_text(json.dumps(second))
    new = install_pcb_checker(CheckerRegistry(), adapter, path)
    assert old.key == new.key and old.installation_hash != new.installation_hash
