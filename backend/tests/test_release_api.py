import io
import json
import zipfile

import pytest
from test_validation import finish, registry
from test_validation import setup as setup

from kleos.api.releases import ReleaseFacade
from kleos.artifacts.service import ArtifactService
from kleos.artifacts.storage import BlobStore
from kleos.contracts.models import Actor, DomainError
from kleos.projects.store import Project, fingerprint
from kleos.validation.service import CheckReport, Release, ValidationService


async def accepted_package(setup, tmp_path):
    store, scope, artifacts, _ = setup

    async def check(scope, subject):
        return CheckReport(subject_hash=subject.subject_hash, status='PASS',
            execution_id='independent-fixture', summary='Fixture only')

    validation = ValidationService(store, registry(check))
    candidate = validation.prepare(scope, [item.id for item in artifacts], 'digital', 'prepare')
    await validation.run(scope, candidate)
    finish(store, scope)
    facade = ReleaseFacade(store, ArtifactService(store, BlobStore(tmp_path / 'blobs')), validation)
    body = {'payload': {'projectId': scope.project_id}, 'expectedRevision': scope.revision}
    receipt = facade.accept(scope.actor, body, 'accept')
    with store.transaction() as session:
        release_id = session.get(Project, scope.project_id).data['acceptedReleaseId']
    return facade, body, receipt, release_id


async def test_release_package_has_exact_partial_sources_and_idempotent_acceptance(setup, tmp_path):
    facade, body, receipt, release_id = await accepted_package(setup, tmp_path)
    _, scope, _, _ = setup
    assert facade.accept(scope.actor, body, 'accept') == receipt
    assert set(receipt) == {'projectId', 'commandId', 'revision'}
    package = facade.content(scope.actor, release_id)
    assert package.media_type == 'application/zip'
    assert package.data == facade.content(scope.actor, release_id).data
    with zipfile.ZipFile(io.BytesIO(package.data)) as archive:
        assert set(archive.namelist()) == {
            'sources/part.py', 'sources/other.py', 'artifacts/outputs/bracket.step', 'manifest.json'}
        assert archive.read('sources/part.py') == b'new bracket'
        assert archive.read('sources/other.py') == b'unchanged'
        manifest = json.loads(archive.read('manifest.json'))
        assert manifest['kind'] == 'digital'
        assert manifest['manifestHash'] == fingerprint({
            k: v for k, v in manifest.items() if k != 'manifestHash'})


async def test_release_access_and_replay_reauthorize_tenant(setup, tmp_path):
    facade, body, _, release_id = await accepted_package(setup, tmp_path)
    foreign = Actor(tenant_id='different', user_id='owner')
    for operation in [lambda: facade.content(foreign, release_id),
                      lambda: facade.accept(foreign, body, 'accept')]:
        with pytest.raises(DomainError, match='not found'):
            operation()
    _, scope, _, _ = setup
    with pytest.raises(DomainError, match='different'):
        facade.accept(scope.actor, {**body, 'expectedRevision': body['expectedRevision'] + 1}, 'accept')


@pytest.mark.parametrize('corruption', ['manifest', 'blob', 'missing_blob', 'size'])
async def test_release_package_rejects_corrupt_or_missing_bytes(setup, tmp_path, corruption):
    facade, _, _, release_id = await accepted_package(setup, tmp_path)
    store, scope, artifacts, _ = setup
    if corruption in {'manifest', 'size'}:
        with store.transaction() as session:
            release = session.get(Release, release_id)
            data = json.loads(json.dumps(release.data))
            data['sources'][0]['size'] += 1
            if corruption == 'size':
                data['manifestHash'] = fingerprint({k: v for k, v in data.items() if k != 'manifestHash'})
            release.data = data
    else:
        path = tmp_path / 'blobs' / artifacts[0].sha256
        if corruption == 'missing_blob':
            path.unlink()
        else:
            path.write_bytes(b'altered')
    with pytest.raises(DomainError):
        facade.content(scope.actor, release_id)


async def test_release_without_independent_evidence_is_not_implicitly_accepted(setup, tmp_path):
    store, scope, _, _ = setup
    facade = ReleaseFacade(store, ArtifactService(store, BlobStore(tmp_path / 'blobs')),
        ValidationService(store, registry(None)))
    with pytest.raises(DomainError, match='independently qualified'):
        facade.accept(scope.actor, {'payload': {'projectId': scope.project_id},
            'expectedRevision': scope.revision}, 'reject')
