import asyncio

from kleos.infrastructure.verification import serve_verification


async def test_verification_worker_stops_inflight_and_preserves_private_errors(caplog):
    stop = asyncio.Event()
    class Service:
        calls = 0
        cancelled = False
        async def process_next(self):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError('PRIVATE_CHECKER_DETAILS')
            stop.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled = True
                raise
    service = Service()
    await asyncio.wait_for(serve_verification(service, stop, poll_seconds=.1), timeout=2)
    assert service.calls == 2 and service.cancelled
    assert 'durable jobs retained' in caplog.text
    assert 'PRIVATE_' not in caplog.text


async def test_verification_worker_drains_ready_jobs_before_polling():
    stop = asyncio.Event()
    class Service:
        calls = 0
        async def process_next(self):
            self.calls += 1
            if self.calls == 3:
                stop.set()
            return True
    service = Service()
    await asyncio.wait_for(serve_verification(service, stop, poll_seconds=30), timeout=1)
    assert service.calls == 3
