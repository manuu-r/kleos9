"""Composition and separate offline fabrication worker; printer dispatch only through the explicit print service."""
import asyncio
import logging
from dataclasses import dataclass

from kleos.api.bambu import BambuFacade
from kleos.api.fabrication import FabricationFacade
from kleos.capabilities.fabrication.printing import PrintDispatcher
from kleos.capabilities.fabrication.slicing import DockerSlicingDriver, PrintPreparations, qualify_slicing
from kleos.contracts.models import DomainError
from kleos.execution.docker import DockerClient

logger = logging.getLogger(__name__)


@dataclass
class FabricationRuntime:
    preparations: PrintPreparations
    facade: FabricationFacade
    bambu: BambuFacade
    docker: DockerClient | None = None
    _closed: bool = False

    @property
    def enabled(self):
        return self.preparations.settings is not None and self.preparations.driver is not None

    async def cycle(self):
        """Reconcile durable work before reserving one global isolated worker slot."""
        if self._closed:
            raise RuntimeError('Fabrication runtime is closed')
        if not self.enabled:
            raise DomainError('No qualified slicer is configured', 'capability_unavailable', 503)
        async def tick():
            completed = await self.preparations.reconcile()
            started = await self.preparations.process_next(max_running=1)
            return {'completed': completed, 'claimed': started}
        return await asyncio.wait_for(tick(), timeout=60)

    async def run(self, stop_event=None, *, poll_seconds=1):
        """Dedicated worker entry; stop leaves active isolated jobs available for recovery."""
        if not self.enabled:
            raise DomainError('No qualified slicer is configured', 'capability_unavailable', 503)
        if not .1 <= poll_seconds <= 30:
            raise ValueError('Fabrication polling must be between 0.1 and 30 seconds')
        stop = stop_event if stop_event is not None else asyncio.Event()
        try:
            while not stop.is_set():
                delay = poll_seconds
                try:
                    await self.cycle()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # Provider/tool diagnostics and transport values are not public log data.
                    logger.warning('Fabrication worker cycle unavailable; durable jobs retained for reconciliation')
                    delay = max(5, poll_seconds)
                try:
                    await asyncio.wait_for(stop.wait(), timeout=delay)
                except TimeoutError:
                    pass
        finally:
            await self.close()

    async def close(self):
        if self._closed:
            return
        self._closed = True
        if self.docker is not None:
            await self.docker.close()


def build_fabrication(settings, projects, artifacts, access, connections, *, bambu=None, docker_factory=DockerClient, capacity=None,
                      print_transport=None):
    """Validate operator evidence before constructing any network-capable client.

    Required settings: environment, docker_socket, slicer_installation (dict|None),
    slicer_control_root (Path), plus existing BambuFacade origin/local settings.
    No installation means available read-only job views and explicit admission failure.
    """
    installation = getattr(settings, 'slicer_installation', None)
    configured, docker, driver = None, None, None
    if docker_factory is not DockerClient and settings.environment != 'test':
        raise ValueError('Injected fabrication transports are test-only')
    if installation is not None:
        if settings.environment not in {'development', 'test'}:
            raise DomainError('Slicer qualification currently supports local development only', 'tool_unqualified', 503)
        try:
            configured = qualify_slicing(installation, settings.slicer_control_root)
        except (AttributeError, TypeError, ValueError, OSError):
            raise DomainError('Slicer installation does not match its qualification evidence', 'tool_unqualified', 503) from None
        docker = docker_factory(socket=settings.docker_socket)
        driver = DockerSlicingDriver(docker, configured)
    preparations = PrintPreparations(projects, artifacts.blobs, configured, driver, capacity=capacity)
    printer = bambu if bambu is not None else BambuFacade(access, connections, settings)
    printer.preparations = preparations
    dispatcher = PrintDispatcher(preparations, connections, print_transport, models=connections.printing_models)
    facade = FabricationFacade(preparations, bambu=printer, dispatcher=dispatcher)
    return FabricationRuntime(preparations, facade, printer, docker)
