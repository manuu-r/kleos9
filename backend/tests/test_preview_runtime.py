"""Production composition reaches artifact previews without providers/native compute."""
import json
from datetime import UTC, datetime, timedelta

import httpx
from test_previews import stl
from test_runtime import migrate, settings, test_persistence

from kleos.api.previews import EngineeringPreviews
from kleos.contracts.models import TaskSpec
from kleos.infrastructure.runtime import Runtime
from kleos.orchestration.engine import RunScope
from kleos.projects.store import Objective, Project


async def test_runtime_preview_api_uses_real_publication_and_current_authority(tmp_path):
    runtime = Runtime(settings(tmp_path), persistence_factory=test_persistence, worker_locks=object())
    migrate(runtime)
    assert isinstance(runtime.services.previews, EngineeringPreviews)
    assert runtime.services.previews is runtime.previews
    assert runtime.previews.artifacts is runtime.artifacts
    actor = runtime.access.authenticate({}, {}, 'GET')
    project = runtime.projects.create(actor, 'Preview the bracket', 'create')['projectId']
    submitted = runtime.projects.submit(actor, project, 'Make bracket', 1, 'run')
    scope = RunScope(actor=actor, project_id=project, run_id=submitted['runId'],
        revision=submitted['revision'], deadline_at=datetime.now(UTC)+timedelta(minutes=5))
    task = TaskSpec(id='cad', objective='Build bracket', capability='cad', allowed_paths=[])
    refs = await runtime.artifacts.publish_native(scope, task, [
        {'path': 'outputs/bracket.stl', 'role': 'output', 'content': stl()},
        {'path': 'outputs/bracket.step', 'role': 'output', 'content': b'fixture STEP'},
        {'path': 'outputs/parts.json', 'role': 'output', 'content': json.dumps({'parts': [
            {'id': 'bracket', 'stl': 'bracket.stl', 'step': 'bracket.step'}]}).encode()}],
        {'nativeJobId': 'job', 'imageDigest': 'sha256:'+'a'*64, 'requestHash': 'b'*64})
    with runtime.projects.transaction() as session:
        run = session.get(Objective, scope.run_id)
        run.status = 'succeeded'
        run.data = {**run.data, 'results': {'cad': {'artifacts': [r.model_dump(mode='json') for r in refs]}}}
    async with runtime.start():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=runtime.app), base_url='http://localhost') as client:
            response = await client.get(f'/api/v1/projects/{project}/engineering-preview')
            assert response.status_code == 200, response.text
            view = response.json()['mechanics']
            assert view['source'] == 'artifact'
            assert view['assembly']['parts'][0]['mesh']['triangles'][0][0] == [10, 20, 30]
            assert (await client.get(f'/api/v1/projects/{project}/circuit-simulation')).json()['status'] == 'UNKNOWN'
            assert (await client.get('/api/v1/projects/not-owned/engineering-preview')).status_code == 404
            with runtime.projects.transaction() as session:
                session.get(Project, project).advance_revision()
            assert (await client.get(f'/api/v1/projects/{project}/engineering-preview')).json()['mechanics']['source'] == 'unavailable'
