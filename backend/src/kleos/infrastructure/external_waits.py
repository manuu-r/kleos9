"""Bounded source polling; all workflow resumption remains in LangGraph."""
import asyncio
import logging

logger = logging.getLogger(__name__)


async def serve_external_waits(bridge, stop, *, poll_seconds=5):
    if not .1 <= poll_seconds <= 60:
        raise ValueError('External observation polling is outside its bounds')
    while not stop.is_set():
        operation = asyncio.create_task(bridge.poll_due())
        stopping = asyncio.create_task(stop.wait())
        try:
            done, _ = await asyncio.wait({operation, stopping}, return_when=asyncio.FIRST_COMPLETED)
            if operation in done:
                operation.result()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning('External observation polling unavailable; durable waits retained')
        finally:
            for task in (operation, stopping):
                if not task.done():
                    task.cancel()
            await asyncio.gather(operation, stopping, return_exceptions=True)
        if not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=poll_seconds)
            except TimeoutError:
                pass
