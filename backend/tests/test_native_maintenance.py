import asyncio
from contextlib import asynccontextmanager

import pytest

from kleos.cli import work
from kleos.contracts.models import DomainError
from kleos.infrastructure.native_maintenance import maintain_native


async def test_native_maintenance_retries_uncertain_cleanup_without_model_work(caplog):
    stop = asyncio.Event()
    class Gateway:
        calls = 0
        async def reconcile(self):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError('PRIVATE_TRANSPORT_DETAILS')
            stop.set()
            return {'reconciled': 1, 'pending': []}
    gateway = Gateway()
    await maintain_native(gateway, stop, poll_seconds=.1)
    assert gateway.calls == 2
    assert 'durable records retained' in caplog.text
    assert 'PRIVATE_TRANSPORT_DETAILS' not in caplog.text


async def test_native_maintenance_can_stop_while_objective_work_is_waiting():
    stop, observed = asyncio.Event(), asyncio.Event()
    class Gateway:
        async def reconcile(self):
            observed.set()
            return {'reconciled': 1, 'pending': []}
    task = asyncio.create_task(maintain_native(Gateway(), stop))
    await asyncio.wait_for(observed.wait(), timeout=1)
    stop.set()
    await asyncio.wait_for(task, timeout=1)
    with pytest.raises(DomainError, match='configured control gateway'):
        await maintain_native(None, stop)


async def test_objective_cli_runs_native_reconciliation_alongside_waiting_worker():
    observed = asyncio.Event()
    class Gateway:
        async def reconcile(self):
            observed.set()
    class Worker:
        async def serve(self, stop):
            await asyncio.wait_for(observed.wait(), timeout=1)
            stop.set()
    class Runtime:
        native_gateway, worker, closed = Gateway(), Worker(), False
        @asynccontextmanager
        async def start(self):
            try:
                yield self
            finally:
                self.closed = True
    runtime = Runtime()
    await work(runtime)
    assert observed.is_set() and runtime.closed
