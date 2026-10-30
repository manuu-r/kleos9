"""Read-only, bounded views of engineering inputs and generated files. No inference.

Only data is decoded here: generated Python, Dart and HTML are never executed.
Previews are diagnostic candidates, not validation evidence or release authority.
"""
import math
import re
import struct
from collections import OrderedDict

from sqlalchemy import select

from .circuit_validation import validate_circuit_contract
from .db import Artifact, Task
from .domain import get_project
from .storage import digest

MAX_PREVIEW_BYTES = 16 * 1024 * 1024


def number(value, fallback=0):
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) and abs(parsed) < 1_000_000 else fallback
    except (TypeError, ValueError, OverflowError):
        return fallback


def mapping(value):
    return value if isinstance(value, dict) else {}


def stl_mesh(content, ceiling=1800):
    """Sample triangles without running a mesh loader or allocating by file claims."""
    triangles = []
    if len(content) >= 84 and 84 + 50 * struct.unpack_from('<I', content, 80)[0] == len(content):
        count = (len(content) - 84) // 50
        stride = max(1, math.ceil(count / ceiling))
        for index in range(0, count, stride):
            values = struct.unpack_from('<9f', content, 84 + index * 50 + 12)
            triangles.append([list(values[i:i + 3]) for i in (0, 3, 6)])
    else:
        vertices = re.findall(rb'\bvertex\s+([-+\d.eE]+)\s+([-+\d.eE]+)\s+([-+\d.eE]+)', content)
        if not vertices or len(vertices) % 3:
            raise ValueError('STL has no complete triangles')
        stride = max(1, math.ceil(len(vertices) / (3 * ceiling)))
        for index in range(0, len(vertices), 3 * stride):
            triangles.append([[float(v) for v in vertex] for vertex in vertices[index:index + 3]])
    if not triangles or any(not math.isfinite(v) or abs(v) > 1_000_000
                            for triangle in triangles for vertex in triangle for v in vertex):
        raise ValueError('STL contains invalid coordinates')
    return {'triangles': triangles, 'sampled': stride > 1}


def sexpr(content):
    """Bounded KiCad data parser: no macros, includes, eval or entity expansion."""
    tokens = re.finditer(r'\(|\)|"(?:[^"\\]|\\.)*"|[^\s()]+', content)
    stack, result, count = [], [], 0
    current = result
    for match in tokens:
        count += 1
        if count > 300_000:
            raise ValueError('PCB preview exceeds token ceiling')
        token = match.group()
        if token == '(':
            if len(stack) >= 64:
                raise ValueError('PCB nesting exceeds preview ceiling')
            child = []
            current.append(child)
            stack.append(current)
            current = child
        elif token == ')':
            if not stack:
                raise ValueError('Unbalanced PCB data')
            current = stack.pop()
        else:
            current.append(token[1:-1].replace('\\"', '"').replace('\\\\', '\\')
                           if token.startswith('"') else token)
    if stack or len(result) != 1 or not result[0] or result[0][0] != 'kicad_pcb':
        raise ValueError('Expected a KiCad PCB')
    return result[0]


def field(node, key):
    return next((item[1:] for item in node if isinstance(item, list) and item and item[0] == key), [])


def pcb_geometry(content):
    board = sexpr(content.decode('utf-8'))
    components, traces, points, outline = [], [], [], []

    def coordinates(values):
        if len(values) < 2 or any(isinstance(v, (list, dict)) for v in values[:2]):
            raise ValueError('PCB placement coordinates are missing or malformed')
        pair = [number(v, float('nan')) for v in values[:2]]
        if not all(math.isfinite(v) for v in pair):
            raise ValueError('PCB placement coordinates are not finite numbers')
        return tuple(pair)
    for item in board[1:]:
        if not isinstance(item, list) or not item:
            continue
        kind = item[0]
        if not isinstance(kind, str):
            continue
        if kind in {'footprint', 'module'} and len(components) < 128:
            if len(item) < 2 or not isinstance(item[1], str):
                raise ValueError('PCB footprint identity is absent')
            at = field(item, 'at')
            x, y = coordinates(at)
            reference, label = '', str(item[1])[:80]
            for prop in item:
                if isinstance(prop, list) and len(prop) > 2:
                    if prop[:2] == ['property', 'Reference'] or prop[:2] == ['fp_text', 'reference']:
                        reference = str(prop[2])[:80]
                    if prop[:2] == ['property', 'Value']:
                        label = str(prop[2])[:80]
            components.append({'id': reference or label, 'label': label, 'x': x, 'y': y,
                               'width': 3, 'height': 2})
            points.append((x, y))
        elif kind == 'segment' and len(traces) < 256:
            start, end = field(item, 'start'), field(item, 'end')
            if len(start) >= 2 and len(end) >= 2:
                pair = [coordinates(start), coordinates(end)]
                traces.append({'points': [{'x': x, 'y': y} for x, y in pair],
                               'layer': str((field(item, 'layer') or ['F.Cu'])[0])[:30]})
                points.extend(pair)
        elif kind in {'gr_rect', 'gr_line', 'gr_arc', 'gr_circle'}:
            if field(item, 'layer') != ['Edge.Cuts']:
                continue
            for key in ('start', 'end', 'center', 'mid'):
                pair = field(item, key)
                if len(pair) >= 2:
                    outline.append(coordinates(pair))
            if kind == 'gr_circle':
                center = coordinates(field(item, 'center'))
                end = coordinates(field(item, 'end'))
                radius = math.dist(center, end)
                outline.extend([(center[0] - radius, center[1] - radius),
                                (center[0] + radius, center[1] + radius)])
            elif kind == 'gr_arc':
                a, b, c = [coordinates(field(item, key)) for key in ('start', 'mid', 'end')]
                determinant = 2 * (a[0] * (b[1] - c[1]) + b[0] * (c[1] - a[1]) + c[0] * (a[1] - b[1]))
                if abs(determinant) < 1e-9:
                    raise ValueError('PCB outline arc is degenerate')
                squared = [x * x + y * y for x, y in (a, b, c)]
                cx = (squared[0] * (b[1] - c[1]) + squared[1] * (c[1] - a[1])
                      + squared[2] * (a[1] - b[1])) / determinant
                cy = (squared[0] * (c[0] - b[0]) + squared[1] * (a[0] - c[0])
                      + squared[2] * (b[0] - a[0])) / determinant
                radius = math.dist((cx, cy), a)
                if radius > 1_000_000:
                    raise ValueError('PCB outline arc exceeds preview bounds')
                start_angle, mid_angle, end_angle = [math.atan2(y - cy, x - cx) for x, y in (a, b, c)]
                span = (end_angle - start_angle) % math.tau
                counterclockwise = (mid_angle - start_angle) % math.tau <= span
                for angle in (0, math.pi / 2, math.pi, 3 * math.pi / 2):
                    in_ccw_span = (angle - start_angle) % math.tau <= span + 1e-9
                    if in_ccw_span == counterclockwise:
                        outline.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
    if outline:
        points = outline
    if not points:
        raise ValueError('PCB has no previewable placement or outline')
    left, top = min(x for x, y in points), min(y for x, y in points)
    width = max(x for x, y in points) - left
    height = max(y for x, y in points) - top
    if width <= 0 or height <= 0:
        raise ValueError('PCB bounds are not two-dimensional')
    for comp in components:
        comp['x'] -= left
        comp['y'] -= top
    for trace in traces:
        for point in trace['points']:
            point['x'] -= left
            point['y'] -= top
    return {'board': {'width': width, 'height': height}, 'components': components, 'traces': traces}


class EngineeringPreview:
    def __init__(self, database, store):
        self.database, self.store = database, store
        self.cache = OrderedDict()

    def read(self, principal, project_id):
        with self.database.transaction(read_only=True) as session:
            project = get_project(session, project_id, principal, lock=False)
            key = (principal.tenant_id, project.id, project.revision)
            if key in self.cache:
                return self.cache[key]
            artifacts = session.scalars(select(Artifact).where(
                Artifact.tenant_id == principal.tenant_id, Artifact.project_id == project_id,
                Artifact.revision == project.engineering_revision,
                Artifact.status.in_(['candidate', 'accepted'])).order_by(Artifact.created_at.desc()).limit(200)).all()
            tasks = session.scalars(select(Task).where(Task.project_id == project_id,
                Task.revision == project.engineering_revision).order_by(Task.created_at.desc()).limit(50)).all()
            states = {}
            for task in tasks:
                states.setdefault(task.lane, task.status)
            interfaces = mapping(project.interfaces)
            mechanical = mapping(interfaces.get('mechanical'))
            bounds = mapping(mechanical.get('externalMm'))
            envelope = (dict(zip(('width', 'height', 'depth'),
                                [number(bounds.get(axis)) for axis in ('x', 'y', 'z')]))
                        if all(number(bounds.get(axis)) > 0 for axis in ('x', 'y', 'z')) else {})
            result = {'revision': project.revision, 'phase': 'generation' if project.stage >= 4 else 'interfaces',
                      'source': 'deterministic', 'files': [
                          {'id': a.id, 'name': a.name, 'lane': a.lane, 'status': a.status,
                           'mediaType': a.media_type} for a in artifacts],
                      'limitations': ['Visual previews do not establish engineering validation.']}
            result['circuitContractCheck'] = validate_circuit_contract(interfaces).model_dump(
                mode='json', by_alias=True)
            for lane, title in [('mechanics', 'Enclosure'), ('electronics', 'Circuit board'),
                                ('application', 'Companion app'), ('firmware', 'Firmware')]:
                result[lane] = {'title': title, 'source': 'interface',
                                'status': states.get(lane, 'queued'),
                                'summary': 'Interface reference · waiting for generated files',
                                'limitations': ['Approximate interface view; no generated output yet.']}
            result['mechanics']['envelope'] = envelope
            components = mechanical.get('components', [])
            if not isinstance(components, list):
                components = []
            internal = mapping(mechanical.get('internalMm'))
            inset_x = max(0, (number(bounds.get('x')) - number(internal.get('x'), number(bounds.get('x')))) / 2)
            inset_y = max(0, (number(bounds.get('y')) - number(internal.get('y'), number(bounds.get('y')))) / 2)
            result['electronics']['pcb'] = {'board': {'width': envelope.get('width', 0),
                                                       'height': envelope.get('height', 0)},
                'components': [{'id': str(c.get('id', 'part'))[:80], 'label': str(c.get('id', 'part'))[:80],
                    'x': inset_x + number(mapping(c.get('positionMm')).get('x'))
                         + max(1, number(mapping(c.get('sizeMm')).get('x'), 4)) / 2,
                    'y': inset_y + number(mapping(c.get('positionMm')).get('y'))
                         + max(1, number(mapping(c.get('sizeMm')).get('y'), 3)) / 2,
                    'width': max(1, number(mapping(c.get('sizeMm')).get('x'), 4)),
                    'height': max(1, number(mapping(c.get('sizeMm')).get('y'), 3))}
                    for c in components[:128] if isinstance(c, dict)], 'traces': []}
            result['electronics']['summary'] = 'Enclosure component layout reference · PCB export pending'
            result['application']['app'] = {'title': project.name[:100], 'theme': 'dark', 'widgets': []}
            selected = {'mechanics': next((a for a in artifacts if a.lane == 'mechanics'
                                          and a.name.endswith('.stl') and a.provenance.get('nativeJobId')), None),
                        'electronics': next((a for a in artifacts if a.lane == 'electronics'
                                            and a.name.endswith('.kicad_pcb')), None),
                        'application': next((a for a in artifacts if a.lane == 'application'
                                            and a.name.endswith('lib/main.dart')), None),
                        'firmware': next((a for a in artifacts if a.lane == 'firmware'
                                         and a.name.endswith('src/main.c')), None)}
            for lane, artifact in selected.items():
                if artifact is None or artifact.size > MAX_PREVIEW_BYTES:
                    continue
                target = result[lane]
                try:
                    content = self.store.get(artifact.key)
                    if len(content) > MAX_PREVIEW_BYTES or digest(content) != artifact.sha256:
                        raise ValueError('Artifact failed preview integrity check')
                    if lane == 'mechanics':
                        target['mesh'] = stl_mesh(content)
                    elif lane == 'electronics':
                        target['pcb'] = pcb_geometry(content)
                    elif lane == 'application':
                        code = content.decode('utf-8')
                        labels = re.findall(r"\bText\(\s*(['\"])([^'\"\n]{1,120})\1", code)
                        target['app']['widgets'] = [{'kind': 'text', 'label': label, 'value': ''}
                                                     for _, label in labels[:10]]
                        target['code'] = code[:12000]
                    else:
                        target['code'] = content.decode('utf-8')[:12000]
                    target.update({'source': 'artifact', 'artifactId': artifact.id, 'name': artifact.name,
                                   'mediaType': artifact.media_type,
                                   'taskStatus': target['status'], 'status': artifact.status,
                                   'summary': f'{artifact.name} · {artifact.status}',
                                   'limitations': ['Generated candidate; validation status is shown separately.']})
                    if lane in {'application', 'firmware'}:
                        target.update({'codeName': artifact.name, 'codeArtifactId': artifact.id,
                                       'codeStatus': artifact.status})
                    if lane == 'application':
                        target['limitations'].append('Static source-derived layout; generated code is not executed.')
                except (ValueError, OSError, UnicodeError, struct.error):
                    target['limitations'].append('Generated file is not previewable; download remains available.')
            for lane, extension in [('mechanics', '.py'), ('electronics', '.ato')]:
                source = next((a for a in artifacts if a.lane == lane and a.name.endswith(extension)
                               and a.size <= 128 * 1024), None)
                if source is not None:
                    try:
                        content = self.store.get(source.key)
                        if len(content) <= 128 * 1024 and digest(content) == source.sha256:
                            result[lane]['code'] = content.decode('utf-8')[:12000]
                            result[lane].update({'codeName': source.name, 'codeArtifactId': source.id,
                                                 'codeStatus': source.status})
                    except (OSError, UnicodeError):
                        pass
            self.cache[key] = result
            while len(self.cache) > 32:
                self.cache.popitem(last=False)
            return result
