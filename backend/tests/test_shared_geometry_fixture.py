from pathlib import Path

import pytest
from pydantic import ValidationError

from kleos.capabilities.design.models import ConceptScene


def test_existing_flutter_geometry_fixture_matches_capture_contract():
    fixture = Path(__file__).parent / 'fixtures/concept-geometry-v2.json'
    scene = ConceptScene.model_validate_json(fixture.read_text())
    assert scene.units == 'relative'
    assert len(scene.parts) > 1
    encoded = scene.model_dump(mode='json', by_alias=True)
    assert ConceptScene.model_validate(encoded) == scene
    with pytest.raises(ValidationError):
        ConceptScene.model_validate({**encoded, 'units': 'mm'})
