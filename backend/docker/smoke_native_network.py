"""Real Linux per-generated-UID network restriction and offline build qualification."""
import base64
import hashlib
import json
import os
import platform
import socket
import sys
import tempfile
from pathlib import Path

from kleos.native_network import filter_program, isolated_loopback_only
from kleos.native_runner import run_command, run_job

GENERATED = r'''
import ctypes,errno,json,os,socket,subprocess,sys
assert os.geteuid()==10001
assert 'KLEOS_NATIVE_DOCKER_NETWORK_NONE' not in os.environ
results={}
for label,family,kind in [('ipv4',socket.AF_INET,socket.SOCK_STREAM),('ipv6',socket.AF_INET6,socket.SOCK_STREAM),
                           ('netlink',socket.AF_NETLINK,socket.SOCK_RAW),('unspecified',socket.AF_UNSPEC,socket.SOCK_STREAM)]:
    try:
        socket.socket(family,kind)
    except OSError as exc:
        assert exc.errno==errno.EPERM,(label,exc.errno)
        results[label]='EPERM'
    else:
        raise AssertionError('Generated code created forbidden '+label+' socket')
for family in [socket.AF_INET,socket.AF_INET6,socket.AF_NETLINK]:
    try:
        socket.socketpair(family,socket.SOCK_STREAM)
    except OSError as exc:
        assert exc.errno==errno.EPERM,('socketpair',family,exc.errno)
    else:
        raise AssertionError('Generated code created forbidden socketpair')
a,b=socket.socketpair(socket.AF_UNIX,socket.SOCK_STREAM)
a.sendall(b'compiler-ipc'); assert b.recv(12)==b'compiler-ipc'; a.close(); b.close()
libc=ctypes.CDLL(None,use_errno=True)
buffer=ctypes.create_string_buffer(256)
assert libc.syscall(425,1,ctypes.byref(buffer))==-1 and ctypes.get_errno()==errno.EPERM
child='import errno,socket;\ntry: socket.socket(socket.AF_INET,socket.SOCK_STREAM)\nexcept OSError as e: assert e.errno==errno.EPERM; print("inherited")\nelse: raise AssertionError("exec bypass")'
completed=subprocess.run([sys.executable,'-I','-c',child],capture_output=True,text=True,check=True)
assert completed.stdout.strip()=='inherited'
print(json.dumps({'uid':os.geteuid(),'forbiddenFamilies':results,'nonUnixSocketpair':'EPERM',
                  'unixSocketpair':'send_recv_passed','ioUringSetup':'EPERM','execChildInherited':True}))
'''

NAMESPACE_GENERATED = r'''
import ctypes,errno,json,os,socket,subprocess,sys
from pathlib import Path
assert os.geteuid()==10001 and 'KLEOS_NATIVE_DOCKER_NETWORK_NONE' not in os.environ
interfaces=[path for path in Path('/sys/class/net').iterdir() if path.is_dir()]
active={path.name for path in interfaces if int((path/'flags').read_text().strip(),16)&1}
assert active=={'lo'}
assert not any(line.strip() for line in Path('/proc/net/route').read_text().splitlines()[1:])
assert all(line.split()[-1]=='lo' for line in Path('/proc/net/ipv6_route').read_text().splitlines() if line.strip())
capabilities=next(line.split()[1] for line in Path('/proc/self/status').read_text().splitlines() if line.startswith('CapEff:'))
assert int(capabilities,16)==0
results={}
for label,family,address in [('ipv4',socket.AF_INET,('203.0.113.1',443)),('ipv6',socket.AF_INET6,('2001:db8::1',443))]:
    with socket.socket(family,socket.SOCK_STREAM) as sock:
        sock.settimeout(1)
        try: sock.connect(address)
        except OSError as exc:
            assert exc.errno in (errno.ENETUNREACH,errno.EHOSTUNREACH),(label,exc.errno)
            results[label]='no_route'
        else: raise AssertionError('Off-container route exists')
a,b=socket.socketpair(socket.AF_UNIX); a.sendall(b'ipc'); assert b.recv(3)==b'ipc'; a.close(); b.close()
libc=ctypes.CDLL(None,use_errno=True)
assert libc.unshare(0x40000000)==-1 and ctypes.get_errno()==errno.EPERM
child='import errno,socket; s=socket.socket();\ntry: s.connect(("203.0.113.1",443))\nexcept OSError as e: assert e.errno in (errno.ENETUNREACH,errno.EHOSTUNREACH); print("isolated")\nelse: raise AssertionError("route bypass")'
assert subprocess.run([sys.executable,'-I','-c',child],check=True,capture_output=True,text=True).stdout.strip()=='isolated'
print(json.dumps({'uid':os.geteuid(),'offContainer':results,'unixSocketpair':'send_recv_passed',
                  'effectiveCapabilities':0,'networkNamespaceEscape':'EPERM','execChildInherited':True}))
'''

APPLICATION_IPC_GENERATED = r'''
import ctypes,errno,json,os,socket,subprocess,sys
assert os.geteuid()==10001 and 'KLEOS_NATIVE_DOCKER_NETWORK_NONE' not in os.environ
with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as server:
    server.bind(('127.0.0.1',0)); server.listen(1)
    with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as client:
        client.connect(server.getsockname())
        peer,_=server.accept()
        with peer:
            client.sendall(b'gradle-tcp'); assert peer.recv(10)==b'gradle-tcp'
with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as receiver:
    receiver.bind(('127.0.0.1',0))
    with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sender:
        sender.sendto(b'gradle-udp',receiver.getsockname()); assert receiver.recv(10)==b'gradle-udp'
with socket.socket(socket.AF_INET6,socket.SOCK_DGRAM) as ipv6:
    ipv6.bind(('::1',0))
with socket.socket(socket.AF_NETLINK,socket.SOCK_RAW,0) as netlink:
    netlink.bind((0,0)); assert netlink.getsockname()[0]>0
interfaces=[name for _,name in socket.if_nameindex()]; assert 'lo' in interfaces
try: socket.socket(socket.AF_UNSPEC,socket.SOCK_STREAM)
except OSError as exc: assert exc.errno==errno.EPERM
else: raise AssertionError('Application policy did not deny unsupported family')
libc=ctypes.CDLL(None,use_errno=True); buffer=ctypes.create_string_buffer(256)
assert libc.syscall(425,1,ctypes.byref(buffer))==-1 and ctypes.get_errno()==errno.EPERM
child='import socket; s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); s.bind(("127.0.0.1",0)); print("ipc-inherited")'
assert subprocess.run([sys.executable,'-I','-c',child],capture_output=True,text=True,check=True).stdout.strip()=='ipc-inherited'
print(json.dumps({'uid':os.geteuid(),'tcpLoopback':'passed','udpLoopback':'passed','ipv6Bind':'passed',
                  'netlinkBind':'passed','interfaceEnumeration':interfaces,'unsupportedFamily':'EPERM',
                  'ioUringSetup':'EPERM','execChildInherited':True}))
'''


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def qualify_network():
    assert sys.platform == 'linux' and os.geteuid() == 0
    # Network-none still permits socket creation, proving failures below come
    # from the generated process filter rather than an absent network route.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM):
        supervisor_allowed = True
    with tempfile.TemporaryDirectory(prefix='native-network-proof-') as temporary:
        directory = Path(temporary)
        directory.chmod(0o755)
        probe = '''import errno,socket\ntry: socket.socket(socket.AF_INET,socket.SOCK_STREAM); print('namespace')
except OSError as e:\n assert e.errno==errno.EPERM; print('seccomp')'''
        code, logs = run_command(['python', '-I', '-c', probe], directory, 10)
        assert code == 0, (code, logs)
        isolation = logs.strip().splitlines()[-1]
        assert isolation == 'seccomp' or (os.environ.get('KLEOS_NATIVE_DOCKER_NETWORK_NONE') == '1'
                                           and isolated_loopback_only()), isolation
        code, logs = run_command(['python', '-I', '-c', GENERATED if isolation == 'seccomp' else NAMESPACE_GENERATED], directory, 30)
        assert code == 0, (code, logs)
        generated = json.loads(logs.strip().splitlines()[-1])
        # A wrong syscall ABI must terminate only a disposable generated child.
        x32_code = None
        if isolation == 'seccomp':
            x32_code, x32_logs = run_command(['python', '-I', '-c',
                'import ctypes; ctypes.CDLL(None).syscall(0x40000000 | 41, 1, 1, 0)'], directory, 10)
            assert x32_code in (-9, -31, 137, 159), (x32_code, x32_logs)
    return {'status': 'passed', 'supervisorIpv4SocketAllowed': supervisor_allowed,
            'isolationMode': isolation, 'generated': generated,
            'x32BypassTerminated': isolation == 'seccomp', 'x32ReturnCode': x32_code,
            'machine': platform.machine(), 'filterProgramSha256': sha(json.dumps(filter_program(platform.machine())).encode())}


def qualify_build(lane):
    template = Path('/fixtures/watch-telemetry-v1') / lane
    artifacts = []
    for path in template.rglob('*'):
        if path.is_file():
            raw = path.read_bytes()
            artifacts.append({'id': str(path.relative_to(template)), 'name': str(path.relative_to(template)), 'lane': lane,
                              'sha256': sha(raw), 'contentBase64': base64.b64encode(raw).decode()})
    assert artifacts, 'Mount checked-in templates at /fixtures'
    with tempfile.TemporaryDirectory(prefix='native-network-build-') as temporary:
        directory = Path(temporary)
        directory.chmod(0o755)
        result = run_job({'schemaVersion': 1, 'purpose': 'build', 'lane': lane, 'inputArtifacts': artifacts,
                          'timeoutSeconds': 240, 'interfaces': {'firmware': {'board': 'nrf52840dk/nrf52840'}}}, directory)
        assert result.status == 'succeeded', result.model_dump(mode='json', exclude={'files'})
        assert any(file.path.endswith('.elf' if lane == 'firmware' else 'companion-web.zip') for file in result.files)
        return {'status': 'passed', 'lane': lane, 'outputs': [{'path': file.path,
                 'sha256': sha(base64.b64decode(file.content))} for file in result.files]}


def qualify_application_ipc():
    from kleos import native_runner
    assert os.environ.get('KLEOS_NATIVE_DOCKER_NETWORK_NONE') is None, 'Actual application seccomp proof requires no fallback authorization'
    native_runner.configure_resources({'lane': 'application', 'purpose': 'build'})
    try:
        with tempfile.TemporaryDirectory(prefix='application-ipc-proof-') as temporary:
            directory = Path(temporary)
            directory.chmod(0o755)
            code, logs = run_command(['python', '-I', '-c', APPLICATION_IPC_GENERATED], directory, 30)
            assert code == 0, (code, logs)
            proof = json.loads(logs.strip().splitlines()[-1])
            terminated, logs = run_command(['python', '-I', '-c',
                'import ctypes; ctypes.CDLL(None).syscall(0x40000000 | 41, 2, 1, 0)'], directory, 10)
            assert terminated in (-9, -31, 137, 159), (terminated, logs)
        return {'status': 'passed', 'isolationMode': 'application_seccomp', 'generated': proof,
                'x32BypassTerminated': True, 'x32ReturnCode': terminated,
                'destinationScope': 'Families are allowed; this filter does not restrict sockaddr destinations. This fixture uses a network-none task namespace.',
                'machine': platform.machine(), 'filterProgramSha256': sha(json.dumps(filter_program(platform.machine(), application_ipc=True)).encode())}
    finally:
        native_runner.configure_resources({'lane': 'mechanics', 'purpose': 'build'})


mode = sys.argv[1] if len(sys.argv) > 1 else 'network'
proof = qualify_network() if mode == 'network' else qualify_application_ipc() if mode == 'application_ipc' else qualify_build(mode)
print(json.dumps({'modelCalls': 0, 'proof': proof, 'sourceHashes': {
    name: sha((Path('/usr/local/lib/python3.12/site-packages/kleos') / name).read_bytes())
    for name in ('native_network.py', 'native_runner.py')}, 'fixtureSha256': sha(Path(__file__).read_bytes())}))
