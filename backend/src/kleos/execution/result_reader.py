"""Fixed root-only result transfer. No agent-selected paths or executable arguments."""
import os
import re
import stat
import sys

MAX_BYTES = 64 * 1024 * 1024


def main():
    if len(sys.argv) != 2 or not re.fullmatch(r'[a-f0-9]{64}\.json', sys.argv[1]):
        raise SystemExit(45)
    try:
        descriptor = os.open('/run/kleos-control/results/' + sys.argv[1], os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        raise SystemExit(44) from None
    with os.fdopen(descriptor, 'rb') as result:
        metadata = os.fstat(result.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or metadata.st_size > MAX_BYTES:
            raise SystemExit(45)
        content = result.read(MAX_BYTES + 1)
        if len(content) > MAX_BYTES:
            raise SystemExit(45)
        sys.stdout.buffer.write(content)


if __name__ == '__main__':
    main()
