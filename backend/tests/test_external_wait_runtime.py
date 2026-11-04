"""Runtime account filtering and bounded observation worker lifecycle."""
import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from test_runtime import migrate, model, runtime_actor, settings, test_persistence

from kleos.infrastructure.external_waits import serve_external_waits
from kleos.infrastructure.runtime import Runtime
from kleos.orchestration.engine import RunScope
from kleos.projects.store import Objective


async def test_runtime_wait_tools_are_filtered_to_exact_actor_and_pinned(tmp_path):
    report = Path(__file__).parents[1] / 'src/kleos/capabilities/qualification/rentahuman-readonly.json'
    configured = settings(tmp_path, model_catalog=[model('text')], text_model_key='text',
        external_installations=[{'provider': 'rentahuman', 'version': 'rest-read-2026-11-02',
            'tenantId': 'tenant', 'userId': 'owner', 'qualificationReference': str(report)}])
    runtime = Runtime(configured, persistence_factory=test_persistence, worker_locks=object())
    migrate(runtime)
    try:
        for user in ('owner', 'other'):
            actor = runtime_actor(runtime, user=user)
            project = runtime.projects.create(actor, 'Review an existing part', user)['projectId']
            run_id = runtime.projects.submit(actor, project, 'Wait for an existing review', 1, user+'-run')['runId']
            scope = RunScope(actor=actor, project_id=project, run_id=run_id,
                revision=runtime.projects.context(actor, project).revision,
                deadline_at=datetime.now(UTC)+timedelta(days=1))
            pins = runtime._run_pins(scope)
            keys = {item['key'] for item in pins['tools']}
            assert ('human_work.wait.rentahuman@1.0.0' in keys) == (user == 'owner')
            assert bool(pins['external']) == (user == 'owner')
            with runtime.projects.transaction() as session:
                assert session.get(Objective, run_id).data['runtimePins'] == pins
    finally:
        await runtime.external_runtime.close()
        runtime.projects.engine.dispose()


async def test_external_observer_stops_inflight_and_redacts_errors(caplog):
    stop = asyncio.Event()
    class Bridge:
        calls = 0
        cancelled = False
        async def poll_due(self):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError('PRIVATE_PROVIDER_DETAILS')
            stop.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled = True
                raise
    bridge = Bridge()
    await asyncio.wait_for(serve_external_waits(bridge, stop, poll_seconds=.1), timeout=2)
    assert bridge.calls == 2 and bridge.cancelled
    assert 'durable waits retained' in caplog.text and 'PRIVATE_' not in caplog.text


@pytest.mark.parametrize('period', [0, 61])
async def test_external_observer_rejects_unbounded_cadence(period):
    with pytest.raises(ValueError):
        await serve_external_waits(None, asyncio.Event(), poll_seconds=period)
