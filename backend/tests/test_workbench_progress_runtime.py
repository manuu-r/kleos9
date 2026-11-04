"""Existing workbench commands cross the real HTTP/runtime composition."""
import httpx
from test_interface_runtime import configured_runtime


async def test_print_preference_and_simulated_assembly_use_existing_api(tmp_path):
    def provider(request):
        raise AssertionError('Workbench preferences must not invoke a model')

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as upstream:
        runtime = configured_runtime(tmp_path, upstream)
        async with runtime.start(), httpx.AsyncClient(
                transport=httpx.ASGITransport(app=runtime.app), base_url='http://local') as client:
            created = await client.post('/api/v1/commands', headers={'Idempotency-Key': 'create'},
                json={'type': 'project.create', 'payload': {'brief': 'A local workbench fixture'}})
            assert created.status_code == 202
            project = created.json()['projectId']
            revision = created.json()['revision']
            for kind, values in [('selectPrint', {'mode': 'home'}), ('assemblyStep', {'step': 0})]:
                request = {'type': 'project.' + kind, 'expectedRevision': revision,
                           'payload': {'projectId': project, **values}}
                response = await client.post('/api/v1/commands',
                    headers={'Idempotency-Key': kind}, json=request)
                assert response.status_code == 202, response.text
                revision = response.json()['revision']
                replay = await client.post('/api/v1/commands',
                    headers={'Idempotency-Key': kind}, json=request)
                assert replay.json() == response.json()
            workspace = await client.get(f'/api/v1/projects/{project}/workspace')
            assert workspace.status_code == 200
            view = workspace.json()['project']
            assert view['engineeringRevision'] == created.json()['revision']
            assert view['state']['printMode'] == 'home'
            assert view['state']['printPreference']['printingStarted'] is False
            assert view['state']['assemblyStep'] == 0
            assert view['state']['assemblyProgress']['evidenceKind'] == 'synthetic'
            assert view['state']['assemblyProgress']['physicalAcceptance'] is False
            assert view['acceptedReleaseId'] is None
