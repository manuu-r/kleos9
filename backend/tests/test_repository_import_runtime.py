"""Remote import command uses real authorization, schema, receipt and source views."""
import asyncio
import io
import zipfile

import httpx
from test_interface_runtime import configured_runtime

from kleos.projects.store import Project


async def test_repository_command_publishes_once_through_runtime(tmp_path):
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, 'w') as output:
        output.writestr('owner-project-abcdef0/model.py', '# inert imported source\n')
        output.writestr('owner-project-abcdef0/README.md', 'Existing enclosure project')
    calls = []

    class Download:
        async def download(self, source):
            calls.append(source)
            return archive.getvalue()

    def provider(request):
        raise AssertionError('Import must not invoke a model')

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as upstream:
        runtime = configured_runtime(tmp_path, upstream)
        runtime.services.repository_import.downloader = Download()
        async with runtime.start(), httpx.AsyncClient(
                transport=httpx.ASGITransport(app=runtime.app), base_url='http://local') as client:
            created = await client.post('/api/v1/commands', headers={'Idempotency-Key': 'create'},
                json={'type': 'project.create', 'payload': {'brief': 'Repair an existing enclosure'}})
            assert created.status_code == 202
            project = created.json()['projectId']
            request = {'type': 'project.importRepository', 'expectedRevision': 1,
                'payload': {'projectId': project, 'repositoryUrl': 'https://github.com/owner/project',
                            'commit': 'a'*40, 'expectedEngineeringRevision': 1}}
            imported = await client.post('/api/v1/commands',
                headers={'Idempotency-Key': 'import'}, json=request)
            assert imported.status_code == 202, imported.text
            replay = await client.post('/api/v1/commands',
                headers={'Idempotency-Key': 'import'}, json=request)
            assert replay.json() == imported.json()
            assert len(calls) == 1
            with runtime.projects.transaction() as session:
                row = session.get(Project, project)
                assert row.context_revision == row.revision == imported.json()['revision'] == 2
                assert set(row.data['sourceManifest']) == {'model.py', 'README.md'}
                record = row.data['archiveImports'][0]
                assert record['repository']['commit'] == 'a'*40
                assert record['repository']['historyIncluded'] is False
            workspace = await client.get(f'/api/v1/projects/{project}/workspace')
            assert workspace.status_code == 200
            assert workspace.json()['project']['engineeringRevision'] == 2
            assert workspace.json()['project']['acceptedReleaseId'] is None


async def test_cancel_command_fences_pending_repository_response(tmp_path):
    entered, finish = asyncio.Event(), asyncio.Event()
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, 'w') as output:
        output.writestr('owner-project-abcdef0/model.py', '# candidate source')

    class Download:
        async def download(self, source):
            entered.set()
            await finish.wait()
            return archive.getvalue()

    def provider(request):
        raise AssertionError('Import cancellation must not invoke a model')

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as upstream:
        runtime = configured_runtime(tmp_path, upstream)
        runtime.services.repository_import.downloader = Download()
        async with runtime.start(), httpx.AsyncClient(
                transport=httpx.ASGITransport(app=runtime.app), base_url='http://local') as client:
            created = (await client.post('/api/v1/commands', headers={'Idempotency-Key': 'create'},
                json={'type': 'project.create', 'payload': {'brief': 'Cancel an import'}})).json()
            project = created['projectId']
            pending = asyncio.create_task(client.post('/api/v1/commands',
                headers={'Idempotency-Key': 'import'}, json={'type': 'project.importRepository',
                'expectedRevision': 1, 'payload': {'projectId': project,
                    'repositoryUrl': 'https://github.com/owner/project', 'commit': 'a'*40,
                    'expectedEngineeringRevision': 1}}))
            try:
                await asyncio.wait_for(entered.wait(), 3)
                request = {'type': 'project.cancelRepositoryImport', 'expectedRevision': 1,
                           'payload': {'projectId': project, 'importKey': 'import'}}
                cancelled = await client.post('/api/v1/commands',
                    headers={'Idempotency-Key': 'cancel'}, json=request)
                assert cancelled.status_code == 202, cancelled.text
                replay = await client.post('/api/v1/commands',
                    headers={'Idempotency-Key': 'cancel'}, json=request)
                assert replay.json() == cancelled.json()
            finally:
                finish.set()
            response = await asyncio.wait_for(pending, 3)
            assert response.status_code == 409
            with runtime.projects.transaction() as session:
                row = session.get(Project, project)
                assert row.context_revision == 1 and row.revision == 2
                assert not row.data.get('sourceManifest')
