import 'dart:convert';
import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../../../data/models/json.dart';
import '../../core/widgets.dart';

/// Display-only separation of the immutable part meshes in an assembly manifest.
class CadAssemblyPreview extends StatefulWidget {
  const CadAssemblyPreview(
      {super.key,
      required this.assembly,
      required this.fallback,
      this.height = 330});
  final JsonMap assembly;
  final Widget fallback;
  final double height;
  @override
  State<CadAssemblyPreview> createState() => _CadAssemblyPreviewState();
}

class _CadAssemblyPreviewState extends State<CadAssemblyPreview> {
  late List<_Part> parts;
  late String signature;
  double azimuth = .75, elevation = .5, zoom = 1, explosion = 0;
  String? selected;

  @override
  void initState() {
    super.initState();
    read();
  }

  void read() {
    signature = jsonEncode(widget.assembly);
    final records = asMaps(widget.assembly['parts']).take(64).toList();
    final ceiling =
        records.isEmpty ? 1800 : math.min(1800, 12000 ~/ records.length);
    final seen = <String>{};
    parts = [
      for (var i = 0; i < records.length; i++)
        if (_Part.parse(records[i], i, ceiling) case final part?)
          if (seen.add(part.id)) part
    ];
    if (!parts.any((p) => p.id == selected)) selected = null;
    if (parts.length < 2) explosion = 0;
  }

  @override
  void didUpdateWidget(covariant CadAssemblyPreview oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!identical(oldWidget.assembly, widget.assembly) &&
        jsonEncode(widget.assembly) != signature) {
      read();
    }
  }

  @override
  Widget build(BuildContext context) {
    if (parts.isEmpty) return widget.fallback;
    final chosen = parts.where((p) => p.id == selected).firstOrNull;
    return SizedBox(
        height: widget.height,
        width: double.infinity,
        child: Column(children: [
          Expanded(
              child: Semantics(
            label:
                'Generated CAD assembly, ${parts.length} parts, ${explosion == 0 ? 'assembled' : 'exploded'} view',
            child: ClipRect(
                child: GestureDetector(
              onPanUpdate: (d) => setState(() {
                azimuth += d.delta.dx * .012;
                elevation = (elevation + d.delta.dy * .008).clamp(.12, 1.35);
              }),
              child: MouseRegion(
                  cursor: SystemMouseCursors.grab,
                  child: CustomPaint(
                    key: const ValueKey('cad-assembly-canvas'),
                    size: Size.infinite,
                    painter: _AssemblyPainter(
                        parts, azimuth, elevation, zoom, explosion, selected),
                  )),
            )),
          )),
          SizedBox(
              height: 36,
              child: SingleChildScrollView(
                  scrollDirection: Axis.horizontal,
                  child: Row(children: [
                    for (final part in parts)
                      Padding(
                          padding: const EdgeInsets.only(right: 7),
                          child: Semantics(
                            label: '${part.label}, quantity ${part.quantity}',
                            selected: part.id == selected,
                            child: ActionChip(
                              key: ValueKey('cad-part-${part.id}'),
                              avatar: Icon(Icons.circle,
                                  color: part.color, size: 9),
                              label: Text(
                                  '${part.label}${part.quantity > 1 ? ' ×${part.quantity}' : ''}',
                                  style: const TextStyle(fontSize: 11)),
                              backgroundColor: selected == part.id
                                  ? KColors.surface3
                                  : KColors.surface,
                              side: BorderSide(
                                  color: selected == part.id
                                      ? part.color
                                      : KColors.border),
                              onPressed: () => setState(() => selected =
                                  selected == part.id ? null : part.id),
                              visualDensity: VisualDensity.compact,
                            ),
                          )),
                  ]))),
          if (parts.length > 1)
            SizedBox(
                height: 38,
                child: Row(children: [
                  const Text('Explode',
                      style: TextStyle(fontSize: 11, color: KColors.muted)),
                  Expanded(
                      child: Slider(
                    key: const ValueKey('cad-explosion-slider'),
                    value: explosion,
                    label: '${(explosion * 100).round()}%',
                    semanticFormatterCallback: (v) =>
                        '${(v * 100).round()} percent exploded',
                    onChanged: (value) => setState(() => explosion = value),
                  )),
                  IconButton(
                      tooltip: 'Assemble CAD parts',
                      onPressed: explosion > 0
                          ? () => setState(() => explosion = 0)
                          : null,
                      icon: const Icon(Icons.layers_outlined, size: 17)),
                ])),
          SizedBox(
              height: 36,
              child: Row(children: [
                Expanded(
                    child: Text(
                  chosen == null
                      ? parts.length > 1
                          ? '${parts.length} parts · drag to orbit'
                          : 'Single CAD body · drag to orbit'
                      : '${chosen.label}${chosen.material == null ? '' : ' · ${chosen.material}'}',
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(fontSize: 11, color: KColors.muted),
                )),
                IconButton(
                    tooltip: 'Zoom out CAD',
                    visualDensity: VisualDensity.compact,
                    onPressed: zoom > .65
                        ? () =>
                            setState(() => zoom = (zoom - .15).clamp(.65, 1.8))
                        : null,
                    icon: const Icon(Icons.remove, size: 16)),
                IconButton(
                    tooltip: 'Zoom in CAD',
                    visualDensity: VisualDensity.compact,
                    onPressed: zoom < 1.8
                        ? () =>
                            setState(() => zoom = (zoom + .15).clamp(.65, 1.8))
                        : null,
                    icon: const Icon(Icons.add, size: 16)),
                IconButton(
                    tooltip: 'Reset CAD view',
                    visualDensity: VisualDensity.compact,
                    onPressed: () => setState(() {
                          azimuth = .75;
                          elevation = .5;
                          zoom = 1;
                          explosion = 0;
                          selected = null;
                        }),
                    icon: const Icon(Icons.restart_alt, size: 16)),
              ])),
        ]));
  }
}

const _colors = [
  Color(0xffd3d8e0),
  Color(0xff7695b3),
  Color(0xffc88f70),
  Color(0xff739f8a),
  Color(0xffaaa1c0),
  Color(0xffbfac6d),
  Color(0xffb67e89),
  Color(0xff72a6a7)
];
double? _finite(Object? raw) {
  final value = raw is num ? raw.toDouble() : double.tryParse('$raw');
  return value != null && value.isFinite && value.abs() <= 1000000
      ? value
      : null;
}

class _P {
  const _P(this.x, this.y, this.z);
  final double x, y, z;
  static const zero = _P(0, 0, 0);
  _P operator +(_P p) => _P(x + p.x, y + p.y, z + p.z);
  _P operator -(_P p) => _P(x - p.x, y - p.y, z - p.z);
  _P operator *(double s) => _P(x * s, y * s, z * s);
  double dot(_P p) => x * p.x + y * p.y + z * p.z;
  _P cross(_P p) => _P(y * p.z - z * p.y, z * p.x - x * p.z, x * p.y - y * p.x);
  double get length => math.sqrt(dot(this));
  static _P vector(Object? raw) {
    final m = asMap(raw);
    return _P(_finite(m['x']) ?? 0, _finite(m['y']) ?? 0, _finite(m['z']) ?? 0);
  }

  _P rotate(_P angle) {
    final a = angle * (math.pi / 180);
    final y1 = y * math.cos(a.x) - z * math.sin(a.x),
        z1 = y * math.sin(a.x) + z * math.cos(a.x);
    final x2 = x * math.cos(a.y) + z1 * math.sin(a.y),
        z2 = -x * math.sin(a.y) + z1 * math.cos(a.y);
    return _P(x2 * math.cos(a.z) - y1 * math.sin(a.z),
        x2 * math.sin(a.z) + y1 * math.cos(a.z), z2);
  }
}

class _Part {
  const _Part(this.id, this.label, this.quantity, this.material, this.color,
      this.triangles, this.center);
  final String id, label;
  final int quantity;
  final String? material;
  final Color color;
  final List<List<_P>> triangles;
  final _P center;

  static _Part? parse(JsonMap data, int index, int ceiling) {
    final id = '${data['id'] ?? ''}';
    if (id.isEmpty || id.length > 120) return null;
    final all = asList(asMap(data['mesh'])['triangles']);
    final stride = math.max(1, (all.length / ceiling).ceil());
    final transform = asMap(data['transform']);
    final translation = _P.vector(transform['translation']),
        rotation = _P.vector(transform['rotation']);
    final triangles = <List<_P>>[];
    for (var i = 0; i < all.length && triangles.length < ceiling; i += stride) {
      final triangle = asList(all[i]);
      if (triangle.length != 3) continue;
      final points = <_P>[];
      for (final raw in triangle) {
        final c = asList(raw);
        if (c.length != 3) break;
        final x = _finite(c[0]), y = _finite(c[1]), z = _finite(c[2]);
        if (x == null || y == null || z == null) break;
        points.add(_P(x, y, z).rotate(rotation) + translation);
      }
      if (points.length == 3 &&
          (points[1] - points[0]).cross(points[2] - points[0]).length > 1e-10) {
        triangles.add(points);
      }
    }
    if (triangles.isEmpty) return null;
    var low = triangles.first.first, high = low;
    for (final t in triangles) {
      for (final p in t) {
        low = _P(
            math.min(low.x, p.x), math.min(low.y, p.y), math.min(low.z, p.z));
        high = _P(math.max(high.x, p.x), math.max(high.y, p.y),
            math.max(high.z, p.z));
      }
    }
    final count = _finite(data['quantity']);
    return _Part(
        id,
        '${data['label'] ?? id}',
        count != null && count >= 1 ? count.floor().clamp(1, 1000) : 1,
        data['material']?.toString(),
        _colors[index % _colors.length],
        triangles,
        (low + high) * .5);
  }
}

class _AssemblyPainter extends CustomPainter {
  _AssemblyPainter(this.parts, this.azimuth, this.elevation, this.zoom,
      this.explosion, this.selected);
  final List<_Part> parts;
  final double azimuth, elevation, zoom, explosion;
  final String? selected;
  @override
  void paint(Canvas canvas, Size size) {
    if (size.isEmpty || parts.isEmpty) return;
    final right = _P(math.sin(azimuth), -math.cos(azimuth), 0);
    final up = _P(-math.cos(azimuth) * math.sin(elevation),
        -math.sin(azimuth) * math.sin(elevation), math.cos(elevation));
    final toward = _P(math.cos(azimuth) * math.cos(elevation),
        math.sin(azimuth) * math.cos(elevation), math.sin(elevation));
    Offset project(_P p) => Offset(p.dot(right), -p.dot(up));
    final center = parts.fold<_P>(_P.zero, (p, part) => p + part.center) *
        (1 / parts.length);
    final extent = parts
        .expand((p) => p.triangles)
        .expand((t) => t)
        .map((p) => (p - center).length)
        .fold<double>(1, math.max);
    final offsets = [
      for (var i = 0; i < parts.length; i++)
        if ((parts[i].center - center).length > .01)
          (parts[i].center - center) *
              (extent * .7 / (parts[i].center - center).length)
        else
          _P(0, 0, (i - (parts.length - 1) / 2) * extent * .8)
    ];
    final fit = <Offset>[];
    for (var i = 0; i < parts.length; i++) {
      for (final t in parts[i].triangles) {
        for (final p in t) {
          fit.add(project(p));
          fit.add(project(p + offsets[i]));
        }
      }
    }
    var low = fit.first, high = low;
    for (final p in fit) {
      low = Offset(math.min(low.dx, p.dx), math.min(low.dy, p.dy));
      high = Offset(math.max(high.dx, p.dx), math.max(high.dy, p.dy));
    }
    final bounds = Rect.fromPoints(low, high);
    final scale = math.min(
            math.max(1, size.width - 70) / math.max(.01, bounds.width),
            math.max(1, size.height - 40) / math.max(.01, bounds.height)) *
        zoom;
    Offset screen(_P p) =>
        (project(p) - bounds.center) * scale + size.center(Offset.zero);
    final faces =
        <({List<_P> points, Color color, double depth, bool chosen})>[];
    for (var i = 0; i < parts.length; i++) {
      final part = parts[i], offset = offsets[i] * explosion;
      for (final triangle in part.triangles) {
        final points = triangle.map((p) => p + offset).toList(growable: false);
        faces.add((
          points: points,
          color: part.color,
          depth: points.fold<double>(0, (n, p) => n + p.dot(toward)),
          chosen: selected == part.id
        ));
      }
    }
    faces.sort((a, b) => a.depth.compareTo(b.depth));
    for (final face in faces) {
      final normal = (face.points[1] - face.points[0])
          .cross(face.points[2] - face.points[0]);
      final brightness =
          normal.length == 0 ? .5 : (normal.dot(toward) / normal.length).abs();
      final path = Path()..addPolygon(face.points.map(screen).toList(), true);
      canvas.drawPath(
          path,
          Paint()
            ..color = Color.lerp(
                KColors.surface2, face.color, .32 + brightness * .68)!);
      if (face.chosen) {
        canvas.drawPath(
            path,
            Paint()
              ..color = KColors.accent.withValues(alpha: .8)
              ..strokeWidth = .8
              ..style = PaintingStyle.stroke);
      }
    }
    if (explosion > .05 || selected != null) {
      for (var i = 0; i < parts.length; i++) {
        final part = parts[i];
        if (parts.length > 8 && selected != part.id) continue;
        final at = screen(part.center + offsets[i] * explosion);
        final text = TextPainter(
            text: TextSpan(
                text: part.label,
                style: const TextStyle(
                    fontFamily: 'Geist', fontSize: 10, color: KColors.text)),
            textDirection: TextDirection.ltr,
            maxLines: 1,
            ellipsis: '…')
          ..layout(maxWidth: math.max(20, math.min(130, size.width * .45)));
        final rect = Rect.fromLTWH(
            (at.dx + 10).clamp(4, math.max(4, size.width - text.width - 14)),
            (at.dy - 20).clamp(4, math.max(4, size.height - text.height - 12)),
            text.width + 10,
            text.height + 6);
        canvas.drawLine(
            at,
            rect.center,
            Paint()
              ..color = part.color
              ..strokeWidth = .8);
        canvas.drawRRect(
            RRect.fromRectAndRadius(rect, const Radius.circular(4)),
            Paint()..color = KColors.bg.withValues(alpha: .9));
        text.paint(canvas, rect.topLeft + const Offset(5, 3));
      }
    }
  }

  @override
  bool shouldRepaint(covariant _AssemblyPainter old) =>
      parts != old.parts ||
      azimuth != old.azimuth ||
      elevation != old.elevation ||
      zoom != old.zoom ||
      explosion != old.explosion ||
      selected != old.selected;
}
