"""Approved reference lineage in an accepted-release fixture, not CAD qualification."""
import io
import json
import zipfile
from copy import deepcopy

import pytest
from test_design_intent import approved

from kleos.contracts import DomainError
from kleos.db import Artifact, Project, Release
from kleos.packaging import ReleasePackaging
from kleos.storage import digest


def release_fixture(platform):
    project_id = approved(platform)
    content = b'accepted-release-payload-fixture'
    platform.store.put('release-reference-fixture', content)
    with platform.db.transaction() as session:
        project = session.get(Project, project_id)
        snapshot = deepcopy(project.state['approvedDesign'])
        session.add(Artifact(id='reference-payload', tenant_id=project.tenant_id, project_id=project_id,
            revision=project.engineering_revision, lane='application', name='accepted-source.txt',
            key='release-reference-fixture', sha256=digest(content), size=len(content), status='accepted'))
        session.add(Release(id='reference-release', tenant_id=project.tenant_id, project_id=project_id,
            revision=project.engineering_revision, mode='digital', approved_by=platform.principal.subject,
            manifest={'artifacts': {'reference-payload': digest(content)}, 'interfaces': {},
                      'requirements': deepcopy(project.requirements), 'approvedDesign': snapshot}))
    return project_id, snapshot


def test_manufacturing_download_retains_the_approved_scene_after_later_project_changes(platform):
    project_id, snapshot = release_fixture(platform)
    with platform.db.transaction() as session:
        project = session.get(Project, project_id)
        project.state = {'concept': {'scene': {'title': 'A later unapproved square design'}}}
        project.requirements = {'futureRevision': True}
        project.engineering_revision += 1
    service = ReleasePackaging(platform.db, platform.store)
    bundle = service.bundle(platform.principal, 'reference-release')
    assert service.bundle(platform.principal, 'reference-release') == bundle
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        raw = archive.read('approved-design-reference.json')
        assert json.loads(raw) == snapshot
        manufacturing = json.loads(archive.read('manufacturing-manifest.json'))
        reference = manufacturing['designReference']
        assert reference['sha256'] == digest(raw)
        assert reference['sceneHash'] == snapshot['sceneHash']
        assert reference['authority'] == 'user_approved_appearance' and reference['units'] == 'relative'
        assert manufacturing['productionAuthorized'] is False


@pytest.mark.parametrize('mutation', ['snapshot', 'missing', 'constraints'])
def test_packaging_rejects_missing_or_changed_approved_reference(platform, mutation):
    release_fixture(platform)
    with platform.db.transaction() as session:
        release = session.get(Release, 'reference-release')
        manifest = deepcopy(release.manifest)
        if mutation == 'snapshot':
            manifest['approvedDesign']['scene']['parts'][0]['shape'] = 'box'
        elif mutation == 'missing':
            manifest.pop('approvedDesign')
        else:
            manifest['requirements']['designIntent']['enclosure']['profile'] = 'rectangle'
        release.manifest = manifest
    with pytest.raises(DomainError, match='Accepted design reference'):
        ReleasePackaging(platform.db, platform.store).bundle(platform.principal, 'reference-release')
