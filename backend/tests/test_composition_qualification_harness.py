"""Harness controls only; no native qualification occurs in these tests."""
import pytest
from qualify_composition import IMAGES, REPORTS, TrustedPlanner, TrustedSpecialist, fixtures

from kleos.contracts.models import DomainError
from kleos.infrastructure.runtime import NativeToolConfiguration, native_qualification


async def test_trusted_plan_is_exact_two_lane_fixture_without_provider_selection():
    sources = fixtures()
    planner = TrustedPlanner(sources)
    decision = await planner.plan(None, '', None, [], [], None)
    assert [task.id for task in decision.tasks] == ['cad', 'application']
    assert {path for task in decision.tasks for path in task.allowed_paths} == {
        'model.py', 'pubspec.yaml', 'pubspec.lock', 'lib/main.dart', 'web/index.html'}
    assert (await planner.plan(None, '', None, [], [object()], None)).done
    for lane in sources:
        config = NativeToolConfiguration(capability=lane, image_digest=IMAGES[lane], provider_version='fixture',
            qualified=True, qualification_reference=REPORTS[lane])
        assert native_qualification(config, desktop=True)['imageDigest'] == IMAGES[lane]


@pytest.mark.parametrize('response', [{}, {'blockers': ['failed']}, {'observations': [
    {'status': 'PASS', 'independent': True, 'details': {'executionSucceeded': True}}]},
    {'observations': [{'status': 'ERROR', 'independent': False, 'details': {'executionSucceeded': True}}]},
    {'observations': [{'status': 'UNKNOWN', 'independent': False, 'details': {}}]}])
def test_fixed_fixture_never_treats_failed_or_independent_claim_as_generation(response):
    with pytest.raises(DomainError):
        TrustedSpecialist.check(response)
