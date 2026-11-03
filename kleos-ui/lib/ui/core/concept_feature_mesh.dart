import 'dart:math' as math;
import '../../data/models/concept_scene.dart';
import '../../data/models/json.dart';

const _eps = 1e-9;

/// Bounded, deterministic illustration geometry; never evaluates model code.
class ConceptFeatureTriangle {
  const ConceptFeatureTriangle(this.a, this.b, this.c);
  final ConceptVector a, b, c;
}

class _P {
  const _P(this.x, this.y);
  final double x, y;
}

double _cross(_P a, _P b, _P c) =>
    (b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x);
bool _same(_P a, _P b) => (a.x - b.x).abs() < _eps && (a.y - b.y).abs() < _eps;
double _area(List<_P> ring) =>
    List.generate(ring.length, (i) {
      final a = ring[i], b = ring[(i + 1) % ring.length];
      return a.x * b.y - b.x * a.y;
    }).fold(0.0, (a, b) => a + b) /
    2;
bool _on(_P p, _P a, _P b) =>
    _cross(a, b, p).abs() < _eps &&
    p.x >= math.min(a.x, b.x) - _eps &&
    p.x <= math.max(a.x, b.x) + _eps &&
    p.y >= math.min(a.y, b.y) - _eps &&
    p.y <= math.max(a.y, b.y) + _eps;
bool _intersects(_P a, _P b, _P c, _P d) {
  final abC = _cross(a, b, c),
      abD = _cross(a, b, d),
      cdA = _cross(c, d, a),
      cdB = _cross(c, d, b);
  return (abC * abD < -_eps && cdA * cdB < -_eps) ||
      _on(c, a, b) ||
      _on(d, a, b) ||
      _on(a, c, d) ||
      _on(b, c, d);
}

bool _inside(_P p, List<_P> ring) {
  var inside = false;
  for (var i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    final a = ring[i], b = ring[j];
    if (_on(p, a, b)) return false;
    if ((a.y > p.y) != (b.y > p.y) &&
        p.x < (b.x - a.x) * (p.y - a.y) / (b.y - a.y) + a.x) {
      inside = !inside;
    }
  }
  return inside;
}

Never _bad(String message) => throw FormatException(message);
double _number(Object? value, {double min = -.5, double max = .5}) {
  if (value is! num || !value.isFinite || value < min || value > max) {
    _bad('Concept geometry has an invalid coordinate.');
  }
  return value.toDouble();
}

List<_P> _ring(Object? value, int max, {bool radial = false}) {
  if (value is! List || value.length < 3 || value.length > max) {
    _bad('Concept profile exceeds its vertex limit.');
  }
  final ring = <_P>[];
  for (final item in value) {
    if (item is! List || item.length != 2) {
      _bad('Concept profile needs coordinate pairs.');
    }
    ring.add(_P(_number(item[0], min: radial ? 0 : -.5), _number(item[1])));
  }
  for (var i = 0; i < ring.length; i++) {
    if (_same(ring[i], ring[(i + 1) % ring.length])) {
      _bad('Concept profile has a repeated edge.');
    }
    for (var j = i + 1; j < ring.length; j++) {
      if (j == i + 1 || (i == 0 && j == ring.length - 1)) continue;
      if (_intersects(ring[i], ring[(i + 1) % ring.length], ring[j],
          ring[(j + 1) % ring.length])) {
        _bad('Concept profile crosses itself.');
      }
    }
  }
  if (_area(ring).abs() < _eps) _bad('Concept profile has no area.');
  return ring;
}

List<_P> _wound(List<_P> ring, bool ccw) =>
    (_area(ring) > 0) == ccw ? ring : ring.reversed.toList();

/// Bridge validated holes to their visible outer boundary, then ear-clip the
/// weakly simple polygon. Duplicate bridge coordinates are intentional.
List<List<_P>> _caps(List<_P> outer, List<List<_P>> holes) {
  var polygon = _wound(outer, true).toList();
  for (final raw in holes) {
    final hole = _wound(raw, false);
    var hi = 0;
    for (var i = 1; i < hole.length; i++) {
      if (hole[i].x > hole[hi].x ||
          (hole[i].x == hole[hi].x && hole[i].y < hole[hi].y)) {
        hi = i;
      }
    }
    final h = hole[hi];
    int? oi;
    var distance = double.infinity;
    for (var i = 0; i < polygon.length; i++) {
      final p = polygon[i];
      var visible = true;
      for (final ring in [polygon, ...holes]) {
        for (var j = 0; j < ring.length; j++) {
          final a = ring[j], b = ring[(j + 1) % ring.length];
          if (_same(a, h) || _same(b, h) || _same(a, p) || _same(b, p)) {
            continue;
          }
          if (_intersects(h, p, a, b)) visible = false;
        }
      }
      final mid = _P((h.x + p.x) / 2, (h.y + p.y) / 2);
      if (!_inside(mid, outer) || holes.any((ring) => _inside(mid, ring))) {
        visible = false;
      }
      final d = (h.x - p.x) * (h.x - p.x) + (h.y - p.y) * (h.y - p.y);
      if (visible && d < distance) {
        oi = i;
        distance = d;
      }
    }
    if (oi == null) _bad('Could not triangulate a concept opening.');
    final path = [
      for (var i = 0; i < hole.length; i++) hole[(hi + i) % hole.length]
    ];
    polygon = [
      ...polygon.take(oi + 1),
      ...path,
      h,
      polygon[oi],
      ...polygon.skip(oi + 1)
    ];
  }
  final triangles = <List<_P>>[];
  final remaining = polygon.toList();
  var steps = 0;
  while (remaining.length > 3) {
    if (++steps > 20000) _bad('Concept triangulation exceeded its work limit.');
    var cut = false;
    for (var i = 0; i < remaining.length; i++) {
      final a = remaining[(i - 1 + remaining.length) % remaining.length],
          b = remaining[i],
          c = remaining[(i + 1) % remaining.length];
      if (_cross(a, b, c) <= _eps) continue;
      final occupied = remaining.any((p) =>
          !_same(p, a) &&
          !_same(p, b) &&
          !_same(p, c) &&
          _cross(a, b, p) >= -_eps &&
          _cross(b, c, p) >= -_eps &&
          _cross(c, a, p) >= -_eps);
      if (occupied) continue;
      triangles.add([a, b, c]);
      remaining.removeAt(i);
      cut = true;
      break;
    }
    if (!cut) _bad('Concept profile cannot be triangulated safely.');
  }
  if (remaining.length == 3 &&
      _cross(remaining[0], remaining[1], remaining[2]) > _eps) {
    triangles.add(remaining);
  }
  final expected =
      _area(outer).abs() - holes.fold(0.0, (sum, h) => sum + _area(h).abs());
  final actual =
      triangles.fold(0.0, (sum, t) => sum + _cross(t[0], t[1], t[2]).abs() / 2);
  if ((actual - expected).abs() > math.max(1e-8, expected * 1e-7)) {
    _bad('Concept opening triangulation did not preserve area.');
  }
  return triangles;
}

double _segmentDistance(
    ConceptVector p, ConceptVector q, ConceptVector r, ConceptVector s) {
  final u = q - p, v = s - r, w = p - r;
  final a = u.dot(u), b = u.dot(v), c = v.dot(v), d = u.dot(w), e = v.dot(w);
  final denominator = a * c - b * b;
  final pairs = <(double, double)>[
    (0, (e / c).clamp(0, 1)),
    (1, ((e + b) / c).clamp(0, 1)),
    ((-d / a).clamp(0, 1), 0),
    (((b - d) / a).clamp(0, 1), 1)
  ];
  if (denominator > _eps * _eps) {
    final t = (b * e - c * d) / denominator, h = (a * e - b * d) / denominator;
    if (t >= 0 && t <= 1 && h >= 0 && h <= 1) pairs.add((t, h));
  }
  return pairs.map((pair) {
    final delta = w + u * pair.$1 - v * pair.$2;
    return math.sqrt(delta.dot(delta));
  }).reduce(math.min);
}

List<ConceptFeatureTriangle> compileConceptFeature(ConceptPart part) {
  final geometry = part.geometry;
  if (geometry == null) _bad('Feature geometry is missing.');
  final result = <ConceptFeatureTriangle>[];
  ConceptVector scaled(ConceptVector p) {
    if ([p.x, p.y, p.z].any((v) => !v.isFinite || v.abs() > .50000001)) {
      _bad('Concept geometry exceeds normalized bounds.');
    }
    return ConceptVector(
        p.x * part.size.x, p.y * part.size.y, p.z * part.size.z);
  }

  void tri(ConceptVector a, ConceptVector b, ConceptVector c) {
    if ((b - a).cross(c - a).dot((b - a).cross(c - a)) < _eps * _eps) return;
    if (result.length >= 4096) _bad('Concept part exceeds 4096 triangles.');
    result.add(ConceptFeatureTriangle(scaled(a), scaled(b), scaled(c)));
  }

  void quad(
      ConceptVector a, ConceptVector b, ConceptVector c, ConceptVector d) {
    tri(a, b, c);
    tri(a, c, d);
  }

  ConceptVector xyz(_P p, double z) => ConceptVector(p.x, p.y, z);
  void cap(List<_P> ring, double z, bool top,
      {List<List<_P>> holes = const []}) {
    for (final t in _caps(ring, holes)) {
      if (top) {
        tri(xyz(t[0], z), xyz(t[1], z), xyz(t[2], z));
      } else {
        tri(xyz(t[2], z), xyz(t[1], z), xyz(t[0], z));
      }
    }
  }

  switch (geometry['kind']) {
    case 'extrusion':
      final outer = _wound(_ring(geometry['outer'], 48), true);
      final values = geometry['holes'] ?? const <Object?>[];
      if (values is! List || values.length > 4) {
        _bad('Concept extrusion has too many holes.');
      }
      final holes = values.map((h) => _wound(_ring(h, 24), false)).toList();
      if (outer.length + holes.fold(0, (sum, h) => sum + h.length) > 96) {
        _bad('Concept extrusion exceeds 96 vertices.');
      }
      for (var i = 0; i < holes.length; i++) {
        if (!holes[i].every((p) => _inside(p, outer))) {
          _bad('Concept hole is outside its profile.');
        }
        for (final other in [outer, ...holes.take(i)]) {
          for (var a = 0; a < holes[i].length; a++) {
            for (var b = 0; b < other.length; b++) {
              if (_intersects(holes[i][a], holes[i][(a + 1) % holes[i].length],
                  other[b], other[(b + 1) % other.length])) {
                _bad('Concept holes touch or overlap.');
              }
            }
          }
        }
        for (final previous in holes.take(i)) {
          if (_inside(holes[i][0], previous) ||
              _inside(previous[0], holes[i])) {
            _bad('Nested concept holes are unsupported.');
          }
        }
      }
      final floor = _number(geometry['floor'] ?? 0, min: 0, max: 1);
      if (floor >= 1 || (floor > 0 && holes.isEmpty)) {
        _bad('Concept pocket floor is invalid.');
      }
      cap(outer, .5, true, holes: holes);
      cap(outer, -.5, false, holes: floor > 0 ? const [] : holes);
      if (floor > 0) {
        for (final hole in holes) {
          cap(hole, -.5 + floor, true);
        }
      }
      for (final ring in [outer, ...holes]) {
        for (var i = 0; i < ring.length; i++) {
          final a = ring[i], b = ring[(i + 1) % ring.length];
          final bottom = identical(ring, outer) ? -.5 : -.5 + floor;
          quad(xyz(a, bottom), xyz(b, bottom), xyz(b, .5), xyz(a, .5));
        }
      }
    case 'revolution':
      // Counterclockwise radius/z profile gives outward normals under rotation.
      final profile =
          _wound(_ring(geometry['profile'], 48, radial: true), true);
      ConceptVector at(_P p, int i) => ConceptVector(
          p.x * math.cos(i * math.pi / 16),
          p.x * math.sin(i * math.pi / 16),
          p.y);
      for (var i = 0; i < profile.length; i++) {
        for (var j = 0; j < 32; j++) {
          final a = profile[i], b = profile[(i + 1) % profile.length];
          quad(at(a, j), at(a, j + 1), at(b, j + 1), at(b, j));
        }
      }
    case 'loft':
      final sections = geometry['sections'];
      if (sections is! List || sections.length < 2 || sections.length > 8) {
        _bad('Concept loft needs 2–8 sections.');
      }
      final rings = <List<_P>>[], heights = <double>[];
      for (final raw in sections) {
        final section = asMap(raw);
        final z = _number(section['z']);
        final ring = _ring(section['outer'], 32);
        if (heights.isNotEmpty &&
            (z <= heights.last || ring.length != rings.first.length)) {
          _bad(
              'Concept loft sections must increase with matching vertex counts.');
        }
        if (rings.isNotEmpty && (_area(ring) > 0) != (_area(rings.first) > 0)) {
          _bad('Concept loft section winding must match.');
        }
        if (rings.isNotEmpty) {
          final previous = rings.last;
          for (final t in [.125, .25, .375, .5, .625, .75, .875]) {
            _ring([
              for (var i = 0; i < ring.length; i++)
                [
                  (1 - t) * previous[i].x + t * ring[i].x,
                  (1 - t) * previous[i].y + t * ring[i].y
                ]
            ], 32);
          }
          for (var i = 0; i < ring.length; i++) {
            final j = (i + 1) % ring.length;
            if ((previous[j].x - previous[i].x) * (ring[j].x - ring[i].x) +
                    (previous[j].y - previous[i].y) * (ring[j].y - ring[i].y) <=
                0) {
              _bad('Concept loft correspondence twists too far.');
            }
          }
        }
        rings.add(ring);
        heights.add(z);
      }
      if (_area(rings.first) < 0) {
        for (var i = 0; i < rings.length; i++) {
          rings[i] = rings[i].reversed.toList();
        }
      }
      cap(rings.first, heights.first, false);
      cap(rings.last, heights.last, true);
      for (var j = 0; j < rings.length - 1; j++) {
        for (var i = 0; i < rings[j].length; i++) {
          final next = (i + 1) % rings[j].length;
          quad(
              xyz(rings[j][i], heights[j]),
              xyz(rings[j][next], heights[j]),
              xyz(rings[j + 1][next], heights[j + 1]),
              xyz(rings[j + 1][i], heights[j + 1]));
        }
      }
    case 'sweep':
      final raw = geometry['path'];
      if (raw is! List || raw.length < 2 || raw.length > 32) {
        _bad('Concept sweep needs 2–32 path points.');
      }
      final radius = _number(geometry['radius'], min: 0, max: .2),
          inner = _number(geometry['innerRadius'] ?? 0, min: 0, max: .2);
      if (radius <= 0 || inner >= radius) {
        _bad('Concept sweep radii are invalid.');
      }
      final path = <ConceptVector>[];
      for (final item in raw) {
        if (item is! List || item.length != 3) {
          _bad('Concept sweep needs 3D path points.');
        }
        final p =
            ConceptVector(_number(item[0]), _number(item[1]), _number(item[2]));
        if (path.isNotEmpty && (p - path.last).dot(p - path.last) < _eps) {
          _bad('Concept sweep has a repeated point.');
        }
        path.add(p);
      }
      if (path.any(
          (p) => [p.x, p.y, p.z].any((v) => v.abs() + radius > .5 + _eps))) {
        _bad('Concept sweep radius exceeds normalized bounds.');
      }
      for (var i = 0; i < path.length - 1; i++) {
        for (var j = i + 2; j < path.length - 1; j++) {
          if (_segmentDistance(path[i], path[i + 1], path[j], path[j + 1]) <=
              2 * radius + _eps) {
            _bad('Concept sweep segments collide.');
          }
        }
      }
      final tangents = <ConceptVector>[];
      for (var i = 0; i < path.length; i++) {
        final before = i == 0
            ? (path[1] - path[0]).normalized
            : (path[i] - path[i - 1]).normalized;
        final after =
            i == path.length - 1 ? before : (path[i + 1] - path[i]).normalized;
        if (before.dot(after) < -.999) {
          _bad('Concept sweep reverses direction.');
        }
        tangents.add((before + after).normalized);
      }
      final normals = <ConceptVector>[];
      var normal = (tangents.first.cross(tangents.first.z.abs() < .9
              ? const ConceptVector(0, 0, 1)
              : const ConceptVector(0, 1, 0)))
          .normalized;
      for (var i = 0; i < tangents.length; i++) {
        if (i > 0) {
          final axis = tangents[i - 1].cross(tangents[i]);
          final length = math.sqrt(axis.dot(axis));
          if (length > _eps) {
            final unit = axis * (1 / length),
                c = tangents[i - 1].dot(tangents[i]).clamp(-1.0, 1.0);
            normal = normal * c +
                unit.cross(normal) * length +
                unit * (unit.dot(normal) * (1 - c));
          }
          normal = (normal - tangents[i] * normal.dot(tangents[i])).normalized;
        }
        normals.add(normal);
      }
      ConceptVector at(int row, int col, double r) {
        final angle = col * math.pi / 8;
        return path[row] +
            normals[row] * (r * math.cos(angle)) +
            tangents[row].cross(normals[row]) * (r * math.sin(angle));
      }
      for (var row = 0; row < path.length - 1; row++) {
        for (var col = 0; col < 16; col++) {
          quad(at(row, col, radius), at(row, col + 1, radius),
              at(row + 1, col + 1, radius), at(row + 1, col, radius));
          if (inner > 0) {
            quad(at(row + 1, col, inner), at(row + 1, col + 1, inner),
                at(row, col + 1, inner), at(row, col, inner));
          }
        }
      }
      for (final row in [0, path.length - 1]) {
        if (inner == 0) {
          for (var col = 1; col < 15; col++) {
            if (row == 0) {
              tri(at(row, 0, radius), at(row, col + 1, radius),
                  at(row, col, radius));
            } else {
              tri(at(row, 0, radius), at(row, col, radius),
                  at(row, col + 1, radius));
            }
          }
          continue;
        }
        for (var col = 0; col < 16; col++) {
          final a = at(row, col, radius),
              b = at(row, col + 1, radius),
              c = inner > 0 ? at(row, col + 1, inner) : path[row],
              d = inner > 0 ? at(row, col, inner) : path[row];
          if (row == 0) {
            quad(d, c, b, a);
          } else {
            quad(a, b, c, d);
          }
        }
      }
    default:
      _bad('Unsupported concept feature kind.');
  }
  if (result.isEmpty) _bad('Concept geometry produced no surface.');
  return List.unmodifiable(result);
}
