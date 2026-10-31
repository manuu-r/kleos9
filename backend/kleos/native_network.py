"""Irreversible per-process network restriction for generated Linux code.

No NET_ADMIN capability, task credentials or mutable firewall executable needed.
The supervisor retains networking for scoped callbacks; all generated descendants
inherit this additional seccomp filter after dropping to the generated-code UID.
"""
import ctypes
import errno
import os
import platform
import sys
from pathlib import Path

ALLOW = 0x7fff0000
KILL = 0x80000000
DENY = 0x00050000 | errno.EPERM


def isolated_loopback_only():
    """Verify the explicit local fallback has no interface or route off-container."""
    try:
        # Docker's kernel can expose dormant tunnel interfaces in a network-none
        # namespace. Only IFF_UP interfaces can transmit; generated code has no
        # NET_ADMIN capability to activate them. Ignore non-interface sysfs files.
        interfaces = [p for p in Path('/sys/class/net').iterdir() if p.is_dir()]
        active = {p.name for p in interfaces if int((p / 'flags').read_text().strip(), 16) & 1}
        if active != {'lo'}:
            return False
        if any(line.strip() for line in Path('/proc/net/route').read_text().splitlines()[1:]):
            return False
        return all(not line.strip() or line.split()[-1] == 'lo'
                   for line in Path('/proc/net/ipv6_route').read_text().splitlines())
    except (OSError, ValueError):
        return False


def filter_program(machine, application_ipc=False):
    if machine == 'x86_64':
        arch, socket, socketpair = 0xc000003e, 41, 53
    elif machine in {'aarch64', 'arm64'}:
        arch, socket, socketpair = 0xc00000b7, 198, 199
    else:
        raise RuntimeError('Native network isolation requires a qualified Linux architecture')
    # Gradle requires IP loopback/lock IPC and interface enumeration. An application
    # task permits IP/netlink families under its no-NAT VPC/DNS/endpoint policy.
    # This filter does NOT claim to inspect sockaddr destinations. Other workers
    # allow only AF_UNIX. Neither policy grants network administration capability.
    # struct seccomp_data: nr@0, arch@4, args[0]@16. Reject a different ABI
    # (including x32) before interpreting syscall numbers or socket families.
    families = [1, 2, 10, 16] if application_ipc else [1]
    deny_index, allow_index = 11 + len(families), 12 + len(families)
    return [
        (0x20, 0, 0, 4), (0x15, 1, 0, arch), (0x06, 0, 0, KILL),
        (0x20, 0, 0, 0), (0x35, 0, 1, 0x40000000), (0x06, 0, 0, KILL),
        (0x15, 3, 0, socket), (0x15, 2, 0, socketpair),
        (0x15, deny_index - 9, 0, 425),  # No io_uring socket-filter bypass.
        (0x06, 0, 0, ALLOW), (0x20, 0, 0, 16),
        *[(0x15, allow_index - (12 + index), 0, family) for index, family in enumerate(families)],
        (0x06, 0, 0, DENY), (0x06, 0, 0, ALLOW),
    ]


def install_network_filter(application_ipc=False):
    if sys.platform != 'linux':
        raise RuntimeError('Native network filters may only be installed in isolated Linux jobs')

    class Instruction(ctypes.Structure):
        _fields_ = [('code', ctypes.c_ushort), ('jt', ctypes.c_ubyte), ('jf', ctypes.c_ubyte), ('k', ctypes.c_uint32)]

    class Program(ctypes.Structure):
        _fields_ = [('len', ctypes.c_ushort), ('filter', ctypes.POINTER(Instruction))]

    instructions = filter_program(platform.machine(), application_ipc=application_ipc)
    array = (Instruction * len(instructions))(*(Instruction(*item) for item in instructions))
    program = Program(len(instructions), array)
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(38, 1, 0, 0, 0):
        raise OSError(ctypes.get_errno(), 'Could not restrict generated-process privileges')
    if libc.prctl(22, 2, ctypes.byref(program), 0, 0):
        failure = ctypes.get_errno()
        # Docker Desktop's amd64 emulator cannot install any seccomp filter.
        # Its explicit local runner already disables networking at the namespace
        # boundary. Verify that stronger off-container isolation before allowing
        # this compatibility path. ECS never receives this environment flag.
        if (failure in {errno.EINVAL, errno.ENOSYS}
                and os.environ.get('KLEOS_NATIVE_DOCKER_NETWORK_NONE') == '1'
                and isolated_loopback_only()):
            return
        raise OSError(ctypes.get_errno(), 'Could not install generated-process network isolation')
