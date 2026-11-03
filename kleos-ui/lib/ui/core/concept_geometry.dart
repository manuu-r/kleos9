import 'dart:math' as math;
import 'dart:ui';

import '../../data/models/concept_scene.dart';
import 'concept_feature_mesh.dart';

// Native adaptation of Anatomy's camera/solid craft. The pinned upstream skill
// and MIT attribution live in the backend's Anatomy skill bundle. This bounded
// renderer accepts data only; it never evaluates generated SVG, JS, or shaders.

class ConceptFace {
  const ConceptFace(
      {required this.partId,
      required this.points,
      required this.normal,
      required this.color,
      this.outline = true});
  final String partId;
  final List<ConceptVector> points;
  final ConceptVector normal;
  final Color color;
  final bool outline;
}

class ConceptProjectedFace {
  const ConceptProjectedFace(this.partId, this.path, this.points, this.depth,
      this.color, this.outline);
  final String partId;
  final Path path;
  final List<Offset> points;
  final double depth;
  final Color color;
  final bool outline;
}

class ConceptDrawing {
  const ConceptDrawing(this.faces, this.bounds);
  final List<ConceptProjectedFace> faces;
  final Rect bounds;

  String? partAt(Offset point) {
    for (final face in faces.reversed) {
      if (face.path.contains(point)) return face.partId;
    }
    return null;
  }
}

/// Small meshes are built once per accepted scene, not on every animation frame.
/// Projection uses painter-order depth sorting, not a z-buffer: intersecting
/// surfaces and cyclic occlusion may remain approximate. These bounded meshes
/// illustrate a concept; they do not establish CAD or manufacturing validity.
class ConceptGeometry {
  ConceptGeometry(this.scene) {
    try {
      var triangles = 0;
      for (final part in scene.parts) {
        final mesh = _partMesh(part);
        final count = mesh.fold(0, (sum, face) => sum + face.points.length - 2);
        triangles += count;
        if (scene.schemaVersion == 'concept-scene-v2' &&
            (count > 4096 || triangles > 12000)) {
          throw const FormatException(
              'Concept geometry exceeds its triangle budget.');
        }
        meshes[part.id] = mesh;
      }
      _layoutExplode();
    } on FormatException catch (error) {
      meshes.clear();
      validationError = error.message;
    }
  }
  final ConceptScene scene;
  final Map<String, List<ConceptFace>> meshes = {};
  String? validationError;
  Map<String, ConceptVector> effectiveExplode = const {};
  bool _automaticExplode = false;

  /// A view layout only: source parts, transforms and provenance stay untouched.
  /// Stable assembly layers are packed into balanced rows in the default camera
  /// plane. Camera-depth is preserved; real rotated mesh envelopes set spacing.
  void _layoutExplode() {
    effectiveExplode = Map.unmodifiable(
        {for (final part in scene.parts) part.id: part.explode});
    if (scene.parts.length < 2 || meshes.isEmpty) return;
    final first = scene.parts.first.explode;
    if (scene.parts.any((part) =>
        part.explode.x != first.x ||
        part.explode.y != first.y ||
        part.explode.z != first.z)) {
      return;
    }
    final azimuth = 40 * math.pi / 180, elevation = 28 * math.pi / 180;
    final right = ConceptVector(math.sin(azimuth), -math.cos(azimuth), 0);
    final up = ConceptVector(-math.cos(azimuth) * math.sin(elevation),
        -math.sin(azimuth) * math.sin(elevation), math.cos(elevation));
    final boxes = <({String id, double layer, Rect rect})>[];
    for (final part in scene.parts) {
      final vertices = meshes[part.id]!
          .expand((face) => face.points)
          .map((p) => p.rotated(part.rotation) + part.position)
          .toList(growable: false);
      if (vertices.isEmpty) return;
      final rect =
          _bounds([for (final p in vertices) Offset(p.dot(right), p.dot(up))]);
      final low = vertices.map((p) => p.z).reduce(math.min),
          high = vertices.map((p) => p.z).reduce(math.max);
      boxes.add((id: part.id, layer: (low + high) / 2, rect: rect));
    }
    boxes.sort((a, b) {
      final order = a.layer.compareTo(b.layer);
      return order == 0 ? a.id.compareTo(b.id) : order;
    });
    final original =
        boxes.map((b) => b.rect).reduce((a, b) => a.expandToInclude(b));
    final dimensions = boxes
        .map((b) => math.max(b.rect.width, b.rect.height))
        .toList()
      ..sort();
    final gap = math.max(.12, dimensions[dimensions.length ~/ 2] * .12);
    // Enumerate at most 24 row widths; choose the layout with the best fit in a
    // landscape preview, rather than shrinking everything into a tall column.
    const aspect = 1.6;
    var bestScore = double.infinity, bestArea = double.infinity;
    Map<String, Offset> best = const {};
    for (var columns = 1; columns <= boxes.length; columns++) {
      final rows = <({int start, int end, double width, double height})>[];
      for (var start = 0; start < boxes.length; start += columns) {
        final end = math.min(start + columns, boxes.length);
        final row = boxes.sublist(start, end);
        rows.add((
          start: start,
          end: end,
          width: row.fold(0.0, (sum, b) => sum + b.rect.width) +
              gap * (row.length - 1),
          height: row.map((b) => b.rect.height).reduce(math.max)
        ));
      }
      final width = rows.map((r) => r.width).reduce(math.max);
      final height =
          rows.fold(0.0, (sum, r) => sum + r.height) + gap * (rows.length - 1);
      final score = math.max(width / aspect, height), area = width * height;
      if (score > bestScore + 1e-9 ||
          ((score - bestScore).abs() < 1e-9 && area >= bestArea)) {
        continue;
      }
      final positions = <String, Offset>{};
      var y = -height / 2;
      for (final row in rows) {
        var x = -row.width / 2;
        for (var i = row.start; i < row.end; i++) {
          final box = boxes[i];
          positions[box.id] =
              Offset(x + box.rect.width / 2, y + row.height / 2) +
                  original.center -
                  box.rect.center;
          x += box.rect.width + gap;
        }
        y += row.height + gap;
      }
      bestScore = score;
      bestArea = area;
      best = positions;
    }
    effectiveExplode = Map.unmodifiable({
      for (final box in boxes)
        box.id: right * best[box.id]!.dx + up * best[box.id]!.dy
    });
    _automaticExplode = true;
  }

  ConceptDrawing project(
    Size viewport, {
    double azimuth = 40,
    double elevation = 28,
    double exploded = .65,
    double zoom = 1,
    String? selected,
  }) {
    if (scene.parts.isEmpty || meshes.isEmpty || viewport.isEmpty) {
      return const ConceptDrawing([], Rect.zero);
    }
    final a = azimuth * math.pi / 180;
    final e = elevation.clamp(8, 78) * math.pi / 180;
    // Same z-up orthographic camera as Anatomy iso()/depthOf().
    final right = ConceptVector(math.sin(a), -math.cos(a), 0);
    final up = ConceptVector(
        -math.cos(a) * math.sin(e), -math.sin(a) * math.sin(e), math.cos(e));
    final toward = ConceptVector(
        math.cos(a) * math.cos(e), math.sin(a) * math.cos(e), math.sin(e));
    Offset screen(ConceptVector p) => Offset(p.dot(right), -p.dot(up));
    final fit = <Offset>[];
    final current = <({
      ConceptFace face,
      List<ConceptVector> points,
      ConceptVector normal
    })>[];
    for (final part in scene.parts) {
      final offset = effectiveExplode[part.id] ?? part.explode;
      // Automatic framing grows with separation so assembled=0 remains exactly
      // as before. Explicit artist-provided layouts retain fixed endpoint fit.
      final fitOffset = _automaticExplode
          ? part.explode + (offset - part.explode) * exploded.clamp(0, 1)
          : offset;
      for (final face in meshes[part.id]!) {
        final posed = face.points
            .map((p) => p.rotated(part.rotation) + part.position)
            .toList(growable: false);
        // Fit both endpoints so toggling exploded view doesn't change scale.
        for (final point in posed) {
          fit.add(screen(point));
          fit.add(screen(point + fitOffset));
        }
        final normal = face.normal.rotated(part.rotation);
        if (normal.dot(toward) <= .00001) continue;
        current.add((
          face: face,
          normal: normal,
          points: [
            for (final point in posed) point + offset * exploded.clamp(0, 1)
          ]
        ));
      }
    }
    final fitRect = _bounds(fit);
    final pad = math.min(viewport.width, viewport.height) * .09;
    final scale = math.min(
            (viewport.width - pad * 2) / math.max(.1, fitRect.width),
            (viewport.height - pad * 2) / math.max(.1, fitRect.height)) *
        zoom.clamp(.65, 1.8);
    final currentRect = _bounds([
      for (final item in current)
        for (final point in item.points) screen(point)
    ]);
    final centre = currentRect.center;
    Offset at(ConceptVector p) =>
        (screen(p) - centre) * scale +
        Offset(viewport.width / 2, viewport.height / 2 - 4);
    final light = (toward + const ConceptVector(-.15, -.25, 1.0)).normalized;
    final faces = <ConceptProjectedFace>[];
    for (final item in current) {
      final points = item.points.map(at).toList(growable: false);
      final path = Path()..addPolygon(points, true);
      final facing = item.normal.dot(light).clamp(0, 1);
      var color = Color.lerp(
          const Color(0xff17181d), item.face.color, .36 + facing * .64)!;
      if (selected == item.face.partId) {
        color = Color.lerp(color, const Color(0xffec7a45), .28)!;
      }
      faces.add(ConceptProjectedFace(
          item.face.partId,
          path,
          points,
          item.points.map((p) => p.dot(toward)).reduce((a, b) => a + b) /
              item.points.length,
          color,
          item.face.outline));
    }
    faces.sort((a, b) => a.depth.compareTo(b.depth));
    return ConceptDrawing(
        List.unmodifiable(faces),
        _bounds([
          for (final face in faces) ...face.points,
        ]));
  }
}

Rect _bounds(List<Offset> points) {
  if (points.isEmpty) return Rect.zero;
  var left = points.first.dx, right = left;
  var top = points.first.dy, bottom = top;
  for (final point in points) {
    left = math.min(left, point.dx);
    right = math.max(right, point.dx);
    top = math.min(top, point.dy);
    bottom = math.max(bottom, point.dy);
  }
  return Rect.fromLTRB(left, top, right, bottom);
}

List<ConceptFace> _partMesh(ConceptPart part) {
  final result = <ConceptFace>[];
  final color =
      Color(0xff000000 | int.parse(part.color.substring(1), radix: 16));
  void solid(ConceptVector size, ConceptVector centre, Color shade,
      {String shape = 'rounded_box', double? radius}) {
    result.addAll(_solid(part.id, size, centre, shade, shape, radius));
  }

  final s = part.size;
  if (part.geometry != null) {
    return [
      for (final triangle in compileConceptFeature(part))
        ConceptFace(
            partId: part.id,
            points: [triangle.a, triangle.b, triangle.c],
            normal: (triangle.b - triangle.a)
                .cross(triangle.c - triangle.a)
                .normalized,
            color: color,
            outline: false)
    ];
  }
  if (part.explicitGeometry) {
    solid(s, ConceptVector.zero, color, shape: part.shape);
    return result;
  }
  if (part.role == 'shell' &&
      (part.shape == 'box' || part.shape == 'rounded_box') &&
      s.z > .18 &&
      math.min(s.x, s.y) > .3) {
    final wall = math.min(s.x, s.y) * .045;
    final floor = math.min(wall, s.z * .18);
    solid(ConceptVector(s.x, s.y, floor),
        ConceptVector(0, 0, -s.z / 2 + floor / 2), color,
        shape: part.shape);
    final h = s.z - floor;
    final z = floor / 2;
    solid(ConceptVector(s.x, wall, h), ConceptVector(0, (s.y - wall) / 2, z),
        color);
    solid(ConceptVector(s.x, wall, h), ConceptVector(0, -(s.y - wall) / 2, z),
        color);
    solid(ConceptVector(wall, s.y - wall * 2, h),
        ConceptVector((s.x - wall) / 2, 0, z), color);
    solid(ConceptVector(wall, s.y - wall * 2, h),
        ConceptVector(-(s.x - wall) / 2, 0, z), color);
  } else {
    solid(s, ConceptVector.zero, color, shape: part.shape);
  }
  final top = s.z / 2;
  if (part.role == 'board' && part.shape != 'sphere') {
    for (final offset in [-.23, .2]) {
      solid(
          ConceptVector(s.x * .27, s.y * .35, math.min(.14, s.z * .8)),
          ConceptVector(s.x * offset, 0, top + math.min(.14, s.z * .8) / 2),
          const Color(0xff30333b),
          shape: 'box');
    }
    final count = (s.y * 3).round().clamp(3, 8);
    for (var i = 0; i < count; i++) {
      solid(
          ConceptVector(s.x * .08, s.y * .035, .012),
          ConceptVector(
              s.x * .43, (i - (count - 1) / 2) * s.y * .10, top + .006),
          const Color(0xffd1b87a),
          shape: 'box');
    }
  } else if (part.role == 'display' && part.shape != 'sphere') {
    solid(ConceptVector(s.x * .84, s.y * .78, .016),
        ConceptVector(0, 0, top + .008), const Color(0xff1b3139),
        radius: math.min(s.x, s.y) * .055);
    // A quiet screen reflection and two display rows, without invented readings.
    for (var i = 0; i < 2; i++) {
      solid(
          ConceptVector(s.x * (i == 0 ? .52 : .32), s.y * .035, .008),
          ConceptVector(-s.x * .10, s.y * (i == 0 ? -.12 : .12), top + .019),
          i == 0 ? const Color(0xffb9e1db) : const Color(0xff6b9f9e),
          shape: 'box');
    }
  } else if (part.role == 'sensor') {
    final radius = math.min(s.x, s.y) * .045;
    for (var i = -1; i <= 1; i++) {
      solid(ConceptVector(radius * 2, radius * 2, .012),
          ConceptVector(i * radius * 4, 0, top + .006), const Color(0xff353941),
          shape: 'cylinder');
    }
  } else if (part.role == 'cover' && math.min(s.x, s.y) > .5) {
    for (final x in [-.38, .38]) {
      for (final y in [-.35, .35]) {
        final radius = math.min(s.x, s.y) * .018;
        solid(
            ConceptVector(radius * 2, radius * 2, .018),
            ConceptVector(s.x * x, s.y * y, top + .009),
            const Color(0xffa5a8b2),
            shape: 'cylinder');
      }
    }
  }
  return result;
}

List<ConceptFace> _solid(String id, ConceptVector size, ConceptVector centre,
    Color color, String shape, double? requestedRadius) {
  if (shape == 'sphere') return _sphere(id, size, centre, color);
  final ring = <ConceptVector>[];
  final round = shape == 'cylinder';
  if (round) {
    for (var i = 0; i < 24; i++) {
      final a = i * math.pi * 2 / 24;
      ring.add(
          ConceptVector(math.cos(a) * size.x / 2, math.sin(a) * size.y / 2, 0));
    }
  } else {
    final radius = shape == 'box'
        ? 0.0
        : (requestedRadius ?? math.min(size.x, size.y) * .13);
    final corners = [
      (x: size.x / 2 - radius, y: size.y / 2 - radius, start: 0),
      (x: -size.x / 2 + radius, y: size.y / 2 - radius, start: 90),
      (x: -size.x / 2 + radius, y: -size.y / 2 + radius, start: 180),
      (x: size.x / 2 - radius, y: -size.y / 2 + radius, start: 270),
    ];
    for (final corner in corners) {
      final steps = radius == 0 ? 0 : 4;
      for (var i = 0; i <= steps; i++) {
        final angle =
            (corner.start + (steps == 0 ? 45 : 90 * i / steps)) * math.pi / 180;
        ring.add(ConceptVector(corner.x + radius * math.cos(angle),
            corner.y + radius * math.sin(angle), 0));
      }
    }
  }
  final lower = [
    for (final p in ring) p + centre + ConceptVector(0, 0, -size.z / 2)
  ];
  final upper = [
    for (final p in ring) p + centre + ConceptVector(0, 0, size.z / 2)
  ];
  final faces = <ConceptFace>[
    ConceptFace(
        partId: id,
        points: upper,
        normal: const ConceptVector(0, 0, 1),
        color: color),
    ConceptFace(
        partId: id,
        points: lower.reversed.toList(),
        normal: const ConceptVector(0, 0, -1),
        color: color),
  ];
  for (var i = 0; i < ring.length; i++) {
    final next = (i + 1) % ring.length;
    final normal =
        (lower[next] - lower[i]).cross(upper[next] - lower[i]).normalized;
    faces.add(ConceptFace(
        partId: id,
        points: [lower[i], lower[next], upper[next], upper[i]],
        normal: normal,
        color: color,
        outline: !round && ring.length == 4));
  }
  return faces;
}

List<ConceptFace> _sphere(
    String id, ConceptVector size, ConceptVector centre, Color color) {
  final faces = <ConceptFace>[];
  ConceptVector at(int row, int col) {
    final latitude = -math.pi / 2 + row * math.pi / 8;
    final longitude = col * math.pi * 2 / 16;
    return centre +
        ConceptVector(
            math.cos(latitude) * math.cos(longitude) * size.x / 2,
            math.cos(latitude) * math.sin(longitude) * size.y / 2,
            math.sin(latitude) * size.z / 2);
  }

  for (var row = 0; row < 8; row++) {
    for (var col = 0; col < 16; col++) {
      final points = [
        at(row, col),
        at(row, col + 1),
        at(row + 1, col + 1),
        at(row + 1, col)
      ];
      final middle = points.reduce((a, b) => a + b) * .25;
      faces.add(ConceptFace(
          partId: id,
          points: points,
          normal: (middle - centre).normalized,
          color: color,
          outline: false));
    }
  }
  return faces;
}
