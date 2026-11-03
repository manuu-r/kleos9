"""Fixed offline slicer supervisor. Its only entrypoints are run and read-result."""
import base64
import ctypes
import errno
import hashlib
import json
import math
import os
import resource
import signal
import socket
import stat
import struct
import subprocess
import sys
import time
from pathlib import Path

PROTOCOL = 'kleos-slicer-v1'
MAX_BYTES = 32 * 1024**2
MAX_TRIANGLES = 200000
PROFILES = {
    'a1-mini-pla-04': ('N1', 'Bambu Lab A1 mini', 'A1M', 180),
    'a1-pla-04': ('N2S', 'Bambu Lab A1', 'A1', 256),
    'p1s-pla-04': ('C12', 'Bambu Lab P1S', 'X1C', 256),
    'x1c-pla-04': ('BL-P001', 'Bambu Lab X1 Carbon', 'X1C', 256),
}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def presets(profile):
    model, machine, suffix, size = PROFILES[profile]
    root = Path('/opt/orca/resources/profiles/BBL')
    def flatten(kind, name, seen=()):
        if name in seen or len(seen) > 12:
            raise ValueError('Invalid vendor preset inheritance')
        path = root / kind / (name + '.json')
        data = json.loads(path.read_bytes())
        parent = data.pop('inherits', None)
        return {**(flatten(kind, parent, (*seen, name)) if parent else {}), **data}
    values = {
        'machine': flatten('machine', machine + ' 0.4 nozzle'),
        'process': flatten('process', '0.20mm Standard @BBL ' + suffix),
        'filament': flatten('filament', 'Bambu PLA Basic @BBL ' + suffix),
    }
    values['process']['curr_bed_type'] = 'Textured PEI Plate'
    raw = json.dumps(values, sort_keys=True, separators=(',', ':')).encode()
    return values, sha(raw)


def mesh(raw, extension, size):
    """Geometry-only ingestion; refuse scripts, materials, arbitrary 3MF settings."""
    if len(raw) > MAX_BYTES:
        raise ValueError('Mesh exceeds byte bound')
    triangles = []
    if extension == 'stl' and len(raw) >= 84 and 84 + struct.unpack_from('<I', raw, 80)[0] * 50 == len(raw):
        count = struct.unpack_from('<I', raw, 80)[0]
        if count > MAX_TRIANGLES:
            raise ValueError('Mesh exceeds triangle bound')
        for offset in range(84, len(raw), 50):
            xyz = struct.unpack_from('<9f', raw, offset + 12)
            triangles.append(tuple(tuple(xyz[n:n + 3]) for n in (0, 3, 6)))
    elif extension == 'stl':
        vertices = []
        for line in raw.decode('ascii').splitlines():
            words = line.split()
            if words and words[0] == 'vertex':
                if len(words) != 4:
                    raise ValueError('Invalid STL vertex')
                vertices.append(tuple(float(v) for v in words[1:]))
        if len(vertices) % 3 or len(vertices) > MAX_TRIANGLES * 3:
            raise ValueError('Invalid STL triangle count')
        triangles = [tuple(vertices[n:n + 3]) for n in range(0, len(vertices), 3)]
    elif extension == 'obj':
        vertices = []
        for line in raw.decode('ascii').splitlines():
            words = line.split()
            if not words or words[0].startswith('#'):
                continue
            if words[0] == 'v' and len(words) == 4:
                vertices.append(tuple(float(v) for v in words[1:]))
                if len(vertices) > MAX_TRIANGLES * 3:
                    raise ValueError('OBJ exceeds vertex bound')
            elif words[0] == 'f' and len(words) == 4:
                indices = [int(v.split('/')[0]) for v in words[1:]]
                if any(n < 1 or n > len(vertices) for n in indices):
                    raise ValueError('OBJ indices must refer to preceding vertices')
                triangles.append(tuple(vertices[n - 1] for n in indices))
            elif words[0] not in {'vn', 'vt', 'o', 'g', 's', 'usemtl', 'mtllib'}:
                raise ValueError('Only triangulated OBJ geometry is supported')
            if len(triangles) > MAX_TRIANGLES:
                raise ValueError('Mesh exceeds triangle bound')
    else:
        raise ValueError('Only STL and triangulated OBJ are qualified')
    if not triangles:
        raise ValueError('Mesh is empty')
    unique, removed = {}, 0
    for triangle in triangles:
        if any(not math.isfinite(v) or abs(v) > 10000 for point in triangle for v in point):
            raise ValueError('Mesh has invalid coordinates')
        a, b, c = triangle
        u, v = [b[n] - a[n] for n in range(3)], [c[n] - a[n] for n in range(3)]
        cross = (u[1]*v[2]-u[2]*v[1], u[2]*v[0]-u[0]*v[2], u[0]*v[1]-u[1]*v[0])
        key = tuple(sorted(triangle))
        if sum(x*x for x in cross) < 1e-18 or key in unique:
            removed += 1
        else:
            unique[key] = triangle
    faces = list(unique.values())
    edges = {}
    for i, tri in enumerate(faces):
        for a, b in zip(tri, (*tri[1:], tri[0])):
            key = tuple(sorted((a, b)))
            edges.setdefault(key, []).append((i, a < b))
    if not faces or any(len(v) != 2 for v in edges.values()):
        raise ValueError('Mesh must be watertight and manifold; automatic hole filling is not qualified')
    adjacent = [[] for _ in faces]
    for (a, ad), (b, bd) in edges.values():
        adjacent[a].append((b, ad == bd))
        adjacent[b].append((a, ad == bd))
    flip, shells = {}, []
    for start in range(len(faces)):
        if start in flip:
            continue
        flip[start] = False
        stack, shell = [start], []
        while stack:
            ident = stack.pop()
            shell.append(ident)
            for other, different in adjacent[ident]:
                expected = flip[ident] ^ different
                if other in flip and flip[other] != expected:
                    raise ValueError('Mesh surface is not orientable')
                if other not in flip:
                    flip[other] = expected
                    stack.append(other)
        shells.append(shell)
    if len(shells) != 1:
        raise ValueError('Prepare each connected physical part separately')
    faces = [tuple(reversed(face)) if flip[i] else face for i, face in enumerate(faces)]
    volume = sum(a[0]*(b[1]*c[2]-b[2]*c[1])+a[1]*(b[2]*c[0]-b[0]*c[2])+a[2]*(b[0]*c[1]-b[1]*c[0])
                 for a, b, c in faces) / 6
    if abs(volume) < 1e-6:
        raise ValueError('Mesh volume is zero')
    if volume < 0:
        faces = [tuple(reversed(face)) for face in faces]
    points = [point for tri in faces for point in tri]
    low = [min(point[i] for point in points) for i in range(3)]
    dimensions = [max(point[i] for point in points) - low[i] for i in range(3)]
    if any(v <= 0 or v > size - 10 for v in dimensions):
        raise ValueError('Part exceeds the qualified build envelope and clearance')
    output = bytearray(b'Kleos geometry-only repaired STL'.ljust(80, b'\0') + struct.pack('<I', len(faces)))
    for tri in faces:
        output.extend(struct.pack('<12fH', 0, 0, 0, *(v for point in tri for v in point), 0))
    return bytes(output), {'triangleCount': len(faces), 'removedFaces': removed, 'reorientedFaces': sum(flip.values()),
                           'volumeMm3': abs(volume), 'dimensionsMm': dimensions, 'watertight': True,
                           'selfIntersections': 'UNKNOWN', 'physicalAcceptance': False}


def restrict():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (64 * 1024**2, 64 * 1024**2))
    resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))
    resource.setrlimit(resource.RLIMIT_CPU, (570, 580))
    libc = ctypes.CDLL(None)
    if libc.prctl(38, 1, 0, 0, 0) != 0:
        raise RuntimeError('Cannot prevent privilege escalation')
    # The daemon supplies a pinned syscall baseline that permits only AF_UNIX.
    if 'Seccomp:\t2' not in Path('/proc/self/status').read_text():
        raise RuntimeError('Missing syscall restrictions')
    for family in (socket.AF_INET, socket.AF_INET6, socket.AF_NETLINK, socket.AF_PACKET):
        try:
            sock = socket.socket(family, socket.SOCK_DGRAM)
        except OSError as error:
            if error.errno != errno.EPERM:
                raise RuntimeError('Cannot verify socket policy') from error
        else:
            sock.close()
            raise RuntimeError('Socket policy is not enforced')
    os.setgroups([])
    os.setgid(10001)
    os.setuid(10001)


def worker(request):
    work = Path('/tmp/work')
    status = Path('/proc/self/status').read_text()
    if os.getuid() != 10001 or 'CapEff:\t0000000000000000' not in status or 'NoNewPrivs:\t1' not in status:
        raise RuntimeError('Worker privileges exceed contract')
    for forbidden in (Path('/run/kleos-slicer/forged.json'), Path('/control/forged.json')):
        try:
            forbidden.write_bytes(b'forbidden')
        except PermissionError:
            pass
        except OSError as error:
            if error.errno != errno.EROFS:
                raise
        else:
            raise RuntimeError('Worker can write trusted control')
    values, bundle = presets(request['profileId'])
    if bundle != request['bundleSha256']:
        raise ValueError('Vendor profile bundle changed')
    raw = base64.b64decode(request['source'], validate=True)
    if sha(raw) != request['sourceSha256']:
        raise ValueError('Source digest changed')
    stl, measurements = mesh(raw, request['extension'], PROFILES[request['profileId']][3])
    (work / 'part.stl').write_bytes(stl)
    for kind, value in values.items():
        (work / (kind + '.json')).write_text(json.dumps(value))
    args = ['/usr/bin/xvfb-run', '-a', '/opt/orca/AppRun', 'part.stl', '--load-settings', 'machine.json;process.json',
            '--load-filaments', 'filament.json', '--arrange', '1', '--orient', '0', '--ensure-on-bed',
            '--mtcpp', str(MAX_TRIANGLES), '--mstpp', '480', '--slice', '0', '--export-3mf', 'prepared.3mf']
    with (work / 'slicer.log').open('wb') as log:
        result = subprocess.run(args, cwd=work, stdin=subprocess.DEVNULL, stdout=log, stderr=log, timeout=540,
                                env={'PATH': '/usr/bin:/bin', 'HOME': str(work), 'TMPDIR': str(work),
                                     'LANG': 'C.UTF-8', 'OMP_NUM_THREADS': '2', 'OPENBLAS_NUM_THREADS': '1'})
    if result.returncode:
        raise ValueError('Slicer rejected the part or preset')
    measurements['isolation'] = {'uid': os.getuid(), 'effectiveCapabilities': 0,
        'trustedControlWritable': False, 'socketPolicy': 'unix-only', 'noNewPrivileges': True}
    (work / 'measurements.json').write_text(json.dumps(measurements))


def collect(path, limit=MAX_BYTES):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit:
        raise ValueError('Unsafe slicer output')
    return path.read_bytes()


def run():
    if sys.platform != 'linux' or os.getuid() != 0 or os.environ.get('KLEOS_SLICER_ISOLATED') != '1':
        raise RuntimeError('Slicer requires the isolated supervisor')
    if any(p.is_dir() and p.name != 'lo' and (p / 'operstate').read_text().strip() not in {'down', 'notpresent'}
           for p in Path('/sys/class/net').iterdir()):
        raise RuntimeError('Slicer network namespace is not isolated')
    if any(line.split()[0] != 'lo' for line in Path('/proc/net/route').read_text().splitlines()[1:] if line.split()):
        raise RuntimeError('Slicer has an external route')
    if any(line.split()[-1] != 'lo' for line in Path('/proc/net/ipv6_route').read_text().splitlines() if line.split()):
        raise RuntimeError('Slicer has an external IPv6 route')
    private = Path('/run/kleos-slicer')
    private.mkdir(mode=0o700)
    work = Path('/tmp/work')
    work.mkdir(mode=0o700)
    os.chown(work, 10001, 10001)
    request = json.loads(collect(Path('/control/request.json'), MAX_BYTES * 2))
    result = {'protocol': PROTOCOL, 'jobId': request['jobId'], 'sourceSha256': request['sourceSha256'],
              'bundleSha256': request['bundleSha256'], 'image': request['image'], 'status': 'failed'}
    child = None
    try:
        child = subprocess.Popen(['/usr/bin/python3', '-I', __file__, 'worker'], preexec_fn=restrict,
                                 start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'})
        child.wait(timeout=570)
        if child.returncode:
            raise ValueError('Slicer did not produce a valid package')
        result['status'] = 'ready'
    except (ValueError, subprocess.TimeoutExpired):
        result['error'] = 'Slicer rejected the geometry or exceeded its limits'
    finally:
        if child:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait()
        # Include detached descendants before reading any worker-controlled output.
        for _ in range(8):
            while True:
                try:
                    if os.waitpid(-1, os.WNOHANG)[0] == 0:
                        break
                except ChildProcessError:
                    break
            found = False
            for entry in Path('/proc').iterdir():
                try:
                    if entry.name.isdecimal() and entry.stat().st_uid == 10001:
                        os.kill(int(entry.name), signal.SIGKILL)
                        found = True
                except (FileNotFoundError, ProcessLookupError):
                    pass
            if not found:
                break
            time.sleep(.05)
        else:
            result['status'] = 'failed'
    if result['status'] == 'ready':
        try:
            package = collect(work / 'prepared.3mf')
            result.update(package=base64.b64encode(package).decode(), preparedSha256=sha(package),
                          measurements=json.loads(collect(work / 'measurements.json', 8192)))
        except (ValueError, OSError):
            result.update(status='failed', error='Slicer output could not be verified')
    temporary = private / 'result.tmp'
    temporary.write_text(json.dumps(result, allow_nan=False))
    temporary.chmod(0o600)
    temporary.replace(private / 'result.json')
    # Keep root-private tmpfs available for the fixed read-result Docker exec.
    time.sleep(900)


if __name__ == '__main__':
    if sys.argv[1:] == ['worker']:
        worker(json.loads(Path('/control/request.json').read_bytes()))
    elif sys.argv[1:] == ['read-result']:
        path = Path('/run/kleos-slicer/result.json')
        if not path.exists():
            sys.exit(44)
        sys.stdout.buffer.write(collect(path, MAX_BYTES * 2))
    elif sys.argv[1:] == ['presets']:
        print(json.dumps({ident: presets(ident)[1] for ident in PROFILES}))
    elif not sys.argv[1:]:
        run()
    else:
        sys.exit(2)
