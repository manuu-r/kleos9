"""Small explicit entry points for the replacement backend."""
import argparse
import asyncio
import json
import signal
import sys
from pathlib import Path

from alembic import command

from kleos.contracts.models import DomainError
from kleos.infrastructure.external_waits import serve_external_waits
from kleos.infrastructure.native_maintenance import maintain_native
from kleos.infrastructure.runtime import Runtime, migration_config, postgres_url, runtime_contracts
from kleos.infrastructure.settings import Settings
from kleos.infrastructure.verification import serve_verification
from kleos.orchestration.persistence import postgres_runtime


def migrate(settings):
    command.upgrade(migration_config(settings.database_url), 'head')
    if settings.database_url.startswith('postgresql'):
        async def setup_graph():
            async with postgres_runtime(postgres_url(settings.database_url), setup=True):
                pass
        asyncio.run(setup_graph())


async def work(runtime, *, fabrication=False, native_maintenance=False, verification=False, external=False):
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stop.set)
    try:
        async with runtime.start():
            if external:
                await serve_external_waits(runtime.external_waits, stop)
            elif verification:
                await serve_verification(runtime.verification, stop)
            elif native_maintenance:
                gateways = [gateway for gateway in (runtime.native_gateway, getattr(runtime, 'mcp_runtime', None))
                            if gateway is not None]
                if not gateways:
                    raise DomainError('Native maintenance requires a configured control gateway', 'tool_unavailable', 503)
                async with asyncio.TaskGroup() as group:
                    for gateway in gateways:
                        group.create_task(maintain_native(gateway, stop))
            elif fabrication:
                await runtime.fabrication_runtime.run(stop)
            else:
                background = []
                if runtime.native_gateway is not None:
                    background.append(asyncio.create_task(maintain_native(runtime.native_gateway, stop)))
                if getattr(runtime, 'mcp_runtime', None) is not None:
                    background.append(asyncio.create_task(maintain_native(runtime.mcp_runtime, stop)))
                if getattr(runtime, 'verification', None) is not None:
                    background.append(asyncio.create_task(serve_verification(runtime.verification, stop)))
                if getattr(runtime, 'external_waits', None) is not None:
                    background.append(asyncio.create_task(serve_external_waits(runtime.external_waits, stop)))
                try:
                    await runtime.worker.serve(stop)
                finally:
                    stop.set()
                    for task in background:
                        task.cancel()
                    await asyncio.gather(*background, return_exceptions=True)
    finally:
        for signum in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(signum)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Kleos replacement backend; explicit schema and process commands')
    commands = parser.add_subparsers(dest='operation', required=True)
    commands.add_parser('migrate', help='Upgrade only the fresh replacement database and graph/store schema')
    export = commands.add_parser('export-runtime-contracts', help='Export implemented HTTP contracts, preserving the frozen UI target')
    export.add_argument('--output', type=Path, default=Path('contracts/runtime-openapi.json'))
    serve = commands.add_parser('serve', help='Start the HTTP API; does not run objective workers')
    serve.add_argument('--host', default='127.0.0.1')
    serve.add_argument('--port', type=int, default=8000)
    preview = commands.add_parser('preview-serve', help='Serve sandboxed companion files on the separate preview origin')
    preview.add_argument('--host', default='127.0.0.1')
    preview.add_argument('--port', type=int, default=8004)
    commands.add_parser('worker', help='Run the durable objective worker; configured models may incur charges')
    commands.add_parser('fabrication-worker', help='Reconcile and process isolated digital slicing jobs; no print dispatch')
    commands.add_parser('native-maintenance', help='Reconcile stopped/expired native compute; no model calls or generation')
    commands.add_parser('verification-worker', help='Run independent checks of completed candidates; no model calls')
    commands.add_parser('external-worker', help='Observe existing external jobs and wake saved graphs; no model calls')
    args = parser.parse_args(argv)
    try:
        if args.operation == 'export-runtime-contracts':
            # Schema generation does not instantiate services, open databases or read credentials.
            document = runtime_contracts()
            if args.output.name == 'openapi.json':
                raise DomainError('Choose a separate runtime contract filename; the UI target is frozen', 'contract_protected')
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(document, indent=2, sort_keys=True) + '\n')
            print('Exported runtime HTTP contracts; route presence does not imply service availability.')
            return 0
        settings = Settings()
        if args.operation == 'migrate':
            migrate(settings)
            print('Replacement business and graph/store schema migration completed.')
        elif args.operation in {'worker', 'fabrication-worker', 'native-maintenance', 'verification-worker', 'external-worker'}:
            asyncio.run(work(Runtime(settings), fabrication=args.operation == 'fabrication-worker',
                native_maintenance=args.operation == 'native-maintenance', verification=args.operation == 'verification-worker',
                external=args.operation == 'external-worker'))
        else:
            if not 1 <= args.port <= 65535:
                raise DomainError('Choose a valid listen port', 'invalid_request')
            import uvicorn
            if args.operation == 'preview-serve':
                if not settings.companion_preview_enabled:
                    raise DomainError('Companion preview hosting is not enabled', 'configuration_required', 503)
                uvicorn.run(Runtime(settings).preview_app, host=args.host, port=args.port,
                    proxy_headers=False, access_log=False, limit_concurrency=16)
            else:
                uvicorn.run(Runtime(settings).app, host=args.host, port=args.port, proxy_headers=False)
        return 0
    except DomainError as error:
        print(f'{error.code}: {error.message}', file=sys.stderr)
        return 2
    except Exception:
        # Provider/DB exceptions can include sensitive URLs, headers or request bodies.
        print('Backend command failed; check explicit configuration, fresh schema and qualified installations.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
