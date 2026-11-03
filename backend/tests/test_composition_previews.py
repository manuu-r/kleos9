"""Canonical preview selection from inert file fixtures, never native/browser execution."""
import base64
import copy
import json
from dataclasses import replace
from pathlib import Path

import pytest
from test_companion import bundle
from test_composition import composed as composed
from test_execution import FakeDocker, accepted_circuit
from test_previews import BOARD, stl

from kleos.access.service import Account, OrganizationMember
from kleos.api.companion import CompanionPreviews
from kleos.api.previews import EngineeringPreviews
from kleos.artifacts.service import Artifact, NativePublication
from kleos.contracts.models import Actor, DomainError, TaskResult, TaskSpec
from kleos.execution.composition import NativeComposition
from kleos.execution.native_runner import atomic_json
from kleos.execution.protocol import PROTOCOL, digest, fingerprint
from kleos.projects.composition_results import CompositionResults
from kleos.projects.store import Objective, Project
from kleos.validation.service import Release


class PreviewDocker(FakeDocker):
    """Writes small static format examples; does not execute any passed source."""
    def __init__(self, grant=None):
        super().__init__()
        self.targets = {target['jobId']: target for target in grant['targets']} if grant else {}

    async def ensure(self, job_id, data, image, control_directory, *, allow_create=True, on_observed=None):
        self.dispatches += 1
        if job_id not in self.sessions:
            assert allow_create
            self.sessions[job_id] = {item['path']: base64.b64decode(item['contentBase64']) for item in data['sources']}
        if on_observed:
            await on_observed(digest(job_id.encode()))
        root, sources = Path(control_directory), self.sessions[job_id]
        for path in sorted((root/'actions').glob('*.json')):
            if (root/'results'/path.name).exists():
                continue
            action = json.loads(path.read_bytes())
            result = {'summary': 'Static preview data fixture; no engineering claim'}
            if action['action'] == 'patch':
                args = action['arguments']
                sources[args['path']] = args['content'].encode()
                result.update(path=args['path'], sha256=digest(sources[args['path']]))
            if action['action'] == 'finish':
                names = ([ref['path'] for ref in self.targets[data['compositionTarget']]['coveredOutputs']]
                         if 'compositionId' in data else ['outputs/routed.kicad_pcb'])
                outputs = {name: (stl(70) if name.endswith('.stl') else BOARD if name.endswith('.kicad_pcb')
                    else bundle() if name.endswith('.zip') else b'Inert STEP fixture') for name in names}
                result['files'] = [{'path': name, 'role': role, 'sha256': digest(content),
                    'size': len(content), 'contentBase64': base64.b64encode(content).decode()}
                    for role, values in [('source', sources), ('output', outputs)] for name, content in values.items()]
            atomic_json(root/'results'/path.name, {'protocol': PROTOCOL, 'jobId': job_id,
                'requestHash': fingerprint(data), 'actionId': action['actionId'], 'actionHash': fingerprint(action),
                'response': {'status': 'succeeded', 'result': result}})
        return job_id


@pytest.fixture
async def preview_composition(composed):
    service, scope, tasks, _, authored, _ = composed
    store = service.repository.store
    # Explicit fixture authority, not model-generated approval or PCB qualification.
    with store.transaction() as session:
        project = session.get(Project, scope.project_id)
        project.data = {**project.data, 'interfaces': {'circuit': accepted_circuit()},
                        'approvedDesign': {'peripheralConceptIds': ['port']}}
        session.add(Account(id=scope.actor.user_id, email='preview@example.test', name='Fixture', password_hash='inert'))
        session.add(OrganizationMember(tenant_id=scope.actor.tenant_id, user_id=scope.actor.user_id, role='owner'))
    pcb = TaskSpec(id='pcb', capability='pcb', objective='Preview fixture PCB', allowed_paths=['board.kicad_pcb'])
    await service.repository.publish_plan(scope, tasks=[*tasks, pcb], superseded={}, turn=1, summary='Fixture PCB', question=None)
    service.gateway.settings = replace(service.gateway.settings, images={**service.gateway.settings.images, 'pcb': 'sha256:'+'c'*64})
    service.gateway.docker = PreviewDocker()
    await service.gateway.invoke(scope, pcb, 'patch', {'actionId': 'patch', 'path': 'board.kicad_pcb',
        'content': BOARD.decode(), 'expectedSha256': None})
    await service.gateway.invoke(scope, pcb, 'build', {'actionId': 'build'})
    result = await service.gateway.invoke(scope, pcb, 'finish', {'actionId': 'finish'})
    await service.repository.publish(scope, TaskResult(task_id=pcb.id, status='succeeded', summary='Fixture', artifacts=result['artifacts']))
    authored = {**authored, 'pcb': result['artifacts']}
    grant = service.freeze(scope)
    service.gateway.docker = PreviewDocker(grant)
    previews = EngineeringPreviews(store, service.artifacts)
    companion = CompanionPreviews(store, service.artifacts, enabled=True, secret='x'*40,
        api_origin='https://api.example.test', frontend_origin='https://workbench.example.test',
        preview_origin='https://preview.example.test')
    previews.companion = companion
    return service, scope, grant, authored, previews, companion


async def compile_preview(env, *, complete=True):
    service, scope, grant, *_ = env
    compiler, results = NativeComposition(service.gateway, service), {}
    for target in grant['targets'] if complete else grant['targets'][:1]:
        for action in target['actions']:
            result = await compiler.invoke(scope, grant['id'], target['taskId'], action)
        results[target['taskId']] = result
    if complete:
        CompositionResults(service).record(scope, grant['id'], results)
    return results


def view(env, actor=None):
    _, scope, _, _, previews, _ = env
    return previews.query(actor or scope.actor, 'engineering_preview', {'projectId': scope.project_id})


async def test_current_cad_pcb_and_companion_use_compiled_refs_preserving_authored_history(preview_composition):
    env = preview_composition
    service, scope, _, authored, _, companion = env
    with service.repository.store.transaction() as session:
        history = copy.deepcopy(session.get(Objective, scope.run_id).data['results'])
    results = await compile_preview(env)
    with service.repository.store.transaction() as session:
        session.get(Objective, scope.run_id).status = 'succeeded'
    result = view(env)
    ids = {ref['id'] for ref in result['files']}
    new_outputs = {ref['id'] for outcome in results.values() for ref in outcome['artifacts']}
    old_outputs = {ref['id'] for refs in authored.values() for ref in refs if ref['path'].startswith('outputs/')}
    assert new_outputs <= ids and not old_outputs & ids
    cad_ids = {ref['id'] for name in ['base', 'lid'] for ref in results[name]['artifacts'] if ref['path'].endswith('.stl')}
    assert {part['artifactId'] for part in result['mechanics']['assembly']['parts']} == cad_ids
    assert result['electronics']['artifactId'] == results['pcb']['artifacts'][0]['id']
    app = results['app']['artifacts'][0]['id']
    assert result['application']['previewArtifactId'] == app
    assert companion.issue(scope.actor, scope.project_id, {'artifactId': app})['artifactId'] == app
    old_app = next(ref['id'] for ref in authored['app'] if ref['path'].endswith('.zip'))
    with pytest.raises(DomainError, match='current published'):
        companion.issue(scope.actor, scope.project_id, {'artifactId': old_app})
    with service.repository.store.transaction() as session:
        assert session.get(Objective, scope.run_id).data['results'] == history


async def test_partial_compilation_does_not_substitute_any_preview(preview_composition):
    env = preview_composition
    results = await compile_preview(env, complete=False)
    ids = {ref['id'] for ref in view(env)['files']}
    assert not {ref['id'] for result in results.values() for ref in result['artifacts']} & ids
    authored = env[3]
    assert {ref['id'] for refs in authored.values() for ref in refs} == ids


@pytest.mark.parametrize('change', ['receipt', 'authority', 'publication', 'hash', 'project', 'finish'])
async def test_forged_or_stale_completion_never_substitutes(preview_composition, change):
    from sqlalchemy import select

    from kleos.execution.gateway import NativeAction
    env = preview_composition
    service, scope, grant, *_ = env
    results = await compile_preview(env)
    ref = results['base']['artifacts'][0]
    with service.repository.store.transaction() as session:
        run = session.get(Objective, scope.run_id)
        row = session.get(Artifact, ref['id'])
        if change == 'receipt':
            altered = copy.deepcopy(run.data['compositionResults'])
            altered[grant['id']]['targets'] = {}
            run.data = {**run.data, 'compositionResults': altered}
        elif change == 'authority':
            project = session.get(Project, scope.project_id)
            project.data = {**project.data, 'requirements': {'changed': True}}
        elif change == 'publication':
            session.delete(session.scalar(select(NativePublication).where(NativePublication.job_id == row.details['nativeJobId'])))
        elif change == 'hash':
            row.sha256 = 'f'*64
        elif change == 'project':
            row.project_id = 'foreign'
        else:
            session.get(NativeAction, (row.details['nativeJobId'], 'composition:finish')).status = 'pending'
    with pytest.raises(DomainError):
        view(env)


async def test_validation_carryforward_cannot_restore_old_outputs(preview_composition):
    env = preview_composition
    service, scope, _, authored, *_ = env
    results = await compile_preview(env)
    carried = [ref for values in authored.values() for ref in values if ref['path'].startswith('outputs/')]
    carried += [ref for result in results.values() for ref in result['artifacts']]
    await service.repository.publish(scope, TaskResult(task_id='check', status='succeeded',
        summary='Echo immutable producer references', artifacts=carried))
    ids = {ref['id'] for ref in view(env)['files']}
    assert not {ref['id'] for refs in authored.values() for ref in refs if ref['path'].startswith('outputs/')} & ids
    assert {ref['id'] for result in results.values() for ref in result['artifacts']} <= ids


async def test_accepted_release_selection_ignores_later_broken_candidate_projection(preview_composition):
    env = preview_composition
    service, scope, grant, *_ = env
    results = await compile_preview(env)
    outputs = [ref for result in results.values() for ref in result['artifacts']]
    with service.repository.store.transaction() as session:
        project = session.get(Project, scope.project_id)
        project.advance_revision()
        release = {'id': 'release-fixture', 'projectId': project.id, 'acceptedRevision': project.context_revision,
            'sources': grant['sources'], 'outputs': outputs}
        release['manifestHash'] = fingerprint(release)
        session.add(Release(id='release-fixture', project_id=project.id, revision=project.context_revision,
            candidate_id='fixture-only', data=release))
        project.data = {**project.data, 'acceptedReleaseId': 'release-fixture'}
        run = session.get(Objective, scope.run_id)
        run.data = {**run.data, 'compositionResults': {'broken': True}}
    result = view(env)
    assert result['mechanics']['status'] == result['electronics']['status'] == 'accepted'
    assert {ref['id'] for ref in outputs} <= {ref['id'] for ref in result['files']}


async def test_cross_tenant_query_cannot_read_completed_projection(preview_composition):
    env = preview_composition
    await compile_preview(env)
    env[0].artifacts.blobs.get = lambda *_: pytest.fail('Unauthorized blob read')
    with pytest.raises(DomainError):
        view(env, Actor(tenant_id='foreign', user_id=env[1].actor.user_id))


@pytest.mark.parametrize('change', ['revision', 'cancelled'])
async def test_old_completed_run_is_no_longer_a_current_preview(preview_composition, change):
    env = preview_composition
    await compile_preview(env)
    service, scope, *_ = env
    with service.repository.store.transaction() as session:
        if change == 'revision':
            session.get(Project, scope.project_id).advance_revision()
        else:
            session.get(Objective, scope.run_id).status = 'cancelled'
    result = view(env)
    assert result['files'] == []
    assert result['mechanics']['source'] == result['electronics']['source'] == 'unavailable'
    assert not result['application']['previewAvailable']
