from copy import deepcopy

import pytest
from test_control_plane import command, create, engine

from kleos.contracts import DomainError
from kleos.db import Attempt, Project, Task
from kleos.design_intent import approved_context, bind_interfaces, derive_intent, scene_hash
from kleos.domain import contract_hash
from kleos.planning import update_concept


def scene(shape='cylinder', **body_changes):
    def part(ident, role, shape, size, position):
        return {'id': ident, 'label': ident, 'role': role, 'shape': shape, 'size': dict(zip('xyz', size)),
                'position': dict(zip('xyz', position)), 'rotation': dict.fromkeys('xyz', 0)}
    body = part('housing', 'shell', shape, (4, 4, 1.2), (0, 0, 0)) | body_changes
    return {'schemaVersion': 'concept-scene-v1', 'title': 'User concept', 'deviceKind': 'wearable', 'units': 'relative',
            'parts': [body, part('processor', 'board', 'box', (2.2, 2.2, .1), (0, 0, .1)),
                      part('power-cell', 'battery', 'box', (2, 1.5, .3), (0, 0, -.25)),
                      part('readout', 'display', 'rounded_box', (2.8, .1, 1.4), (0, 2, .1))]}


def interfaces():
    return {'mechanical': {'externalMm': {'x': 40, 'y': 40, 'z': 12}, 'internalMm': {'x': 36, 'y': 36, 'z': 8},
        'minimumWallMm': 2, 'components': [
            {'id': 'pcb', 'conceptPartId': 'processor', 'sizeMm': {'x': 22, 'y': 22, 'z': 1}, 'positionMm': {'x': 7, 'y': 7, 'z': 4}},
            {'id': 'battery', 'conceptPartId': 'power-cell', 'sizeMm': {'x': 18, 'y': 14, 'z': 3}, 'positionMm': {'x': 9, 'y': 10, 'z': 1}}]},
        'power': {}, 'firmware': {'board': 'nrf52840dk/nrf52840'}}


def prepared(platform, raw_scene=None):
    project = create(platform, 'A round wearable with a rectangular internal board')['projectId']
    with platform.db.transaction() as session:
        p = session.get(Project, project)
        p.state = {**p.state, 'proposal': {'summary': 'A reviewed concept', 'questions': [],
                   'requirements': {'diameterMm': 40, 'designIntent': {'enclosure': {'profile': 'rectangle'}}}}}
        update_concept(p, {'conceptScene': raw_scene or scene()}, 'feasibility', 'model-run', 'model-attempt', 0)
    return project


def approved(platform, raw_scene=None):
    project = prepared(platform, raw_scene)
    command(platform, project, 'project.confirm')
    return project


def test_only_explicit_confirmation_freezes_real_saved_scene_and_ignores_forged_model_constraints(platform):
    ident = prepared(platform)
    before = platform.control.workspace(platform.principal, ident)['project']
    assert 'approvedDesign' not in before['state']
    command(platform, ident, 'project.confirm')
    project = platform.control.workspace(platform.principal, ident)['project']
    approval, intent = project['state']['approvedDesign'], project['requirements']['designIntent']
    assert approval['source']['kind'] == 'explicit_confirmation'
    assert approval['source']['attemptId'] == 'model-attempt'
    assert approval['sceneHash'] == scene_hash(approval['scene']) == intent['sceneHash']
    assert approval['approvedBy'] == platform.principal.subject
    assert intent['enclosure']['profile'] == 'circle'
    assert intent['components'][0]['shape'] == 'box'  # no demand for a round PCB
    assert project['requirements']['diameterMm'] == 40  # no inferred relative-to-mm scale
    assert project['state']['approvedDesignStatus'] == 'approved'
    assert project['state']['designIntentCheck']['status'] == 'UNKNOWN'
    assert project['state']['designIntentCheck']['details']['reasonCode'] == 'awaiting_engineering_checks'
    revision, version, requirements = project['engineeringRevision'], approval['version'], deepcopy(project['requirements'])
    command(platform, ident, 'project.confirm', key='repeat-identical-approval')
    same = platform.control.workspace(platform.principal, ident)['project']
    assert same['engineeringRevision'] == revision and same['state']['approvedDesign']['version'] == version
    assert same['requirements'] == requirements  # time/actor metadata cannot cause revision loops


def test_provisional_seed_and_legacy_projects_do_not_gain_invented_approval(platform):
    ident = create(platform)['projectId']
    with platform.db.transaction() as session:
        p = session.get(Project, ident)
        p.state = {**p.state, 'proposal': {'summary': 'Legacy', 'questions': [], 'requirements': {'designIntent': {'fake': True}}}}
    command(platform, ident, 'project.confirm')
    result = platform.control.workspace(platform.principal, ident)['project']
    assert 'approvedDesign' not in result['state'] and 'designIntent' not in result['requirements']


def test_stale_generated_scene_cannot_be_freshly_approved(platform):
    ident = prepared(platform)
    with platform.db.transaction() as session:
        p = session.get(Project, ident)
        p.state = {**p.state, 'concept': {**p.state['concept'], 'stale': True}}
    with pytest.raises(DomainError) as error:
        command(platform, ident, 'project.confirm')
    assert error.value.code == 'concept_review_required'


def test_every_engineering_provider_attempt_uses_frozen_scene_once_not_later_draft(platform):
    ident = approved(platform)
    with platform.db.transaction() as session:
        p = session.get(Project, ident)
        bound, _ = bind_interfaces(p, interfaces())
        p.interfaces = bound
        frozen = deepcopy(p.state['approvedDesign'])
        # Even a changed draft must not substitute for the explicitly approved scene.
        update_concept(p, {'conceptScene': scene('box')}, 'refining', 'later', 'unapproved', 0)
    policy = platform.control.workspace(platform.principal, ident)['project']['modelPolicy']
    policy['mechanics'] = {'provider': 'meta', 'model': platform.control.registry.models['meta'][1]}
    command(platform, ident, 'project.setModelPolicy', {'modelPolicy': policy})
    receipt = command(platform, ident, 'project.generate', {'lane': 'mechanics'})
    worker = engine(platform)
    task = worker.admit_run(receipt['runId'])[0]
    attempt = worker.begin_attempt(task)
    context = worker.attempt_input(attempt)[0]
    assert context['concept']['scene'] == frozen['scene']
    assert context['approvedDesign']['sceneHash'] == frozen['sceneHash']
    assert 'scene' not in context['approvedDesign']
    assert 'conceptScene' not in context['proposal']
    assert context['requirements']['designIntent']['enclosure']['profile'] == 'circle'
    assert context['manifest']['approvedDesign']['sceneHash'] == frozen['sceneHash']
    with platform.db.transaction() as session:
        assert session.get(Attempt, attempt).model == platform.control.registry.models['meta'][1]
        assert session.get(Project, ident).state['approvedDesign'] == frozen


def test_interface_binding_derives_immutable_profile_and_links_internal_components_only(platform):
    ident = approved(platform)
    with platform.db.transaction() as session:
        project = session.get(Project, ident)
        proposed = interfaces()
        proposed['mechanical']['designIntent'] = {'enclosure': {'profile': 'square'}, 'sceneHash': 'fake'}
        bound, check = bind_interfaces(project, proposed)
    layout = bound['mechanical']['designIntent']
    assert check['status'] == 'PASS' and 'independent' in check['details']['scope']
    assert layout['enclosure']['profile'] == 'circle'
    assert [p['conceptPartId'] for p in layout['components']] == ['processor', 'power-cell']
    assert layout['components'][0]['shape'] == 'box'
    assert layout['components'][0]['sizeMm']['x'] == 22
    assert layout['features'][0]['conceptPartId'] == 'readout'
    assert layout['components'][0]['region']['center']['z'] == pytest.approx(.1 / 1.2)
    assert proposed['mechanical']['designIntent']['sceneHash'] == 'fake'  # no input mutation


def test_shape_drift_or_missing_mapping_blocks_interfaces_without_accepting_candidate(platform):
    ident = approved(platform)
    worker = engine(platform)
    receipt = command(platform, ident, 'project.solveInterfaces')
    task = worker.admit_run(receipt['runId'])[0]
    attempt = worker.begin_attempt(task)
    wrong = interfaces()
    wrong['mechanical']['externalMm']['x'] = 48
    worker.finish_attempt(attempt, {'summary': 'Wrong square-ish enclosure', 'interfaces': wrong})
    with platform.db.transaction() as session:
        p = session.get(Project, ident)
        assert p.interfaces == {}
        assert p.state['designIntentCheck']['status'] == 'FAIL'
        assert p.requirements['designIntent']['enclosure']['profile'] == 'circle'
        assert session.get(Task, task).status == 'blocked'
        missing = interfaces()
        missing['mechanical']['components'][0]['conceptPartId'] = 'invented-board'
        with pytest.raises(DomainError):
            bind_interfaces(p, missing)


def test_research_scene_changes_require_another_explicit_approval_and_revision(platform):
    ident = approved(platform)
    with platform.db.transaction() as session:
        p = session.get(Project, ident)
        original = deepcopy(p.state['approvedDesign'])
        old_contract = contract_hash(p.requirements, p.interfaces)
        p.interfaces, _ = bind_interfaces(p, interfaces())
    receipt = command(platform, ident, 'project.requestReview', {'autoResume': True})
    worker = engine(platform)
    task = worker.admit_run(receipt['runId'])[0]
    attempt = worker.begin_attempt(task)
    worker.finish_attempt(attempt, {'summary': 'Proposed rectangular variation', 'interfaces': {}, 'requirements': {},
                                    'conceptScene': scene('box'), 'questions': []})
    worker.finish_run(receipt['runId'])
    draft = platform.control.workspace(platform.principal, ident)['project']
    assert draft['state']['approvedDesign'] == original and not draft['state']['confirmed']
    command(platform, ident, 'project.confirm')
    updated = platform.control.workspace(platform.principal, ident)['project']
    assert updated['state']['approvedDesign']['version'] == 2
    assert updated['requirements']['designIntent']['enclosure']['profile'] == 'rectangle'
    assert updated['engineeringRevision'] > original['engineeringRevision']
    assert updated['interfaces'] == {}
    assert contract_hash(updated['requirements'], updated['interfaces']) != old_contract


def test_rounded_radius_scales_approved_proportion_and_rotated_or_oval_forms_remain_unknown(platform):
    ident = approved(platform, scene('rounded_box'))
    with platform.db.transaction() as session:
        p = session.get(Project, ident)
        bound, check = bind_interfaces(p, interfaces())
        enclosure = bound['mechanical']['designIntent']['enclosure']
        assert check['status'] == 'PASS'
        assert enclosure['cornerRadiusMm'] == pytest.approx(.13 * 40)
        assert enclosure['internalCornerRadiusMm'] == pytest.approx(.13 * 40 - 2)
        approval = deepcopy(p.state['approvedDesign'])
        approval['scene']['parts'][0]['rotation']['z'] = 90
        approval['scene']['parts'][1]['position'] = {'x': 0, 'y': 1, 'z': .1}
        approval['sceneHash'] = scene_hash(approval['scene'])
        intent = derive_intent(approval)
        assert intent['components'][0]['region']['center']['x'] == pytest.approx(.25)
        assert intent['enclosure']['unsupportedReasons']
        p.state = {**p.state, 'approvedDesign': approval}
        p.requirements = {**p.requirements, 'designIntent': intent}
        assert bind_interfaces(p, interfaces())[1]['status'] == 'UNKNOWN'


def test_approved_peripheral_reference_positions_are_server_derived_and_unmapped_are_unknown(platform):
    proposed_scene = scene()
    proposed_scene['parts'].append({'id': 'air-sensor', 'label': 'Air sensor', 'role': 'sensor', 'shape': 'box',
        'size': {'x': .3, 'y': .3, 'z': .1}, 'position': {'x': .8, 'y': .6, 'z': 0}})
    ident = approved(platform, proposed_scene)
    with platform.db.transaction() as session:
        p = session.get(Project, ident)
        proposed = interfaces()
        proposed['mechanical']['components'].append({'id': 'sensor-envelope', 'conceptPartId': 'air-sensor',
            'sizeMm': {'x': 3, 'y': 3, 'z': 1}, 'positionMm': {'x': 24, 'y': 22, 'z': 4}})
        bound, check = bind_interfaces(p, proposed)
        assert check['status'] == 'UNKNOWN'
        assert bound['electronics']['designIntent']['unmappedConceptPartIds'] == ['air-sensor']
        proposed['circuit'] = {'components': [{'reference': 'U3'}]}
        proposed['electronics'] = {'componentReferences': [{'conceptPartId': 'air-sensor', 'reference': 'U3',
                                                           'expectedPositionMm': {'x': 1000, 'y': 1000}}]}
        bound, check = bind_interfaces(p, proposed)
        mapping = bound['electronics']['designIntent']['componentReferences'][0]
        assert mapping['region']['center']['x'] == pytest.approx(.2)
        assert 'expectedPositionMm' not in mapping
        assert mapping['reference'] == 'U3' and check['status'] == 'PASS'


@pytest.mark.parametrize('mutation', ['missing_intent', 'changed_intent', 'huge_dimension', 'invalid_mapping'])
def test_reference_tampering_and_malformed_nested_numbers_cannot_downgrade_or_crash(platform, mutation):
    ident = approved(platform)
    with platform.db.transaction() as session:
        p = session.get(Project, ident)
        candidate = interfaces()
        if mutation == 'missing_intent':
            p.requirements = {k: v for k, v in p.requirements.items() if k != 'designIntent'}
        elif mutation == 'changed_intent':
            value = deepcopy(p.requirements)
            value['designIntent']['enclosure']['profile'] = 'rectangle'
            p.requirements = value
        elif mutation == 'huge_dimension':
            candidate['mechanical']['externalMm']['x'] = 10 ** 1000
        else:
            candidate['mechanical']['components'][0]['conceptPartId'] = []
        with pytest.raises(DomainError):
            bind_interfaces(p, candidate)
        if mutation.startswith('missing') or mutation.startswith('changed'):
            with pytest.raises(DomainError):
                approved_context(p)


def test_oval_intent_is_not_silently_promoted_to_circle_and_internal_display_mapping_is_allowed(platform):
    oval = scene()
    oval['parts'][0]['size']['y'] = 3
    ident = approved(platform, oval)
    with platform.db.transaction() as session:
        p = session.get(Project, ident)
        proposed = interfaces()
        proposed['mechanical']['components'].append({'id': 'screen', 'conceptPartId': 'readout',
            'sizeMm': {'x': 20, 'y': 1, 'z': 10}, 'positionMm': {'x': 8, 'y': 34, 'z': 0}})
        result, check = bind_interfaces(p, proposed)
        assert p.requirements['designIntent']['enclosure']['profile'] == 'ellipse'
        assert check['status'] == 'UNKNOWN'
        assert result['mechanical']['designIntent']['components'][-1]['mount'] == 'internal'
        assert not result['mechanical']['designIntent']['features']


def test_release_requires_approved_design_checks_and_freezes_full_scene_for_future_baselines(platform):
    from kleos.db import Artifact, Release, Validation
    from kleos.storage import digest
    ident = approved(platform)
    with platform.db.transaction() as session:
        p = session.get(Project, ident)
        p.interfaces, _ = bind_interfaces(p, interfaces())
        snapshot = deepcopy(p.state['approvedDesign'])
        session.add(Artifact(id='accepted-fixture', tenant_id='org-a', project_id=ident, revision=p.engineering_revision,
            lane='mechanics', name='fixture.txt', key='fixture', sha256=digest(b'fixture'), size=7,
            status='candidate', provenance={}))
        expected = {'accepted-fixture': digest(b'fixture')}
        for name in ('interfaces', 'mechanics', 'electronics', 'firmware', 'application'):
            session.add(Validation(tenant_id='org-a', project_id=ident, task_id='independent-fixture-task',
                validator='independent-fixture', revision=p.engineering_revision,
                name=name, status='PASS', evidence_kind='digital', artifact_hashes=expected,
                details={'contractHash': contract_hash(p.requirements, p.interfaces)}))
    with pytest.raises(DomainError) as error:
        command(platform, ident, 'project.acceptRelease')
    assert error.value.code == 'gate_failed'
    with platform.db.transaction() as session:
        p = session.get(Project, ident)
        for name in ('approved_design_contract', 'approved_enclosure_form', 'approved_pcb_layout'):
            session.add(Validation(tenant_id='org-a', project_id=ident, task_id='independent-fixture-task',
                validator='independent-fixture', revision=p.engineering_revision,
                name=name, status='PASS', evidence_kind='digital', artifact_hashes=expected,
                details={'contractHash': contract_hash(p.requirements, p.interfaces)}))
    command(platform, ident, 'project.acceptRelease', key='accept-with-approved-design-checks')
    with platform.db.transaction() as session:
        p = session.get(Project, ident)
        release = session.get(Release, p.accepted_release_id)
        assert release.manifest['approvedDesign'] == snapshot
        release_id = release.id
        p.state = {**p.state, 'approvedDesign': {'changed': 'later draft'}, 'concept': {}}
    with platform.db.transaction() as session:
        assert session.get(Release, release_id).manifest['approvedDesign'] == snapshot


@pytest.mark.parametrize('dimensions', [{'x': 44, 'y': 44, 'z': 14}, {'x': 80, 'y': 80, 'z': 24}])
def test_outer_aspect_reference_allows_millimetre_scale_and_ordinary_fit_adjustments(platform, dimensions):
    ident = approved(platform)
    with platform.db.transaction() as session:
        p = session.get(Project, ident)
        candidate = interfaces()
        candidate['mechanical']['externalMm'] = dimensions
        bound, check = bind_interfaces(p, candidate)
        assert check['status'] == 'PASS'
        assert p.requirements['designIntent']['aspectToleranceFraction'] == .25
        assert bound['mechanical']['designIntent']['enclosure']['relativeSize'] == {'x': 4, 'y': 4, 'z': 1.2}


@pytest.mark.parametrize('dimensions', [{'x': 40, 'y': 40, 'z': 4}, {'x': 40, 'y': 40, 'z': 50}])
def test_flat_approved_watch_cannot_become_a_thinner_puck_or_tall_tube_without_new_approval(platform, dimensions):
    ident = approved(platform)
    with platform.db.transaction() as session:
        p = session.get(Project, ident)
        candidate = interfaces()
        candidate['mechanical']['externalMm'] = dimensions
        with pytest.raises(DomainError, match='approved design proportions'):
            bind_interfaces(p, candidate)


def test_elongated_rectangle_cannot_become_square_but_small_dimension_refinement_is_allowed(platform):
    approved_scene = scene('box', size={'x': 6, 'y': 3, 'z': 1.2})
    ident = approved(platform, approved_scene)
    with platform.db.transaction() as session:
        p = session.get(Project, ident)
        candidate = interfaces()
        candidate['mechanical']['externalMm'] = {'x': 58, 'y': 31, 'z': 11}
        assert bind_interfaces(p, candidate)[1]['status'] == 'PASS'
        candidate['mechanical']['externalMm'] = {'x': 40, 'y': 40, 'z': 8}
        with pytest.raises(DomainError, match='approved design proportions'):
            bind_interfaces(p, candidate)


def test_superseded_draft_blocks_current_engineering_and_release_but_retains_historical_download(platform):
    import io
    import json
    import zipfile

    from kleos.db import Artifact, Release, Run, Validation
    from kleos.packaging import ReleasePackaging
    from kleos.storage import digest

    ident = approved(platform)
    platform.store.put('historical-reference-fixture', b'fixture')
    with platform.db.transaction() as session:
        p = session.get(Project, ident)
        p.interfaces, _ = bind_interfaces(p, interfaces())
        frozen = deepcopy(p.state['approvedDesign'])
        session.add(Artifact(id='historical-fixture', tenant_id='org-a', project_id=ident,
            revision=p.engineering_revision, lane='mechanics', name='fixture.txt', key='historical-reference-fixture',
            sha256=digest(b'fixture'), size=7, status='candidate', provenance={}))
        for name in ('interfaces', 'mechanics', 'electronics', 'firmware', 'application',
                     'approved_design_contract', 'approved_enclosure_form', 'approved_pcb_layout'):
            session.add(Validation(tenant_id='org-a', project_id=ident, task_id='historical-fixture-validator',
                validator='independent-fixture', revision=p.engineering_revision, name=name, status='PASS',
                evidence_kind='digital', artifact_hashes={'historical-fixture': digest(b'fixture')},
                details={'contractHash': contract_hash(p.requirements, p.interfaces)}))
    command(platform, ident, 'project.acceptRelease', key='historical-initial-accept')
    with platform.db.transaction() as session:
        p = session.get(Project, ident)
        release_id, baseline_revision = p.accepted_release_id, p.engineering_revision
    worker = engine(platform)
    review = command(platform, ident, 'project.requestReview', {'autoResume': True})
    task = worker.admit_run(review['runId'])[0]
    attempt = worker.begin_attempt(task)
    worker.finish_attempt(attempt, {'summary': 'New rectangular draft', 'requirements': {}, 'questions': [],
                                    'conceptScene': scene('box')})
    worker.finish_run(review['runId'])
    draft = platform.control.workspace(platform.principal, ident)['project']
    assert not draft['state']['confirmed'] and draft['state']['approvedDesignStatus'] == 'superseded'
    assert draft['engineeringRevision'] == baseline_revision
    for kind in ('project.acceptRelease', 'project.verify', 'project.generate', 'project.solveInterfaces',
                 'project.prepareKit', 'project.requestQuotes'):
        with pytest.raises(DomainError) as error:
            command(platform, ident, kind, key='draft-blocks-' + kind)
        assert error.value.code == 'confirmation_required'
    with pytest.raises(DomainError) as error:
        command(platform, ident, 'project.retryNative', {'jobId': 'not-a-current-job'}, key='draft-native-retry')
    assert error.value.code == 'confirmation_required'
    # Trusted-worker admission also refuses an out-of-band engineering task;
    # changing an API guard alone must never permit paid work on a draft.
    with platform.db.transaction() as session:
        p = session.get(Project, ident)
        session.add(Run(id='draft-bypass-run', tenant_id='org-a', project_id=ident, command_id='fixture',
                        kind='project.generate', revision=p.engineering_revision, status='queued'))
        session.add(Task(id='draft-bypass-task', tenant_id='org-a', project_id=ident, run_id='draft-bypass-run',
                         lane='mechanics', kind='project.generate', revision=p.engineering_revision, status='queued'))
    with pytest.raises(DomainError) as error:
        worker.begin_attempt('draft-bypass-task')
    assert error.value.code == 'confirmation_required'
    with platform.db.transaction() as session:
        session.get(Run, 'draft-bypass-run').status = 'cancelled'
    package = ReleasePackaging(platform.db, platform.store).bundle(platform.principal, release_id)
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        assert json.loads(archive.read('approved-design-reference.json')) == frozen
    command(platform, ident, 'project.confirm', key='explicitly-confirm-new-draft')
    current = platform.control.workspace(platform.principal, ident)['project']
    assert current['engineeringRevision'] == baseline_revision + 1
    assert current['state']['approvedDesignStatus'] == 'approved' and current['state']['confirmed']
    with platform.db.transaction() as session:
        assert session.get(Release, release_id).manifest['approvedDesign'] == frozen
    with zipfile.ZipFile(io.BytesIO(ReleasePackaging(platform.db, platform.store).bundle(platform.principal, release_id))) as archive:
        assert json.loads(archive.read('approved-design-reference.json')) == frozen
