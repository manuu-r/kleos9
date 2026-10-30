import base64
import math
import struct

import pytest

from kleos.auth import Principal
from kleos.contracts import Command, DomainError
from kleos.db import Artifact, Project
from kleos.engineering_preview import EngineeringPreview, pcb_geometry, stl_mesh
from kleos.storage import artifact_key, digest


def create(platform):
    return platform.control.command(platform.principal, Command(type='project.create', payload={
        'brief': 'A small air quality device', 'autoStart': False}), 'preview-create')['projectId']


def test_preview_uses_accepted_interfaces_and_cannot_cross_tenants(platform):
    project_id = create(platform)
    with platform.db.transaction() as session:
        project = session.get(Project, project_id)
        project.interfaces = {'mechanical': {'externalMm': {'x': 38, 'y': 38, 'z': 11.2}}}
    service = EngineeringPreview(platform.db, platform.store)
    preview = service.read(platform.principal, project_id)
    assert preview['mechanics']['envelope'] == {'width': 38, 'height': 38, 'depth': 11.2}
    assert preview['mechanics']['source'] == 'interface'
    # Authorization is performed even when a revision has a cached preview.
    with pytest.raises(DomainError):
        service.read(Principal('someone', 'org-b', 'owner'), project_id)


@pytest.mark.parametrize('interfaces', [
    {'mechanical': None},
    {'mechanical': []},
    {'mechanical': {'externalMm': []}},
    {'mechanical': {'externalMm': {'x': 10**400, 'y': 30, 'z': 12}}},
    {'mechanical': {'components': None}},
    {'mechanical': {'components': {'unexpected': 'object'}}},
    {'mechanical': {'components': [{'id': 'board', 'positionMm': None}]}},
    {'mechanical': {'components': [{'id': 'board', 'sizeMm': []}]}},
    {'mechanical': {'components': [{'id': 'board', 'positionMm': {'x': 10**400}}]}},
    {'circuit': {'schemaVersion': 'circuit-netlist-v1', 'components': [{
        'id': 'led', 'reference': 'D1', 'symbol': 'Device:LED', 'value': 'LED',
        'footprint': 'LED_SMD:LED_0603', 'loadIds': ['led'], 'pins': {'1': 'VCC', '2': 'GND'},
    }]}, 'power': None},
])
def test_malformed_interface_shapes_return_bounded_fallback_instead_of_500(platform, interfaces):
    project_id = create(platform)
    with platform.db.transaction() as session:
        session.get(Project, project_id).interfaces = interfaces
    response = platform.client.get(f'/api/v1/projects/{project_id}/engineering-preview')
    assert response.status_code == 200
    preview = response.json()
    assert preview['source'] == 'deterministic'
    assert preview['mechanics']['source'] == 'interface'
    assert preview['electronics']['source'] == 'interface'
    assert len(preview['electronics']['pcb']['components']) <= 128
    assert preview['circuitContractCheck']['status'] in {'UNKNOWN', 'FAIL'}


@pytest.mark.parametrize('board', [
    b'(kicad_pcb (footprint))',
    b'(kicad_pcb ((footprint)))',
    b'(kicad_pcb (footprint "Broken" (at (bad) 4)))',
])
def test_malformed_pcb_artifacts_keep_endpoint_available(platform, board):
    project_id = create(platform)
    with platform.db.transaction() as session:
        project = session.get(Project, project_id)
        key = artifact_key('org-a', project_id, 'broken-pcb', 'broken.kicad_pcb', board)
        platform.store.put(key, board)
        session.add(Artifact(tenant_id='org-a', project_id=project_id,
            revision=project.engineering_revision, lane='electronics', name='broken.kicad_pcb',
            key=key, sha256=digest(board), size=len(board), media_type='application/x-kicad-pcb',
            status='candidate', provenance={}))
    response = platform.client.get(f'/api/v1/projects/{project_id}/engineering-preview')
    assert response.status_code == 200
    preview = response.json()
    assert preview['electronics']['source'] == 'interface'
    assert len(preview['files']) == 1
    assert any('not previewable' in item for item in preview['electronics']['limitations'])


def test_preview_cache_rechecks_revoked_same_tenant_project_membership(platform):
    from kleos.db import ProjectMembership

    project_id = create(platform)
    with platform.db.transaction() as session:
        membership = ProjectMembership(project_id=project_id, subject='reader', role='viewer')
        session.add(membership)
        session.flush()
        membership_id = membership.id
    service = EngineeringPreview(platform.db, platform.store)
    reader = Principal('reader', 'org-a', 'member')
    assert service.read(reader, project_id)['revision'] >= 1
    with platform.db.transaction() as session:
        session.delete(session.get(ProjectMembership, membership_id))
    with pytest.raises(DomainError):
        service.read(reader, project_id)


def test_stl_preview_bounds_and_rejects_nonfinite_coordinates():
    triangle = struct.pack('<12fH', 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0)
    content = b'\x00' * 80 + struct.pack('<I', 2000) + triangle * 2000
    assert 0 < len(stl_mesh(content)['triangles']) <= 1800
    bad = b'\x00' * 80 + struct.pack('<I', 1) + struct.pack(
        '<12fH', 0, 0, 1, math.nan, 0, 0, 1, 0, 0, 0, 1, 0, 0)
    with pytest.raises(ValueError):
        stl_mesh(bad)


def test_kicad_preview_decodes_only_data_and_normalizes_coordinates():
    board = b'''(kicad_pcb (version 20241229)
      (gr_rect (start 100 100) (end 130 120) (layer "Edge.Cuts"))
      (footprint "Resistor:R_0603" (at 110 105) (property "Reference" "R1") (property "Value" "10k"))
      (segment (start 110 105) (end 115 110) (layer "F.Cu")))'''
    view = pcb_geometry(board)
    assert view['board'] == {'width': 30, 'height': 20}
    assert view['components'][0]['id'] == 'R1'
    assert view['components'][0]['x'] == 10
    assert view['traces'][0]['points'][1] == {'x': 15, 'y': 10}
    with pytest.raises(ValueError):
        pcb_geometry(('(' * 100).encode())


def test_kicad_major_arc_bounds_include_extrema_and_sampling_is_honest():
    board = b'''(kicad_pcb (gr_arc (start 1 0) (mid -1 0) (end 0 1) (layer "Edge.Cuts")))'''
    view = pcb_geometry(board)
    assert view['board']['width'] == pytest.approx(2)
    assert view['board']['height'] == pytest.approx(2)
    triangle = struct.pack('<12fH', 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0)
    assert stl_mesh(b'\x00' * 80 + struct.pack('<I', 2000) + triangle * 2000)['sampled'] is True


def test_preview_does_not_execute_generated_code_and_excludes_stale_files(platform):
    project_id = create(platform)
    code = b"Text('Air quality'); throw Exception('never executed');"
    stale = b"Text('Old private screen');"
    with platform.db.transaction() as session:
        project = session.get(Project, project_id)
        for index, (content, status) in enumerate([(code, 'candidate'), (stale, 'stale')]):
            key = artifact_key('org-a', project_id, str(index), 'lib/main.dart', content)
            platform.store.put(key, content)
            session.add(Artifact(tenant_id='org-a', project_id=project_id, revision=project.engineering_revision,
                lane='application', name='lib/main.dart', key=key, sha256=digest(content), size=len(content),
                media_type='text/plain', status=status, provenance={}))
    endpoint = platform.client.get(f'/api/v1/projects/{project_id}/engineering-preview')
    assert endpoint.status_code == 200
    preview = endpoint.json()
    assert preview['application']['source'] == 'artifact'
    assert preview['application']['app']['widgets'] == [{'kind': 'text', 'label': 'Air quality', 'value': ''}]
    assert len(preview['files']) == 1
    assert base64.b64encode(stale).decode() not in str(preview)


def test_companion_prepares_image_owned_web_scaffold_without_replacing_user_files(tmp_path):
    from kleos.native_runner import prepare_companion
    work, fonts, template = [tmp_path / name for name in ('work', 'fonts', 'template')]
    for directory in (work, fonts, template):
        directory.mkdir()
    (work / 'pubspec.yaml').write_text('name: companion\n')
    (work / 'web').mkdir()
    (work / 'web/manifest.json').write_text('user metadata')
    (fonts / 'Roboto-Regular.ttf').write_bytes(b'font')
    (template / 'index.html').write_text('image-owned bootstrap')
    (template / 'manifest.json').write_text('template metadata')
    details = prepare_companion(work, fonts, {'Roboto-Regular.ttf': digest(b'font')}, template)
    assert details['webScaffoldAdded'] is True
    assert (work / 'web/index.html').read_text() == 'image-owned bootstrap'
    assert (work / 'web/manifest.json').read_text() == 'user metadata'
    assert details['sourceHashes']['web/index.html'] == digest(b'image-owned bootstrap')


def test_incomplete_pcb_exports_are_visible_candidates_and_never_success(tmp_path, monkeypatch):
    from kleos.native_runner import run_job
    monkeypatch.setattr('kleos.circuit_validation.prepare_kicad_libraries', lambda _: {'source': 'test_fixture'})

    def incomplete_export(args, work, timeout):
        assert args == ['ato', 'build']
        (work / 'build').mkdir()
        (work / 'build/device.kicad_pcb').write_text('(kicad_pcb (version 20241229))')
        return 0, 'One actual board export; schematic unavailable'

    content = b'requires-atopile: "==0.12.5"'
    result = run_job({'schemaVersion': 1, 'purpose': 'build', 'lane': 'electronics', 'inputArtifacts': [{
        'id': 'source', 'name': 'ato.yaml', 'lane': 'electronics', 'sha256': digest(content),
        'contentBase64': base64.b64encode(content).decode()}]}, tmp_path, incomplete_export)
    assert result.status == 'unsupported'
    assert len(result.files) == 1 and result.files[0].path.startswith('native/preview/')
    assert not result.checks
    assert '0 schematic' in result.limitations[0]


def test_firmware_preview_is_literal_current_source_not_companion_layout(platform):
    project_id = create(platform)
    source = b'int main(void) { return 0; }'
    with platform.db.transaction() as session:
        project = session.get(Project, project_id)
        key = artifact_key('org-a', project_id, 'firmware', 'src/main.c', source)
        platform.store.put(key, source)
        session.add(Artifact(tenant_id='org-a', project_id=project_id, revision=project.engineering_revision,
            lane='firmware', name='src/main.c', key=key, sha256=digest(source), size=len(source),
            media_type='text/x-c', status='candidate', provenance={}))
    data = platform.client.get(f'/api/v1/projects/{project_id}/engineering-preview').json()['firmware']
    assert data['code'] == source.decode() and data['codeName'] == 'src/main.c'
    assert data['source'] == 'artifact' and 'app' not in data
