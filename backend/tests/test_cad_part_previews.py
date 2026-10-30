"""Hash/preview integration only: dummy STEP bytes never qualify actual CAD geometry."""
import json
import struct

import pytest
from sqlalchemy import select
from test_control_plane import create

from kleos.auth import Principal
from kleos.cad_parts import ZERO
from kleos.contracts import DomainError
from kleos.db import Artifact, Attempt, NativeJob, Project, Run, Task, now
from kleos.engineering_preview import EngineeringPreview
from kleos.storage import digest


def binary_box(low, high):
    vertices = [(x, y, z) for x in (low[0], high[0]) for y in (low[1], high[1]) for z in (low[2], high[2])]
    faces = [(0, 2, 3), (0, 3, 1), (4, 5, 7), (4, 7, 6), (0, 1, 5), (0, 5, 4),
             (2, 6, 7), (2, 7, 3), (0, 4, 6), (0, 6, 2), (1, 3, 7), (1, 7, 5)]
    return b'CAD preview fixture'.ljust(80, b'\0') + struct.pack('<I', len(faces)) + b''.join(
        struct.pack('<12fH', 0, 0, 0, *(value for index in face for value in vertices[index]), 0)
        for face in faces)


def bounds(low, high):
    return {'min': dict(zip('xyz', low)), 'max': dict(zip('xyz', high)),
            'size': dict(zip('xyz', (b - a for a, b in zip(low, high)))),
            'center': dict(zip('xyz', ((a + b) / 2 for a, b in zip(low, high))))}


@pytest.fixture
def cad(platform):
    project_id = create(platform, 'CAD physical parts fixture')['projectId']
    files = {}
    def entry(stem, low, high):
        step, stl = f'native/outputs/{stem}.step', f'native/outputs/{stem}.stl'
        files[step] = b'ISO-10303-21; /* HASH FIXTURE ONLY: not geometry qualification */ ' + stem.encode()
        files[stl] = binary_box(low, high)
        return {'step': {'path': step, 'sha256': digest(files[step])},
                'stl': {'path': stl, 'sha256': digest(files[stl])}, 'bounds': bounds(low, high)}
    manifest = {'schemaVersion': 'cad-assembly-v1', 'units': 'mm', 'coordinateFrame': 'assembled',
        'separation': 'named_parts', 'assembly': entry('model', (10, 20, 30), (12, 22, 32)),
        'parts': []}
    for ident, label, low, high in [('body', 'Case body', (10, 20, 30), (12, 22, 31)),
                                  ('lid', 'Case lid', (10, 20, 31), (12, 22, 32))]:
        manifest['parts'].append({'id': ident, 'label': label, 'quantity': 1,
            'material': 'PLA', 'materialStatus': 'source_declared_unverified',
            'transform': {'translation': dict(ZERO), 'rotation': dict(ZERO)}, **entry('parts/' + ident, low, high)})
    files['native/outputs/assembly-manifest.json'] = json.dumps(manifest).encode()
    with platform.db.transaction() as session:
        session.add(Run(id='cad-run', tenant_id='org-a', project_id=project_id, command_id='cad-fixture',
                        kind='project.generate', revision=1, status='succeeded'))
        session.add(Task(id='cad-task', tenant_id='org-a', project_id=project_id, run_id='cad-run', lane='mechanics',
                         kind='project.generate', revision=1, status='succeeded'))
        session.add(Attempt(id='cad-attempt', tenant_id='org-a', task_id='cad-task', number=1, fence=1,
                            policy_revision=1, provider='google', model='fixture', status='succeeded', context={}))
        session.add(NativeJob(id='cad-job', tenant_id='org-a', task_id='cad-task', attempt_id='cad-attempt',
                             purpose='build', status='succeeded', token_hash='fixture', expires_at=now(), request={}))
        for index, (name, content) in enumerate(files.items()):
            key = f'cad-fixtures/{project_id}/{index}'
            platform.store.put(key, content)
            session.add(Artifact(id=f'cad-{index}', tenant_id='org-a', project_id=project_id, task_id='cad-task',
                attempt_id='cad-attempt', revision=1, lane='mechanics', name=name, key=key, sha256=digest(content),
                size=len(content), status='candidate', provenance={'nativeJobId': 'cad-job'}))
    return platform, project_id, manifest, files


def test_actual_part_meshes_keep_physical_ids_metadata_and_manufacturing_coordinates(cad):
    platform, project_id, manifest, original = cad
    preview = EngineeringPreview(platform.db, platform.store)
    with platform.db.transaction() as session:
        result = preview.assembly(session, session.get(Project, project_id))
        for file in session.scalars(select(Artifact).where(Artifact.project_id == project_id)):
            assert platform.store.get(file.key) == original[file.name]
        assert not session.get(Project, project_id).accepted_release_id
    assert result['schemaVersion'] == 'cad-assembly-v1'
    assert result['coordinateFrame'] == 'assembled' and result['units'] == 'mm'
    assert result['separation'] == 'named_parts'
    assert [part['id'] for part in result['parts']] == ['body', 'lid']
    for actual, expected in zip(result['parts'], manifest['parts']):
        assert actual['bounds'] == expected['bounds']
        assert actual['transform'] == {'translation': ZERO, 'rotation': ZERO}
        assert actual['materialStatus'] == 'source_declared_unverified'
        points = [point for face in actual['mesh']['triangles'] for point in face]
        assert min(p[0] for p in points) == 10  # no preview recentering or physical explosion
        assert max(p[2] for p in points) == expected['bounds']['max']['z']
        assert actual['stepArtifactId'] and actual['stlArtifactId']
    assert any('display-only' in item for item in result['limitations'])
    assert 'independentGeometryVerified' not in result
    response = platform.client.get(f'/api/v1/projects/{project_id}/engineering-preview')
    assert response.status_code == 200
    assert response.json()['mechanics']['assembly']['parts'][1]['id'] == 'lid'


@pytest.mark.parametrize('mutation', ['missing', 'stale', 'other_attempt', 'other_tenant', 'other_revision',
                                     'wrong_metadata_hash', 'missing_blob', 'corrupted_bytes', 'bad_mesh'])
def test_inapplicable_or_changed_part_closures_fall_back_without_a_server_error(cad, mutation, monkeypatch):
    platform, project_id, _, _ = cad
    with platform.db.transaction() as session:
        part = session.scalar(select(Artifact).where(Artifact.name == 'native/outputs/parts/lid.stl'))
        key = part.key
        if mutation == 'missing':
            session.delete(part)
        elif mutation == 'stale':
            part.status = 'stale'
        elif mutation == 'other_attempt':
            part.attempt_id = 'a-different-attempt'
        elif mutation == 'other_tenant':
            part.tenant_id = 'org-b'
        elif mutation == 'other_revision':
            part.revision = 2
        elif mutation == 'wrong_metadata_hash':
            part.sha256 = '0' * 64
    original = platform.store.get_limited
    if mutation in {'missing_blob', 'corrupted_bytes', 'bad_mesh'}:
        def changed(name, ceiling):
            if name == key:
                if mutation == 'missing_blob':
                    raise FileNotFoundError(name)
                return b'corrupted' if mutation == 'corrupted_bytes' else b'not an STL'
            return original(name, ceiling)
        monkeypatch.setattr(platform.store, 'get_limited', changed)
    response = platform.client.get(f'/api/v1/projects/{project_id}/engineering-preview')
    assert response.status_code == 200
    mechanics = response.json()['mechanics']
    assert 'assembly' not in mechanics
    assert any('manifest' in item for item in mechanics['limitations'])


@pytest.mark.parametrize('mutation', ['stale', 'other_tenant', 'other_revision', 'missing_native_provenance', 'bad_manifest_hash'])
def test_manifest_itself_must_be_current_and_native_attributed(cad, mutation):
    platform, project_id, _, _ = cad
    with platform.db.transaction() as session:
        artifact = session.scalar(select(Artifact).where(Artifact.name == 'native/outputs/assembly-manifest.json'))
        if mutation == 'stale':
            artifact.status = 'stale'
        elif mutation == 'other_tenant':
            artifact.tenant_id = 'org-b'
        elif mutation == 'other_revision':
            artifact.revision = 2
        elif mutation == 'missing_native_provenance':
            artifact.provenance = {}
        elif mutation == 'bad_manifest_hash':
            artifact.sha256 = '0' * 64
    response = platform.client.get(f'/api/v1/projects/{project_id}/engineering-preview')
    assert response.status_code == 200 and 'assembly' not in response.json()['mechanics']


def test_assembly_preview_cannot_cross_project_authorization(cad):
    platform, project_id, _, _ = cad
    preview = EngineeringPreview(platform.db, platform.store)
    with pytest.raises(DomainError):
        preview.read(Principal('outsider', 'org-b', 'owner'), project_id)
    assert preview.read(platform.principal, project_id)['mechanics']['assembly']['parts']
    with pytest.raises(DomainError):
        preview.read(Principal('outsider', 'org-b', 'owner'), project_id)  # including after cache warmup


@pytest.mark.parametrize('mutation', ['display_transform', 'malformed_stl'])
def test_hash_valid_but_invalid_part_geometry_or_manufacturing_transform_is_not_previewed(cad, mutation):
    platform, project_id, original_manifest, _ = cad
    manifest = json.loads(json.dumps(original_manifest))
    with platform.db.transaction() as session:
        if mutation == 'display_transform':
            manifest['parts'][0]['transform']['translation']['x'] = 10
        else:
            part = session.scalar(select(Artifact).where(Artifact.name == 'native/outputs/parts/lid.stl'))
            data = b'not a triangle mesh'
            part.key = f'cad-fixtures/{project_id}/invalid-stl'
            platform.store.put(part.key, data)
            part.sha256, part.size = digest(data), len(data)
            manifest['parts'][1]['stl']['sha256'] = digest(data)
        artifact = session.scalar(select(Artifact).where(Artifact.name == 'native/outputs/assembly-manifest.json'))
        raw = json.dumps(manifest).encode()
        artifact.key = f'cad-fixtures/{project_id}/invalid-manifest'
        platform.store.put(artifact.key, raw)
        artifact.sha256, artifact.size = digest(raw), len(raw)
    response = platform.client.get(f'/api/v1/projects/{project_id}/engineering-preview')
    assert response.status_code == 200
    assert 'assembly' not in response.json()['mechanics']
