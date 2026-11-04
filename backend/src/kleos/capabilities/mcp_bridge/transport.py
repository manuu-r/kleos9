"""Bounded MCP stdio framing over a runner-owned isolated byte channel.

This module never starts a subprocess. The runner owns isolation, capacity,
staging, cancellation and termination proof; a transport disconnect is not proof
that a remote action did not happen.
"""
import json
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Protocol

import anyio
from mcp import ClientSession, types
from mcp.shared.message import SessionMessage

from kleos.contracts.models import DomainError

PROTOCOL_VERSION = '2025-11-25'
SDK_VERSION = '1.26.0'


class ByteChannel(Protocol):
    async def receive(self, max_bytes: int) -> bytes: ...
    async def send(self, content: bytes) -> None: ...


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key')
        result[key] = value
    return result


def frame(content: bytes):
    try:
        raw = json.loads(content.decode('utf-8'), object_pairs_hook=_object,
                         parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite JSON')))
        # No reverse sampling, elicitation, roots, URL requests or dynamic tools.
        if not isinstance(raw, dict) or ('method' in raw and
                (raw['method'] != 'notifications/progress' or 'id' in raw)):
            raise ValueError('Unsupported server message')
        return SessionMessage(types.JSONRPCMessage.model_validate(raw))
    except (ValueError, RecursionError):
        raise DomainError('MCP server sent an invalid or unsupported message', 'mcp_protocol_error') from None


@asynccontextmanager
async def session(channel: ByteChannel, *, timeout_seconds=30, max_frame_bytes=262144,
                  max_session_bytes=1048576, max_messages=64):
    if (not 1 <= timeout_seconds <= 120 or not 1024 <= max_frame_bytes <= 1048576
            or not max_frame_bytes <= max_session_bytes <= 8388608 or not 4 <= max_messages <= 256):
        raise ValueError('Invalid MCP transport bounds')
    inbound, reader = anyio.create_memory_object_stream(1)
    writer, outbound = anyio.create_memory_object_stream(1)

    async def receive():
        buffer, total, count = bytearray(), 0, 0
        async with inbound:
            while True:
                requested = min(16384, max_frame_bytes + 1 - len(buffer))
                chunk = await channel.receive(requested)
                if not chunk:
                    raise DomainError('MCP connection ended before completion', 'mcp_disconnected')
                if not isinstance(chunk, bytes) or len(chunk) > requested:
                    raise DomainError('MCP runner exceeded the channel bound', 'mcp_output_limit')
                total += len(chunk)
                if total > max_session_bytes:
                    raise DomainError('MCP response exceeded its byte allowance', 'mcp_output_limit')
                buffer.extend(chunk)
                while b'\n' in buffer:
                    encoded, _, remainder = buffer.partition(b'\n')
                    buffer = bytearray(remainder)
                    count += 1
                    if len(encoded) > max_frame_bytes or count > max_messages:
                        raise DomainError('MCP response exceeded its message allowance', 'mcp_output_limit')
                    await inbound.send(frame(bytes(encoded)))
                if len(buffer) > max_frame_bytes:
                    raise DomainError('MCP response exceeded its frame allowance', 'mcp_output_limit')

    async def send():
        total = 0
        async with outbound:
            async for item in outbound:
                encoded = item.message.model_dump_json(by_alias=True, exclude_none=True).encode() + b'\n'
                total += len(encoded)
                if len(encoded) > max_frame_bytes or total > max_session_bytes:
                    raise DomainError('MCP request exceeded its byte allowance', 'mcp_input_limit')
                await channel.send(encoded)

    try:
        with anyio.fail_after(timeout_seconds):
            async with anyio.create_task_group() as group:
                group.start_soon(receive)
                group.start_soon(send)
                async with ClientSession(reader, writer,
                        read_timeout_seconds=timedelta(seconds=timeout_seconds),
                        client_info=types.Implementation(name='kleos-qualified-bridge', version='1.0.0')) as client:
                    yield client
                group.cancel_scope.cancel()
    except TimeoutError:
        raise DomainError('MCP request timed out; its effect requires reconciliation', 'mcp_timeout') from None
    except ExceptionGroup as errors:
        def domain(group):
            for error in group.exceptions:
                if isinstance(error, DomainError):
                    return error
                if isinstance(error, ExceptionGroup) and (nested := domain(error)):
                    return nested
            return None
        error = domain(errors)
        if error:
            raise error from None
        raise DomainError('MCP connection failed; its effect requires reconciliation', 'mcp_transport_error') from None


async def inspect_call(client, *, server_name, server_version, tool_name, input_schema, arguments):
    initialized = await client.initialize()
    if (initialized.protocolVersion != PROTOCOL_VERSION or
            initialized.serverInfo.name != server_name or initialized.serverInfo.version != server_version
            or initialized.capabilities.tools is None):
        raise DomainError('MCP server identity differs from its installation', 'mcp_installation_conflict')
    catalog = await client.list_tools()
    if catalog.nextCursor is not None or len(catalog.tools) > 32:
        raise DomainError('MCP tool catalog exceeds its qualified scope', 'mcp_catalog_conflict')
    names = [tool.name for tool in catalog.tools]
    matches = [tool for tool in catalog.tools if tool.name == tool_name]
    if (len(names) != len(set(names)) or len(matches) != 1 or matches[0].inputSchema != input_schema
            or (matches[0].execution and matches[0].execution.taskSupport != 'forbidden')):
        raise DomainError('MCP tool schema differs from its installation', 'mcp_catalog_conflict')
    # Do not invoke the SDK's opportunistic remote-output-schema discovery. The
    # bridge has its own strict output contract and never resolves server URLs.
    return await client.send_request(types.ClientRequest(types.CallToolRequest(
        params=types.CallToolRequestParams(name=tool_name, arguments=arguments))), types.CallToolResult)
