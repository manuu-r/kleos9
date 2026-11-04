"""Trusted, standalone export parser. Never imports or executes candidate source code."""
import hashlib
import json
import math
import os
import re
import socket
import struct
from pathlib import Path

PROTOCOL = 'kleos-cad-check-v1'
MAX_FILE = 32 * 1024 * 1024


class InvalidExport(ValueError):
    pass


def stl_measurements(data):
    """Read bounded binary/ASCII STL; check nondegenerate closed oriented edges."""
    if len(data) >= 84 and 84 + 50 * struct.unpack_from('<I', data, 80)[0] == len(data):
        count = struct.unpack_from('<I', data, 80)[0]
        if not 4 <= count <= 200000:
            raise InvalidExport('STL triangle limit')
        triangles = [tuple(tuple(struct.unpack_from('<3f', data, 84 + 50*i + 12 + j*12))
                           for j in range(3)) for i in range(count)]
    else:
        text = data.decode('ascii', errors='strict')
        # No permissive partial parsing: every triangle must have exactly three vertices.
        if not text.lstrip().startswith('solid') or not text.rstrip().splitlines()[-1].strip().startswith('endsolid'):
            raise InvalidExport('Invalid STL envelope')
        vertices = re.findall(r'^\s*vertex\s+([^\n]+)', text, re.M)
        if len(vertices) % 3 or not 12 <= len(vertices) <= 600000:
            raise InvalidExport('STL triangle limit')
        points = [tuple(map(float, row.split())) for row in vertices]
        triangles = [tuple(points[i:i+3]) for i in range(0, len(points), 3)]
    edges, volume = {}, 0.0
    mins, maxs = [math.inf]*3, [-math.inf]*3
    for tri in triangles:
        if any(len(p) != 3 or any(not math.isfinite(v) or abs(v) > 1e6 for v in p) for p in tri):
            raise InvalidExport('Nonfinite or excessive STL coordinate')
        a, b, c = tri
        ab, ac = [b[i]-a[i] for i in range(3)], [c[i]-a[i] for i in range(3)]
        cross = (ab[1]*ac[2]-ab[2]*ac[1], ab[2]*ac[0]-ab[0]*ac[2], ab[0]*ac[1]-ab[1]*ac[0])
        if sum(x*x for x in cross) <= 1e-18:
            raise InvalidExport('Degenerate STL triangle')
        volume += sum(a[i]*cross[i] for i in range(3))/6
        for p in tri:
            for i in range(3):
                mins[i], maxs[i] = min(mins[i], p[i]), max(maxs[i], p[i])
        for p, q in zip(tri, (b, c, a)):
            key = tuple(sorted((p, q)))
            count, orientation = edges.get(key, (0, 0))
            edges[key] = (count + 1, orientation + (1 if p < q else -1))
    if any(count != 2 or orientation != 0 for count, orientation in edges.values()):
        raise InvalidExport('STL is not a consistently oriented closed mesh')
    if volume <= 1e-9:
        raise InvalidExport('STL has no positive oriented volume')
    return {'boundsMinMm': mins, 'boundsMaxMm': maxs, 'volumeMm3': volume,
            'triangleCount': len(triangles), 'closedOrientedMesh': True}


def step_measurements(path, data):
    # Restrict this qualified scope to explicit SI millimetres; do not guess units.
    text = data.decode('ascii', errors='strict')
    units = re.findall(r'LENGTH_UNIT\s*\(\s*\)\s*NAMED_UNIT\s*\(\s*\*\s*\)\s*SI_UNIT\s*\(\s*([^)]*)\)', text)
    if (not units or len(re.findall(r'\bLENGTH_UNIT\s*\(', text)) != len(units)
            or any(re.sub(r'\s+', '', unit) != '.MILLI.,.METRE.' for unit in units)):
        return None
    from build123d import import_step
    shape = import_step(str(path))
    solids = shape.solids()
    if len(solids) != 1 or not shape.is_valid or not solids[0].is_valid:
        raise InvalidExport('STEP must contain exactly one valid solid per part export')
    box = shape.bounding_box()
    low, high = list(box.min), list(box.max)
    volume = shape.volume
    if (not math.isfinite(volume) or volume <= 1e-9
            or any(not math.isfinite(x) or abs(x) > 1e6 for x in low+high)):
        raise InvalidExport('STEP has invalid volume or bounds')
    return {'boundsMinMm': low, 'boundsMaxMm': high, 'volumeMm3': volume,
            'solidCount': len(solids), 'solidValid': True, 'units': 'mm'}


def inspect_request(request, directory, *, step_reader=step_measurements):
    """Only hash-addressed, bounded exports are consumed; sources are never mounted."""
    if request.get('protocol') != PROTOCOL or request.get('units') != 'mm':
        raise InvalidExport('Unsupported checker protocol or declared units')
    files = request['files']
    if not 2 <= len(files) <= 48 or len(request['constraints']) > 128:
        raise InvalidExport('Input count limit')
    measurements, unknown, seen = {}, list(request['unsupportedRequirements']), set()
    for item in files:
        digest, kind, part = item['sha256'], item['kind'], item['part']
        if (not re.fullmatch('[a-f0-9]{64}', digest) or kind not in {'step', 'stl'}
                or not re.fullmatch(r'[a-zA-Z0-9_/-]{1,200}', part) or (part, kind) in seen):
            raise InvalidExport('Invalid or duplicate export binding')
        seen.add((part, kind))
        path = directory / digest
        if path.is_symlink() or not path.is_file():
            raise InvalidExport('Missing export')
        with path.open('rb') as stream:
            data = stream.read(MAX_FILE+1)
        if len(data) != item['size'] or len(data) > MAX_FILE or hashlib.sha256(data).hexdigest() != digest:
            raise InvalidExport('Export digest or byte size mismatch')
        value = step_reader(path, data) if kind == 'step' else stl_measurements(data)
        if value is None:
            unknown.append('Unsupported or absent explicit STEP millimetre units: ' + part)
        measurements.setdefault(part, {})[kind] = value
    for part, values in measurements.items():
        if set(values) != {'step', 'stl'}:
            raise InvalidExport('Each part needs matching STEP and STL exports')
        step, stl = values['step'], values['stl']
        if step is None:
            continue
        if any(abs(step[key][i]-stl[key][i]) > .02 for key in ('boundsMinMm', 'boundsMaxMm') for i in range(3)):
            raise InvalidExport('STEP/STL coordinate bounds mismatch')
        if abs(step['volumeMm3']-stl['volumeMm3']) > max(.02, step['volumeMm3']*.005):
            raise InvalidExport('STEP/STL volume mismatch')
    for constraint in request['constraints']:
        part = measurements.get(constraint['part'], {}).get('step')
        metric = constraint['metric']
        if part is None:
            unknown.append('Constraint target has no supported STEP solid: '+constraint['part'])
            continue
        if metric == 'volume_mm3':
            actual = part['volumeMm3']
        elif metric in ('extent_x_mm', 'extent_y_mm', 'extent_z_mm'):
            axis = 'xyz'.index(metric[7])
            actual = part['boundsMaxMm'][axis]-part['boundsMinMm'][axis]
        else:
            unknown.append('Unsupported numeric metric')
            continue
        expected, tolerance = constraint['expected'], constraint['tolerance']
        if not all(math.isfinite(x) and 0 <= x <= 1e12 for x in (expected, tolerance)):
            raise InvalidExport('Invalid dimensional constraint')
        if abs(actual-expected) > tolerance:
            raise InvalidExport('Explicit numeric constraint failed: '+constraint['part']+'/'+metric)
    return {'status': 'UNKNOWN' if unknown else 'PASS', 'measurements': measurements,
            'unsupportedRequirements': unknown}


def main():
    request = json.loads(Path('/input/request.json').read_text())
    # OpenCascade can emit native stdout. Suppress parser chatter, then emit one bounded record.
    saved = os.dup(1)
    sink = os.open(os.devnull, os.O_WRONLY)
    os.dup2(sink, 1)
    os.dup2(sink, 2)
    stage = 'isolation'
    isolation = {}
    try:
        status = Path('/proc/self/status').read_text()
        isolation = {'uid': os.getuid(), 'gid': os.getgid(),
                     'noNewPrivileges': 'NoNewPrivs:\t1' in status, 'seccomp': 'Seccomp:\t2' in status,
                     'capabilities': int(next(line.split()[1] for line in status.splitlines() if line.startswith('CapEff:')), 16),
                     'generatorCodeAbsent': not Path('/opt/kleos-native').exists()}
        if (os.getuid() != 10001 or os.getgid() != 10001 or 'NoNewPrivs:\t1' not in status
                or 'Seccomp:\t2' not in status
                or int(next(line.split()[1] for line in status.splitlines() if line.startswith('CapEff:')), 16) != 0
                or Path('/opt/kleos-native').exists()):
            raise RuntimeError('Checker isolation policy mismatch')
        try:
            connection = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        except PermissionError:
            pass
        else:
            connection.close()
            raise RuntimeError('Checker network socket was permitted')
        stage = 'export_parser'
        result = inspect_request(request, Path('/input'))
        result['isolation'] = {'uid': os.getuid(), 'noNewPrivileges': True,
                               'seccomp': True, 'capabilities': 0, 'internetSocketDenied': True,
                               'generatorCodeAbsent': True}
    except InvalidExport as exc:
        result = {'status': 'FAIL', 'measurements': {}, 'reason': str(exc)}
    except Exception as exc:
        result = {'status': 'ERROR', 'measurements': {}, 'reason': 'Independent export parser failed',
                  'failureStage': stage, 'failureClass': type(exc).__name__, 'isolation': isolation}
    os.dup2(saved, 1)
    os.close(saved)
    os.close(sink)
    result.update(protocol=PROTOCOL, subjectHash=request['subjectHash'], executionId=request['executionId'],
                  checkerSourceSha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    print(json.dumps(result, allow_nan=False, separators=(',', ':')), flush=True)


if __name__ == '__main__':
    main()
