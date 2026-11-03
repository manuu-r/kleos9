"""Engineering-file previews only: no generated code, CAD kernels or native builds."""
import json
import struct
from datetime import UTC, datetime, timedelta

import pytest

from kleos.api.previews import EngineeringPreviews
from kleos.artifacts.service import Artifact, ArtifactService, NativePublication
from kleos.artifacts.storage import BlobStore
from kleos.contracts.models import Actor, DomainError, TaskSpec
from kleos.execution.preview_geometry import PreviewGeometryError, pcb_geometry, sexpression, stl_mesh
from kleos.infrastructure.schema import metadata
from kleos.orchestration.engine import RunScope
from kleos.projects.graph_repository import GraphRepository
from kleos.projects.store import Objective, Project, ProjectStore


def stl(x=10):
    vertices = (x, 20, 30, x+2, 20, 30, x, 23, 30)
    return b'fixture'.ljust(80, b'\0') + struct.pack('<I', 1) + struct.pack('<12fH', 0, 0, 1, *vertices, 0)


BOARD = b'''(kicad_pcb (version 20241229)
 (gr_rect (start 10 20) (end 50 50) (layer "Edge.Cuts"))
 (footprint "Fixture:Resistor" (layer "F.Cu") (at 20 30)
  (property "Reference" "R1")
  (pad "1" smd rect (at -1 0) (size 1 2) (layers "F.Cu"))
  (pad "2" smd rect (at 1 0) (size 1 2) (layers "F.Cu")))
 (segment (start 19 30) (end 21 30) (width 0.25) (layer "F.Cu"))
 (via (at 21 30) (size .8) (drill .4)))'''


@pytest.fixture
async def fixture(tmp_path):
    metadata()
    store = ProjectStore(f'sqlite:///{tmp_path / "preview.db"}')
    store.initialize_for_tests()
    artifacts = ArtifactService(store, BlobStore(tmp_path / 'blobs'))
    actor = Actor(tenant_id='tenant', user_id='owner')
    project = store.create(actor, 'Preview actual files', 'create')
    run = store.submit(actor, project['projectId'], 'Build parts', 1, 'run')
    scope = RunScope(actor=actor, project_id=project['projectId'], run_id=run['runId'],
        revision=run['revision'], deadline_at=datetime.now(UTC)+timedelta(minutes=5))
    task = TaskSpec(id='cad', objective='Build separate pieces', capability='cad', allowed_paths=['model.py'])
    await GraphRepository(store).publish_plan(scope, tasks=[task], superseded={}, turn=0, summary='Build', question=None)
    files = [{'path': 'model.py', 'role': 'source', 'content': b'raise RuntimeError("MUST NEVER EXECUTE")', 'expectedSha256': None}]
    for ident, x in [('base', 10), ('lid', 50)]:
        files.extend([{'path': 'outputs/'+ident+'.stl', 'role': 'output', 'content': stl(x)},
                      {'path': 'outputs/'+ident+'.step', 'role': 'output', 'content': b'test STEP '+ident.encode()}])
    files.append({'path': 'outputs/parts.json', 'role': 'output', 'content': json.dumps({'parts': [
        {'id': ident, 'step': ident+'.step', 'stl': ident+'.stl'} for ident in ('base', 'lid')]}).encode()})
    refs = await artifacts.publish_native(scope, task, files, {'nativeJobId': 'job', 'requestHash': 'a'*64, 'imageDigest': 'sha256:'+'b'*64})
    with store.transaction() as session:
        objective = session.get(Objective, scope.run_id)
        objective.status = 'succeeded'
        objective.data = {**objective.data, 'results': {'cad': {'artifacts': [ref.model_dump(mode='json') for ref in refs]}}}
    yield store, artifacts, scope, refs, EngineeringPreviews(store, artifacts)
    store.engine.dispose()


def preview(fixture, operation='engineering_preview', actor=None):
    _, _, scope, _, service = fixture
    return service.query(actor or scope.actor, operation, {'projectId': scope.project_id})


def test_actual_parts_keep_manufacturing_coordinates_and_display_explosion(fixture):
    result = preview(fixture)
    mechanics = result['mechanics']
    assert mechanics['source'] == 'artifact' and mechanics['status'] == 'candidate'
    parts = mechanics['assembly']['parts']
    assert [part['id'] for part in parts] == ['base', 'lid']
    assert parts[0]['mesh']['triangles'][0][0] == [10, 20, 30]
    assert parts[1]['mesh']['triangles'][0][0] == [50, 20, 30]
    assert all(part['transform'] == {'translation': {'x': 0, 'y': 0, 'z': 0}, 'rotation': {'x': 0, 'y': 0, 'z': 0}} for part in parts)
    assert parts[0]['displayExplodeMm'][0] < 0 < parts[1]['displayExplodeMm'][0]
    assert 'MUST NEVER EXECUTE' in mechanics['code']
    assert len(result['files']) == 6
    assert result['application']['previewAvailable'] is False
    simulation = preview(fixture, 'circuit_simulation')
    assert simulation['status'] == 'UNKNOWN' and simulation['result'] is None


@pytest.mark.parametrize('mutation', ['revision', 'cancelled', 'superseded'])
def test_stale_cancelled_and_superseded_geometry_is_not_current(fixture, mutation):
    store, _, scope, _, _ = fixture
    with store.transaction() as session:
        if mutation == 'revision':
            session.get(Project, scope.project_id).advance_revision()
        else:
            run = session.get(Objective, scope.run_id)
            if mutation == 'cancelled':
                run.status = 'cancelled'
            else:
                run.data = {**run.data, 'superseded': {'cad': 'replacement'}}
    result = preview(fixture)
    assert result['mechanics']['source'] == 'unavailable' and 'assembly' not in result['mechanics']


@pytest.mark.parametrize('actor', [Actor(tenant_id='other', user_id='owner'), Actor(tenant_id='tenant', user_id='stranger')])
def test_preview_authorization_before_blob_io(fixture, actor):
    fixture[1].blobs.get = lambda *args: pytest.fail('Unauthorized content read')
    with pytest.raises(DomainError):
        preview(fixture, actor=actor)
    with pytest.raises(DomainError):
        preview(fixture, operation='circuit_simulation', actor=actor)


@pytest.mark.parametrize('mutation', ['metadata_hash', 'content_hash', 'size', 'receipt', 'project'])
def test_preview_rejects_changed_artifact_bindings(fixture, mutation):
    store, artifacts, scope, refs, _ = fixture
    ref = next(ref for ref in refs if ref.path.endswith('base.stl'))
    with store.transaction() as session:
        row = session.get(Artifact, ref.id)
        if mutation == 'metadata_hash':
            row.sha256 = 'f'*64
        elif mutation == 'size':
            row.size += 1
        elif mutation == 'project':
            row.project_id = 'another'
        elif mutation == 'receipt':
            session.delete(session.query(NativePublication).one())
    if mutation == 'content_hash':
        (artifacts.blobs.root/ref.sha256).write_bytes(b'corrupt')
    with pytest.raises(DomainError):
        preview(fixture)


def test_malformed_stl_cannot_be_replaced_by_planning_concept(fixture):
    store, artifacts, scope, refs, _ = fixture
    ref = next(ref for ref in refs if ref.path.endswith('base.stl'))
    # Keep publication binding intact while exercising parser with trusted hash IO stub.
    original = artifacts.blobs.get
    artifacts.blobs.get = lambda key: b'x'*ref.size if key == ref.sha256 else original(key)
    with store.transaction() as session:
        project = session.get(Project, scope.project_id)
        project.data = {**project.data, 'approvedDesign': {'parts': [{'id': 'pretend'}]}}
    mechanics = preview(fixture)['mechanics']
    assert mechanics['status'] == 'UNKNOWN' and mechanics['source'] == 'unavailable'
    assert 'assembly' not in mechanics


def test_binary_stl_normal_header_and_ascii_are_data_only():
    binary = b'solid misleading header'.ljust(80, b'\0') + stl()[80:]
    assert stl_mesh(binary)['triangles'][0][0] == [10, 20, 30]
    ascii_stl = b'solid fixture\nfacet normal 0 0 1\nouter loop\nvertex 10 20 30\nvertex 12 20 30\nvertex 10 23 30\nendloop\nendfacet\nendsolid fixture\n'
    assert stl_mesh(ascii_stl)['triangles'] == stl_mesh(binary)['triangles']


@pytest.mark.parametrize('raw', [b'', b'solid\nvertex 1 2 3\nendsolid', stl()[:-1],
    b' '*80+struct.pack('<I', 500000), b'solid\nfacet normal nan 0 1\nendsolid'])
def test_stl_malformed_and_resource_bounds(raw):
    with pytest.raises(PreviewGeometryError):
        stl_mesh(raw)


def test_mesh_sampling_has_independent_part_limit_and_full_bounds():
    raw = stl()[:80] + struct.pack('<I', 10) + b''.join(stl(index)[84:] for index in range(10))
    mesh = stl_mesh(raw, triangle_limit=3)
    assert len(mesh['triangles']) == 3 and mesh['sampled'] and mesh['triangleCount'] == 10
    assert mesh['bounds']['min'][0] == 0 and mesh['bounds']['max'][0] == 11


def test_pcb_reads_actual_origin_placement_pad_extents_and_tracks():
    pcb = pcb_geometry(BOARD)
    assert pcb['board'] == {'width': 40, 'height': 30} and pcb['originMm'] == [10, 20]
    assert pcb['components'] == [{'id': 'R1', 'label': 'R1', 'x': 10, 'y': 10, 'width': 3, 'height': 2, 'geometryKind': 'padEnvelope'}]
    assert pcb['traces'][0]['points'] == [{'x': 9, 'y': 10}, {'x': 11, 'y': 10}]
    assert any('vias' in item for item in pcb['limitations'])


@pytest.mark.parametrize('raw', [BOARD.replace(b'(at 20 30)', b'(at 20 30 90)'),
    BOARD.replace(b'(gr_rect', b'(gr_arc'), BOARD.replace(b'(width 0.25)', b'(width nan)'),
    b'('*65+b')'*65, BOARD+b' trailing', BOARD.replace(b'"R1"', b'"../R1"')])
def test_pcb_unsupported_or_unbounded_data_is_explicit(raw):
    with pytest.raises(PreviewGeometryError):
        pcb_geometry(raw)


def test_parser_does_not_resolve_paths_or_evaluate_code():
    parsed = sexpression(b'(kicad_pcb (model "/private/secret") (property "python" "__import__(os)"))')
    assert parsed[1] == ['model', '/private/secret']


async def test_actual_pcb_artifact_reaches_unchanged_viewer_shape(fixture):
    store, artifacts, scope, _, _ = fixture
    task = TaskSpec(id='pcb', objective='Route accepted board', capability='pcb', allowed_paths=[])
    with store.transaction() as session:
        session.get(Objective, scope.run_id).status = 'running'
    refs = await artifacts.publish_native(scope, task, [
        {'path': 'outputs/routed.kicad_pcb', 'role': 'output', 'content': BOARD}],
        {'nativeJobId': 'pcb-job', 'requestHash': 'd'*64, 'imageDigest': 'sha256:'+'e'*64})
    with store.transaction() as session:
        run = session.get(Objective, scope.run_id)
        run.data = {**run.data, 'results': {**run.data['results'], 'pcb': {'artifacts': [r.model_dump(mode='json') for r in refs]}}}
    result = preview(fixture)['electronics']
    assert result['source'] == 'artifact' and result['artifactId'] == refs[0].id
    assert result['pcb']['components'][0]['id'] == 'R1'
    assert result['pcb']['traces'][0]['width'] == .25


def test_current_release_retains_preview_after_acceptance_revision(fixture):
    from kleos.projects.store import fingerprint
    from kleos.validation.service import Release

    store, _, scope, refs, _ = fixture
    with store.transaction() as session:
        project = session.get(Project, scope.project_id)
        project.advance_revision()
        data = {'id': 'accepted', 'projectId': project.id, 'acceptedRevision': project.revision,
                'sources': [ref.model_dump(mode='json') for ref in refs if not ref.path.startswith('outputs/')],
                'outputs': [ref.model_dump(mode='json') for ref in refs if ref.path.startswith('outputs/')]}
        data['manifestHash'] = fingerprint(data)
        session.add(Release(id='accepted', project_id=project.id, candidate_id='fixture', revision=project.revision, data=data))
        project.data = {**project.data, 'acceptedReleaseId': 'accepted'}
    result = preview(fixture)
    assert result['mechanics']['status'] == 'accepted' and len(result['mechanics']['assembly']['parts']) == 2
    with store.transaction() as session:
        session.get(Project, scope.project_id).advance_revision()
    assert preview(fixture)['mechanics']['source'] == 'unavailable'


def test_malformed_assembly_does_not_return_partial_parts(fixture):
    store, _, _, refs, _ = fixture
    with store.transaction() as session:
        ref = next(ref for ref in refs if ref.path.endswith('lid.step'))
        row = session.get(Artifact, ref.id)
        row.details = {**row.details, 'nativeJobId': 'different-job'}
    with pytest.raises(DomainError):
        preview(fixture)


async def test_existing_http_preview_routes_keep_current_workbench_shapes(fixture):
    from types import SimpleNamespace

    from httpx import ASGITransport, AsyncClient

    from kleos.api import create_app

    _, _, scope, _, previews = fixture
    services = SimpleNamespace(authenticate=lambda *_: scope.actor, query=previews.query)
    async with AsyncClient(transport=ASGITransport(app=create_app(services)), base_url='http://test') as client:
        response = await client.get(f'/api/v1/projects/{scope.project_id}/engineering-preview')
        assert response.status_code == 200
        data = response.json()
        assert set(('revision', 'engineeringRevision', 'mechanics', 'electronics', 'firmware', 'application', 'files')).issubset(data)
        assert len(data['mechanics']['assembly']['parts']) == 2
        response = await client.get(f'/api/v1/projects/{scope.project_id}/circuit-simulation')
        assert response.status_code == 200
        assert response.json() == {'revision': scope.revision, 'engineeringRevision': scope.revision,
            'status': 'UNKNOWN', 'artifactId': None, 'artifactSha256': None, 'result': None,
            'reason': 'No independent circuit simulation is qualified for these artifacts.'}
