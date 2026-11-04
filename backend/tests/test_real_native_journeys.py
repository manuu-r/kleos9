"""Opt-in composed journeys: real runtime, real API, real native containers (KLEOS_TEST_REAL_NATIVE=1).

Scripted mode (default) replays fixed model tool calls so only the model is simulated: the CAD and PCB source is
executed by the qualified container images and every assertion reads real exported files. Live mode
(`KLEOS_TEST_LIVE_MODEL=1`, see journey_support) lets a real model decide and asserts only invariants that must
hold for ANY model: scope, provenance, no acceptance, and honest terminal states.
"""
import hashlib
import io
import json
import os
import subprocess
import zipfile
from pathlib import Path

import httpx
import pytest
from journey_support import LIVE, Script, real_runtime
from sqlalchemy import select
from test_imported_interfaces import CIRCUIT, SOURCES

from kleos.artifacts.service import Artifact
from kleos.execution.gateway import NativeJob
from kleos.projects.interfaces import accepted_circuit_identity
from kleos.projects.store import Objective, Project

pytestmark = pytest.mark.skipif(os.environ.get('KLEOS_TEST_REAL_NATIVE') != '1',
    reason='Set KLEOS_TEST_REAL_NATIVE=1 (local Docker daemon and the qualified native images are required)')

scripted = pytest.mark.skipif(LIVE, reason='Scripted journeys assert exact model-independent outputs; live mode has its own tests')
live = pytest.mark.skipif(not LIVE, reason='Set KLEOS_TEST_LIVE_MODEL=1, a Meta key and KLEOS_LIVE_MAX_COST_USD')

BRACKET = '''from build123d import Box, Cylinder, Pos
plate = Box(40, 20, 3)
bracket = plate - Pos(-12, 0, 0) * Cylinder(2.2, 3) - Pos(12, 0, 0) * Cylinder(2.2, 3)
parts = {'bracket': bracket}
'''
BRACKET_VOLUME_MM3 = 40 * 20 * 3 - 2 * 3.141592653589793 * 2.2 ** 2 * 3


def stl_geometry(raw):
    """Independent measurement of a binary STL: signed tetrahedron volume and axis-aligned bounds."""
    import struct
    count = struct.unpack_from('<I', raw, 80)[0]
    assert len(raw) == 84 + 50 * count, 'not a well-formed binary STL'
    volume, low, high = 0.0, [1e30] * 3, [-1e30] * 3
    for index in range(count):
        values = struct.unpack_from('<12f', raw, 84 + 50 * index)
        a, b, c = values[3:6], values[6:9], values[9:12]
        volume += (a[0] * (b[1] * c[2] - b[2] * c[1]) - a[1] * (b[0] * c[2] - b[2] * c[0])
                   + a[2] * (b[0] * c[1] - b[1] * c[0])) / 6
        for vertex in (a, b, c):
            low = [min(x, y) for x, y in zip(low, vertex)]
            high = [max(x, y) for x, y in zip(high, vertex)]
    return abs(volume), [h - low_ for h, low_ in zip(high, low)]


def usage_report(runtime, run_ids):
    from kleos.orchestration.models import ModelAttempt
    with runtime.projects.transaction() as session:
        rows = list(session.scalars(select(ModelAttempt).where(ModelAttempt.run_id.in_(run_ids))))
    return {'modelAttempts': len(rows), 'statuses': sorted({row.status for row in rows}),
            'committedMicroUsd': sum(int(row.data.get('committedMicroUsd', 0)) for row in rows)}


def write_report(tmp_path, name, report):
    directory = Path(os.environ.get('KLEOS_LIVE_REPORT_DIR', str(tmp_path)))
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f'{name}.json').write_text(json.dumps(report, indent=2, default=str) + '\n')
    print('REPORT ' + json.dumps(report, default=str))
    return report


def scope_invariants(runtime, ident, run_id, *, lanes, manifest):
    """Properties that must hold for ANY model: lane scope, provenance, no self-acceptance, untouched sources."""
    with runtime.projects.transaction() as session:
        run = session.get(Objective, run_id)
        project = session.get(Project, ident)
        assert run.status in {'succeeded', 'blocked', 'failed', 'waiting'}, run.status  # never a silent hang
        used = {task['capability'] for task in run.data.get('tasks', [])}
        assert used <= lanes, f'unrequested capabilities were planned: {used - lanes}'
        assert project.data['sourceManifest'] == manifest  # candidates never rewrite the accepted baseline
        assert not project.data.get('acceptedReleaseId')
        artifacts = [session.get(Artifact, ref['id']) for result in run.data.get('results', {}).values()
                     for ref in result.get('artifacts', [])]
        assert all(row.status == 'candidate' for row in artifacts)  # a generator cannot accept its own output
        return run.status, artifacts


async def assert_jobs_cleaned_up(runtime, run_id):
    """Finished jobs keep their container until the gateway's periodic reconcile; afterwards none may remain."""
    await runtime.native_gateway.reconcile()
    with runtime.projects.transaction() as session:
        job_ids = [job.id for job in session.scalars(select(NativeJob).where(NativeJob.run_id == run_id))]
    assert job_ids
    for job_id in job_ids:
        out = subprocess.run(['docker', 'ps', '-a', '-q', '--filter', 'name=kleos-native-' + job_id],
                             capture_output=True, text=True, check=True)
        assert not out.stdout.split(), 'native container was not removed by reconcile'


async def upload(runtime, actor, ident, files):
    uploaded = {}
    for path, content in files.items():
        revision = runtime.projects.context(actor, ident).revision
        row = runtime.artifacts.upload(actor, ident, {'expectedEngineeringRevision': revision,
            'sha256': hashlib.sha256(content.encode()).hexdigest(),
            'file': {'path': path, 'content': content, 'mediaType': 'text/plain'}}, 'upload-' + path)
        uploaded[path] = row['artifact']
    return uploaded


@scripted
async def test_missing_bracket_in_an_imported_project_runs_real_cad_and_touches_nothing_else(tmp_path):
    objective = 'Create only the missing mounting bracket (40x20x3 mm plate, two 4.4 mm holes) for the existing enclosure.'
    sources = {'enclosure.py': '# Existing enclosure; mounting datum x=20 mm.\n', 'pcb/board.kicad_pcb': '(kicad_pcb)\n'}
    inputs = {}
    script = Script(
        PlanDecision=[{'tasks': [{'id': 'bracket-only', 'capability': 'cad', 'objective': objective,
            'inputArtifactIds': [], 'allowedPaths': ['model.py'], 'maxActions': 10}], 'summary': 'Only the bracket.'},
            {'done': True, 'summary': 'Bracket candidate produced; independent checking remains required.'}],
        Action=[{'action': 'tool', 'tool': 'cad.patch', 'arguments': {'path': 'model.py', 'content': BRACKET, 'expectedSha256': None}},
                {'action': 'tool', 'tool': 'cad.build', 'arguments': {}},
                {'action': 'tool', 'tool': 'cad.analyze', 'arguments': {}},
                {'action': 'tool', 'tool': 'cad.finish', 'arguments': {}},
                {'action': 'finish', 'summary': 'Bracket candidate exported; no independent geometry or physical validation.'}])
    upstream = httpx.AsyncClient(transport=httpx.MockTransport(script))
    runtime = real_runtime(tmp_path, ['cad'], upstream)
    async with upstream, runtime.start(), httpx.AsyncClient(
            transport=httpx.ASGITransport(app=runtime.app), base_url='http://local') as client:
        created = await client.post('/api/v1/commands', headers={'Idempotency-Key': 'create'},
            json={'type': 'project.create', 'payload': {'brief': objective, 'autoStart': False}})
        ident = created.json()['projectId']
        actor = runtime.access.authenticate({}, {}, 'POST')
        inputs = await upload(runtime, actor, ident, sources)
        workspace = (await client.get(f'/api/v1/projects/{ident}/workspace')).json()
        admitted = await client.post('/api/v1/commands', headers={'Idempotency-Key': 'bracket'}, json={
            'type': 'project.generate', 'expectedRevision': workspace['revision'],
            'payload': {'projectId': ident, 'objective': objective, 'allowedPaths': ['model.py']}})
        assert admitted.status_code == 202, admitted.text
        run_id = admitted.json()['runId']
        assert await runtime.worker.process(run_id)
        with runtime.projects.transaction() as session:
            run = session.get(Objective, run_id)
            assert run.status == 'succeeded', run.data.get('blocker')
            assert {task['capability'] for task in run.data['tasks']} == {'cad'}  # no unrequested lanes
            jobs = list(session.scalars(select(NativeJob).where(NativeJob.run_id == run_id)))
            assert len(jobs) == 1 and jobs[0].status == 'finished'
            published = [session.get(Artifact, ref['id']) for ref in run.data['results']['bracket-only']['artifacts']]
            names = {row.name for row in published}
            assert {'model.py', 'outputs/bracket.step', 'outputs/bracket.stl', 'outputs/parts.json'} <= names
            assert all(row.status == 'candidate' for row in published)  # never accepted by its generator
            assert not {'enclosure.py', 'pcb/board.kicad_pcb'} & names  # untouched context is not republished
            project = session.get(Project, ident)
            assert project.data['sourceManifest'] == {path: inputs[path]['id'] for path in sources}
            assert not project.data.get('acceptedReleaseId')
            step = next(row for row in published if row.name.endswith('.step'))
            stl = next(row for row in published if row.name.endswith('.stl'))
            parts = next(row for row in published if row.name.endswith('parts.json'))
            step_bytes, stl_bytes = (runtime.artifacts.blobs.get(row.sha256) for row in (step, stl))
            measured = json.loads(runtime.artifacts.blobs.get(parts.sha256))
        assert b'ISO-10303-21' in step_bytes and len(stl_bytes) > 84  # real STEP header, real binary STL
        assert [part['id'] for part in measured['parts']] == ['bracket']
        volume, size = stl_geometry(stl_bytes)  # measured from the exported file, not from the generator's report
        assert size == pytest.approx([40, 20, 3], abs=1e-3)
        assert volume == pytest.approx(BRACKET_VOLUME_MM3, rel=.01)
        assert volume < 40 * 20 * 3 - 80  # both 4.4 mm holes are really cut (a solid plate would be 2400 mm3)
        for path, content in sources.items():
            assert runtime.artifacts.content(actor, inputs[path]['id'], ident).data == content.encode()
        assert script.count('PlanDecision') == 2 and script.count('CaptureResult') == 0
        scope_invariants(runtime, ident, run_id, lanes={'cad'}, manifest={path: inputs[path]['id'] for path in sources})
        await assert_jobs_cleaned_up(runtime, run_id)


@scripted
async def test_imported_pcb_is_routed_by_real_kicad_and_freerouting_after_explicit_identity_approval(tmp_path):
    board_id = None
    script = Script()

    def proposal(request):
        nonlocal board_id
        return script(request)
    upstream = httpx.AsyncClient(transport=httpx.MockTransport(proposal))
    runtime = real_runtime(tmp_path, ['pcb'], upstream)
    async with upstream, runtime.start(), httpx.AsyncClient(
            transport=httpx.ASGITransport(app=runtime.app), base_url='http://local') as client:
        created = await client.post('/api/v1/commands', headers={'Idempotency-Key': 'create'}, json={
            'type': 'project.create', 'payload': {'brief': 'Route an existing PCB; no new visual concept.', 'autoStart': False}})
        ident = created.json()['projectId']
        actor = runtime.access.authenticate({}, {}, 'GET')
        uploaded = await upload(runtime, actor, ident, dict(sorted(SOURCES.items())))
        board_id = uploaded['board.kicad_pcb']['id']
        source_ids = [row['id'] for row in uploaded.values()]
        script.payloads.update(
            InterfaceProposal=[{'summary': 'Route this existing board without inventing a visual concept.',
                'interfaces': {'circuit': CIRCUIT}, 'circuitIdentity': {'mode': 'imported_no_concept', 'sourceArtifactIds': [board_id]},
                'sourceIds': [board_id], 'limitations': ['Routing and engineering checks have not run.']}],
            PlanDecision=[{'tasks': [{'id': 'route-board', 'capability': 'pcb', 'objective': 'Route this accepted imported PCB only.',
                'inputArtifactIds': source_ids, 'allowedPaths': [], 'maxActions': 8}], 'summary': 'Routing only.'},
                {'done': True, 'summary': 'Routed candidate produced; ERC/DRC and correspondence remain independent checks.'}],
            Action=[{'action': 'tool', 'tool': 'pcb.build', 'arguments': {}},
                    {'action': 'tool', 'tool': 'pcb.finish', 'arguments': {}},
                    {'action': 'finish', 'summary': 'Routed board exported; no independent check or fabrication claim.'}])
        before = (await client.get(f'/api/v1/projects/{ident}/workspace')).json()
        solved = await client.post('/api/v1/commands', headers={'Idempotency-Key': 'solve'}, json={
            'type': 'project.solveInterfaces', 'expectedRevision': before['revision'],
            'payload': {'projectId': ident, 'objective': 'Route only the imported PCB while preserving exact board sources.'}})
        assert solved.status_code == 202, solved.text
        assert await runtime.worker.process(solved.json()['runId'])
        pending = (await client.get(f'/api/v1/projects/{ident}/workspace')).json()
        assert pending['project']['interfaces'] == {}  # a proposal is never self-approved
        approved = await client.post('/api/v1/commands', headers={'Idempotency-Key': 'approve'}, json={
            'type': 'project.message', 'expectedRevision': pending['revision'], 'payload': {
                'projectId': ident, 'text': 'approve interfaces', 'autoResume': True, 'referenceImageIds': []}})
        assert approved.status_code == 202, approved.text
        with runtime.projects.transaction() as session:
            accepted = accepted_circuit_identity(session, runtime.projects.authorized(session, actor, ident))
        generation = await client.post('/api/v1/commands', headers={'Idempotency-Key': 'generate'}, json={
            'type': 'project.generate', 'expectedRevision': approved.json()['revision'], 'payload': {
                'projectId': ident, 'objective': 'Route this accepted imported PCB only. Preserve placement and source closure.'}})
        assert generation.status_code == 202, generation.text
        run_id = generation.json()['runId']
        assert await runtime.worker.process(run_id)
        with runtime.projects.transaction() as session:
            run = session.get(Objective, run_id)
            assert run.status == 'succeeded', run.data.get('blocker')
            assert {task['capability'] for task in run.data['tasks']} == {'pcb'}
            jobs = list(session.scalars(select(NativeJob).where(NativeJob.run_id == run_id)))
            assert len(jobs) == 1 and jobs[0].status == 'finished'
            published = {row.name: row for row in (session.get(Artifact, ref['id'])
                for ref in run.data['results']['route-board']['artifacts'])}
            assert {'outputs/routed.kicad_pcb', 'outputs/erc.json', 'outputs/drc.json', 'outputs/netlist.xml',
                    'outputs/pcb-project.zip', 'outputs/gerbers/board.drl'} <= set(published)
            assert all(row.status == 'candidate' for row in published.values())
            assert accepted_circuit_identity(session, runtime.projects.authorized(session, actor, ident)) == accepted
            assert not session.get(Project, ident).data.get('acceptedReleaseId')
            blobs = {name: runtime.artifacts.blobs.get(row.sha256) for name, row in published.items()}
        routed = blobs['outputs/routed.kicad_pcb'].decode()
        assert routed.startswith('(kicad_pcb') and '(segment' in routed  # Freerouting actually placed copper
        for report, schema in (('outputs/erc.json', 'erc.v1'), ('outputs/drc.json', 'drc.v1')):
            document = json.loads(blobs[report])
            assert document['$schema'].endswith(schema + '.json') and document['coordinate_units'] == 'mm', report
            # ERC nests violations per sheet; DRC lists them at the top level. Missing keys must not pass silently.
            found = (document['violations'] if 'violations' in document
                     else [v for sheet in document['sheets'] for v in sheet['violations']])
            errors = [v for v in found if v.get('severity') == 'error']
            assert not errors, (report, errors[:3])
        drc = json.loads(blobs['outputs/drc.json'])
        assert drc['unconnected_items'] == [] and drc['violations'] == []  # every accepted net is routed and clean
        with zipfile.ZipFile(io.BytesIO(blobs['outputs/pcb-project.zip'])) as archive:
            assert {'board.kicad_pcb', 'circuit.kicad_sch', 'circuit-contract.json'} <= set(archive.namelist())
        for path, content in SOURCES.items():  # imported sources are byte-for-byte unchanged
            assert runtime.artifacts.content(actor, uploaded[path]['id'], ident).data == content.encode()
        scope_invariants(runtime, ident, run_id, lanes={'pcb'}, manifest={path: uploaded[path]['id'] for path in SOURCES})
        await assert_jobs_cleaned_up(runtime, run_id)
    assert LIVE or script.count('InterfaceProposal') == 1


@live
async def test_live_model_bracket_only_task_on_an_imported_project(tmp_path):
    objective = ('Create only the missing mounting bracket: a 40 x 20 x 3 mm plate with two 4.4 mm through holes, '
                 'for the existing enclosure. Do not change the enclosure or the board.')
    sources = {'enclosure.py': '# Existing enclosure; mounting datum x=20 mm.\n', 'pcb/board.kicad_pcb': '(kicad_pcb)\n'}
    runtime = real_runtime(tmp_path, ['cad'], None)
    async with runtime.start(), httpx.AsyncClient(
            transport=httpx.ASGITransport(app=runtime.app), base_url='http://local') as client:
        created = await client.post('/api/v1/commands', headers={'Idempotency-Key': 'create'},
            json={'type': 'project.create', 'payload': {'brief': objective, 'autoStart': False}})
        ident = created.json()['projectId']
        actor = runtime.access.authenticate({}, {}, 'POST')
        inputs = await upload(runtime, actor, ident, sources)
        workspace = (await client.get(f'/api/v1/projects/{ident}/workspace')).json()
        admitted = await client.post('/api/v1/commands', headers={'Idempotency-Key': 'bracket'}, json={
            'type': 'project.generate', 'expectedRevision': workspace['revision'],
            'payload': {'projectId': ident, 'objective': objective, 'allowedPaths': ['model.py']}})
        assert admitted.status_code == 202, admitted.text
        run_id = admitted.json()['runId']
        await runtime.worker.process(run_id)
        status, artifacts = scope_invariants(runtime, ident, run_id, lanes={'cad'},
                                             manifest={path: inputs[path]['id'] for path in sources})
        report = {'journey': 'bracket-only', 'status': status, **usage_report(runtime, [run_id]),
                  'artifacts': sorted(row.name for row in artifacts)}
        step = [row for row in artifacts if row.name.endswith('.step')]
        stl = [row for row in artifacts if row.name.endswith('.stl')]
        if status == 'succeeded':
            assert step and stl, 'a run reported as succeeded must have exported real geometry'
            measured = [stl_geometry(runtime.artifacts.blobs.get(row.sha256)) for row in stl]
            assert all(volume > 0 for volume, _ in measured)
            report['geometry'] = [{'volumeMm3': round(v, 2), 'sizeMm': [round(s, 3) for s in size]} for v, size in measured]
            report['matchesRequestedPlate'] = any(abs(size[0] - 40) < .5 and abs(size[1] - 20) < .5 and abs(size[2] - 3) < .1
                                                  for _, size in measured)
        assert report['committedMicroUsd'] <= runtime.settings.max_model_cost_micro_usd
        write_report(tmp_path, 'live-bracket', report)


@live
async def test_live_model_routes_an_imported_pcb_after_explicit_approval(tmp_path):
    runtime = real_runtime(tmp_path, ['pcb'], None)
    async with runtime.start(), httpx.AsyncClient(
            transport=httpx.ASGITransport(app=runtime.app), base_url='http://local') as client:
        created = await client.post('/api/v1/commands', headers={'Idempotency-Key': 'create'}, json={
            'type': 'project.create', 'payload': {'brief': 'Route an existing PCB; no new visual concept.', 'autoStart': False}})
        ident = created.json()['projectId']
        actor = runtime.access.authenticate({}, {}, 'GET')
        uploaded = await upload(runtime, actor, ident, dict(sorted(SOURCES.items())))
        manifest = {path: uploaded[path]['id'] for path in SOURCES}
        before = (await client.get(f'/api/v1/projects/{ident}/workspace')).json()
        solved = await client.post('/api/v1/commands', headers={'Idempotency-Key': 'solve'}, json={
            'type': 'project.solveInterfaces', 'expectedRevision': before['revision'],
            'payload': {'projectId': ident, 'objective': 'Route only the imported PCB while preserving exact board sources.'}})
        assert solved.status_code == 202, solved.text
        run_ids = [solved.json()['runId']]
        await runtime.worker.process(run_ids[0])
        report = {'journey': 'imported-pcb-routing', 'proposalRunStatus': scope_invariants(
            runtime, ident, run_ids[0], lanes=set(), manifest=manifest)[0]}
        pending = (await client.get(f'/api/v1/projects/{ident}/workspace')).json()
        assert pending['project']['interfaces'] == {}  # a proposal is never self-approved
        if pending['project']['state'].get('interfaceProposal', {}).get('status') != 'pending':
            report['outcome'] = 'the model asked for more input instead of proposing interfaces'
            report.update(usage_report(runtime, run_ids))
            write_report(tmp_path, 'live-pcb', report)
            return
        approved = await client.post('/api/v1/commands', headers={'Idempotency-Key': 'approve'}, json={
            'type': 'project.message', 'expectedRevision': pending['revision'], 'payload': {
                'projectId': ident, 'text': 'approve interfaces', 'autoResume': True, 'referenceImageIds': []}})
        assert approved.status_code == 202, approved.text
        generation = await client.post('/api/v1/commands', headers={'Idempotency-Key': 'generate'}, json={
            'type': 'project.generate', 'expectedRevision': approved.json()['revision'], 'payload': {
                'projectId': ident, 'objective': 'Route this accepted imported PCB only. Preserve placement and source closure.'}})
        assert generation.status_code == 202, generation.text
        run_ids.append(generation.json()['runId'])
        await runtime.worker.process(run_ids[1])
        status, artifacts = scope_invariants(runtime, ident, run_ids[1], lanes={'pcb'}, manifest=manifest)
        report.update(status=status, **usage_report(runtime, run_ids), artifacts=sorted(row.name for row in artifacts))
        routed = [row for row in artifacts if row.name == 'outputs/routed.kicad_pcb']
        if status == 'succeeded':
            assert routed, 'a run reported as succeeded must have exported a routed board'
            report['routedCopper'] = '(segment' in runtime.artifacts.blobs.get(routed[0].sha256).decode()
        for path, content in SOURCES.items():
            assert runtime.artifacts.content(actor, uploaded[path]['id'], ident).data == content.encode()
        assert report['committedMicroUsd'] <= runtime.settings.max_model_cost_micro_usd
        write_report(tmp_path, 'live-pcb', report)


def test_live_mode_is_never_implicit():
    # A real model run costs money: it needs the flag, a key and an explicit ceiling, all supplied by the operator.
    assert LIVE == (os.environ.get('KLEOS_TEST_LIVE_MODEL') == '1')
