"""Opt-in real offline CAD/application composition; generated code runs only in pinned images.

Uses a fresh retained schema in an explicitly disposable PostgreSQL database.
The first invocation stops before acceptance. --accept-existing is a separate,
explicit fixture-owner acceptance after independent review; it dispatches no jobs.
"""
import argparse
import asyncio
import copy
import hashlib
import io
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import psycopg
from alembic import command
from alembic.config import Config
from psycopg import sql
from sqlalchemy import select
from sqlalchemy.engine import make_url

from kleos.access.service import Account, OrganizationMember
from kleos.api.releases import ReleaseFacade
from kleos.artifacts.service import Artifact, ArtifactService, NativePublication
from kleos.artifacts.storage import BlobStore
from kleos.capabilities.registry import CapabilityRegistry
from kleos.contracts.models import Actor, DomainError, TaskResult, TaskSpec
from kleos.execution.capacity import CapacityReservation, CapacityService
from kleos.execution.docker import DockerClient, NativeSettings
from kleos.execution.gateway import NativeAction, NativeGateway, NativeJob
from kleos.execution.qualification.qualify_cad import FIXTURE
from kleos.infrastructure.native_maintenance import maintain_native
from kleos.infrastructure.runtime import NativeToolConfiguration, native_qualification
from kleos.infrastructure.validation import configure_validation
from kleos.orchestration.composition import FinalCompilation
from kleos.orchestration.engine import ObjectiveEngine, PlanDecision, RunScope
from kleos.orchestration.models import ModelAttempt
from kleos.orchestration.persistence import postgres_runtime
from kleos.orchestration.worker import ObjectiveWorker
from kleos.projects.graph_repository import Effect, GraphRepository
from kleos.projects.store import Objective, Project, ProjectStore, fingerprint
from kleos.projects.verification import VerificationJob, VerificationService, current_candidates
from kleos.validation.policies import authored_native_rows, published_native_rows
from kleos.validation.service import Candidate, Release, ValidationCheck

ROOT = Path(__file__).resolve().parents[1]
IMAGES = {
    'cad': 'sha256:62280256393a1f822fa1a256eb2991220bb2ec225f7e17894dbb52ec7a02d918',
    'application': 'sha256:572fd5f8d64fd6d7a1bed7c955ee33c8223529a43fc0dedd8e53dcc81328e315',
    'cadChecker': 'sha256:e3c5823091b7dde83d1043bd455ef96567939424ab447a3efed1149dc1030e69',
    'applicationChecker': 'sha256:995f1bea80f8f66a48aa12773d60c95fdb3f56706a323d1bcc85b98092948503',
}
REPORTS = {
    'cad': ROOT/'src/kleos/execution/qualification/cad-exact-image.json',
    'application': ROOT/'docs/companion-native-font-browser.json',
    'cadChecker': ROOT/'src/kleos/validation/qualification/cad-check-exact-image.json',
    'applicationChecker': ROOT/'src/kleos/validation/qualification/export-check-application-exact-image.json',
}


def save(path, value):
    path.write_text(json.dumps(value, indent=2, default=str)+'\n')


def fixtures():
    application = json.loads((ROOT/'src/kleos/execution/qualification/web-fixture-sources.json').read_bytes())
    return {'cad': {'model.py': FIXTURE}, 'application': application}


class TrustedPlanner:
    def __init__(self, sources):
        self.sources, self.calls = sources, 0

    async def plan(self, scope, objective, context, tasks, results, answer, **kwargs):
        self.calls += 1
        if results:
            return PlanDecision(done=True, summary='Trusted fixture generation completed; independent checks required')
        return PlanDecision(tasks=[TaskSpec(id=lane, capability=lane,
            objective='Build the unchanged trusted '+lane+' fixture', allowed_paths=sorted(paths), max_actions=8)
            for lane, paths in self.sources.items()])


class TrustedSpecialist:
    def __init__(self, gateway, sources):
        self.gateway, self.sources, self.calls = gateway, sources, []

    async def execute(self, scope, task, context):
        self.calls.append(task.id)
        for number, (path, content) in enumerate(self.sources[task.id].items()):
            result = await self.gateway.invoke(scope, task, 'patch', {'actionId': f'source-{number}',
                'path': path, 'content': content, 'expectedSha256': None})
            self.check(result)
        actions = ['build', 'analyze', 'render', 'finish'] if task.id == 'cad' else ['build', 'finish']
        for action in actions:
            print(f'{task.id}: {action}', flush=True)
            result = await self.gateway.invoke(scope, task, action,
                {'actionId': action, **({'view': 'isometric'} if action == 'render' else {})})
            self.check(result)
        return TaskResult(task_id=task.id, status='succeeded', summary='Real isolated trusted fixture exports',
            artifacts=result['artifacts'], observations=result['observations'])

    @staticmethod
    def check(result):
        if result.get('blockers') or not result.get('observations') or any(
            row['status'] in {'FAIL', 'ERROR'} or row.get('independent')
            or row.get('details', {}).get('executionSucceeded') is not True for row in result['observations']):
            raise DomainError('Trusted native fixture did not complete its fixed action', 'qualification_native_failed')


def database(args, state=None):
    raw = json.loads(args.database_config.read_bytes())['url']
    parsed = make_url(raw)
    if not parsed.database.startswith(('kleos_rewrite_verify_', 'kleos_test_')):
        raise ValueError('Only an explicitly disposable PostgreSQL database is allowed')
    schema = state['schema'] if state else 'composition_'+uuid4().hex
    if state is None:
        with psycopg.connect(raw, autocommit=True) as conn:
            conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
    scoped = parsed.update_query_dict({'options': '-csearch_path='+schema})
    store = ProjectStore(scoped.set(drivername='postgresql+psycopg').render_as_string(hide_password=False),
                         require_account_authority=True)
    if state is None:
        with store.engine.begin() as connection:
            migration = Config(str(ROOT/'alembic.ini'))
            migration.attributes['connection'] = connection
            command.upgrade(migration, 'head')
            command.check(migration)
    return store, scoped.render_as_string(hide_password=False), schema


def settings(work, socket):
    return SimpleNamespace(environment='development', docker_socket=socket, checker_control_root=work/'checks',
        cad_checker_installation={'imageDigest': IMAGES['cadChecker'], 'qualificationReference': str(REPORTS['cadChecker'])},
        application_checker_installation={'imageDigest': IMAGES['applicationChecker'],
            'qualificationReference': str(REPORTS['applicationChecker'])})


def ledger(store):
    tables = [Objective, NativeJob, NativeAction, NativePublication, Artifact, Effect,
              CapacityReservation, VerificationJob, Candidate, ValidationCheck, Release]
    with store.transaction() as session:
        return {table.__tablename__: [{column.name: getattr(row, column.name) for column in table.__table__.columns}
            for row in session.scalars(select(table))] for table in tables}


async def qualify(args):
    work = args.work.resolve()
    if args.accept_existing:
        return await accept_existing(args)
    work.mkdir(parents=True, exist_ok=False)
    sources = fixtures()
    save(work/'fixture-sources.json', sources)
    report = {'qualified': False, 'scope': 'One trusted CAD and Flutter web fixture, real composed digital export checks',
        'startedAt': datetime.now(UTC).isoformat(), 'images': IMAGES, 'sourceOverride': False,
        'qualificationReportHashes': {key: hashlib.sha256(path.read_bytes()).hexdigest() for key, path in REPORTS.items()},
        'sourceHashes': {lane: {path: hashlib.sha256(data.encode()).hexdigest() for path, data in values.items()}
                         for lane, values in sources.items()},
        'modelCalls': 0, 'memoryCalls': 0, 'acceptanceAttempted': False, 'containers': {},
        'fixtureConstraintAuthority': 'Trusted qualification fixture numeric requirements; no user concept/approval claim',
        'hostSourceHashes': {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in [Path(__file__).resolve(), *[ROOT/'src/kleos'/name for name in (
                'execution/composition.py', 'execution/gateway.py', 'execution/docker.py', 'execution/capacity.py',
                'orchestration/composition.py', 'orchestration/engine.py', 'orchestration/worker.py',
                'projects/composition.py', 'projects/composition_results.py', 'projects/graph_repository.py',
                'projects/verification.py', 'validation/policies.py', 'validation/service.py',
                'infrastructure/validation.py', 'api/releases.py')]]},
        'limitations': ['Local Docker Desktop only', 'Single producer per lane; no multi-CAD entrypoint qualification',
            'No arbitrary device, browser, BLE, firmware, PCB, manufacturing or physical acceptance',
            'Capacity belongs to this retained qualification schema; no cross-database arbitration claim']}
    store, url, schema = database(args)
    report['schema'] = schema
    docker = DockerClient(socket=args.socket)
    artifacts = ArtifactService(store, BlobStore(work/'blobs'))
    capacity = CapacityService(store, pool_id='composed-cad-app-qualification')
    repository = GraphRepository(store)
    gateway = NativeGateway(store, artifacts, NativeSettings(work/'control',
        {lane: IMAGES[lane] for lane in sources}, session_seconds=600, docker_desktop_compatibility=True),
        docker, repository, capacity=capacity)
    stop, maintenance, run_id = asyncio.Event(), None, None
    original_get = docker.client.get

    async def observed_get(path, **kwargs):
        response = await original_get(path, **kwargs)
        if (str(path) != '/containers/json' and str(path).startswith('/containers/')
                and str(path).endswith('/json') and response.status_code == 200):
            data = response.json()
            host = data['HostConfig']
            report['containers'][data['Id']] = {'image': data['Image'], 'running': data['State']['Running'],
                'name': data['Name'], 'networkMode': host['NetworkMode'], 'readonlyRootfs': host['ReadonlyRootfs'],
                'memory': host['Memory'], 'nanoCpus': host['NanoCpus'], 'pidsLimit': host['PidsLimit'],
                'readonlyMounts': all(not row['RW'] for row in data['Mounts']), 'tmpfs': host['Tmpfs']}
        return response
    docker.client.get = observed_get
    try:
        active = await docker.client.get('/containers/json')
        active.raise_for_status()
        assert not any(any(key.startswith('kleos.') for key in row.get('Labels', {})) or any(
            any(word in name for word in ('native', 'checker', 'slicer', 'mcp')) for name in row.get('Names', []))
            for row in active.json()), 'Another engineering workload is active'
        for image in IMAGES.values():
            inspected = await docker.client.get('/images/'+image+'/json')
            inspected.raise_for_status()
            assert inspected.json()['Id'] == image
        pins = [native_qualification(NativeToolConfiguration(capability=lane, image_digest=IMAGES[lane],
            provider_version='existing-exact-image', qualified=True, qualification_reference=REPORTS[lane]), desktop=True)
            for lane in sources]
        wiring = configure_validation(settings(work, args.socket), store, artifacts, CapabilityRegistry(),
                                      docker=docker, capacity=capacity)
        actor = Actor(tenant_id='composed-qualification', user_id='trusted-fixture-owner')
        with store.transaction() as session:
            session.add(Account(id=actor.user_id, email='fixture@example.invalid', name='Trusted fixture', password_hash='not-loginable'))
            session.add(OrganizationMember(tenant_id=actor.tenant_id, user_id=actor.user_id, role='owner'))
        project_id = store.create(actor, 'Trusted two-part CAD and local counter web application', 'create')['projectId']
        with store.transaction() as session:
            project = session.get(Project, project_id)
            project.data = {**project.data, 'interfaces': {'validation': {'cad': {'units': 'mm', 'constraints': [
                {'part': 'outputs/base', 'metric': 'extent_x_mm', 'expected': 24, 'tolerance': .001},
                {'part': 'outputs/base', 'metric': 'extent_y_mm', 'expected': 16, 'tolerance': .001},
                {'part': 'outputs/lid', 'metric': 'extent_z_mm', 'expected': 2, 'tolerance': .001}]}}}}
        submitted = store.submit(actor, project_id, 'Build and independently check trusted offline fixtures', 1, 'objective')
        run_id = submitted['runId']
        with store.transaction() as session:
            run = session.get(Objective, run_id)
            run.data = {**run.data, 'runtimePins': {'native': pins, 'models': [], 'qualificationOnly': True}}
        save(work/'state.json', {'schema': schema, 'actor': actor.model_dump(), 'projectId': project_id, 'runId': run_id})
        planner, specialist = TrustedPlanner(sources), TrustedSpecialist(gateway, sources)
        finalizer = FinalCompilation(repository, artifacts, gateway)
        maintenance = asyncio.create_task(maintain_native(gateway, stop, poll_seconds=.5))
        async with postgres_runtime(url, setup=True) as (saver, memory_store):
            def engine_factory(scope):
                return ObjectiveEngine(repository, planner, specialist, checkpointer=saver, store=memory_store,
                                       concurrency=1, composition=finalizer)
            worker = ObjectiveWorker(store, engine_factory, max_run_seconds=3000, max_lifetime_seconds=3600)
            print('Starting actual worker and serial native generation', flush=True)
            await worker.process(run_id)
            with store.transaction() as session:
                run = session.get(Objective, run_id)
                report['objectiveStatus'] = run.status
                report['objectiveBlocker'] = run.data.get('blocker')
                assert run.status == 'succeeded', report['objectiveBlocker']
                scope = RunScope.model_validate(run.data['scope'])
                report['compositionGrant'] = copy.deepcopy(run.data['sourceComposition'])
                report['compositionResults'] = copy.deepcopy(run.data['compositionResults'])
                authored = authored_native_rows(session, run)
                canonical = published_native_rows(session, run)
                original_outputs = {r.id for r in authored if r.details['role'] == 'output'}
                final_outputs = {r.id for r in canonical if r.details['role'] == 'output'}
                assert original_outputs.isdisjoint(final_outputs)
                closure = report['compositionGrant']['closure']
                assert set(closure) == {path for values in sources.values() for path in values}
                for path, ident in closure.items():
                    row = session.get(Artifact, ident)
                    expected = next(values[path] for values in sources.values() if path in values)
                    assert artifacts.blobs.get(row.sha256) == expected.encode()
                assert all(r.details['sourceClosure'] == closure for r in canonical if r.details['role'] == 'output')
                assert session.get(Project, project_id).data.get('sourceManifest', {}) == {}
                assert session.scalar(select(ModelAttempt)) is None
                report['selectedCandidates'] = current_candidates(session, run, 'digital.cad-application')
                report['activeBudget'] = copy.deepcopy(run.data.get('activeBudget'))
            snapshot = await saver.aget_tuple(engine_factory(scope)._config(scope))
            assert snapshot is not None
            report['checkpointId'] = snapshot.checkpoint['id']
        async with postgres_runtime(url) as (saver, _):
            snapshot = await saver.aget_tuple({'configurable': {'thread_id': scope.thread_id}})
            assert snapshot is not None
            report['checkpointReopened'] = snapshot.checkpoint['id'] == report['checkpointId']
        report['plannerCalls'] = planner.calls
        report['specialistCalls'] = specialist.calls
        assert planner.calls == 2 and specialist.calls == ['cad', 'application']
        await gateway.reconcile()
        assert capacity.usage()['jobs'] == 0
        verification = VerificationService(store, wiring.registry)
        with store.transaction() as session:
            revision = session.get(Project, project_id).revision
        admission = verification.submit(actor, project_id, revision, 'verify', policy_key=None, run_id=run_id)
        assert admission['policyKey'] == 'digital.cad-application'
        print('Starting actual independent CAD and application checks', flush=True)
        await verification.process_next()
        verified = verification.get(actor, project_id, admission['verificationId'])
        report['verification'] = verified
        assert verified['status'] == 'completed' and set(verified['checkStatuses'].values()) == {'PASS'}, verified
        with store.transaction() as session:
            checks = list(session.scalars(select(ValidationCheck)))
            jobs = {job.id for job in session.scalars(select(NativeJob))}
            assert len(jobs) == 4 and len(checks) == 2
            assert all(row.data['report']['execution_id'] not in jobs for row in checks)
        report['awaitingIndependentReviewBeforeAcceptance'] = True
        state = json.loads((work/'state.json').read_bytes())
        save(work/'state.json', {**state, 'candidateId': verified['candidateId'], 'revision': revision})
        print('Independent checks complete; explicit release acceptance is held for review', flush=True)
    except Exception as error:
        report['failure'] = {'type': type(error).__name__, 'code': getattr(error, 'code', 'qualification_failed')}
        raise
    finally:
        if run_id:
            with store.transaction() as session:
                run = session.get(Objective, run_id)
                if run.status in {'pending', 'running', 'waiting'}:
                    run.status = 'cancelled'
            await gateway.reconcile()
        stop.set()
        if maintenance:
            await maintenance
        report['finalCapacity'] = capacity.usage()
        report['finishedAt'] = datetime.now(UTC).isoformat()
        save(work/'ledger.json', ledger(store))
        save(work/'report.json', report)
        await docker.close()
        store.engine.dispose()
        print('Evidence retained at '+str(work), flush=True)


async def accept_existing(args):
    work = args.work.resolve()
    state = json.loads((work/'state.json').read_bytes())
    report = json.loads((work/'report.json').read_bytes())
    assert report.get('awaitingIndependentReviewBeforeAcceptance') and not report.get('failure')
    assert report['finalCapacity']['jobs'] == 0
    store, _, _ = database(args, state)
    artifacts = ArtifactService(store, BlobStore(work/'blobs'))
    # Configuration validates reports; this branch never invokes Docker/model/native actions.
    wiring = configure_validation(settings(work, args.socket), store, artifacts, CapabilityRegistry())
    try:
        actor = Actor.model_validate(state['actor'])
        with store.transaction() as session:
            candidate = session.get(Candidate, state['candidateId'])
            reviewed_subject = candidate.subject_hash
            reviewed_authority = copy.deepcopy(candidate.data.get('interfaceAuthority'))
            assert candidate.status == 'qualified'
            assert not any(row.status != 'released' for row in session.scalars(select(CapacityReservation)))
        receipt = wiring.service.accept(actor, state['projectId'], state['candidateId'], state['revision'], 'accept')
        assert wiring.service.accept(actor, state['projectId'], state['candidateId'], state['revision'], 'accept') == receipt
        content = ReleaseFacade(store, artifacts, wiring.service).content(actor, receipt['releaseId'])
        with zipfile.ZipFile(io.BytesIO(content.data)) as archive:
            manifest = json.loads(archive.read('manifest.json'))
            assert manifest['subjectHash'] == reviewed_subject
            assert manifest.get('interfaceAuthority') == reviewed_authority
            assert fingerprint({key: value for key, value in manifest.items() if key != 'manifestHash'}) == manifest['manifestHash']
            assert {check['checkerKey'] for check in manifest['checks']} == {
                'cad.export-integrity', 'application.web-bundle-integrity'}
            assert all(check['status'] == 'PASS' and check['independent']
                and check['subjectHash'] == reviewed_subject for check in manifest['checks'])
            for group, prefix in [('sources', 'sources/'), ('outputs', 'artifacts/')]:
                for ref in manifest[group]:
                    assert hashlib.sha256(archive.read(prefix+ref['path'])).hexdigest() == ref['sha256']
        (work/'release.zip').write_bytes(content.data)
        report.update(qualified=True, acceptanceAttempted=True, release=receipt,
            releaseManifest=manifest, acceptanceHarnessSha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            releaseZipSha256=hashlib.sha256(content.data).hexdigest(),
            awaitingIndependentReviewBeforeAcceptance=False)
        save(work/'accepted-report.json', report)
        save(work/'accepted-ledger.json', ledger(store))
        print('Explicit fixture release accepted and package hashes verified', flush=True)
    finally:
        if wiring.docker:
            await wiring.docker.close()
        store.engine.dispose()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--database-config', type=Path, required=True)
    parser.add_argument('--socket', default='/var/run/docker.sock')
    parser.add_argument('--accept-existing', action='store_true')
    asyncio.run(qualify(parser.parse_args()))
