"""Thin dispatch for durable independent checks; no planning or model calls."""
import asyncio
import logging

logger = logging.getLogger(__name__)


async def serve_verification(service, stop, *, poll_seconds=1):
    if not .1 <= poll_seconds <= 30:
        raise ValueError('Verification polling is outside its bounds')
    while not stop.is_set():
        operation = asyncio.create_task(service.process_next())
        stopping = asyncio.create_task(stop.wait())
        worked = False
        try:
            done, _ = await asyncio.wait({operation, stopping}, return_when=asyncio.FIRST_COMPLETED)
            if operation in done:
                worked = operation.result()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning('Independent verification dispatch unavailable; durable jobs retained')
        finally:
            for task in (operation, stopping):
                if not task.done():
                    task.cancel()
            await asyncio.gather(operation, stopping, return_exceptions=True)
        if not worked and not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=poll_seconds)
            except TimeoutError:
                pass
