"""Bounded, data-only engineering preview readers; never import generated sources.

STL vertices retain manufacturing coordinates. KiCad's documented S-expression
format is parsed without evaluation or external file resolution. Unsupported
geometry fails explicitly rather than being replaced with concept geometry.
"""
import math
import re
import struct

MAX_STL_BYTES = 16 * 1024**2
MAX_TRIANGLES = 250_000
MAX_PARTS = 24
MAX_COORDINATE = 100_000


class PreviewGeometryError(ValueError):
    pass


def number(value):
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        raise PreviewGeometryError('Invalid geometry coordinate') from None
    if not math.isfinite(result) or abs(result) > MAX_COORDINATE:
        raise PreviewGeometryError('Geometry coordinate exceeds bounds')
    return result


def stl_mesh(raw, *, triangle_limit=1800):
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_STL_BYTES:
        raise PreviewGeometryError('STL byte limit exceeded')
    count = struct.unpack_from('<I', raw, 80)[0] if len(raw) >= 84 else 0
    if len(raw) >= 84 and 84 + count * 50 == len(raw):
        if not 0 < count <= MAX_TRIANGLES:
            raise PreviewGeometryError('STL triangle limit exceeded')
        def vertices():
            for offset in range(84, len(raw), 50):
                values = struct.unpack_from('<12fH', raw, offset)
                yield [[number(v) for v in values[i:i+3]] for i in (3, 6, 9)]
        triangles = vertices()
    else:
        if len(raw) > 2 * 1024**2:
            raise PreviewGeometryError('ASCII STL byte limit exceeded')
        try:
            lines = raw.decode('ascii').splitlines()
        except UnicodeDecodeError:
            raise PreviewGeometryError('Unsupported STL encoding') from None
        if len(lines) > MAX_TRIANGLES * 7 + 2 or not lines or not lines[0].strip().startswith('solid'):
            raise PreviewGeometryError('Invalid ASCII STL')
        result, pending, phase = [], [], 'facet'
        ended = False
        for line in lines[1:]:
            fields = line.split()
            if not fields:
                continue
            if fields[0] == 'endsolid' and phase == 'facet':
                ended = True
                continue
            if ended:
                raise PreviewGeometryError('Trailing ASCII STL data')
            if phase == 'facet' and fields[:2] == ['facet', 'normal'] and len(fields) == 5:
                for value in fields[2:]:
                    number(value)
                phase = 'loop'
            elif phase == 'loop' and fields == ['outer', 'loop']:
                phase, pending = 'vertices', []
            elif phase == 'vertices' and fields[0] == 'vertex' and len(fields) == 4 and len(pending) < 3:
                pending.append([number(v) for v in fields[1:]])
            elif phase == 'vertices' and fields == ['endloop'] and len(pending) == 3:
                phase = 'endfacet'
            elif phase == 'endfacet' and fields == ['endfacet']:
                result.append(pending)
                if len(result) > MAX_TRIANGLES:
                    raise PreviewGeometryError('STL triangle limit exceeded')
                phase = 'facet'
            else:
                raise PreviewGeometryError('Malformed ASCII STL')
        if not ended or not result:
            raise PreviewGeometryError('Incomplete ASCII STL')
        triangles, count = result, len(result)
    stride = max(1, math.ceil(count / triangle_limit))
    sampled, low, high, valid = [], [math.inf]*3, [-math.inf]*3, 0
    for index, triangle in enumerate(triangles):
        a, b, c = triangle
        u, v = [b[i]-a[i] for i in range(3)], [c[i]-a[i] for i in range(3)]
        cross = [u[1]*v[2]-u[2]*v[1], u[2]*v[0]-u[0]*v[2], u[0]*v[1]-u[1]*v[0]]
        if sum(x*x for x in cross) <= 1e-20:
            continue
        valid += 1
        for point in triangle:
            for axis in range(3):
                low[axis], high[axis] = min(low[axis], point[axis]), max(high[axis], point[axis])
        if index % stride == 0:
            sampled.append(triangle)
    if not sampled:
        raise PreviewGeometryError('STL has no previewable triangles')
    return {'triangles': sampled, 'triangleCount': count, 'validTriangleCount': valid,
            'sampled': len(sampled) < count, 'bounds': {'min': low, 'max': high}}


_TOKEN = re.compile(r'\s+|;[^\r\n]*|[()]|"(?:[^"\\]|\\.)*"|[^\s()";]+')


def sexpression(raw):
    if not isinstance(raw, bytes) or len(raw) > 8 * 1024**2:
        raise PreviewGeometryError('PCB byte limit exceeded')
    try:
        text = raw.decode('utf8')
    except UnicodeDecodeError:
        raise PreviewGeometryError('Invalid PCB text') from None
    stack, roots, position, count = [], [], 0, 0
    for match in _TOKEN.finditer(text):
        if match.start() != position:
            raise PreviewGeometryError('Malformed PCB token')
        position = match.end()
        token = match.group()
        if token.isspace() or token.startswith(';'):
            continue
        count += 1
        if count > 400_000:
            raise PreviewGeometryError('PCB token limit exceeded')
        if token == '(':
            node = []
            (stack[-1] if stack else roots).append(node)
            stack.append(node)
            if len(stack) > 64:
                raise PreviewGeometryError('PCB nesting limit exceeded')
        elif token == ')':
            if not stack:
                raise PreviewGeometryError('Unbalanced PCB expression')
            stack.pop()
        elif stack:
            if len(token) > 4096:
                raise PreviewGeometryError('PCB token length exceeded')
            if token.startswith('"'):
                token = re.sub(r'\\(["\\])', r'\1', token[1:-1])
            stack[-1].append(token)
        else:
            raise PreviewGeometryError('Unexpected PCB text')
    if position != len(text) or stack or len(roots) != 1 or roots[0][:1] != ['kicad_pcb']:
        raise PreviewGeometryError('Invalid PCB root')
    return roots[0]


def children(node, kind):
    return [item for item in node[1:] if isinstance(item, list) and item[:1] == [kind]]


def field(node, kind, *, default=None):
    items = children(node, kind)
    if len(items) != 1:
        if not items and default is not None:
            return default
        raise PreviewGeometryError('Missing or duplicated PCB field: ' + kind)
    return items[0][1:]


def xy(node, kind):
    values = field(node, kind)
    if len(values) < 2:
        raise PreviewGeometryError('Invalid PCB coordinate')
    return [number(value) for value in values[:2]]


def pcb_geometry(raw):
    board = sexpression(raw)
    # The unchanged UI represents a rectangular board. Never substitute a bounding
    # rectangle for a curved, cut-out or otherwise unsupported manufacturing edge.
    edges = [item for item in board[1:] if isinstance(item, list) and item
             and item[0].startswith('gr_') and field(item, 'layer', default=['']) == ['Edge.Cuts']]
    points = []
    if len(edges) == 1 and edges[0][0] == 'gr_rect':
        points = [xy(edges[0], 'start'), xy(edges[0], 'end')]
    elif len(edges) == 4 and all(item[0] == 'gr_line' for item in edges):
        segments = [(tuple(xy(item, 'start')), tuple(xy(item, 'end'))) for item in edges]
        if any(a == b or (a[0] != b[0] and a[1] != b[1]) for a, b in segments):
            raise PreviewGeometryError('PCB outline is not an axis-aligned rectangle')
        points = [point for segment in segments for point in segment]
        corners = set(points)
        if (len(corners) != 4 or len({tuple(sorted(segment)) for segment in segments}) != 4
                or any(points.count(point) != 2 for point in corners)):
            raise PreviewGeometryError('PCB outline is not a closed rectangle')
    else:
        raise PreviewGeometryError('This PCB outline is outside the rectangular viewer contract')
    low = [min(point[i] for point in points) for i in range(2)]
    high = [max(point[i] for point in points) for i in range(2)]
    width, height = [high[i]-low[i] for i in range(2)]
    if not 0 < width <= 1000 or not 0 < height <= 1000:
        raise PreviewGeometryError('PCB dimensions exceed preview bounds')
    components, seen = [], set()
    footprints = children(board, 'footprint')
    if len(footprints) > 128:
        raise PreviewGeometryError('PCB component limit exceeded')
    for footprint in footprints:
        properties = [item for item in children(footprint, 'property') if item[1:2] == ['Reference']]
        if not properties:
            properties = [item for item in children(footprint, 'fp_text') if item[1:2] == ['reference']]
        if len(properties) != 1 or len(properties[0]) < 3:
            raise PreviewGeometryError('PCB reference is missing or ambiguous')
        reference = properties[0][2]
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_.-]{0,63}', reference) or reference in seen:
            raise PreviewGeometryError('PCB reference is duplicated or invalid')
        seen.add(reference)
        at = field(footprint, 'at')
        if len(at) not in {2, 3} or len(at) == 3 and number(at[2]) % 360 != 0:
            raise PreviewGeometryError('Rotated footprint previews require a qualified coordinate transform')
        origin = [number(value) for value in at[:2]]
        extents = []
        pads = children(footprint, 'pad')
        if not 1 <= len(pads) <= 512:
            raise PreviewGeometryError('PCB pad count exceeds preview scope')
        for pad in pads:
            pos, size = field(pad, 'at'), xy(pad, 'size')
            if len(pos) not in {2, 3} or (len(pos) == 3 and number(pos[2]) % 360 != 0) or min(size) <= 0:
                raise PreviewGeometryError('Unsupported rotated or invalid PCB pad')
            center = [number(pos[i]) + origin[i] for i in range(2)]
            extents.extend([[center[i] + sign * size[i]/2 for i in range(2)] for sign in (-1, 1)])
        left, bottom = [min(point[i] for point in extents) for i in range(2)]
        right, top = [max(point[i] for point in extents) for i in range(2)]
        components.append({'id': reference, 'label': reference, 'x': (left+right)/2-low[0],
                           'y': (bottom+top)/2-low[1], 'width': right-left, 'height': top-bottom,
                           'geometryKind': 'padEnvelope'})
    segments = children(board, 'segment')
    if len(segments) > 256:
        raise PreviewGeometryError('PCB track count exceeds viewer bound')
    traces = []
    for segment in segments:
        layer = field(segment, 'layer')
        track_width = field(segment, 'width')
        if layer not in [['F.Cu'], ['B.Cu']] or len(track_width) != 1 or number(track_width[0]) <= 0:
            raise PreviewGeometryError('Unsupported PCB track layer or width')
        traces.append({'layer': layer[0], 'width': number(track_width[0]),
                       'points': [{'x': point[0]-low[0], 'y': point[1]-low[1]}
                                  for point in (xy(segment, 'start'), xy(segment, 'end'))]})
    limitations = ['Component rectangles show actual pad extents, not package bodies; this is a visual preview, not electrical validation.']
    if children(board, 'arc') or children(board, 'zone') or children(board, 'via'):
        limitations.append('Copper arcs, zones and vias are not drawn by this viewer.')
    return {'board': {'width': width, 'height': height}, 'components': components, 'traces': traces,
            'units': 'mm', 'originMm': low, 'limitations': limitations}
