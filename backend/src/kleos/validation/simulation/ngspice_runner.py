"""Image entry point: run exactly one platform-compiled deck and print one JSON document.

Runs as an unprivileged user in a read-only container with no network. The deck is read from the
read-only `/input/deck.cir`; nothing else from the control plane is visible. Standard library only.
"""
import json
import os
import resource
import subprocess
import sys
from pathlib import Path

PROTOCOL = 'kleos-circuit-sim-v1'
MAX_DECK, MAX_OUTPUT = 64 * 1024, 2_000_000
WORK = Path('/tmp/work')


def limits():
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_OUTPUT + 4096, MAX_OUTPUT + 4096))
    resource.setrlimit(resource.RLIMIT_CPU, (20, 20))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def read_output(name):
    path = WORK / name
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_OUTPUT:
        raise SystemExit(f'{name} is missing, linked or oversized')
    return path.read_text()


def main():
    deck = Path('/input/deck.cir')
    if deck.is_symlink() or not deck.is_file() or deck.stat().st_size > MAX_DECK:
        raise SystemExit('deck is missing, linked or oversized')
    WORK.mkdir(mode=0o700, exist_ok=True)
    (WORK / 'deck.cir').write_bytes(deck.read_bytes())
    done = subprocess.run(['/usr/bin/ngspice', '-b', '-o', 'log.txt', 'deck.cir'], cwd=WORK, capture_output=True,
        text=True, timeout=15, preexec_fn=limits, env={'PATH': '/usr/bin:/bin', 'HOME': '/tmp', 'LANG': 'C'}, check=False)
    if done.returncode != 0:
        raise SystemExit('solver exited with status ' + str(done.returncode))
    banner = next((line for line in (done.stdout + done.stderr).splitlines() if 'ngspice-' in line), '')
    json.dump({'protocol': PROTOCOL, 'banner': banner[:200], 'op': read_output('op.txt'),
               'tran': read_output('tran.txt')}, sys.stdout)


if __name__ == '__main__':
    os.umask(0o077)
    main()
