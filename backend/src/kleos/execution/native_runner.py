"""New credential-free native supervisor. Never execute generated code on the host."""
import base64
import ctypes
import errno
import json
import os
import platform
import resource
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from uuid import uuid4

from kleos.execution.pcb_contract import circuit_contract
from kleos.execution.protocol import (
    LANES,
    MAX_FILE,
    MAX_RESULT,
    MAX_SOURCES,
    PROTOCOL,
    action_request,
    digest,
    fingerprint,
    output_path,
    source_path,
)

UID = 10001
CAD_BUILD = '''import json,runpy,re,sys
from pathlib import Path
from build123d import export_step,export_stl
sys.path.insert(0,'.')
v=runpy.run_path('model.py')
parts=v.get('parts')
if not isinstance(parts,dict) or not parts: raise ValueError('Define a nonempty parts mapping with stable physical IDs')
if len(parts)>24: raise ValueError('Too many manufactured parts')
Path('outputs').mkdir(exist_ok=True)
manifest=[]
for ident,shape in parts.items():
 if not isinstance(ident,str) or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_-]{0,63}',ident): raise ValueError('Invalid part ID')
 export_step(shape,'outputs/'+ident+'.step'); export_stl(shape,'outputs/'+ident+'.stl')
 manifest.append({'id':ident,'step':ident+'.step','stl':ident+'.stl'})
Path('outputs/parts.json').write_text(json.dumps({'parts':manifest}))
'''
CAD_ANALYZE = '''import json
from pathlib import Path
from build123d import import_step
parts=[]
for p in sorted(Path('outputs').glob('*.step')):
 s=import_step(str(p)); b=s.bounding_box()
 parts.append({'id':p.stem,'volumeMm3':s.volume,'sizeMm':[b.size.X,b.size.Y,b.size.Z],'valid':s.is_valid})
print(json.dumps({'parts':parts}))
'''
CAD_RENDER = '''from pathlib import Path
from build123d import import_step
import json,sys
parts=[]
for p in sorted(Path('outputs').glob('*.step')):
 s=import_step(str(p)); vertices,triangles=s.tessellate(0.5)
 if len(triangles)>30000: raise ValueError('Render mesh exceeds bound')
 view=sys.argv[1]
 pts=[(v.X,-v.Z) if view=='front' else (v.X,-v.Y) if view=='top' else (v.X-v.Y*.65, -v.Z+(v.X+v.Y)*.35) for v in vertices]
 if not pts: raise ValueError('Empty mesh')
 lo=[min(x[i] for x in pts) for i in (0,1)]; hi=[max(x[i] for x in pts) for i in (0,1)]
 scale=min(460/max(hi[0]-lo[0],.01),340/max(hi[1]-lo[1],.01))
 polys=[]
 for tri in triangles:
  coords=' '.join(f'{(pts[j][0]-lo[0])*scale+26:.2f},{(pts[j][1]-lo[1])*scale+22:.2f}' for j in tri)
  polys.append(f'<polygon points="{coords}" fill="#a7b7c7" stroke="#506078" stroke-width="0.3"/>')
 Path('outputs/'+p.stem+'.svg').write_text('<svg xmlns="http://www.w3.org/2000/svg" width="512" height="384">'+''.join(polys)+'</svg>')
 parts.append(p.stem)
print(json.dumps({'renderedParts':parts,'projection':sys.argv[1],'illustrative':True}))
'''


def safe_file(root: Path, path: str, *, exists=True, max_size=MAX_FILE):
    target = root / path
    current = root
    for part in Path(path).parts:
        current /= part
        if current.is_symlink():
            raise ValueError('Symlink is not allowed in a native artifact')
    if exists:
        info = target.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > max_size:
            raise ValueError('Unsafe or oversized native file')
    return target


def atomic_json(path, value):
    raw = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    if len(raw) > MAX_RESULT:
        raise ValueError('Native result exceeds transfer bound')
    temporary = path.with_name(path.name + '.' + uuid4().hex + '.tmp')
    temporary.write_bytes(raw)
    temporary.chmod(0o600)
    temporary.replace(path)


def clean_environment(work, application=False):
    private = work / '.runtime'
    values = {'PATH': '/opt/venv/bin:/opt/java17/bin:/usr/local/bin:/usr/bin:/bin', 'HOME': str(work / '.home'),
              'TMPDIR': str(work / '.tmp'), 'LANG': 'C.UTF-8', 'CI': 'true', 'PYTHONDONTWRITEBYTECODE': '1',
              'OPENBLAS_NUM_THREADS': '1', 'OMP_NUM_THREADS': '1', 'MAKEFLAGS': '-j2',
              'CMAKE_BUILD_PARALLEL_LEVEL': '2', 'FLUTTER_SUPPRESS_ANALYTICS': 'true',
              'DART_SUPPRESS_ANALYTICS': 'true', 'ZEPHYR_BASE': '/opt/zephyrproject/zephyr',
              'ZEPHYR_TOOLCHAIN_VARIANT': 'gnuarmemb', 'GNUARMEMB_TOOLCHAIN_PATH': '/usr'}
    if application:
        values.update(PATH=str(private / 'flutter/bin') + ':' + values['PATH'],
            PUB_CACHE=str(private / 'pub-cache'), GRADLE_USER_HOME=str(private / 'gradle-cache'),
            JAVA_HOME='/opt/java17', ANDROID_HOME='/opt/android-sdk', ANDROID_SDK_ROOT='/opt/android-sdk',
            JAVA_TOOL_OPTIONS='-XX:ActiveProcessorCount=2 -Djava.net.preferIPv4Stack=true')
    return values


def verify_no_network(net_root=Path('/sys/class/net'), routes_file=Path('/proc/net/route'),
                      ipv6_file=Path('/proc/net/ipv6_route')):
    # Docker --network=none is mandatory, verified again inside the namespace.
    for entry in net_root.iterdir():
        if entry.is_dir() and entry.name != 'lo' and (entry / 'operstate').read_text().strip() not in {'down', 'notpresent'}:
            raise RuntimeError('Native namespace has an active external network interface')
    routes = routes_file.read_text().splitlines()[1:]
    if any(line.split()[0] != 'lo' for line in routes if line.split()):
        raise RuntimeError('Native namespace contains an external route')
    try:
        ipv6 = ipv6_file.read_text().splitlines()
    except FileNotFoundError:
        ipv6 = []  # A kernel without IPv6 cannot hold an IPv6 route; any other read failure still fails closed.
    if any(line.split()[-1] != 'lo' for line in ipv6 if line.split()):
        raise RuntimeError('Native namespace contains an external IPv6 route')


def verify_daemon_sockets():
    status = Path('/proc/self/status').read_text()
    if 'Seccomp:\t2' not in status:
        raise RuntimeError('Docker Desktop daemon syscall filtering is missing')
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM):
        pass
    for family in (socket.AF_INET, socket.AF_INET6, socket.AF_NETLINK, socket.AF_PACKET):
        try:
            blocked = socket.socket(family, socket.SOCK_DGRAM)
        except OSError as error:
            if error.errno != errno.EPERM:
                raise RuntimeError('Docker Desktop socket filter cannot be verified') from error
        else:
            blocked.close()
            raise RuntimeError('Docker Desktop socket filter is not enforced')


def restrict_sockets():
    # Apply before dropping privileges; descendants inherit this additional filter.
    # AF_UNIX is sufficient for local tool IPC. IP sockets fail even on loopback.
    library = ctypes.CDLL('libseccomp.so.2', use_errno=True)
    library.seccomp_init.argtypes = [ctypes.c_uint32]
    library.seccomp_init.restype = ctypes.c_void_p
    library.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    library.seccomp_syscall_resolve_name.restype = ctypes.c_int
    library.seccomp_load.argtypes = [ctypes.c_void_p]
    library.seccomp_release.argtypes = [ctypes.c_void_p]
    class Comparison(ctypes.Structure):
        _fields_ = [('arg', ctypes.c_uint), ('op', ctypes.c_int),
                    ('datum_a', ctypes.c_uint64), ('datum_b', ctypes.c_uint64)]
    library.seccomp_rule_add_array.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int,
                                              ctypes.c_uint, ctypes.POINTER(Comparison)]
    context = library.seccomp_init(0x7fff0000)
    if not context:
        raise RuntimeError('Cannot initialize native socket restrictions')
    try:
        comparison = Comparison(0, 1, 1, 0)  # SCMP_CMP_NE, AF_UNIX.
        for name in (b'socket', b'socketpair'):
            syscall = library.seccomp_syscall_resolve_name(name)
            if syscall < 0 or library.seccomp_rule_add_array(context, 0x50001, syscall, 1,
                                                            ctypes.byref(comparison)) != 0:
                raise RuntimeError('Cannot install native socket restrictions')
        if library.seccomp_load(context) != 0:
            raise RuntimeError('Cannot enforce native socket restrictions')
    finally:
        library.seccomp_release(context)


def reap_generated_processes():
    # Also stop double-forked/setsid children before inspecting their writable files.
    for _ in range(8):
        # As container PID 1, reap orphaned grandchildren after killing their parents.
        while True:
            try:
                if os.waitpid(-1, os.WNOHANG)[0] == 0:
                    break
            except ChildProcessError:
                break
        found = []
        for path in Path('/proc').iterdir():
            if not path.name.isdecimal():
                continue
            try:
                if path.stat().st_uid == UID:
                    os.kill(int(path.name), signal.SIGSTOP)
                    found.append(int(path.name))
            except (ProcessLookupError, FileNotFoundError):
                pass
        if not found:
            return
        for pid in found:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        time.sleep(.02)
    raise RuntimeError('Generated processes did not terminate')


def restrict(timeout, limits, application):
    def setup():
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        resource.setrlimit(resource.RLIMIT_FSIZE, (256 * 1024**2, 256 * 1024**2))
        resource.setrlimit(resource.RLIMIT_NOFILE, (4096, 4096) if application else (256, 256))
        resource.setrlimit(resource.RLIMIT_NPROC, (limits['pids'], limits['pids']))
        resource.setrlimit(resource.RLIMIT_CPU, (max(1, timeout), max(1, timeout) + 1))
        if ctypes.CDLL(None, use_errno=True).prctl(38, 1, 0, 0, 0) != 0:
            raise RuntimeError('Cannot prevent privilege escalation')
        if os.environ.get("KLEOS_NATIVE_DOCKER_DESKTOP") == "1":
            verify_daemon_sockets()
        else:
            restrict_sockets()
        os.setgroups([])
        os.setgid(UID)
        os.setuid(UID)
    return setup


def scratch_size(work):
    """Observe regular-file bytes while build tools create and remove temporary files.

    Deleted entries no longer contribute. Other inspection failures remain errors;
    the container's hard scratch allocation independently bounds unlinked open files.
    """
    total = 0
    for path in work.rglob('*'):
        try:
            info = path.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISREG(info.st_mode):
            total += info.st_size
    return total


def run_fixed(command, work, timeout, limits, application=False):
    if sys.platform != 'linux' or os.geteuid() != 0 or os.environ.get('KLEOS_NATIVE_SANDBOX') != '1':
        raise RuntimeError('Native commands require the isolated Linux supervisor')
    verify_no_network()
    for path in [work, *work.rglob('*')]:
        if not path.is_symlink():
            os.chown(path, UID, UID)
    with tempfile.TemporaryFile() as logs:
        process = subprocess.Popen(command, cwd=work, env=clean_environment(work, application),
            stdin=subprocess.DEVNULL, stdout=logs, stderr=subprocess.STDOUT, start_new_session=True,
            preexec_fn=restrict(timeout, limits, application))
        deadline = time.monotonic() + timeout
        try:
            while process.poll() is None:
                if time.monotonic() >= deadline:
                    raise TimeoutError('Native action deadline exceeded')
                if logs.tell() > 1024 * 1024:
                    raise RuntimeError('Native logs exceed bound')
                size = scratch_size(work)
                if size > limits['scratchBytes']:
                    raise RuntimeError('Native scratch exceeds bound')
                time.sleep(.5)
            if logs.tell() > 1024 * 1024:
                raise RuntimeError('Native logs exceed bound')
            if scratch_size(work) > limits['scratchBytes']:
                raise RuntimeError('Native scratch exceeds bound')
            logs.seek(max(0, logs.tell() - 64000))
            output = logs.read(64000).decode(errors='replace')
            if process.returncode:
                raise RuntimeError(f'Native command failed ({process.returncode}): {output}')
            return output
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            reap_generated_processes()


class NativeSession:
    """Private journal outside generated writable paths; failed observations never accept work."""
    def __init__(self, request, control: Path, work: Path, execute=run_fixed):
        if request.get('protocol') != PROTOCOL or request.get('lane') not in LANES:
            raise ValueError('Unsupported native protocol or lane')
        self.request, self.control, self.work, self.execute = request, control, work, execute
        self.control.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.control.chmod(0o700)
        self.work.mkdir(mode=0o755, parents=True, exist_ok=True)
        self.state_path = control / 'state.json'
        if self.state_path.exists():
            self.state = json.loads(self.state_path.read_bytes())
            if any(not (work / p).is_file() for p in self.state['sources']):
                raise RuntimeError('Interrupted native session lost its working directory')
            if self.state['requestHash'] != fingerprint(request):
                raise ValueError('Native session input changed')
        else:
            self.state = {'requestHash': fingerprint(request), 'sources': {}, 'outputs': [], 'built': False,
                          'finished': False, 'actions': {}, 'deadline': request['deadline']}
            if len(request['sources']) > 50:
                raise ValueError('Source count exceeds bound')
            for item in request['sources']:
                path = source_path(item['path'])
                content = base64.b64decode(item['contentBase64'], validate=True)
                if len(content) > MAX_FILE or digest(content) != item['sha256'] or path in self.state['sources']:
                    raise ValueError('Invalid native source manifest')
                self._write(path, content)
                self.state['sources'][path] = digest(content)
            if sum((work / p).stat().st_size for p in self.state['sources']) > MAX_SOURCES:
                raise ValueError('Source closure exceeds bound')
            atomic_json(self.state_path, self.state)
        for name in ('.home', '.tmp', 'outputs'):
            (work / name).mkdir(mode=0o755, exist_ok=True)

    def _write(self, path, content):
        target = safe_file(self.work, path, exists=False)
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(target.name + '.kleos-tmp')
        if temp.exists() or temp.is_symlink():
            raise ValueError('Reserved native temporary path')
        temp.write_bytes(content)
        temp.replace(target)

    def _remaining(self):
        return max(0, int(self.state['deadline'] - time.time()))

    def _command(self, arguments):
        remaining = self._remaining()
        if remaining <= 0:
            raise TimeoutError('Native session expired')
        return self.execute(arguments, self.work, remaining, self.request['resources'], self.request['lane'] == 'application')

    def _build(self):
        for path, checksum in self.state['sources'].items():
            if digest(safe_file(self.work, path).read_bytes()) != checksum:
                raise ValueError('Tracked source changed outside an authorized patch')
        outputs = self.work / 'outputs'
        if outputs.is_symlink():
            raise ValueError('Unsafe output directory')
        shutil.rmtree(outputs)
        outputs.mkdir()
        lane = self.request['lane']
        if lane == 'cad':
            if 'model.py' not in self.state['sources']:
                raise ValueError('CAD source contract requires model.py with parts mapping')
            self._command(['python', '-I', '-c', CAD_BUILD])
            found = ['outputs/parts.json', *[str(p.relative_to(self.work)) for p in outputs.glob('*.step')],
                     *[str(p.relative_to(self.work)) for p in outputs.glob('*.stl')]]
            if len(found) < 3:
                raise ValueError('CAD produced no manufactured parts')
        elif lane == 'pcb':
            found = self._pcb_build()
        elif lane == 'firmware':
            board = self.request.get('board')
            if board not in {'nrf52840dk/nrf52840', 'native_sim'}:
                raise ValueError('An explicit supported firmware board is required')
            if (self.request.get('firmwareTarget') != {'board': board}
                    or fingerprint(self.request['firmwareTarget']) != self.request.get('firmwareTargetHash')):
                raise ValueError('Firmware target differs from its pinned accepted snapshot')
            command = ['west', 'build', '-b', board, '-d', 'build', '.']
            if board == 'native_sim':
                command += ['--', '-DZEPHYR_TOOLCHAIN_VARIANT=host']
            self._command(command)
            found = []
            for suffix in ('elf', 'bin'):
                source = safe_file(self.work, 'build/zephyr/zephyr.' + suffix)
                target = 'outputs/firmware.' + suffix
                self._write(target, source.read_bytes())
                found.append(target)
        else:
            if not {'pubspec.yaml', 'pubspec.lock', 'lib/main.dart', 'web/index.html'} <= self.state['sources'].keys():
                raise ValueError('Application requires complete locked Flutter web sources')
            runtime = self.work / '.runtime'
            for name, original in [('flutter', '/opt/flutter'), ('pub-cache', '/opt/pub-cache')]:
                destination = runtime / name
                if not destination.exists():
                    shutil.copytree(original, destination, symlinks=True)
            self._command(['flutter', 'pub', 'get', '--offline'])
            self._command(['flutter', 'analyze', '--no-pub'])
            self._command(['flutter', 'build', 'web', '--no-pub', '--no-web-resources-cdn', '--no-wasm-dry-run'])
            from kleos.execution.web_bundle import package_web_fonts
            package_web_fonts(self.work / 'build/web')
            shutil.make_archive(str(outputs / 'companion-web'), 'zip', self.work / 'build/web')
            found = ['outputs/companion-web.zip']
        for path, checksum in self.state['sources'].items():
            if digest(safe_file(self.work, path).read_bytes()) != checksum:
                raise ValueError('Builder changed a source without an authorized patch')
        for path in found:
            safe_file(self.work, output_path(path))
        self.state['outputs'], self.state['built'] = sorted(found), True
        return {'summary': 'Native build completed; outputs are candidates', 'outputs': self.state['outputs']}

    def _rule_check(self, command, report):
        try:
            return self._command(command)
        except RuntimeError as error:
            path = self.work / report
            details = safe_file(self.work, report).read_bytes()[:16000].decode(errors='replace') if path.exists() else ''
            raise RuntimeError(str(error) + '\nRule-check report: ' + details) from error

    def _pcb_build(self):
        accepted = circuit_contract(self.request.get('circuitContract'), self.request.get('peripheralConceptIds'))
        if fingerprint(accepted) != self.request.get('circuitContractHash'):
            raise ValueError('Accepted circuit snapshot hash differs')
        if not {'board.kicad_pcb', 'circuit.kicad_sch'} <= self.state['sources'].keys():
            raise ValueError('PCB contract requires circuit.kicad_sch and board.kicad_pcb')
        if digest(Path('/opt/freerouting/freerouting.jar').read_bytes()) != '2c07d58f75dac03782664081e7a58b41c25400d871a9fcf166a2ea6fe60d5def':
            raise ValueError('Freerouting dependency differs from the qualified installation')
        stage = self.work / 'build/pcb'
        if stage.exists():
            shutil.rmtree(stage)
        stage.mkdir(parents=True)
        for name in self.state['sources']:
            if (name in {'board.kicad_pcb', 'circuit.kicad_sch', 'fp-lib-table', 'sym-lib-table'}
                    or name.endswith(('.kicad_mod', '.kicad_sym'))):
                target = stage / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(safe_file(self.work, name).read_bytes())
        contract_data = {'circuitContract': accepted, 'peripheralConceptIds': self.request['peripheralConceptIds']}
        (self.work / 'build/pcb-contract.json').write_text(json.dumps(contract_data))
        limits = accepted['constraints']
        project = {'board': {'design_settings': {'rules': {
            'min_clearance': limits['minClearanceMm'], 'min_track_width': limits['minTrackWidthMm']}}},
            'net_settings': {'classes': [{'name': 'Default', 'clearance': limits['minClearanceMm'],
                'track_width': limits['minTrackWidthMm'], 'via_diameter': .8, 'via_drill': .4}]}}
        (stage / 'board.kicad_pro').write_text(json.dumps(project))
        (stage / 'circuit.kicad_pro').write_text(json.dumps(project))
        (stage / 'board.kicad_dru').write_text('(version 1)\n'
            f'(rule "Accepted clearance" (constraint clearance (min {limits["minClearanceMm"]}mm)))\n'
            f'(rule "Accepted track width" (constraint track_width (min {limits["minTrackWidthMm"]}mm)))\n')
        self._command(['kicad-cli', 'sch', 'export', 'netlist', '--format', 'kicadxml',
                       '--output', 'build/pcb/netlist.xml', 'build/pcb/circuit.kicad_sch'])
        self._command(['/usr/bin/python3', '-I', '/opt/kleos-native/pcb_runner.py', 'prepare'])
        self._rule_check(['kicad-cli', 'sch', 'erc', '--exit-code-violations', '--format', 'json',
                       '--output', 'outputs/erc.json', 'build/pcb/circuit.kicad_sch'], 'outputs/erc.json')
        self._command(['java', '-Xmx768m', '-XX:ActiveProcessorCount=2', '-Djava.awt.headless=true',
                       '-Djava.io.tmpdir=' + str(self.work / '.tmp'), '-jar', '/opt/freerouting/freerouting.jar',
                       '-de', 'build/pcb/board.dsn', '-do', 'build/pcb/board.ses', '-da', '-mp', '20', '-mt', '2',
                       '--gui.enabled=false', '--api_server.enabled=false', '--router.job_timeout=00:02:00'])
        self._command(['/usr/bin/python3', '-I', '/opt/kleos-native/pcb_runner.py', 'finish'])
        self._rule_check(['kicad-cli', 'pcb', 'drc', '--exit-code-violations', '--format', 'json',
                       '--output', 'outputs/drc.json', 'build/pcb/board.kicad_pcb'], 'outputs/drc.json')
        self._command(['kicad-cli', 'pcb', 'export', 'gerbers', '--output', 'outputs/gerbers/',
                       '--no-protel-ext', '--layers', 'F.Cu,B.Cu,F.Mask,B.Mask,F.Paste,B.Paste,F.Silkscreen,B.Silkscreen,Edge.Cuts',
                       'build/pcb/board.kicad_pcb'])
        self._command(['kicad-cli', 'pcb', 'export', 'drill', '--output', 'outputs/gerbers/',
                       'build/pcb/board.kicad_pcb'])
        for extension in ('kicad_pcb', 'kicad_pro', 'kicad_dru'):
            self._write('outputs/routed.' + extension, safe_file(self.work, 'build/pcb/board.' + extension).read_bytes())
        self._write('outputs/netlist.xml', safe_file(self.work, 'build/pcb/netlist.xml').read_bytes())
        self._write('outputs/circuit-contract.json', json.dumps(contract_data, sort_keys=True).encode())
        with zipfile.ZipFile(self.work / 'outputs/pcb-project.zip', 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            total = 0
            for file in stage.rglob('*'):
                if not file.is_file() or not (file.suffix in {'.kicad_pcb', '.kicad_sch', '.kicad_pro', '.kicad_dru',
                        '.kicad_mod', '.kicad_sym', '.xml'} or file.name in {'fp-lib-table', 'sym-lib-table'}):
                    continue
                name = source_path(str(file.relative_to(stage)))
                content = safe_file(stage, name).read_bytes()
                total += len(content)
                if total > MAX_FILE:
                    raise ValueError('PCB project archive exceeds its uncompressed bound')
                archive.writestr(name, content)
            archive.writestr('circuit-contract.json', json.dumps(contract_data, sort_keys=True))
        if json.loads((self.work / 'build/pcb-contract.json').read_bytes()) != contract_data:
            raise ValueError('Builder changed accepted circuit authority')
        return [str(p.relative_to(self.work)) for p in (self.work / 'outputs').rglob('*') if p.is_file()]

    def _apply(self, action, args):
        if action == 'read':
            path = args.get('path')
            if path not in self.state['sources'] and path not in self.state['outputs']:
                raise ValueError('Read path is outside the tracked manifest')
            raw = safe_file(self.work, path).read_bytes()
            offset, length = args.get('offset', 0), args.get('maxBytes', 16384)
            if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0 or isinstance(length, bool) or not isinstance(length, int) or not 1 <= length <= 262144:
                raise ValueError('Invalid read window')
            return {'path': path, 'sha256': digest(raw), 'size': len(raw), 'content': raw[offset:offset + length].decode(errors='replace')}
        if action == 'patch':
            path = source_path(args.get('path'))
            if path not in self.request['allowedPaths']:
                raise ValueError('Patch exceeds task path authority')
            if 'expectedSha256' not in args or args['expectedSha256'] != self.state['sources'].get(path):
                raise ValueError('Patch source hash conflict')
            content = args.get('content')
            if not isinstance(content, str) or len(content.encode()) > 1024 * 1024:
                raise ValueError('Patch content exceeds bound')
            if path not in self.state['sources'] and len(self.state['sources']) >= 50:
                raise ValueError('Source count exceeds bound')
            total = len(content.encode()) + sum(safe_file(self.work, p).stat().st_size for p in self.state['sources'] if p != path)
            if total > MAX_SOURCES:
                raise ValueError('Source closure exceeds bound')
            self._write(path, content.encode())
            self.state['sources'][path] = digest(content.encode())
            self.state['built'], self.state['outputs'] = False, []
            return {'path': path, 'sha256': self.state['sources'][path], 'buildRequired': True}
        if action == 'build':
            self.state['built'], self.state['outputs'] = False, []
            return self._build()
        if not self.state['built']:
            raise ValueError('Build successfully before inspection or finish')
        if action in {'analyze', 'render'}:
            if self.request['lane'] != 'cad':
                raise ValueError('This inspection action is not qualified for the selected lane')
            command = ['python', '-I', '-c', CAD_ANALYZE if action == 'analyze' else CAD_RENDER]
            if action == 'render':
                command.append(args.get('view', 'isometric'))
            result = self._command(command)
            if action == 'render':
                for image in (self.work / 'outputs').glob('*.svg'):
                    name = str(image.relative_to(self.work))
                    safe_file(self.work, output_path(name))
                    if name not in self.state['outputs']:
                        self.state['outputs'].append(name)
            return {'summary': result[-16000:], 'independent': False}
        for path, checksum in self.state['sources'].items():
            if digest(safe_file(self.work, path).read_bytes()) != checksum:
                raise ValueError('Source changed outside an authorized patch')
        files = []
        for path in [*self.state['sources'], *self.state['outputs']]:
            raw = safe_file(self.work, path).read_bytes()
            if sum(f['size'] for f in files) + len(raw) > MAX_FILE:
                raise ValueError('Native finish exceeds transfer bound')
            files.append({'path': path, 'contentBase64': base64.b64encode(raw).decode(), 'sha256': digest(raw),
                          'size': len(raw), 'role': 'source' if path in self.state['sources'] else 'output'})
        self.state['finished'] = True
        return {'summary': 'Native candidate files collected', 'files': files, 'checks': []}

    def apply(self, value):
        item = action_request(value)
        ident, request_hash = item['actionId'], fingerprint(item)
        existing = self.state['actions'].get(ident)
        if existing:
            if existing['requestHash'] != request_hash:
                raise ValueError('Native action identity reused for different input')
            if 'result' not in existing:
                return {'status': 'failed', 'error': 'Interrupted action; inspect state before a new action'}
            return existing['result']
        if self.state['finished'] or self._remaining() <= 0 or len(self.state['actions']) >= self.request['maxActions']:
            raise ValueError('Native session closed or exhausted')
        self.state['actions'][ident] = {'requestHash': request_hash}
        atomic_json(self.state_path, self.state)
        try:
            result = {'status': 'succeeded', 'result': self._apply(item['action'], item['arguments'])}
        except (ValueError, RuntimeError, TimeoutError, OSError, subprocess.SubprocessError) as error:
            result = {'status': 'failed', 'error': str(error)[-64000:]}
        self.state['actions'][ident]['result'] = result
        atomic_json(self.state_path, self.state)
        return result


def main():
    if platform.system() != 'Linux' or os.geteuid() != 0 or os.environ.get('KLEOS_NATIVE_SANDBOX') != '1':
        raise SystemExit('Native supervisor is restricted to isolated Linux images')
    verify_no_network()
    control = Path('/control')
    raw = safe_file(control, 'request.json').read_bytes()
    request = json.loads(raw)
    with tempfile.TemporaryDirectory(prefix='kleos-native-', dir='/tmp') as directory:
        work = Path(directory)
        private = Path('/run/kleos-control')
        private.mkdir(mode=0o700, exist_ok=True)
        private.chmod(0o700)
        session = NativeSession(request, private / 'journal', work)
        inbox, outbox = control / 'actions', private / 'results'
        inbox.mkdir(mode=0o700, exist_ok=True)
        outbox.mkdir(mode=0o700, exist_ok=True)
        while time.time() < request['deadline'] and not session.state['finished']:
            if (control / 'cancel').exists():
                return
            for path in sorted(inbox.glob('*.json')):
                output = outbox / path.name
                if output.exists():
                    continue
                action = json.loads(safe_file(inbox, path.name).read_bytes())
                result = session.apply(action)
                atomic_json(output, {'protocol': PROTOCOL, 'jobId': request['jobId'], 'requestHash': digest(raw),
                                     'actionId': action['actionId'], 'actionHash': fingerprint(action), 'response': result})
                if session.state['finished']:
                    acknowledgement = control / ('ack-' + path.name)
                    while time.time() < request['deadline']:
                        if acknowledgement.exists() or (control / 'cancel').exists():
                            return
                        time.sleep(.1)
                    return
            time.sleep(.1)


if __name__ == '__main__':
    main()
