import json

import pytest
from pydantic import ValidationError

from kleos.anatomy_skill import runtime_guidance, scene_issues, seed_concept, skill_metadata
from kleos.concept_models import PlanningOutput
from kleos.contracts import ConceptScene


def test_pinned_anatomy_and_bounded_runtime_card():
    metadata = skill_metadata()
    assert metadata['commit'] == '1482e8469b0a9e0d8cc18ea2740a01423d29ae80'
    assert len(metadata['skillSha256']) == 64 and metadata['license'] == 'MIT'
    assert 'relative' in runtime_guidance() and len(runtime_guidance()) < 3200
    metadata['commit'] = 'changed'
    assert skill_metadata()['commit'] != 'changed'


@pytest.mark.parametrize('brief,kind', [('A smart watch','wearable'),
    ('A handheld air quality analyser','handheld'),('Desk air quality monitor','desktop'),
    ('An adjustable lamp','lamp'),('A little robot','robot')])
def test_seeded_scene_is_typed_recognizable_and_has_exploded_geometry(brief, kind):
    scene = ConceptScene.model_validate(seed_concept(brief))
    assert scene.device_kind == kind and scene.units == 'relative'
    assert 4 <= len(scene.parts) <= 12
    assert any(p.explode.x or p.explode.y or p.explode.z for p in scene.parts)
    assert all(p.size.x > 0 and p.size.y > 0 and p.size.z > 0 for p in scene.parts)


def test_planning_contract_cannot_return_generated_scripts_or_cad():
    scene = seed_concept('A watch')
    with pytest.raises(ValidationError):
        PlanningOutput(summary='Concept', conceptScene=scene,
                       files=[{'path':'danger.js','content':'anything'}])
    valid = PlanningOutput(summary='Concept', conceptScene=scene)
    assert len(json.dumps(valid.model_dump())) < 16000


@pytest.mark.parametrize('brief', ['A smart watch', 'A handheld analyser', 'Desk air quality monitor', 'An adjustable lamp', 'A little robot'])
def test_reference_scenes_satisfy_their_own_composition_rules(brief):
    assert scene_issues(seed_concept(brief)) == []


def test_scene_issues_report_only_unmistakable_layout_faults():
    scene = seed_concept('A smart watch')
    floating = {**scene, 'parts': [*scene['parts'], {**scene['parts'][0], 'id': 'stray', 'position': {'x': 30, 'y': 30, 'z': 30}}]}
    assert 'stray' in ' '.join(scene_issues(floating))
    flat = {**scene, 'parts': [{**p, 'explode': {'x': 0, 'y': 0, 'z': 0}} for p in scene['parts']]}
    assert any('exploded' in issue for issue in scene_issues(flat))


def test_wholesale_part_id_churn_is_reported_but_small_edits_are_not():
    before = seed_concept('A smart watch')
    renamed = {**before, 'parts': [{**p, 'id': p['id'] + '-v2'} for p in before['parts']]}
    assert any('IDs' in issue for issue in scene_issues(renamed, before))
    edited = {**before, 'parts': [{**p, 'id': 'screen2' if p['id'] == 'face' else p['id']} for p in before['parts']]}
    assert scene_issues(edited, before) == []
