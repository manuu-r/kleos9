"""Pinned Docker baseline plus stricter Unix-only socket rules for local emulation."""
import json
from pathlib import Path

from kleos.execution.protocol import digest

BASELINE_SHA256 = '6416b47770785a41ac59073cdc77d9fe98517df2799dc83ef207e622de3053f6'


def daemon_socket_profile():
    raw = Path(__file__).with_name('security').joinpath('docker-default-seccomp.json').read_bytes()
    if digest(raw) != BASELINE_SHA256:
        raise RuntimeError('Pinned Docker syscall baseline changed')
    profile = json.loads(raw)
    rules = []
    for rule in profile['syscalls']:
        names = [name for name in rule['names'] if name not in {'socket', 'socketpair', 'socketcall'}]
        if names:
            rules.append({**rule, 'names': names})
    rules.append({'names': ['socket', 'socketpair'], 'action': 'SCMP_ACT_ALLOW',
                  'args': [{'index': 0, 'value': 1, 'op': 'SCMP_CMP_EQ'}]})
    profile['syscalls'] = rules
    return profile
