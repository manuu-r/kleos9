import json

import pytest
from test_application_services import services as services

from kleos.access.service import OrganizationMember
from kleos.contracts.models import Actor, DomainError
from kleos.execution.gateway import NativeAction, NativeJob
from kleos.projects.store import Objective


def test_run_shows_rebuildable_job_without_source_bytes_and_logs_require_admin(services):
    actor = services.authenticate({}, {}, 'GET')
    project = services.projects.create(actor, 'Inspect native work', 'create')['projectId']
    admitted = services.projects.submit(actor, project, 'Build bracket', 1, 'build')
    with services.projects.transaction() as session:
        run = session.get(Objective, admitted['runId'])
        run.data = {**run.data, 'tasks': [{'id': 'bracket', 'capability': 'cad'}]}
        session.add(NativeJob(id='job', project_id=project, run_id=run.id, task_id='bracket',
            task_hash='a' * 64, request_hash='b' * 64, status='finished', active_action=None,
            source_manifest={'model.py': 'c' * 64}, request={
                'lane': 'cad', 'imageDigest': 'sha256:' + 'd' * 64, 'revision': 2,
                'sources': [{'contentBase64': 'PRIVATE_SOURCE'}], 'resources': {'cpus': 2}}))
        session.add(NativeAction(job_id='job', action_id='read', request_hash='e' * 64,
            status='completed', result={'summary': 'PRIVATE_OUTPUT', 'observations': [
                {'name': 'native.read', 'status': 'UNKNOWN', 'details': {'content': 'PRIVATE_SOURCE'}}]}))
    view = services.query(actor, 'run', {'runId': admitted['runId']})
    job = view['tasks'][0]['nativeJobs'][0]
    assert job['purpose'] == 'build' and job['status'] == 'succeeded'
    assert job['inputManifest']['sources'] == [{'path': 'model.py', 'sha256': 'c' * 64}]
    logs = services.query(actor, 'native_logs', {'jobId': 'job'})
    assert logs['actions'][0]['observations'] == [{'name': 'native.read', 'status': 'UNKNOWN'}]
    assert 'PRIVATE_' not in json.dumps([view, logs])
    with pytest.raises(DomainError, match='not found'):
        services.query(Actor(tenant_id='foreign', user_id=actor.user_id), 'native_logs', {'jobId': 'job'})
    with services.projects.transaction() as session:
        session.get(OrganizationMember, (actor.tenant_id, actor.user_id)).role = 'editor'
    with pytest.raises(DomainError, match='administrator'):
        services.query(actor, 'native_logs', {'jobId': 'job'})
