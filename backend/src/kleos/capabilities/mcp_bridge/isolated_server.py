"""Image-owned MCP wrapper for the exact MM-core read_image handler.

Only run inside its qualified container. This module is not imported by the
control plane and never imports a project source file or user plugin.
"""
import asyncio
import base64
import hashlib
import importlib.util
import io
import json
import os
import re
import socket
import stat
import sys
import warnings
from pathlib import Path

ROOT = Path('/opt/kleos-mcp')
INPUTS = Path('/inputs')
MAX_BYTES = 8388608
MAX_PIXELS = 8000000


def read_file(path, limit):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        value = os.fstat(stream.fileno())
        if not stat.S_ISREG(value.st_mode) or value.st_nlink != 1 or value.st_size > limit:
            raise ValueError('Input file exceeds its fixed contract')
        content = stream.read(limit + 1)
    if len(content) > limit:
        raise ValueError('Input file exceeds its fixed contract')
    return content


def isolation():
    if os.getuid() != 10001 or os.getgid() != 10001:
        raise RuntimeError('MCP process identity is not isolated')
    fields = dict(line.split(':', 1) for line in Path('/proc/self/status').read_text().splitlines() if ':' in line)
    if fields.get('NoNewPrivs', '').strip() != '1' or fields.get('Seccomp', '').strip() != '2':
        raise RuntimeError('MCP syscall restrictions are missing')
    if int(fields.get('CapEff', '1').strip(), 16) != 0:
        raise RuntimeError('MCP process capabilities are not empty')
    for family in (socket.AF_INET, socket.AF_INET6):
        try:
            opened = socket.socket(family, socket.SOCK_STREAM)
        except PermissionError:
            continue
        else:
            opened.close()
            raise RuntimeError('MCP network sockets are not restricted')
    if os.environ.get('QWEN_MM_NATIVE_MODE') != '1' or os.environ.get('QWEN_MM_AUTOLAUNCH') != '0':
        raise RuntimeError('MCP native-only configuration is missing')


def load_handler():
    manifest = json.loads(read_file(ROOT / 'vendor-source.json', 65536))
    for name, record in manifest['files'].items():
        path = ROOT / 'vendor' / name
        if hashlib.sha256(read_file(path, 65536)).hexdigest() != record['sha256']:
            raise RuntimeError('MM-core source differs from the pinned image')
    sys.path.insert(0, str(ROOT / 'vendor'))
    spec = importlib.util.spec_from_file_location('kleos_pinned_mm_read_image', ROOT / 'vendor/mm_read_image.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.handle


def inspect_images(arguments, request, handle):
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    warnings.simplefilter('error', Image.DecompressionBombWarning)
    if not isinstance(arguments, dict) or set(arguments) != {'inputs'} or arguments['inputs'] != request['inputs']:
        raise ValueError('Tool inputs differ from the staged source closure')
    items = arguments['inputs']
    if not isinstance(items, list) or not 1 <= len(items) <= 16:
        raise ValueError('Input count exceeds fixed bounds')
    records, total = [], 0
    for index, item in enumerate(items):
        if (set(item) != {'artifactId', 'path', 'sha256', 'size', 'mediaType'}
                or item['path'] != f'inputs/{index:03}'
                or not isinstance(item['artifactId'], str) or not 1 <= len(item['artifactId']) <= 100
                or not isinstance(item['sha256'], str) or not re.fullmatch(r'[a-f0-9]{64}', item['sha256'])
                or type(item['size']) is not int or not 0 <= item['size'] <= MAX_BYTES
                or item['mediaType'] not in {'image/png', 'image/jpeg'}):
            raise ValueError('Unsupported input contract')
        path = INPUTS / f'{index:03}'
        content = read_file(path, item['size'])
        total += len(content)
        if total > MAX_BYTES or len(content) != item['size'] or hashlib.sha256(content).hexdigest() != item['sha256']:
            raise ValueError('Input checksum or length changed')
        with Image.open(io.BytesIO(content)) as original:
            if (original.format not in {'PNG', 'JPEG'} or getattr(original, 'n_frames', 1) != 1
                    or original.width * original.height > MAX_PIXELS):
                raise ValueError('Only bounded single-frame PNG/JPEG inputs are supported')
            width, height = original.size
        # This is the unmodified upstream MM-core tool handler, not a replacement
        # Pillow implementation presented as a plugin invocation.
        blocks = handle({'image_path': str(path), 'budget': 'small'})
        if not isinstance(blocks, list) or len(blocks) != 2 or blocks[1].get('type') != 'image':
            raise ValueError('MM-core read_image returned unsupported content')
        encoded = blocks[1].get('data', '')
        if not isinstance(encoded, str) or len(encoded) > 4194304:
            raise ValueError('MM-core image result exceeds its bound')
        rendered = base64.b64decode(encoded, validate=True)
        with Image.open(io.BytesIO(rendered)) as image:
            normalized_width, normalized_height = image.size
        records.append({'artifactId': item['artifactId'], 'sha256': item['sha256'], 'size': item['size'],
            'mediaType': item['mediaType'], 'text': 'MM-core read_image decoded and normalized this image. '
                'This is file inspection, not semantic interpretation or engineering verification.',
            'metadata': {'plugin': 'qwen-mm-plugins-core', 'pluginVersion': '1.3.0', 'tool': 'read_image',
                'width': width, 'height': height, 'normalizedWidth': normalized_width,
                'normalizedHeight': normalized_height, 'normalizedSha256': hashlib.sha256(rendered).hexdigest(),
                'normalizedMediaType': blocks[1]['mimeType'], 'budget': 'small'}})
    return {'records': records}


async def main():
    isolation()
    from mcp import types
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    request = json.loads(read_file(INPUTS / 'request.json', 65536))
    if (request.get('protocol') != 'kleos-mcp-inspection-v1' or request.get('toolName') != 'inspect_artifacts'
            or request.get('serverName') != 'kleos-mm-core-inspector' or request.get('serverVersion') != '1.3.0'
            or type(request.get('timeoutSeconds')) is not int or not 1 <= request['timeoutSeconds'] <= 120):
        raise ValueError('Unsupported MCP session request')
    handler = load_handler()
    schema = json.loads(read_file(ROOT / 'tool-schema.json', 32768))
    server = Server('kleos-mm-core-inspector', version='1.3.0')

    @server.list_tools()
    async def tools():
        return [types.Tool(name='inspect_artifacts', inputSchema=schema)]

    @server.call_tool()
    async def call(name, arguments):
        try:
            if name != 'inspect_artifacts':
                raise ValueError('Unsupported tool')
            result = inspect_images(arguments, request, handler)
            return types.CallToolResult(content=[], structuredContent=result)
        except Exception:
            # Never reveal paths, malformed private bytes or parser exception data.
            return types.CallToolResult(isError=True, content=[types.TextContent(
                type='text', text='MM-core image inspection failed or input is unsupported')])

    async with stdio_server() as (reader, writer):
        await server.run(reader, writer, server.create_initialization_options())


if __name__ == '__main__':
    asyncio.run(main())
