"""Bounded native compute cleanup, independent of model and graph execution."""
import asyncio
import logging

from kleos.contracts.models import DomainError

logger = logging.getLogger(__name__)


async def maintain_native(gateway, stop, *, poll_seconds=2):
    if gateway is None:
        raise DomainError('Native maintenance requires a configured control gateway', 'tool_unavailable', 503)
    if not .1 <= poll_seconds <= 30:
        raise ValueError('Native maintenance polling is outside its bounds')
    while not stop.is_set():
        try:
            async with asyncio.timeout(30):
                await gateway.reconcile()
        except asyncio.CancelledError:
            raise
        except Exception:
            # An unconfirmed termination remains durable work; do not leak transport diagnostics.
            logger.warning('Native reconciliation incomplete; durable records retained for retry')
        try:
            await asyncio.wait_for(stop.wait(), timeout=poll_seconds)
        except TimeoutError:
            pass
