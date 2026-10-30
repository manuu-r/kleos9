import json
from pathlib import Path

import pytest

from kleos.native_runner import validate_interfaces

CASES = json.loads((Path(__file__).parents[1] / "fixtures/golden_briefs.json").read_text())


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_golden_interface_brief(case):
    result = validate_interfaces(case["interfaces"])
    assert result.status == case["expectedInterfaceStatus"]
    assert result.evidence_kind == "digital"
    assert result.details
