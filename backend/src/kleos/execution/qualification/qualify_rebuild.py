"""Opt-in real-Docker native-only rebuild qualification, without model or retrieval clients."""
import argparse
import asyncio
import json
import tempfile
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import select

from kleos.artifacts.service import ArtifactService
from kleos.artifacts.storage import BlobStore
from kleos.contracts.models import Actor, TaskSpec
from kleos.execution.capacity import CapacityService
from kleos.execution.docker import DockerClient, NativeSettings, Resources
from kleos.execution.gateway import NativeAction, NativeGateway, NativeJob
from kleos.execution.protocol import digest
from kleos.execution.qualification.qualify_cad import FIXTURE
from kleos.infrastructure.rebuild import configure_native_rebuild
from kleos.orchestration.engine import RunScope
from kleos.projects.graph_repository import GraphRepository
from kleos.projects.rebuild import NativeRebuildService
from kleos.projects.store import Objective, ProjectStore


async def qualify(args):
    work = Path(tempfile.mkdtemp(prefix='kleos-rebuild-qualification-'))
    projects = ProjectStore(f'sqlite:///{work}/state.db')
    projects.initialize_for_tests()
    actor = Actor(tenant_id='qualification', user_id='fixture')
    project = projects.create(actor, 'Exact source native-only rebuild', 'create')
    receipt = projects.submit(actor, project['projectId'], 'Build trusted CAD fixture', 1, 'parent')
    scope = RunScope(actor=actor, project_id=project['projectId'], run_id=receipt['runId'], revision=2,
                     deadline_at=datetime.now(UTC)+timedelta(minutes=10))
    task = TaskSpec(id='original-fixture', objective='Build fixed two-part CAD', capability='cad',
                    allowed_paths=['model.py'], max_actions=6)
    artifacts = ArtifactService(projects, BlobStore(work/'blobs'))
    docker = DockerClient(socket=args.socket)
    settings = NativeSettings(work/'control', {'cad': args.image}, docker_desktop_compatibility=True, deployment='local')
    capacity = CapacityService(projects)
    gateway = NativeGateway(projects, artifacts, settings, docker, GraphRepository(projects), capacity=capacity)
    report = {'qualified': False, 'imageId': args.image, 'qualificationKind': 'native-only-rebuild',
              'sourceOverride': False, 'modelCalls': 0, 'retrievalCalls': 0, 'parentActions': [],
              'scope': 'Exact saved two-part CAD source, same pinned image/resources, new bounded linked grant'}
    try:
        for action, arguments in [('patch', {'path': 'model.py', 'content': FIXTURE, 'expectedSha256': None}),
                                  ('build', {}), ('finish', {})]:
            print('Parent ' + action, flush=True)
            result = await gateway.invoke(scope, task, action, {'actionId': action, **arguments})
            report['parentActions'].append({'action': action, 'response': result})
            assert result['observations'][0]['details']['executionSucceeded'], result['summary']
        with projects.transaction() as session:
            original = session.scalar(select(NativeJob))
            assert session.get(NativeAction, (original.id, '_termination')).result['containerStopped']
            session.get(Objective, scope.run_id).status = 'succeeded'
        assert capacity.usage()['jobs'] == 1
        await gateway.reconcile()
        assert capacity.usage()['jobs'] == 0
        service = NativeRebuildService(projects, gateway)
        admitted = service.submit(actor, scope.project_id, original.id, 2, 'rebuild')
        assert service.submit(actor, scope.project_id, original.id, 2, 'rebuild') == admitted
        with projects.transaction() as session:
            run = session.get(Objective, admitted['runId'])
            rebuilt_scope = RunScope.model_validate(run.data['scope'])
            grant = run.data['nativeRebuild']['grant']
            snapshot = run.data['nativeRebuild']['snapshot']
        # Current operator configuration cannot silently repin the explicit historical rebuild.
        gateway.settings = replace(settings, images={}, resources=Resources(cpus=1), session_seconds=1)
        engine = configure_native_rebuild(projects, gateway, rebuilt_scope, checkpointer=InMemorySaver())
        print('Starting deterministic native-only graph', flush=True)
        result = await engine.start(rebuilt_scope, 'Rebuild exact saved files')
        assert result['done'] and result['results']['native-rebuild']['status'] == 'succeeded', result
        refs = result['results']['native-rebuild']['artifacts']
        assert len(refs) == 8
        for ref in refs:
            assert digest(artifacts.blobs.get(ref['sha256'])) == ref['sha256']
        assert next(ref['sha256'] for ref in refs if ref['path'] == 'model.py') == digest(FIXTURE.encode())
        with projects.transaction() as session:
            rebuilt = session.scalar(select(NativeJob).where(NativeJob.run_id == rebuilt_scope.run_id))
            assert rebuilt.source_manifest == original.source_manifest == {'model.py': digest(FIXTURE.encode())}
            for key in ('imageDigest', 'resources', 'networkPolicy', 'baselines', 'allowedPaths'):
                assert rebuilt.request[key] == original.request[key]
            assert rebuilt.request['rebuildOf'] == original.id
            assert rebuilt.request['sessionSeconds'] == 300 and rebuilt.request['maxActions'] == 3
            actions = list(session.scalars(select(NativeAction).where(NativeAction.job_id == rebuilt.id)))
            assert {row.action_id for row in actions} == {
                'rebuild:build', 'rebuild:render', 'rebuild:finish', '_dispatch', '_termination'}
            assert all(row.status == 'completed' for row in actions)
            finish = next(row.result for row in actions if row.action_id == 'rebuild:finish')
            assert all(item['status'] == 'candidate' for item in artifacts.list(session, scope.project_id))
        replay_task = engine.specialist.task
        assert await gateway.invoke_rebuild(rebuilt_scope, replay_task, snapshot, 'finish',
                                           {'actionId': 'rebuild:finish'}) == finish
        assert capacity.usage()['jobs'] == 1
        await gateway.reconcile()
        assert capacity.usage()['jobs'] == 0
        report.update(qualified=True, originalJobId=original.id, rebuiltJobId=rebuilt.id, grant=grant,
                      originalRequestHash=original.request_hash, rebuiltRequestHash=rebuilt.request_hash,
                      sourceManifest=rebuilt.source_manifest, resources=rebuilt.request['resources'],
                      networkPolicy=rebuilt.request['networkPolicy'], artifacts=refs,
                      stoppedContainerProof=True, exactSourceReplay=True, changedOperatorSettingsIgnored=True,
                      sharedCapacity=True, finalCapacity=capacity.usage(),
                      limitations=['SQLite fixture with in-memory LangGraph checkpoint; PostgreSQL recovery separately tested',
                                   'No manufacturing acceptance or physical/device execution'])
        print('Native-only graph passed', flush=True)
    finally:
        with projects.transaction() as session:
            jobs = list(session.scalars(select(NativeJob)))
        for job in jobs:
            await docker.stop(job.id)
            await docker.cleanup(job.id)
        report['finishedAt'] = datetime.now(UTC).isoformat()
        Path(args.output).write_text(json.dumps(report, indent=2)+'\n')
        print('Evidence: '+args.output, flush=True)
        await docker.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--image', required=True)
    parser.add_argument('--socket', default='/var/run/docker.sock')
    parser.add_argument('--output', required=True)
    asyncio.run(qualify(parser.parse_args()))
