"""Fixed offline orthographic rasterization of actual STEP meshes.

Run only in the isolated native image. This imports no generated Python and
uses no display server, browser, network, or image-generation model.
"""
import hashlib
import json
import math
import struct
import sys
import zlib
from pathlib import Path

VIEWS = {"front": ((1., 0., 0.), (0., 0., 1.), (0., -1., 0.)),
         "top": ((1., 0., 0.), (0., 1., 0.), (0., 0., 1.)),
         "isometric": ((.70710678, -.70710678, 0.), (.40824829, .40824829, .81649658),
                       (-.57735027, -.57735027, .57735027))}
MAX_IMAGE_BYTES = 512 * 1024
MAX_TRIANGLES = 100_000
WIDTH, HEIGHT = 512, 384


def _chunk(kind, raw):
    return struct.pack('>I', len(raw)) + kind + raw + struct.pack('>I', zlib.crc32(kind + raw) & 0xffffffff)


def png_bytes(pixels, width=WIDTH, height=HEIGHT):
    if len(pixels) != width * height * 3:
        raise ValueError('Raster dimensions do not match pixels')
    rows = b''.join(b'\0' + pixels[row * width * 3:(row + 1) * width * 3] for row in range(height))
    return (b'\x89PNG\r\n\x1a\n' + _chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
            + _chunk(b'IDAT', zlib.compress(rows, 6)) + _chunk(b'IEND', b''))


def _dot(vector, axis):
    return sum(a * b for a, b in zip(vector, axis))


def rasterize(vertices, triangles, view, width=WIDTH, height=HEIGHT):
    if view not in VIEWS or not vertices or not triangles or len(triangles) > MAX_TRIANGLES:
        raise ValueError('Unsupported or empty bounded CAD mesh/view')
    if any(len(point) != 3 or any(not math.isfinite(value) or abs(value) > 1e6 for value in point) for point in vertices):
        raise ValueError('CAD mesh has invalid coordinates')
    right, up, depth = VIEWS[view]
    projected = [(_dot(point, right), _dot(point, up), _dot(point, depth)) for point in vertices]
    low = [min(point[axis] for point in projected) for axis in (0, 1)]
    high = [max(point[axis] for point in projected) for axis in (0, 1)]
    extent = max(high[0] - low[0], high[1] - low[1])
    if extent < 1e-9:
        raise ValueError('CAD mesh has no visible extent')
    scale = min((width - 48) / max(high[0] - low[0], 1e-9), (height - 48) / max(high[1] - low[1], 1e-9))
    screen = [((p[0] - (high[0] + low[0]) / 2) * scale + width / 2,
               height / 2 - (p[1] - (high[1] + low[1]) / 2) * scale, p[2]) for p in projected]
    pixels = bytearray(bytes((22, 24, 29)) * width * height)
    zbuffer = [-math.inf] * (width * height)
    visible = 0
    for triangle in triangles:
        if len(triangle) != 3 or any(not isinstance(index, int) or not 0 <= index < len(screen) for index in triangle):
            raise ValueError('CAD mesh triangle is invalid')
        a, b, c = [screen[index] for index in triangle]
        area = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1])
        if abs(area) < 1e-10:
            continue
        first, second, third = [vertices[index] for index in triangle]
        ab = [second[i] - first[i] for i in range(3)]
        ac = [third[i] - first[i] for i in range(3)]
        normal = (ab[1] * ac[2] - ab[2] * ac[1], ab[2] * ac[0] - ab[0] * ac[2], ab[0] * ac[1] - ab[1] * ac[0])
        magnitude = math.sqrt(sum(value * value for value in normal))
        shade = .5 + .5 * abs(_dot(normal, (.35, -.45, .82))) / max(magnitude, 1e-12)
        color = tuple(min(255, int(channel * shade)) for channel in (176, 188, 207))
        for y in range(max(0, math.floor(min(a[1], b[1], c[1]))), min(height - 1, math.ceil(max(a[1], b[1], c[1]))) + 1):
            for x in range(max(0, math.floor(min(a[0], b[0], c[0]))), min(width - 1, math.ceil(max(a[0], b[0], c[0]))) + 1):
                first_weight = ((b[1] - c[1]) * (x + .5 - c[0]) + (c[0] - b[0]) * (y + .5 - c[1])) / area
                second_weight = ((c[1] - a[1]) * (x + .5 - c[0]) + (a[0] - c[0]) * (y + .5 - c[1])) / area
                third_weight = 1 - first_weight - second_weight
                if min(first_weight, second_weight, third_weight) < -1e-9:
                    continue
                z = first_weight * a[2] + second_weight * b[2] + third_weight * c[2]
                index = y * width + x
                if z > zbuffer[index]:
                    zbuffer[index] = z
                    pixels[index * 3:index * 3 + 3] = bytes(color)
                    visible += 1
    if not visible:
        raise ValueError('CAD view contains no rendered geometry')
    return png_bytes(pixels, width, height)


def render_step(step_path, output_directory, views=('front', 'top', 'isometric')):
    from build123d import import_step

    source, output = Path(step_path), Path(output_directory)
    if source.is_symlink() or not source.is_file() or not 0 < source.stat().st_size <= 16 * 1024 * 1024:
        raise ValueError('A bounded regular STEP file is required')
    if (not isinstance(views, (list, tuple)) or not 1 <= len(views) <= 3 or len(set(views)) != len(views)
            or any(view not in VIEWS for view in views)):
        raise ValueError('Choose at most three fixed orthographic CAD views')
    shape = import_step(str(source))
    vertices, triangles = shape.tessellate(.2, .2)
    if not len(vertices) <= 200_000 or not 0 < len(triangles) <= MAX_TRIANGLES:
        raise ValueError('CAD mesh exceeds the bounded preview complexity')
    points = [tuple(float(value) for value in vertex.to_tuple()) for vertex in vertices]
    output.mkdir(parents=True, exist_ok=True)
    if output.is_symlink():
        raise ValueError('CAD render output must be a regular directory')
    images = []
    step_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    for view in views:
        raw = rasterize(points, triangles, view)
        if len(raw) > MAX_IMAGE_BYTES:
            raise ValueError('CAD PNG exceeds the 512 KiB preview ceiling')
        path = output / f'{view}.png'
        if path.is_symlink():
            raise ValueError('CAD PNG output must not be a symlink')
        path.write_bytes(raw)
        images.append({'name': path.name, 'mediaType': 'image/png', 'sha256': hashlib.sha256(raw).hexdigest(),
                       'size': len(raw), 'width': WIDTH, 'height': HEIGHT, 'view': view, 'stepSha256': step_hash})
    return {'images': images, 'stepSha256': step_hash, 'triangleCount': len(triangles), 'candidate': True,
            'checks': [], 'provenance': 'Actual STEP tessellation; fixed orthographic camera; no generated Python execution'}


def main():
    if len(sys.argv) != 4:
        raise SystemExit('Expected STEP path, output directory and JSON views')
    print(json.dumps(render_step(sys.argv[1], sys.argv[2], json.loads(sys.argv[3])), separators=(',', ':')))


if __name__ == '__main__':
    main()
