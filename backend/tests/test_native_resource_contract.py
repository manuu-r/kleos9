from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from test_execution import execution as execution

from kleos.config import Settings
from kleos.db import NativeJob
from kleos.native_resources import NativeResources, default_profiles


def test_shared_resource_profiles_are_valid_and_independently_loaded():
    for profile in default_profiles().values():
        NativeResources.model_validate(profile)
    first, second = default_profiles(), default_profiles()
    first['mechanics']['memoryMiB'] = 999
    assert second['mechanics']['memoryMiB'] == 2048
    assert Settings(_env_file=None).native_job_reservation_usd == .1


@pytest.mark.parametrize('change', [{'cpuUnits': 777}, {'cpuUnits': 256, 'memoryMiB': 1536, 'tmpMiB': 1024},
    {'memoryMiB': 1000}, {'tmpMiB': 99999}, {'pidsLimit': 0}, {'ephemeralStorageGiB': 20}, {'cpuUnits': True}])
def test_unqualified_native_resources_fail_before_dispatch(change):
    with pytest.raises(ValidationError):
        NativeResources.model_validate({**default_profiles()['mechanics'], **change})


def test_missing_pull_times_stay_unknown_and_real_observations_preserve_existing_results(execution):
    job = execution.submit('attempt', 'build')
    execution.record_task_timing(job, {})
    with execution.db.transaction() as session:
        assert 'infrastructureUsage' not in session.get(NativeJob, job).result
        session.get(NativeJob, job).result = {'logKey': 'existing-log', 'sourceArtifacts': []}
    created = datetime(2026, 1, 1, tzinfo=UTC)
    execution.record_task_timing(job, {'createdAt': created, 'startedAt': created+timedelta(seconds=15),
        'pullStartedAt': created+timedelta(seconds=2), 'pullStoppedAt': created+timedelta(seconds=12)})
    with execution.db.transaction() as session:
        result = session.get(NativeJob, job).result
        assert result['logKey'] == 'existing-log'
        assert result['infrastructureUsage'] == {'imagePullSeconds': 10, 'launchToStartSeconds': 15}
    execution.record_task_timing(job, {'pullStartedAt': 'invalid', 'pullStoppedAt': '9999999',
                                      'createdAt': created, 'startedAt': created-timedelta(seconds=1)})
    with execution.db.transaction() as session:
        assert session.get(NativeJob, job).result['infrastructureUsage']['imagePullSeconds'] == 10
