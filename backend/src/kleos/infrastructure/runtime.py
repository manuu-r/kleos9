"""Explicit production composition; schema creation and workflow execution are separate commands."""
import base64
import hashlib
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from fastapi import Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import Field
from sqlalchemy import inspect
from sqlalchemy.engine import make_url

from kleos.access.service import AccessService, AccessSettings
from kleos.api.app import create_app
from kleos.api.bambu import BambuFacade
from kleos.api.catalog import CatalogFacade
from kleos.api.design import DesignFacade
from kleos.api.engineering import EngineeringFacade
from kleos.api.human_preview import HumanPreviewFacade
from kleos.api.interfaces import InterfaceFacade
from kleos.api.policies import PolicyService
from kleos.api.previews import EngineeringPreviews
from kleos.api.releases import ReleaseFacade
from kleos.api.research import ResearchFacade
from kleos.api.services import ApplicationServices
from kleos.api.tool_policy import ToolPolicyService
from kleos.artifacts.service import ArtifactService
from kleos.artifacts.storage import BlobStore
from kleos.capabilities.design.runner import DesignPlanner, DesignRepository, DesignRunner
from kleos.capabilities.design.service import DesignService
from kleos.capabilities.external_wait_tools import register_external_wait
from kleos.capabilities.fabrication.connections import BambuConnections
from kleos.capabilities.fabrication.credentials import EncryptedCredentialStore
from kleos.capabilities.native import register_native
from kleos.capabilities.registry import CapabilityRegistry
from kleos.capabilities.research import register_project_sources
from kleos.contracts.models import Capability, DomainError, WireModel
from kleos.execution.capacity import CapacityLimits, CapacityService
from kleos.execution.docker import DockerClient, NativeSettings, Resources
from kleos.execution.gateway import NativeGateway
from kleos.execution.protocol import PROTOCOL
from kleos.infrastructure.external import build_external
from kleos.infrastructure.fabrication import build_fabrication
from kleos.infrastructure.rebuild import configure_native_rebuild
from kleos.infrastructure.research import configure_web_research
from kleos.infrastructure.schema import metadata
from kleos.infrastructure.validation import configure_simulation, configure_validation
from kleos.memory.service import Knowledge
from kleos.orchestration.engine import ObjectiveEngine
from kleos.orchestration.external_waits import ExternalWaits
from kleos.orchestration.models import ModelConfiguration, ModelGateway, ModelLedger, ModelPolicy
from kleos.orchestration.persistence import postgres_runtime
from kleos.orchestration.planner import ModelPlanner
from kleos.orchestration.specialist import Action, ModelSpecialist
from kleos.orchestration.worker import ObjectiveWorker
from kleos.projects.engineering import EngineeringService
from kleos.projects.graph_repository import GraphRepository
from kleos.projects.interface_runner import InterfacePlanner, InterfaceRepository, InterfaceRunner
from kleos.projects.interfaces import InterfaceService
from kleos.projects.rebuild import NativeRebuildService
from kleos.projects.research import ResearchRepository, ResearchService
from kleos.projects.store import ProjectStore, fingerprint
from kleos.projects.verification import VerificationService

BACKEND_ROOT = Path(__file__).resolve().parents[3]


def readiness_endpoint(request: Request):
    return request.app.state.runtime.readiness()


def runtime_contracts():
    app = create_app(None)
    app.add_api_route('/api/v1/ready', readiness_endpoint, methods=['GET'])
    return app.openapi()


def migration_config(database_url=None):
    config = Config(str(BACKEND_ROOT / 'alembic.ini'))
    if database_url:
        config.set_main_option('sqlalchemy.url', database_url.replace('%', '%%'))
    return config


def postgres_url(database_url):
    parsed = make_url(database_url)
    if parsed.get_backend_name() != 'postgresql':
        raise DomainError('Production graph persistence requires PostgreSQL', 'database_unavailable', 503)
    return parsed.set(drivername='postgresql').render_as_string(hide_password=False)


def verify_schema(projects):
    expected = set(ScriptDirectory.from_config(migration_config()).get_heads())
    with projects.engine.connect() as connection:
        heads = set(MigrationContext.configure(connection, opts={'version_table': 'kleos2_alembic_version'}).get_current_heads())
        tables = set(inspect(connection).get_table_names())
        if heads != expected or not set(metadata().tables).issubset(tables):
            raise DomainError('Replacement database needs the explicit migrate command', 'schema_unavailable', 503)


class NativeToolConfiguration(WireModel):
    capability: Literal['cad', 'pcb', 'firmware', 'application']
    image_digest: str = Field(pattern=r'^sha256:[a-f0-9]{64}$')
    provider_version: str = Field(min_length=1, max_length=100)
    qualified: bool = False
    qualification_reference: Path


def native_qualification(config: NativeToolConfiguration, *, desktop=False):
    """Read exact-image evidence supplied by the operator; never infer qualification from a tag."""
    path = config.qualification_reference
    if not config.qualified or path.is_symlink() or not path.is_file() or path.stat().st_size > 1000000:
        raise DomainError('Native installation lacks bounded qualification evidence', 'tool_unqualified')
    try:
        with path.open('rb') as stream:
            raw = stream.read(1000001)
        if len(raw) > 1000000:
            raise ValueError('Oversized qualification evidence')
        report = json.loads(raw)
    except (OSError, ValueError):
        raise DomainError('Native qualification evidence cannot be read', 'tool_unqualified') from None
    policy = 'docker-desktop-daemon-seccomp' if desktop else 'in-process-seccomp'
    if (report.get('qualified') is not True or report.get('imageId') != config.image_digest
            or report.get('protocol') != PROTOCOL or report.get('lane') != config.capability
            or report.get('sourceOverride') is not False or report.get('networkPolicy') != policy):
        raise DomainError('Native evidence does not qualify this exact image and execution policy', 'tool_unqualified')
    checks = {item.get('action'): item.get('response', {}) for item in report.get('checks', [])}
    required = {'patch', 'build', 'finish'}
    if config.capability == 'cad':
        required.update({'analyze', 'render'})
    if not required.issubset(checks) or any(checks[key].get('blockers') for key in required):
        raise DomainError('Native qualification did not finish the required actions', 'tool_unqualified')
    if any(observation.get('status') in {'FAIL', 'ERROR'} for key in required
           for observation in checks[key].get('observations', [])) or not checks['finish'].get('artifacts'):
        raise DomainError('Native qualification contains failures or no actual outputs', 'tool_unqualified')
    containers = report.get('containers', [])
    if not containers or any(row.get('image') != config.image_digest or row.get('readonlyRootfs') is not True
        or row.get('networkMode') != 'none' or not row.get('memory') or not row.get('nanoCpus')
        or not row.get('pidsLimit') for row in containers):
        raise DomainError('Native isolation evidence is incomplete', 'tool_unqualified')
    pin = {'imageDigest': config.image_digest, 'capability': config.capability,
        'providerVersion': config.provider_version, 'qualificationHash': hashlib.sha256(raw).hexdigest(),
        'networkPolicy': policy, 'qualificationScope': str(report.get('scope', ''))[:1000]}
    if config.capability == 'firmware':
        if report.get('firmwareBoard') not in {'nrf52840dk/nrf52840', 'native_sim'}:
            raise DomainError('Firmware evidence must qualify an explicit supported board', 'tool_unqualified')
        pin['firmwareBoard'] = report['firmwareBoard']
    if config.capability == 'application':
        limits = {'cpus': 2, 'memoryBytes': 6 * 1024**3, 'pids': 256, 'scratchBytes': 4 * 1024**3}
        if (report.get('applicationTarget') != 'web'
                or report.get('storagePolicy') != 'hard-bounded-private-tmpfs'
                or report.get('resources') != limits
                or any(row.get('memory') != limits['memoryBytes']
                    or row.get('nanoCpus') != limits['cpus'] * 1_000_000_000
                    or row.get('pidsLimit') != limits['pids']
                    or row.get('writableHostMounts') is not False
                    or set(str(row.get('scratch', '')).split(',')) != {
                        'rw', 'nosuid', 'nodev', 'size=4294967296', 'mode=1777', 'exec'} for row in containers)):
            raise DomainError('Application evidence must bind its qualified web target and hard resource limits', 'tool_unqualified')
        pin.update(applicationTarget='web', resources=limits, storagePolicy=report['storagePolicy'])
    return pin


class BoundedPlanner(ModelPlanner):
    def __init__(self, *args, max_actions, **kwargs):
        super().__init__(*args, **kwargs)
        self.max_actions = max_actions

    async def plan(self, *args, **kwargs):
        decision = await super().plan(*args, **kwargs)
        if any(task.max_actions > self.max_actions for task in decision.tasks):
            raise DomainError('Planner exceeded the pinned task action allowance', 'budget_exhausted')
        return decision


class Runtime:
    def __init__(self, settings, *, registry=None, persistence_factory=postgres_runtime, worker_locks=None,
                 model_client=None, research_client=None):
        if not settings.session_secret or len(settings.session_secret.get_secret_value()) < 32:
            raise DomainError('Configure a persistent session secret before serving', 'configuration_required', 503)
        if settings.environment != 'test' and make_url(settings.database_url).get_backend_name() != 'postgresql':
            raise DomainError('Runtime requires a fresh PostgreSQL database', 'database_unavailable', 503)
        if persistence_factory is not postgres_runtime and settings.environment != 'test':
            raise ValueError('Alternative persistence is test-only')
        if research_client is not None and settings.environment != 'test':
            raise ValueError('Injected research transports are test-only')
        self.settings, self.persistence_factory = settings, persistence_factory
        self.worker_locks, self.model_client = worker_locks, model_client
        self.projects = ProjectStore(settings.database_url, require_account_authority=True)
        self.capacity = CapacityService(self.projects, CapacityLimits(
            cpus=settings.native_capacity_cpus, memory_bytes=settings.native_capacity_memory_bytes,
            scratch_bytes=settings.native_capacity_scratch_bytes, max_jobs=settings.native_capacity_max_jobs),
            pool_id=settings.native_capacity_pool_id)
        self.repository = GraphRepository(self.projects)
        self.artifacts = ArtifactService(self.projects, BlobStore(settings.artifact_root))
        self.previews = EngineeringPreviews(self.projects, self.artifacts)
        from kleos.api.companion import CompanionPreviews, create_preview_app
        self.companion = CompanionPreviews(self.projects, self.artifacts,
            secret=settings.session_secret.get_secret_value(), api_origin=settings.api_origin,
            frontend_origin=settings.frontend_origin, preview_origin=settings.preview_origin,
            enabled=settings.companion_preview_enabled, ttl_seconds=settings.companion_preview_ttl_seconds)
        self.previews.companion = self.companion
        self.preview_app = create_preview_app(self.companion)
        self.preview_app.state.runtime = self
        self.preview_app.router.lifespan_context = self._lifespan
        self.access = AccessService(self.projects, AccessSettings(secret_key=settings.session_secret.get_secret_value(),
            environment=settings.environment, development_no_login=settings.development_no_login,
            setup_token=settings.setup_token.get_secret_value() if settings.setup_token else None))
        credential_store = None
        if settings.bambu_local_enabled:
            try:
                key = base64.b64decode(settings.printer_encryption_key.get_secret_value(), validate=True)
            except (AttributeError, ValueError):
                raise DomainError('Configure the persistent printer encryption key', 'configuration_required', 503) from None
            credential_store = EncryptedCredentialStore(self.projects.sessions, key)
        self.connections = BambuConnections(enabled=settings.bambu_local_enabled, secrets=credential_store)
        self.bambu = BambuFacade(self.access, self.connections, settings)
        self.fabrication_runtime = build_fabrication(settings, self.projects, self.artifacts,
            self.access, self.connections, bambu=self.bambu, capacity=self.capacity)
        from kleos.api.bambu_test_print import BambuTestPrintFacade
        self.bambu_test_print = BambuTestPrintFacade(self.bambu, self.fabrication_runtime.preparations)
        self.design = DesignService(self.projects)
        self.interfaces = InterfaceService(self.projects, self.artifacts)
        from kleos.projects.prerequisites import PrerequisiteService
        self.prerequisites = PrerequisiteService(self.projects, self.interfaces, self.repository)
        self.registry = registry if registry is not None else CapabilityRegistry()
        register_project_sources(self.registry, self.repository, self.artifacts)
        from kleos.capabilities.repository_context import register_repository_context
        self.repository_context_tools = register_repository_context(self.registry, self.repository, self.artifacts)
        from kleos.infrastructure.mcp import McpRuntime
        self.mcp_runtime = McpRuntime(settings, self.registry, self.repository, self.artifacts, self.capacity)
        self.web_research_tools = configure_web_research(settings, self.registry, self.repository, client=research_client)
        self.external_runtime = build_external(settings, self.projects, self.access, self.registry, self.repository)
        self.external_jobs, self.external = self.external_runtime.jobs, self.external_runtime.facade
        self.human_preview = HumanPreviewFacade(self.external)
        self.external_waits = ExternalWaits(self.projects, self.repository, external_jobs=self.external_jobs,
            preparations=self.fabrication_runtime.preparations)
        self.external_wait_tools = {}
        for pin in self.external_runtime.pins:
            provider = pin['provider']
            kind = 'sourcing' if provider == 'digikey' else 'human_work'
            key = register_external_wait(self.registry, self.external_waits, self.repository,
                kind=kind, provider=provider, provider_version=pin['providerVersion'], qualified=True)
            self.external_wait_tools[key] = provider
        if self.fabrication_runtime.enabled:
            register_external_wait(self.registry, self.external_waits, self.repository,
                kind='print_preparation', provider='orca', provider_version='2.4.2', qualified=True)
        self.native_pins, self.docker, self.native_gateway = [], None, None
        self.checkpointer, self.memory_store, self.memory, self.worker = None, None, None, None
        self.services = ApplicationServices(self.projects, self.access, artifacts=self.artifacts,
            bambu=self.bambu, external=self.external, fabrication=self.fabrication_runtime.facade,
            engineering=EngineeringFacade(EngineeringService(self.projects)),
            interfaces=InterfaceFacade(self.interfaces), previews=self.previews, human_preview=self.human_preview)
        self.ready = False
        from kleos.api.prerequisites import PrerequisiteFacade
        self.services.prerequisites = PrerequisiteFacade(self.prerequisites)
        self.services.companion = self.companion
        self.services.bambu_test_print = self.bambu_test_print
        from kleos.artifacts.import_archive import ArchiveImportService
        self.services.archive_import = ArchiveImportService(self.artifacts)
        from kleos.artifacts.repository_download import RepositoryDownloader
        from kleos.artifacts.repository_import import RepositoryImportService
        self.services.repository_import = RepositoryImportService(
            self.artifacts, RepositoryDownloader(), access=self.access)
        from kleos.projects.workbench_edits import WorkbenchEdits
        self.services.workbench_edits = WorkbenchEdits(self.projects, self.design)
        from kleos.projects.workbench_progress import WorkbenchProgress
        self.services.workbench_progress = WorkbenchProgress(self.projects)
        self._configure_native()
        if self.native_gateway is not None:
            self.services.engineering.rebuild = NativeRebuildService(self.projects, self.native_gateway)
        validation = configure_validation(settings, self.projects, self.artifacts, self.registry,
            docker=self.docker, capacity=self.capacity)
        self.validation, self.checker_registry, self.docker = validation.service, validation.registry, validation.docker
        self.simulation, self.docker = configure_simulation(settings, self.projects, self.artifacts,
            docker=self.docker, capacity=self.capacity)
        self.previews.simulation = self.simulation
        self.verification = VerificationService(self.projects, self.checker_registry, simulation=self.simulation)
        self.services.engineering.verification = self.verification
        self.services.releases = ReleaseFacade(self.projects, self.artifacts, self.validation)
        self.services.catalog = CatalogFacade(settings, self.registry, native_pins=self.native_pins)
        self.tool_policies = ToolPolicyService(self.projects, self.registry, native_pins=self.native_pins)
        self.services.tool_policies = self.tool_policies
        self.services.research = ResearchFacade(ResearchService(self.projects, self.registry))
        from kleos.api.connectors import Connectors
        self.services.connectors = Connectors(self.access, self.registry, external=self.external_runtime,
            bambu=self.bambu, fabrication=self.fabrication_runtime)
        self.policies = PolicyService(self.projects, settings)
        self.services.policies = self.policies
        self.services.design = DesignFacade(self.design, policies=self.policies)
        self.app = create_app(self.services)
        self.app.state.runtime = self
        self.app.router.lifespan_context = self._lifespan
        self.app.add_middleware(CORSMiddleware, allow_origins=[settings.frontend_origin.rstrip('/')],
            allow_credentials=True, allow_methods=['GET', 'POST', 'DELETE', 'OPTIONS'],
            allow_headers=['Content-Type', 'Authorization', 'Idempotency-Key', 'X-CSRF-Token', 'X-Organization-Id'])
        self.app.add_api_route('/api/v1/ready', readiness_endpoint, methods=['GET'])

    def _configure_native(self):
        configurations = [NativeToolConfiguration.model_validate(item) for item in self.settings.native_tools]
        if not configurations:
            return
        if len({item.capability for item in configurations}) != len(configurations):
            raise DomainError('Choose one qualified image per native capability', 'configuration_required')
        self.native_pins = [native_qualification(item, desktop=self.settings.native_docker_desktop_compatibility)
                            for item in configurations]
        native_settings = NativeSettings(self.settings.native_control_root,
            {item.capability: item.image_digest for item in configurations},
            firmware_boards=tuple(pin['firmwareBoard'] for pin in self.native_pins if pin['capability'] == 'firmware'),
            application_resources=Resources(**{
                'cpus': pin['resources']['cpus'], 'memory_bytes': pin['resources']['memoryBytes'],
                'pids': pin['resources']['pids'], 'scratch_bytes': pin['resources']['scratchBytes']})
                if (pin := next((p for p in self.native_pins if p['capability'] == 'application'), None))
                else NativeSettings(self.settings.native_control_root, {}).application_resources,
            daemon_control_root=self.settings.native_daemon_control_root,
            docker_desktop_compatibility=self.settings.native_docker_desktop_compatibility,
            deployment='ecs' if self.settings.environment == 'production' else 'local')
        self.docker = DockerClient(socket=self.settings.docker_socket)
        self.native_gateway = NativeGateway(self.projects, self.artifacts, native_settings, self.docker,
            self.repository, capacity=self.capacity)
        for item, pin in zip(configurations, self.native_pins, strict=True):
            register_native(self.registry, self.native_gateway, capability=Capability(item.capability), provider='native',
                provider_version=item.provider_version, installation_hash=fingerprint(pin), qualified=True,
                actions=('read', 'patch', 'build', 'analyze', 'render', 'finish') if item.capability == 'cad'
                        else ('read', 'patch', 'build', 'finish'),
                qualification_scope=pin['qualificationScope'])

    async def authorize_memory(self, actor, project_id, *, write=False):
        with self.projects.transaction() as session:
            self.projects.authorized(session, actor, project_id, write=write)
        return self.projects.context(actor, project_id)

    def _run_pins(self, scope):
        with self.projects.transaction() as session:
            _, run = self.repository._check(session, scope)
            if run.data.get('runtimePins'):
                current_accounts = {pin['provider']: pin for pin in self.external_runtime.pins_for(scope.actor)}
                for pin in run.data['runtimePins'].get('external', []):
                    if current_accounts.get(pin['provider']) != pin:
                        raise DomainError('Pinned external account installation changed', 'provider_changed')
                return run.data['runtimePins']
            if not self.settings.text_model_key:
                raise DomainError('Configure an explicit text model', 'model_unavailable', 503)
            configurations = [ModelConfiguration.model_validate(item) for item in self.settings.model_catalog]
            _, lane_keys = self.policies.resolve(run.data.get('modelPolicySnapshot', {}))
            policy = ModelPolicy(text_model_key=self.settings.text_model_key, vision_model_key=self.settings.vision_model_key,
                modal_rate_limit_fallback_key=self.settings.modal_rate_limit_fallback_key,
                max_calls=self.settings.max_model_calls, max_cost_micro_usd=self.settings.max_model_cost_micro_usd)
            account_tools = set(self.external_runtime.tool_keys_for(scope.actor))
            actor_providers = {pin['provider'] for pin in self.external_runtime.pins_for(scope.actor)}
            account_tools.update(key for key, provider in self.external_wait_tools.items() if provider in actor_providers)
            external_tools = set(self.external_runtime.tool_keys) | set(self.external_wait_tools)
            tools = [tool for tool in self.registry.catalog()
                     if tool['key'] not in external_tools or tool['key'] in account_tools]
            tools = self.tool_policies.filter_tools(run.data.get('toolPolicySnapshot', {}), tools)
            if run.data.get('kind') == 'project.research':
                admitted = run.data.get('researchRequest', {}).get('tools', [])
                self.registry.tools(admitted)  # Recheck exact installations before a queued attempt starts.
                available = {tool['key']: tool for tool in tools}
                if not admitted or any(available.get(tool['key']) != tool for tool in admitted):
                    raise DomainError('Research installation changed after admission', 'installation_conflict')
                tools = admitted
            pins = {'models': [item.model_dump(mode='json') for item in configurations],
                'modelPolicy': policy.model_dump(mode='json'), 'tools': tools,
                'native': self.native_pins, 'maxContextChars': self.settings.max_context_chars,
                'external': self.external_runtime.pins_for(scope.actor),
                'maxActions': self.settings.max_actions, 'laneModels': lane_keys}
            run.data = {**run.data, 'runtimePins': pins}
            return pins

    def _gateway(self, scope, pins):
        credentials = {name: key for name, key in [('meta', self.settings.meta_api_key),
            ('modal', self.settings.modal_api_key)] if key is not None}
        return ModelGateway([ModelConfiguration.model_validate(item) for item in pins['models']],
            ModelPolicy.model_validate(pins['modelPolicy']), ModelLedger(self.projects), credentials, client=self.model_client)

    def engine_factory(self, scope):
        if self.checkpointer is None:
            raise DomainError('Graph persistence is not connected', 'persistence_unavailable', 503)
        with self.projects.transaction() as session:
            _, run = self.repository._check(session, scope)
            native_rebuild = run.data.get('kind') == 'project.nativeRebuild'
        if native_rebuild:
            if self.native_gateway is None:
                raise DomainError('Native rebuild execution is unavailable', 'tool_unavailable', 503)
            return configure_native_rebuild(self.projects, self.native_gateway, scope,
                checkpointer=self.checkpointer, store=self.memory_store)
        pins = self._run_pins(scope)
        gateway = self._gateway(scope, pins)
        context = self.projects.context(scope.actor, scope.project_id)
        coordinator_key = pins.get('laneModels', {}).get('coordination')
        with self.projects.transaction() as session:
            _, run = self.repository._check(session, scope)
            capture = run.data.get('kind') == 'project.capture'
            interface_proposal = run.data.get('kind') == 'project.solveInterfaces'
            research = run.data.get('kind') == 'project.research'
        if interface_proposal:
            config = gateway.select(images=bool(context.reference_images), model_key=coordinator_key)
            if not config.tool_calls:
                raise DomainError('Interface proposals require typed model output', 'model_capability_missing')
            repository = InterfaceRepository(self.projects, self.interfaces)
            runner = InterfaceRunner(gateway.chat_model(scope, images=bool(context.reference_images), model_key=coordinator_key),
                repository, self.interfaces, self.artifacts.planner_images,
                output_tokens=min(8192, config.max_output_tokens), max_context_chars=pins['maxContextChars'])
            return ObjectiveEngine(repository, InterfacePlanner(), runner, checkpointer=self.checkpointer, store=self.memory_store)
        if capture:
            config = gateway.select(images=bool(context.reference_images), model_key=coordinator_key)
            if not config.tool_calls:
                raise DomainError('Capture requires typed model output', 'model_capability_missing')
            repository = DesignRepository(self.projects, self.design)
            runner = DesignRunner(gateway.chat_model(scope, images=bool(context.reference_images), model_key=coordinator_key), repository,
                self.design, self.artifacts.planner_images, output_tokens=min(8192, config.max_output_tokens),
                max_context_chars=pins['maxContextChars'])
            return ObjectiveEngine(repository, DesignPlanner(), runner, checkpointer=self.checkpointer, store=self.memory_store)
        tools = self.registry.tools(pins['tools'])
        capabilities = sorted({capability for tool in tools for capability in tool.capabilities})
        if not capabilities:
            raise DomainError('No qualified engineering capabilities are configured', 'tool_unavailable', 503)
        planner_config = gateway.select(images=bool(context.reference_images), model_key=coordinator_key)
        specialist_config = gateway.select()
        if not planner_config.tool_calls or not specialist_config.tool_calls:
            raise DomainError('Configured models must support typed tool output', 'model_capability_missing')
        def planner_model(selected_scope, selected_context):
            return gateway.chat_model(selected_scope, images=bool(selected_context.reference_images), model_key=coordinator_key)

        planner = BoundedPlanner(gateway.chat_model(scope, images=bool(context.reference_images), model_key=coordinator_key), capabilities,
            output_tokens=min(4096, planner_config.max_output_tokens), max_actions=pins['maxActions'],
            max_context_chars=pins['maxContextChars'], image_loader=self.artifacts.planner_images,
            memory_loader=self.planner_memory, model_resolver=planner_model)
        def specialist_model(selected_scope, task):
            lane = {'cad': 'mechanics', 'pcb': 'electronics', 'firmware': 'firmware',
                    'application': 'application', 'validation': 'review'}.get(task.capability.value, 'research')
            selected = gateway.select(model_key=pins.get('laneModels', {}).get(lane))
            if not selected.tool_calls:
                raise DomainError('Selected specialist model requires typed tool output', 'model_capability_missing')
            return gateway.chat_model(selected_scope, model_key=selected.key).with_structured_output(Action).bind(
                max_tokens=min(4096, selected.max_output_tokens))

        repository = ResearchRepository(self.projects) if research else self.repository
        specialist = ModelSpecialist(gateway.chat_model(scope), tools, repository,
            output_tokens=min(4096, specialist_config.max_output_tokens), max_context_chars=pins['maxContextChars'],
            model_resolver=specialist_model)
        composition = None
        prerequisites = None
        if not research and self.native_gateway is not None:
            from kleos.orchestration.composition import FinalCompilation
            composition = FinalCompilation(repository, self.artifacts, self.native_gateway)
            from kleos.orchestration.prerequisites import InterfacePrerequisites
            def prerequisite_model(selected_scope, selected_context):
                config = gateway.select(images=bool(selected_context.reference_images), model_key=coordinator_key)
                if not config.tool_calls:
                    raise DomainError('Interface prerequisites require typed output', 'model_capability_missing')
                return gateway.chat_model(selected_scope, images=bool(selected_context.reference_images), model_key=coordinator_key)
            prerequisites = InterfacePrerequisites(repository, self.prerequisites, prerequisite_model,
                image_loader=self.artifacts.planner_images, output_tokens=min(8192, planner_config.max_output_tokens),
                max_context_chars=pins['maxContextChars'])
        return ObjectiveEngine(repository, planner, specialist, checkpointer=self.checkpointer, store=self.memory_store,
            external_waits=self.external_waits, composition=composition, prerequisites=prerequisites)

    async def planner_memory(self, scope, objective):
        await self.repository.assert_active(scope)
        return await self.memory.search(scope.actor, scope.project_id, objective[:2000], limit=8)

    def memory_for_run(self, scope):
        if self.memory_store is None:
            raise DomainError('Memory persistence is not connected', 'persistence_unavailable', 503)
        pins = self._run_pins(scope)
        async def authorize(actor, project_id, *, write=False):
            if actor != scope.actor or project_id != scope.project_id:
                raise DomainError('Memory scope differs from the objective', 'forbidden', 403)
            await self.repository.assert_active(scope)
            return await self.authorize_memory(actor, project_id, write=write)
        return Knowledge(self.memory_store, authorize, model=self._gateway(scope, pins).chat_model(scope))

    async def observe_memory(self, scope, state):
        with self.projects.transaction() as session:
            _, run = self.repository._check(session, scope)
            if run.data.get('kind') == 'project.nativeRebuild':
                return {'status': 'not_applicable', 'reason': 'native_only_rebuild', 'factIds': []}
        objective = str(state.get('objective', ''))[:12000]
        summary = str(state.get('summary', ''))[:4000]
        messages = [{'role': 'user', 'content': objective}, {'role': 'assistant', 'content': summary}]
        # Persist only public summaries, never provider payloads or tool traces.
        facts = await self.memory_for_run(scope).extract(scope.actor, scope.project_id, messages)
        return {'factIds': [fact['id'] for fact in facts]}

    def readiness(self):
        # Route/service parity and independent tools are not yet universally qualified.
        return JSONResponse({'status': 'partial' if self.ready else 'unavailable', 'apiAvailable': self.ready,
            'graphPersistence': self.ready, 'capabilities': self.registry.available(),
            'physicalActions': False, 'frontendContractComplete': False,
            'limitations': ['Unimplemented operations return 501; frontend cutover is not yet qualified.',
                            'Only explicitly configured qualified tool installations can run.']},
            status_code=200 if self.ready else 503)

    @asynccontextmanager
    async def start(self):
        try:
            verify_schema(self.projects)
            url = (postgres_url(self.settings.database_url) if self.persistence_factory is postgres_runtime
                   else self.settings.database_url)
            async with self.persistence_factory(url, setup=False) as (saver, memory_store):
                # Connection success alone does not establish migrated graph/store tables.
                await saver.aget_tuple({'configurable': {'thread_id': '__kleos_readiness__'}})
                await memory_store.asearch(('kleos', '__readiness__'), limit=1)
                self.checkpointer, self.memory_store = saver, memory_store
                self.memory = Knowledge(memory_store, self.authorize_memory)
                self.worker = ObjectiveWorker(self.projects, self.engine_factory, locks=self.worker_locks,
                    max_run_seconds=self.settings.max_run_seconds, max_tasks=self.settings.max_tasks,
                    max_lifetime_seconds=self.settings.max_objective_lifetime_seconds,
                    on_observed=self.observe_memory)
                self.services.memory, self.services.worker = self.memory, self.worker
                self.ready = True
                yield self
        finally:
            self.ready = False
            self.checkpointer, self.memory_store = None, None
            self.connections.close()
            await self.fabrication_runtime.close()
            await self.external_runtime.close()
            await self.mcp_runtime.close()
            if self.docker is not None:
                await self.docker.close()
            self.projects.engine.dispose()

    @asynccontextmanager
    async def _lifespan(self, app):
        async with self.start():
            yield
