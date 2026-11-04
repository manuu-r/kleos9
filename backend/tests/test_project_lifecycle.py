import base64
import hashlib

import httpx
import pytest
from sqlalchemy import select

from kleos.access.service import AccessService, AccessSettings
from kleos.api import create_app
from kleos.api.services import ApplicationServices
from kleos.artifacts.service import Artifact, ArtifactService
from kleos.artifacts.storage import BlobStore
from kleos.capabilities.external import ExternalJob
from kleos.capabilities.fabrication.slicing import PrintPreparation
from kleos.contracts.models import Actor, DomainError
from kleos.projects.lifecycle import delete_project
from kleos.projects.store import Event, Membership, Objective, Project, ProjectStore
from kleos.projects.verification import VerificationJob
from kleos.projects.views import ProjectViews


@pytest.fixture
def app(tmp_path):
    projects = ProjectStore(f'sqlite:///{tmp_path}/lifecycle.db')
    projects.initialize_for_tests()
    access = AccessService(projects, AccessSettings(secret_key='x'*40, environment='test', development_no_login=True))
    artifacts = ArtifactService(projects, BlobStore(tmp_path/'blobs'))
    services = ApplicationServices(projects, access, artifacts=artifacts)
    yield services, create_app(services)
    projects.engine.dispose()


async def test_http_delete_fences_work_retains_bytes_and_replays(app):
    services, application = app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url='http://local') as client:
        create = await client.post('/api/v1/commands', json={'type': 'project.create', 'payload': {'brief': 'Delete this fixture'}},
            headers={'Idempotency-Key': 'create'})
        ident = create.json()['projectId']
        actor = services.access.authenticate({}, {}, 'GET')
        raw = b'preserve source bytes'
        upload = services.artifacts.upload(actor, ident, {'file': {'path': 'part.py', 'encoding': 'base64',
            'content': base64.b64encode(raw).decode(), 'mediaType': 'text/plain'},
            'sha256': hashlib.sha256(raw).hexdigest(), 'expectedEngineeringRevision': 1}, 'source')
        submitted = services.projects.submit(actor, ident, 'Make a part', upload['revision'], 'run')
        revision = submitted['revision']
        with services.projects.transaction() as session:
            source = session.scalar(select(Artifact).where(Artifact.project_id == ident))
            artifact_id = source.id
            session.add(VerificationJob(id='check', project_id=ident, run_id=submitted['runId'],
                request_hash='a'*64, status='running', lease_until=100, data={}))
            session.add(PrintPreparation(id='slice', project_id=ident, tenant_id=actor.tenant_id, creator_id=actor.user_id,
                engineering_revision=revision, status='running', source_artifact_id=source.id, source_sha256=source.sha256,
                profile_id='fixture', profile={}, measurements={}, generation=3, container_id='uncertain-container', created_at=0))
            session.add(ExternalJob(id='external', project_id=ident, tenant_id=actor.tenant_id, creator_id=actor.user_id,
                revision=revision, kind='human_work', provider='fixture', provider_version='1', status='waiting',
                request={}, request_hash='b'*64, action_key='c'*64, effect_state='uncertain', generation=2, deadline_at=100))
        url = f'/api/v1/projects/{ident}?expectedRevision={revision}'
        response = await client.delete(url)
        assert response.status_code == 202, response.text
        result = response.json()
        assert result['deleted'] and result['dataRetained'] and result['externalCancellationConfirmed'] is False
        assert result['revision'] == revision+1 and (await client.delete(url)).json() == result
        assert (await client.get('/api/v1/projects')).json()['projects'] == []
        assert (await client.get(f'/api/v1/projects/{ident}/workspace')).status_code == 404
        assert (await client.get(f'/api/v1/artifacts/{artifact_id}/content')).status_code == 404
        with pytest.raises(DomainError):
            services.projects.submit(actor, ident, 'Restart', revision+1, 'new-run')
        with services.projects.transaction() as session:
            project = session.get(Project, ident)
            assert project.context_revision == revision+1 and project.data['sourceManifest']['part.py'] == artifact_id
            assert session.get(Objective, submitted['runId']).status == 'cancelled'
            assert session.get(VerificationJob, 'check').status == 'cancelled'
            preparation = session.get(PrintPreparation, 'slice')
            assert preparation.status == 'cancelled' and preparation.generation == 4
            assert preparation.container_id == 'uncertain-container'  # compute reconciliation owns cleanup
            external = session.get(ExternalJob, 'external')
            assert external.status == 'cancelled' and external.generation == 3 and external.effect_state == 'uncertain'
            assert services.artifacts.blobs.get(source.sha256) == raw
            events = list(session.scalars(select(Event).where(Event.project_id == ident)))
            assert sum(event.data['type'] == 'project.deleted' for event in events) == 1
            session.delete(session.get(Membership, (ident, actor.user_id)))
        assert (await client.delete(url)).status_code == 404  # revoked owner cannot replay


@pytest.mark.parametrize('revision', [None, True, '1.0', '0', '-1', 'x', '9'*100])
def test_delete_requires_explicit_integer_revision(app, revision):
    services, _ = app
    actor = Actor(tenant_id='tenant', user_id='owner')
    project = services.projects.create(actor, 'Keep', 'create')
    with pytest.raises(DomainError) as error:
        delete_project(services.projects, actor, {'projectId': project['projectId'], 'expectedRevision': revision}, {})
    assert error.value.status == 422


def test_only_owner_current_revision_can_delete(app):
    services, _ = app
    actor = Actor(tenant_id='tenant', user_id='owner')
    project = services.projects.create(actor, 'Keep', 'create')
    with services.projects.transaction() as session:
        session.add(Membership(project_id=project['projectId'], user_id='editor', role='editor'))
    for who, revision, status in [(Actor(tenant_id='tenant', user_id='editor'), 1, 403),
                                  (Actor(tenant_id='elsewhere', user_id='owner'), 1, 404), (actor, 2, 409)]:
        with pytest.raises(DomainError) as error:
            delete_project(services.projects, who, {'projectId': project['projectId'], 'expectedRevision': revision}, {})
        assert error.value.status == status
    assert len(services.views.list(actor)['projects']) == 1


def test_external_observer_and_deletion_lock_order_postgres(monkeypatch):
    """Exercise the same job->project lock order as live external observation."""
    import os
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from uuid import uuid4

    from sqlalchemy import event, text

    url = os.environ.get('KLEOS_TEST_POSTGRES_URL')
    if not url:
        pytest.skip('Explicit disposable PostgreSQL opt-in required')
    projects = ProjectStore(url)
    actor = Actor(tenant_id='delete-'+uuid4().hex, user_id='owner-'+uuid4().hex)
    project_id = projects.create(actor, 'Disposable deletion lock-order fixture', 'create')['projectId']
    job_id = str(uuid4())
    with projects.transaction() as session:
        session.add(ExternalJob(id=job_id, project_id=project_id, tenant_id=actor.tenant_id, creator_id=actor.user_id,
            revision=1, kind='human_work', provider='fixture', provider_version='1', status='waiting',
            request={}, request_hash='b'*64, action_key=uuid4().hex, effect_state='uncertain', generation=2, deadline_at=100))
    observer_locked, delete_waiting = threading.Event(), threading.Event()
    deletion_thread = [None]

    def before_query(connection, cursor, statement, parameters, context, executemany):
        if (threading.get_ident() == deletion_thread[0] and 'kleos2_external_jobs' in statement
                and 'FOR UPDATE' in statement):
            delete_waiting.set()

    event.listen(projects.engine, 'before_cursor_execute', before_query)

    def observe():
        with projects.transaction() as session:
            session.execute(text("SET LOCAL lock_timeout = '3s'"))
            job = session.get(ExternalJob, job_id, with_for_update=True)
            observer_locked.set()
            assert delete_waiting.wait(8), 'Deletion did not reach external-job lock'
            # Old project->job deletion would hold Project here and cause a deadlock.
            project = projects.authorized(session, actor, project_id)
            assert project.id == job.project_id
        return True

    def remove():
        assert observer_locked.wait(8)
        deletion_thread[0] = threading.get_ident()
        return delete_project(projects, actor, {'projectId': project_id, 'expectedRevision': 1}, {})

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            observation, deletion = pool.submit(observe), pool.submit(remove)
            assert observation.result(timeout=12)
            assert deletion.result(timeout=12)['deleted']
        assert ProjectViews(projects).list(actor)['projects'] == []
    finally:
        event.remove(projects.engine, 'before_cursor_execute', before_query)
        projects.engine.dispose()
