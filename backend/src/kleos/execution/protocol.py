"""Small serializable supervisor protocol, independent of control-plane services."""
import hashlib
import json
import re
from pathlib import PurePosixPath

PROTOCOL = 'kleos-native-session-v2'
ACTIONS = frozenset({'read', 'patch', 'build', 'analyze', 'render', 'finish'})
LANES = frozenset({'cad', 'pcb', 'firmware', 'application'})
# Wall-clock ceiling for one native job; model latency between its actions counts against it.
NATIVE_MAX_SESSION_SECONDS = 4 * 3600
MAX_FILE = 32 * 1024 * 1024
MAX_SOURCES = 16 * 1024 * 1024
MAX_RESULT = 64 * 1024 * 1024
# Starter project the control plane seeds for a PCB job; the supervisor recognizes it by version and hash.
PCB_SCAFFOLD_VERSION = 'pcb-contract-scaffold-v1'
MAX_FINDINGS = 50


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def fingerprint(value) -> str:
    return digest(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode())


def identity(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}', value):
        raise ValueError('Invalid bounded action identity')
    return value


def source_path(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 240 or '\\' in value:
        raise ValueError('Invalid source path')
    parts = value.split('/')
    if PurePosixPath(value).is_absolute() or any(not p or p.startswith('.') or any(ord(c) < 32 for c in p) for p in parts):
        raise ValueError('Unsafe source path')
    if any(p in {'', '.', '..'} for p in parts) or parts[0] in {'outputs', 'control', 'build'}:
        raise ValueError('Reserved source path')
    return value


def output_path(value):
    if not isinstance(value, str) or not value.startswith('outputs/'):
        raise ValueError('Output must be in its fixed output directory')
    source_path(value.removeprefix('outputs/'))
    return value


def action_request(value):
    if not isinstance(value, dict) or set(value) - {'actionId', 'action', 'arguments'}:
        raise ValueError('Invalid native action envelope')
    identity(value.get('actionId'))
    if value.get('action') not in ACTIONS or not isinstance(value.get('arguments', {}), dict):
        raise ValueError('Unsupported native action')
    arguments = value.get('arguments', {})
    allowed = {'read': {'path', 'offset', 'maxBytes'}, 'patch': {'path', 'content', 'expectedSha256'},
               'build': set(), 'analyze': set(), 'render': {'view'}, 'finish': set()}[value['action']]
    if set(arguments) - allowed:
        raise ValueError('Native action contains unsupported arguments')
    if value['action'] == 'render' and arguments.get('view', 'isometric') not in {'front', 'top', 'isometric'}:
        raise ValueError('Unsupported render view')
    if len(json.dumps(arguments)) > 1500000:
        raise ValueError('Native action exceeds its byte bound')
    return {'actionId': value['actionId'], 'action': value['action'], 'arguments': arguments}
