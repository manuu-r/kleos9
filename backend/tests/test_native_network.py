"""Pure filter-policy tests: never install a process filter on the developer host."""
import errno
from types import SimpleNamespace

import pytest

from kleos import native_network as network
from kleos import native_runner as runner


def evaluate(program, arch, number, family=0):
    words, accumulator, index = {0: number & 0xffffffff, 4: arch, 16: family & 0xffffffff}, 0, 0
    while 0 <= index < len(program):
        code, yes, no, value = program[index]
        if code == 0x20:  # BPF_LD | BPF_W | BPF_ABS
            accumulator = words[value]
        elif code == 0x15:  # BPF_JMP | BPF_JEQ | BPF_K
            index += yes if accumulator == value else no
        elif code == 0x35:  # BPF_JMP | BPF_JGE | BPF_K
            index += yes if accumulator >= value else no
        elif code == 0x06:
            return value
        else:
            raise AssertionError('Unexpected filter instruction')
        index += 1
    raise AssertionError('Filter has no terminal decision')


@pytest.mark.parametrize('machine,arch,socket,socketpair', [
    ('x86_64', 0xc000003e, 41, 53), ('aarch64', 0xc00000b7, 198, 199), ('arm64', 0xc00000b7, 198, 199)])
def test_only_unix_socket_families_survive_the_architecture_qualified_filter(machine, arch, socket, socketpair):
    program = network.filter_program(machine)
    for syscall in (socket, socketpair):
        assert evaluate(program, arch, syscall, 1) == network.ALLOW
        for family in (0, 2, 10, 16, 17, 40, 0x7fffffff, -1):
            assert evaluate(program, arch, syscall, family) == network.DENY
    assert evaluate(program, arch, 425) == network.DENY
    assert evaluate(program, arch, 1) == network.ALLOW  # Ordinary file I/O remains available.
    assert evaluate(program, arch ^ 0x40000000, socket, 1) == network.KILL
    assert evaluate(program, arch, 0x40000000 | socket, 1) == network.KILL
    assert evaluate(program, arch, -1, 1) == network.KILL


def test_unknown_architectures_fail_closed():
    with pytest.raises(RuntimeError, match='qualified Linux architecture'):
        network.filter_program('i686')


@pytest.mark.parametrize('machine,arch,socket,socketpair', [
    ('x86_64', 0xc000003e, 41, 53), ('aarch64', 0xc00000b7, 198, 199)])
def test_application_ipc_policy_allows_gradle_families_without_widening_default_or_abi_guards(machine, arch, socket, socketpair):
    application = network.filter_program(machine, application_ipc=True)
    for syscall in (socket, socketpair):
        for family in (1, 2, 10, 16):
            assert evaluate(application, arch, syscall, family) == network.ALLOW
        for family in (0, 17, 40, -1):
            assert evaluate(application, arch, syscall, family) == network.DENY
    assert evaluate(application, arch, 425) == network.DENY
    assert evaluate(application, arch, 0x40000000 | socket, 2) == network.KILL
    assert evaluate(application, arch ^ 0x40000000, socket, 2) == network.KILL
    assert evaluate(network.filter_program(machine), arch, socket, 2) == network.DENY


@pytest.mark.parametrize('lane,purpose,expected', [('application', 'build', True), ('application', 'session', True),
                                                  ('application', 'validate', False), ('mechanics', 'build', False)])
def test_only_application_build_or_session_selects_application_ipc(monkeypatch, lane, purpose, expected):
    monkeypatch.setattr(runner, 'RUNTIME_APPLICATION_IPC', False)
    monkeypatch.setattr(runner, 'RUNTIME_PIDS_LIMIT', 256)
    runner.configure_resources({'lane': lane, 'purpose': purpose})
    assert runner.RUNTIME_APPLICATION_IPC is expected


def test_installer_requests_no_new_privileges_before_the_exact_filter(monkeypatch):
    calls = []
    def prctl(*arguments):
        calls.append(arguments[0])
        if arguments[0] == 22:
            program = arguments[2]._obj
            actual = [(item.code, item.jt, item.jf, item.k) for item in program.filter[:program.len]]
            assert actual == network.filter_program('x86_64')
        return 0
    monkeypatch.setattr(network.sys, 'platform', 'linux')
    monkeypatch.setattr(network.platform, 'machine', lambda: 'x86_64')
    monkeypatch.setattr(network.ctypes, 'CDLL', lambda *args, **kwargs: type('MockLibc', (), {'prctl': staticmethod(prctl)})())
    network.install_network_filter()
    assert calls == [38, 22]


@pytest.mark.parametrize('failure', [38, 22])
def test_filter_installation_failure_never_continues_unrestricted(monkeypatch, failure):
    calls = []
    def prctl(option, *arguments):
        calls.append(option)
        return -1 if option == failure else 0
    monkeypatch.setattr(network.sys, 'platform', 'linux')
    monkeypatch.setattr(network.platform, 'machine', lambda: 'x86_64')
    monkeypatch.setattr(network.ctypes, 'get_errno', lambda: errno.EPERM)
    monkeypatch.setattr(network.ctypes, 'CDLL', lambda *args, **kwargs: type('MockLibc', (), {'prctl': staticmethod(prctl)})())
    with pytest.raises(OSError, match='generated-process'):
        network.install_network_filter()
    assert calls == ([38] if failure == 38 else [38, 22])


def test_installer_refuses_non_linux_without_calling_the_host_kernel(monkeypatch):
    monkeypatch.setattr(network.sys, 'platform', 'darwin')
    monkeypatch.setattr(network.ctypes, 'CDLL', lambda *args, **kwargs: pytest.fail('No kernel calls are allowed'))
    with pytest.raises(RuntimeError, match='isolated Linux'):
        network.install_network_filter()


@pytest.mark.parametrize('application_ipc', [False, True])
def test_native_child_installs_filter_after_dropping_its_identity(monkeypatch, application_ipc):
    events, limits = [], []
    monkeypatch.setattr(runner, 'RUNTIME_APPLICATION_IPC', application_ipc)
    monkeypatch.setattr(runner.resource, 'setrlimit', lambda *args: limits.append(args))
    monkeypatch.setattr(runner.ctypes, 'CDLL', lambda *args, **kwargs: type('MockLibc', (), {'prctl': staticmethod(lambda *args: 0)})())
    monkeypatch.setattr(runner.os, 'setgroups', lambda groups: events.append(('groups', groups)))
    monkeypatch.setattr(runner.os, 'setgid', lambda ident: events.append(('gid', ident)))
    monkeypatch.setattr(runner.os, 'setuid', lambda ident: events.append(('uid', ident)))
    monkeypatch.setattr(runner, 'install_network_filter', lambda **kwargs: events.append(('filter', kwargs.get('application_ipc'))))
    runner.unprivileged_limits(10)()
    assert events == [('groups', []), ('gid', 10001), ('uid', 10001), ('filter', True if application_ipc else None)]
    descriptors = 4096 if application_ipc else 256
    assert (runner.resource.RLIMIT_NOFILE, (descriptors, descriptors)) in limits


@pytest.mark.parametrize('failure,authorized,isolated,allowed', [
    (errno.EINVAL, True, True, True), (errno.ENOSYS, True, True, True),
    (errno.EINVAL, False, True, False), (errno.EINVAL, True, False, False),
    (errno.EPERM, True, True, False), (errno.EACCES, True, True, False)])
def test_docker_emulation_fallback_requires_explicit_authorization_verified_isolation_and_known_error(
        monkeypatch, failure, authorized, isolated, allowed):
    monkeypatch.setattr(network.sys, 'platform', 'linux')
    monkeypatch.setattr(network.platform, 'machine', lambda: 'x86_64')
    monkeypatch.setattr(network.ctypes, 'get_errno', lambda: failure)
    monkeypatch.setattr(network.ctypes, 'CDLL', lambda *args, **kwargs: type('MockLibc', (), {
        'prctl': staticmethod(lambda option, *args: 0 if option == 38 else -1)})())
    monkeypatch.setattr(network, 'isolated_loopback_only', lambda: isolated)
    monkeypatch.setenv('KLEOS_NATIVE_DOCKER_NETWORK_NONE', '1' if authorized else '0')
    if allowed:
        network.install_network_filter()
    else:
        with pytest.raises(OSError):
            network.install_network_filter()


@pytest.mark.parametrize('interfaces,ipv4,ipv6,expected', [
    ({'lo': '0x9'}, 'header\n', 'entry lo\n', True),
    ({'lo': '0x9', 'tunl0': '0x80', 'bonding_masters': None}, 'header\n', 'entry lo\n', True),
    ({'lo': '0x9', 'eth0': '0x1003'}, 'header\n', 'entry lo\n', False),
    ({'lo': '0x8'}, 'header\n', 'entry lo\n', False),
    ({'lo': 'invalid'}, 'header\n', 'entry lo\n', False),
    ({'lo': '0x9'}, 'header\nroute eth0\n', 'entry lo\n', False),
    ({'lo': '0x9'}, 'header\n', 'entry eth0\n', False)])
def test_namespace_fallback_verifies_interfaces_and_both_route_tables(monkeypatch, interfaces, ipv4, ipv6, expected):
    class Interface:
        def __init__(self, name, flags):
            self.name, self.flags = name, flags
        def is_dir(self):
            return self.flags is not None
        def __truediv__(self, name):
            assert name == 'flags'
            return SimpleNamespace(read_text=lambda: self.flags)
    class FixturePath:
        def __init__(self, name):
            self.name = name
        def iterdir(self):
            return [Interface(name, flags) for name, flags in interfaces.items()]
        def read_text(self):
            return ipv4 if self.name == '/proc/net/route' else ipv6
    monkeypatch.setattr(network, 'Path', FixturePath)
    assert network.isolated_loopback_only() is expected


def test_namespace_probe_failure_and_clean_environment_fail_closed(monkeypatch, tmp_path):
    class MissingPath:
        def __init__(self, *args):
            pass
        def iterdir(self):
            raise PermissionError('unavailable')
    monkeypatch.setattr(network, 'Path', MissingPath)
    assert network.isolated_loopback_only() is False
    monkeypatch.setenv('KLEOS_NATIVE_DOCKER_NETWORK_NONE', '1')
    assert 'KLEOS_NATIVE_DOCKER_NETWORK_NONE' not in runner.clean_environment(tmp_path)


def test_privilege_restriction_failure_cannot_use_emulation_fallback(monkeypatch):
    monkeypatch.setattr(network.sys, 'platform', 'linux')
    monkeypatch.setattr(network.platform, 'machine', lambda: 'x86_64')
    monkeypatch.setattr(network.ctypes, 'get_errno', lambda: errno.EINVAL)
    monkeypatch.setattr(network.ctypes, 'CDLL', lambda *args, **kwargs: type('MockLibc', (), {
        'prctl': staticmethod(lambda *args: -1)})())
    monkeypatch.setattr(network, 'isolated_loopback_only', lambda: True)
    monkeypatch.setenv('KLEOS_NATIVE_DOCKER_NETWORK_NONE', '1')
    with pytest.raises(OSError, match='privileges'):
        network.install_network_filter()
