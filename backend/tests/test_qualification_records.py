"""The committed strict-Linux qualification records must be accepted by the runtime's own evidence gate."""
import json

import pytest

from kleos.infrastructure.runtime import NativeToolConfiguration, native_qualification

RECORDS = {'cad': 'cad', 'pcb': 'pcb', 'firmware': 'firmware', 'application': 'web'}


@pytest.mark.parametrize('capability,name', sorted(RECORDS.items()))
def test_runtime_accepts_each_committed_linux_record_for_its_exact_image(capability, name):
    from journey_support import QUALIFICATION
    path = QUALIFICATION / f'{name}-linux-strict-2026-11-03.json'
    report = json.loads(path.read_text())
    config = NativeToolConfiguration(capability=capability, image_digest=report['imageId'],
                                     provider_version='local-qualified', qualified=True, qualification_reference=path)
    pin = native_qualification(config, desktop=False)
    assert pin['imageDigest'] == report['imageId'] and pin['networkPolicy'] == 'in-process-seccomp'
    assert report['parentImageId'].startswith('sha256:') and report['qualified'] is True


def test_a_record_for_another_image_or_policy_is_rejected(tmp_path):
    from journey_support import QUALIFICATION

    from kleos.contracts.models import DomainError
    path = QUALIFICATION / 'cad-linux-strict-2026-11-03.json'
    report = json.loads(path.read_text())
    wrong_image = NativeToolConfiguration(capability='cad', image_digest='sha256:' + '0' * 64,
        provider_version='x', qualified=True, qualification_reference=path)
    with pytest.raises(DomainError):
        native_qualification(wrong_image, desktop=False)
    same = NativeToolConfiguration(capability='cad', image_digest=report['imageId'], provider_version='x',
        qualified=True, qualification_reference=path)
    with pytest.raises(DomainError):
        native_qualification(same, desktop=True)  # a Linux-engine record never qualifies the Docker Desktop policy
