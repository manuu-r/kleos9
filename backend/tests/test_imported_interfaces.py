"""Imported PCB identity authority, using the fresh native qualification source fixture.

No KiCad, generated code, model provider or physical tool runs in these tests.
"""
import base64
import copy
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from kleos.api.design import DesignFacade
from kleos.api.interfaces import InterfaceFacade
from kleos.api.services import ApplicationServices
from kleos.artifacts.service import Artifact, ArtifactService
from kleos.artifacts.storage import BlobStore
from kleos.capabilities.design.service import DesignService
from kleos.contracts.models import Actor, DomainError, Observation, TaskResult, TaskSpec
from kleos.execution.docker import NativeSettings
from kleos.execution.gateway import NativeGateway
from kleos.orchestration.engine import RunScope
from kleos.projects.graph_repository import GraphRepository
from kleos.projects.interfaces import InterfaceProposal, InterfaceService, accepted_circuit_identity
from kleos.projects.store import Objective, Project, ProjectStore

SOURCES = json.loads((Path(__file__).parents[1] / 'src/kleos/execution/qualification/pcb-fixture-sources.json').read_text())
CIRCUIT = {'references': [
    {'reference': 'J1', 'value': 'Connector', 'footprint': 'Fixture:TwoPin', 'pins': {'1': '/NET_A', '2': '/NET_B'}},
    {'reference': 'R1', 'value': '1k', 'footprint': 'Fixture:TwoPin', 'pins': {'1': '/NET_A', '2': '/NET_B'}}],
    'conceptReferences': {}, 'constraints': {'maxWidthMm': 42, 'maxHeightMm': 32,
        'minTrackWidthMm': .25, 'minClearanceMm': .2, 'copperLayers': ['F.Cu', 'B.Cu']}}


@pytest.fixture
def imported(tmp_path):
    projects = ProjectStore(f'sqlite:///{tmp_path / "import.db"}')
    projects.initialize_for_tests()
    actor = Actor(tenant_id='tenant', user_id='owner')
    ident = projects.create(actor, 'Route this existing PCB; preserve placement and references.', 'create')['projectId']
    artifacts = ArtifactService(projects, BlobStore(tmp_path / 'files'))
    for index, (path, content) in enumerate(sorted(SOURCES.items())):
        with projects.transaction() as session:
            revision = session.get(Project, ident).context_revision
        artifacts.upload(actor, ident, {'expectedEngineeringRevision': revision,
            'sha256': hashlib.sha256(content.encode()).hexdigest(),
            'file': {'path': path, 'content': content, 'mediaType': 'text/plain'}}, f'upload-{index}')
    yield InterfaceService(projects, artifacts), actor, ident
    projects.engine.dispose()


def row(imported):
    service, actor, ident = imported
    with service.projects.transaction() as session:
        return service.projects.authorized(session, actor, ident)


def proposed(imported):
    return {'summary': 'Route the imported board without a visual concept; retain J1/R1.',
        'interfaces': {'circuit': copy.deepcopy(CIRCUIT)},
        'circuitIdentity': {'mode': 'imported_no_concept',
            'sourceArtifactIds': [row(imported).data['sourceManifest']['board.kicad_pcb']]},
        'limitations': ['Source identity only; electrical checks and geometry preservation are separate.']}


def queue(imported, key='solve'):
    service, actor, ident = imported
    receipt = service.solve(actor, ident, row(imported).revision, key,
        objective='Route the imported board only; no visual concept is needed.')
    return RunScope(actor=actor, project_id=ident, run_id=receipt['runId'], revision=receipt['revision'],
        deadline_at=datetime.now(UTC) + timedelta(minutes=5))


def publish(imported, candidate=None, *, key='solve'):
    service, _, _ = imported
    scope = queue(imported, key)
    candidate = candidate or proposed(imported)
    service.publish(scope, TaskResult(task_id='interfaces', status='succeeded', summary='Imported identity candidate',
        observations=[Observation(name='interfaces.proposal', status='UNKNOWN', details={'candidate': candidate})]))
    with service.projects.transaction() as session:
        session.get(Objective, scope.run_id).status = 'succeeded'
    return scope


def accepted(imported):
    publish(imported)
    service, actor, ident = imported
    return service.approve(actor, ident, row(imported).revision, 'approve', message=True)


def identity(imported):
    service, actor, ident = imported
    with service.projects.transaction() as session:
        project = service.projects.authorized(session, actor, ident)
        return accepted_circuit_identity(session, project)


def test_no_concept_requires_explicit_complete_approval_of_current_source_hashes(imported):
    service, actor, ident = imported
    original = copy.deepcopy(row(imported).data['sourceManifest'])
    scope = publish(imported)
    with pytest.raises(DomainError) as error:
        identity(imported)
    assert error.value.code == 'interface_approval_required'
    project = row(imported)
    pending = project.data['state']['interfaceProposal']
    assert pending['circuitIdentity']['mode'] == 'imported_no_concept'
    assert len(pending['circuitIdentity']['sourceArtifacts']) == len(SOURCES)
    assert 'without a visual concept' in project.data['state']['conversation'][-1]['text']
    for ref in pending['circuitIdentity']['sourceArtifacts']:
        assert ref['sha256'] == hashlib.sha256(SOURCES[ref['path']].encode()).hexdigest()
        assert ref['sha256'] in project.data['state']['conversation'][-1]['text']
    facade = InterfaceFacade(service)
    command = {'type': 'project.approveInterfaces', 'expectedRevision': project.revision, 'payload': {
        'projectId': ident, 'proposalId': pending['id'], 'proposalHash': pending['hash']}}
    receipt = facade.command(actor, command, 'approve')
    assert facade.command(actor, command, 'approve') == receipt
    binding = identity(imported)
    assert binding['mode'] == 'imported_no_concept' and binding['peripheralConceptIds'] == []
    assert binding['importedSourceIds'] == [original['board.kicad_pcb']]
    assert binding['approval']['proposalId'] == scope.run_id
    assert len(binding['bindingHash']) == 64
    project = row(imported)
    assert project.data.get('approvedDesign') is None
    assert project.data['sourceManifest'] == original and project.data['interfaces']['circuit'] == CIRCUIT
    assert project.data['acceptedInterfaces']['engineeringValidated'] is False


@pytest.mark.parametrize('approved', [{}, {'peripheralConceptIds': []}, {'peripheralConceptIds': ['connector'], 'scene': {'preserve': True}}])
def test_import_declaration_never_downgrades_existing_approved_concept(imported, approved):
    service, _, ident = imported
    with service.projects.transaction() as session:
        project = session.get(Project, ident)
        project.data = {**project.data, 'approvedDesign': copy.deepcopy(approved)}
    with pytest.raises(DomainError, match='cannot be downgraded'):
        publish(imported)
    assert row(imported).data['approvedDesign'] == approved
    assert 'circuit' not in row(imported).data['interfaces']


def test_board_import_does_not_implicitly_declare_no_concept(imported):
    candidate = proposed(imported)
    candidate.pop('circuitIdentity')
    with pytest.raises(DomainError) as error:
        publish(imported, candidate)
    assert error.value.code == 'interface_mapping_invalid'


@pytest.mark.parametrize('change', ['foreign', 'missing_board', 'stale_id', 'not_uploaded', 'invalid_bytes'])
def test_import_identity_requires_actual_current_uploaded_board(imported, change):
    service, actor, ident = imported
    candidate = proposed(imported)
    board_id = candidate['circuitIdentity']['sourceArtifactIds'][0]
    if change == 'foreign':
        other = service.projects.create(actor, 'Other project', 'other')['projectId']
        content = SOURCES['board.kicad_pcb']
        uploaded = service.artifacts.upload(actor, other, {'expectedEngineeringRevision': 1,
            'sha256': hashlib.sha256(content.encode()).hexdigest(), 'file': {'path': 'board.kicad_pcb', 'content': content}}, 'foreign')
        candidate['circuitIdentity']['sourceArtifactIds'] = [uploaded['artifact']['id']]
    elif change == 'missing_board':
        candidate['circuitIdentity']['sourceArtifactIds'] = [row(imported).data['sourceManifest']['circuit.kicad_sch']]
    elif change == 'stale_id':
        content = SOURCES['board.kicad_pcb']
        service.artifacts.upload(actor, ident, {'expectedEngineeringRevision': row(imported).context_revision,
            'sha256': hashlib.sha256(content.encode()).hexdigest(), 'file': {'path': 'board.kicad_pcb', 'content': content}}, 'replace')
    elif change == 'not_uploaded':
        with service.projects.transaction() as session:
            session.get(Artifact, board_id).details = {'origin': 'native_candidate'}
    else:
        invalid = b'(kicad_pcb_forgery arbitrary data)'
        with service.projects.transaction() as session:
            board = session.get(Artifact, board_id)
            board.sha256, board.size = service.artifacts.blobs.put(invalid), len(invalid)
    with pytest.raises(DomainError):
        publish(imported, candidate)
    assert 'acceptedCircuitIdentity' not in row(imported).data


def test_board_hash_cannot_change_between_visible_proposal_and_approval(imported):
    service, actor, ident = imported
    publish(imported)
    before = row(imported)
    board_id = before.data['sourceManifest']['board.kicad_pcb']
    changed = SOURCES['board.kicad_pcb'].replace('J1', 'J9').encode()
    with service.projects.transaction() as session:
        board = session.get(Artifact, board_id)
        board.sha256, board.size = service.artifacts.blobs.put(changed), len(changed)
    # Even a metadata mutation that failed to advance revision cannot reuse approval.
    with pytest.raises(DomainError) as error:
        service.approve(actor, ident, before.revision, 'approve', message=True)
    assert error.value.code == 'interface_binding_stale'


@pytest.mark.parametrize('change', ['source_bytes', 'new_source', 'circuit', 'requirements', 'history', 'concept', 'metadata'])
def test_accepted_native_identity_is_exact_and_fails_when_its_binding_changes(imported, change):
    service, actor, ident = imported
    accepted(imported)
    before = identity(imported)
    if change == 'new_source':
        service.artifacts.upload(actor, ident, {'expectedEngineeringRevision': row(imported).context_revision,
            'sha256': hashlib.sha256(b'new').hexdigest(), 'file': {'path': 'new.txt', 'content': 'new'}}, 'new')
    else:
        with service.projects.transaction() as session:
            project = session.get(Project, ident)
            data = copy.deepcopy(project.data)
            if change == 'source_bytes':
                source = session.get(Artifact, data['sourceManifest']['board.kicad_pcb'])
                raw = SOURCES['board.kicad_pcb'].replace('J1', 'J9').encode()
                source.sha256, source.size = service.artifacts.blobs.put(raw), len(raw)
            elif change == 'circuit':
                data['interfaces']['circuit']['references'][0]['value'] = 'Changed'
            elif change == 'requirements':
                data['requirements'] = {'items': ['Changed PCB requirements']}
            elif change == 'history':
                data['interfaceApprovalHistory'] = []
            elif change == 'concept':
                data['approvedDesign'] = {'peripheralConceptIds': []}
            else:
                data['acceptedCircuitIdentity'].pop('approvedAt')
            project.data = data
    with pytest.raises(DomainError):
        identity(imported)
    assert before['approval']['approvedBy'] == actor.user_id


def test_policy_or_generation_revision_does_not_rewrite_exact_import_approval(imported):
    service, actor, ident = imported
    accepted(imported)
    original = identity(imported)
    service.projects.submit(actor, ident, 'Route only the accepted imported PCB', row(imported).revision, 'generate')
    assert identity(imported) == original
    with service.projects.transaction() as session:
        project = session.get(Project, ident)
        project.advance_revision(context=False)
        project.data = {**project.data, 'modelPolicy': {'coordination': 'changed'}}
    assert identity(imported) == original


def test_firmware_only_approval_preserves_prior_circuit_identity_and_sources(imported):
    service, actor, ident = imported
    accepted(imported)
    original = identity(imported)
    publish(imported, {'summary': 'Explicit firmware target only.',
        'interfaces': {'firmware': {'board': 'nrf52840dk/nrf52840'}}}, key='firmware')
    service.approve(actor, ident, row(imported).revision, 'approve-firmware', message=True)
    assert identity(imported) == original
    assert row(imported).data['interfaces']['firmware'] == {'board': 'nrf52840dk/nrf52840'}


async def test_real_native_admission_consumes_the_actual_approved_import_binding(imported, tmp_path):
    service, actor, ident = imported
    accepted(imported)
    expected = identity(imported)
    receipt = service.projects.submit(actor, ident, 'Route accepted imported sources', row(imported).revision, 'route')
    scope = RunScope(actor=actor, project_id=ident, run_id=receipt['runId'], revision=receipt['revision'],
        deadline_at=datetime.now(UTC)+timedelta(minutes=5))
    repository = GraphRepository(service.projects)
    task = TaskSpec(id='route', capability='pcb', objective='Route the exact imported project',
        input_artifact_ids=[ref['id'] for ref in expected['sourceArtifacts']], allowed_paths=sorted(SOURCES))
    # This test stops at production admission; the inert Docker object cannot run anything.
    gateway = NativeGateway(service.projects, service.artifacts,
        NativeSettings(tmp_path / 'native', {'pcb': 'sha256:'+'a'*64}), object(), repository)
    resolved = await gateway.authorize(scope, task, {})
    job = gateway._job(scope, task, resolved)
    assert job.request['acceptedCircuitIdentity'] == expected
    assert job.request['peripheralConceptIds'] == [] and job.request['circuitContract'] == CIRCUIT
    assert {source['path']: base64.b64decode(source['contentBase64']).decode()
            for source in job.request['sources']} == SOURCES
    incomplete = task.model_copy(update={'id': 'missing-library', 'input_artifact_ids': [expected['importedSourceIds'][0]]})
    with pytest.raises(DomainError) as error:
        gateway._job(scope, incomplete, await gateway.authorize(scope, incomplete, {}))
    assert error.value.code == 'imported_sources_required'


def test_imported_context_supplies_bounded_actual_board_text(imported):
    service, _, _ = imported
    scope = queue(imported)
    packet = service.request(scope)
    board = next(item for item in packet['importedSources'] if item['path'] == 'board.kicad_pcb')
    assert board['text'] == SOURCES['board.kicad_pcb']
    assert board['sha256'] == hashlib.sha256(board['text'].encode()).hexdigest()
    # The larger project file is explicitly not in the bounded inline source packet.
    project = next(item for item in packet['importedSources'] if item['path'] == 'board.kicad_pro')
    assert project['textIncluded'] is False and 'text' not in project


@pytest.mark.parametrize('text', ['Route this imported board', 'Make only a bracket that fits this existing board',
    'Add the missing firmware file'])
def test_imported_project_chat_preserves_dynamic_intent_without_mandatory_capture(imported, text):
    service, actor, ident = imported
    design = DesignFacade(DesignService(service.projects))
    facade = InterfaceFacade(service)
    body = {'type': 'project.message', 'expectedRevision': row(imported).revision,
        'payload': {'projectId': ident, 'text': text, 'autoResume': True, 'referenceImageIds': []}}
    assert not facade.handles(actor, body) and not design.handles(actor, body)
    services = ApplicationServices(service.projects, None, design=design, interfaces=facade)
    receipt = services.command(actor, body, 'dynamic')
    with service.projects.transaction() as session:
        run = session.get(Objective, receipt['runId'])
        assert run.data['objective'] == text and run.data.get('kind') != 'project.capture'
    assert row(imported).data.get('approvedDesign') is None
    assert design.handles(actor, {'type': 'project.capture', 'payload': {'projectId': ident}})


@pytest.mark.parametrize('identity', [
    {'mode': 'imported_no_concept', 'sourceArtifactIds': []},
    {'mode': 'imported_no_concept', 'sourceArtifactIds': ['board', 'board']},
    {'mode': 'approved_concept', 'sourceArtifactIds': ['board']},
    {'mode': 'imported_no_concept', 'sourceArtifactIds': ['board'], 'approved': True}])
def test_model_identity_schema_does_not_grant_approval(identity):
    with pytest.raises(ValidationError):
        InterfaceProposal.model_validate({'summary': 'Invalid identity', 'interfaces': {'circuit': CIRCUIT},
            'circuitIdentity': identity})
