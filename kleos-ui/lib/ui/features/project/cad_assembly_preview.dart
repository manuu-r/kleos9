import 'dart:convert';
import 'dart:math' as math;

import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';
import 'package:flutter/physics.dart';

import '../../../data/models/json.dart';
import '../../core/widgets.dart';

const _minZoom = .6, _maxZoom = 4.0;

/// Display-only separation of the immutable part meshes in an assembly manifest.
///
/// Built for thumbs first: one finger orbits (with a short inertial spin), two
/// fingers pinch to zoom, double tap resets, the mouse wheel zooms, and every
/// control is at least 44px. On a phone the viewer can open full screen so the
/// model gets the whole display instead of a strip of the page.
class CadAssemblyPreview extends StatefulWidget {
  const CadAssemblyPreview(
      {super.key,
      required this.assembly,
      required this.fallback,
      this.height = 330,
      this.fullscreen = false});
  final JsonMap assembly;
  final Widget fallback;
  final double height;

  /// Fill the available space instead of using [height]; hides the expand
  /// button. Used by the full-screen route.
  final bool fullscreen;
  @override
  State<CadAssemblyPreview> createState() => _CadAssemblyPreviewState();
}

class _CadAssemblyPreviewState extends State<CadAssemblyPreview>
    with SingleTickerProviderStateMixin {
  late List<_Part> parts;
  late String signature;
  double azimuth = .75, elevation = .5, zoom = 1, explosion = 0;
  double _zoomAtStart = 1;
  String? selected;
  bool _interacting = false;

  /// Drives the inertial spin after a fling; unbounded so it can run past 1.
  late final AnimationController _spin =
      AnimationController.unbounded(vsync: this)..addListener(_onSpin);
  double _spinFrom = 0;

  @override
  void initState() {
    super.initState();
    read();
  }

  @override
  void dispose() {
    _spin.dispose();
    super.dispose();
  }

  void _onSpin() => setState(() => azimuth = _spinFrom + _spin.value);

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

  void _reset() {
    _spin.stop();
    setState(() {
      azimuth = .75;
      elevation = .5;
      zoom = 1;
      explosion = 0;
      selected = null;
    });
  }

  void _zoomBy(double factor) =>
      setState(() => zoom = (zoom * factor).clamp(_minZoom, _maxZoom));

  void _onScaleStart(ScaleStartDetails d) {
    _spin.stop();
    _zoomAtStart = zoom;
    setState(() => _interacting = true);
  }

  void _onScaleUpdate(ScaleUpdateDetails d) => setState(() {
        azimuth += d.focalPointDelta.dx * .012;
        elevation = (elevation + d.focalPointDelta.dy * .008).clamp(.12, 1.35);
        if (d.pointerCount > 1) {
          zoom = (_zoomAtStart * d.scale).clamp(_minZoom, _maxZoom);
        }
      });

  void _onScaleEnd(ScaleEndDetails d) {
    setState(() => _interacting = false);
    final vx = d.velocity.pixelsPerSecond.dx;
    if (MediaQuery.disableAnimationsOf(context) || vx.abs() < 120) return;
    _spinFrom = azimuth;
    _spin.value = 0;
    _spin.animateWith(FrictionSimulation(.06, 0, (vx * .012).clamp(-9, 9)));
  }

  Widget _control(
          {required IconData icon,
          required String tooltip,
          required VoidCallback? onPressed}) =>
      SizedBox(
          width: 44,
          height: 44,
          child: IconButton(
              tooltip: tooltip,
              onPressed: onPressed,
              style: IconButton.styleFrom(
                  backgroundColor: KColors.surface3.withValues(alpha: .92),
                  disabledBackgroundColor:
                      KColors.surface2.withValues(alpha: .8),
                  foregroundColor: KColors.text,
                  disabledForegroundColor: KColors.faint.withValues(alpha: .6),
                  shape: KShape.border(KRadius.control)),
              icon: Icon(icon, size: 19)));

  void _openFullscreen() => Navigator.of(context).push(MaterialPageRoute<void>(
      fullscreenDialog: true,
      builder: (_) => Scaffold(
          backgroundColor: KColors.bg,
          appBar: AppBar(
              backgroundColor: KColors.bg,
              scrolledUnderElevation: 0,
              title: Text('CAD assembly · ${parts.length} '
                  '${parts.length == 1 ? 'part' : 'parts'}')),
          body: SafeArea(
              child: Padding(
                  padding: const EdgeInsets.fromLTRB(12, 4, 12, 12),
                  child: CadAssemblyPreview(
                      assembly: widget.assembly,
                      fallback: widget.fallback,
                      fullscreen: true))))));

  @override
  Widget build(BuildContext context) {
    if (parts.isEmpty) return widget.fallback;
    final chosen = parts.where((p) => p.id == selected).firstOrNull;
    final body = Column(children: [
      Expanded(
          child: KGridBackdrop(
              child: Stack(children: [
        Positioned.fill(
            child: Semantics(
          label:
              'Generated CAD assembly, ${parts.length} parts, ${explosion == 0 ? 'assembled' : 'exploded'} view. Drag to rotate, pinch to zoom.',
          child: ClipRect(
              child: Listener(
            onPointerSignal: (event) {
              if (event is PointerScrollEvent) {
                _zoomBy(math.exp(-event.scrollDelta.dy * .0015));
              }
            },
            child: GestureDetector(
              behavior: HitTestBehavior.opaque,
              onScaleStart: _onScaleStart,
              onScaleUpdate: _onScaleUpdate,
              onScaleEnd: _onScaleEnd,
              onDoubleTap: _reset,
              child: MouseRegion(
                  cursor: _interacting
                      ? SystemMouseCursors.grabbing
                      : SystemMouseCursors.grab,
                  child: CustomPaint(
                    key: const ValueKey('cad-assembly-canvas'),
                    size: Size.infinite,
                    painter: _AssemblyPainter(
                        parts, azimuth, elevation, zoom, explosion, selected),
                  )),
            ),
          )),
        )),
        if (!widget.fullscreen)
          Positioned(
              left: 8,
              top: 8,
              child: _control(
                  icon: Icons.open_in_full,
                  tooltip: 'Expand CAD viewer',
                  onPressed: _openFullscreen)),
        Positioned(
            right: 8,
            top: 8,
            child: Column(mainAxisSize: MainAxisSize.min, children: [
              _control(
                  icon: Icons.add,
                  tooltip: 'Zoom in CAD',
                  onPressed: zoom < _maxZoom ? () => _zoomBy(1.25) : null),
              const SizedBox(height: 8),
              _control(
                  icon: Icons.remove,
                  tooltip: 'Zoom out CAD',
                  onPressed: zoom > _minZoom ? () => _zoomBy(.8) : null),
              const SizedBox(height: 8),
              _control(
                  icon: Icons.restart_alt,
                  tooltip: 'Reset CAD view',
                  onPressed: _reset),
            ])),
        Positioned(
            left: 12,
            bottom: 8,
            right: 64,
            child: IgnorePointer(
                child: Text(
              chosen == null
                  ? parts.length > 1
                      ? '${parts.length} parts · drag to orbit'
                      : 'Single CAD body · drag to orbit'
                  : '${chosen.label}${chosen.material == null ? '' : ' · ${chosen.material}'}',
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: const TextStyle(fontSize: 12.5, color: KColors.muted),
            ))),
      ]))),
      const SizedBox(height: 10),
      SizedBox(
          height: 40,
          child: ListView.separated(
              scrollDirection: Axis.horizontal,
              itemCount: parts.length,
              separatorBuilder: (_, __) => const SizedBox(width: 8),
              itemBuilder: (context, i) {
                final part = parts[i];
                final on = part.id == selected;
                return Semantics(
                    label: '${part.label}, quantity ${part.quantity}',
                    selected: on,
                    child: ExcludeSemantics(
                        child: KTap(
                            key: ValueKey('cad-part-${part.id}'),
                            flat: true,
                            onTap: () =>
                                setState(() => selected = on ? null : part.id),
                            builder: (context, hovered) => AnimatedContainer(
                                duration: KMotion.base,
                                curve: KMotion.out,
                                height: 40,
                                padding:
                                    const EdgeInsets.symmetric(horizontal: 14),
                                decoration: KShape.surface(
                                    on
                                        ? KColors.text
                                        : hovered
                                            ? const Color(0xff343438)
                                            : KColors.surface3,
                                    radius: KRadius.pill),
                                child: Row(
                                    mainAxisSize: MainAxisSize.min,
                                    children: [
                                      Container(
                                          width: 8,
                                          height: 8,
                                          decoration: BoxDecoration(
                                              color: part.color,
                                              shape: BoxShape.circle)),
                                      const SizedBox(width: 8),
                                      Text(
                                          '${part.label}${part.quantity > 1 ? ' ×${part.quantity}' : ''}',
                                          style: TextStyle(
                                              fontSize: 13,
                                              fontWeight: on
                                                  ? FontWeight.w600
                                                  : FontWeight.w400,
                                              color: on
                                                  ? KColors.bg
                                                  : KColors.muted)),
                                    ])))));
              })),
      if (parts.length > 1)
        SizedBox(
            height: 48,
            child: Row(children: [
              const Text('Explode',
                  style: TextStyle(fontSize: 13, color: KColors.muted)),
              Expanded(
                  child: Slider(
                key: const ValueKey('cad-explosion-slider'),
                value: explosion,
                label: '${(explosion * 100).round()}%',
                semanticFormatterCallback: (v) =>
                    '${(v * 100).round()} percent exploded',
                onChanged: (value) => setState(() => explosion = value),
              )),
              SizedBox(
                  width: 44,
                  height: 44,
                  child: IconButton(
                      tooltip: 'Assemble CAD parts',
                      onPressed: explosion > 0
                          ? () => setState(() => explosion = 0)
                          : null,
                      icon: const Icon(Icons.layers_outlined, size: 19))),
            ])),
    ]);
    if (widget.fullscreen) {
      return SizedBox.expand(child: body);
    }
    return SizedBox(height: widget.height, width: double.infinity, child: body);
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
            math.max(1, size.width - 72) / math.max(.01, bounds.width),
            math.max(1, size.height - 64) / math.max(.01, bounds.height)) *
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
              ..color = KColors.accent
              ..strokeWidth = 1.2
              ..style = PaintingStyle.stroke);
      }
    }
    if (explosion > .05 || selected != null) {
      final placed = <Rect>[];
      for (var i = 0; i < parts.length; i++) {
        final part = parts[i];
        if (parts.length > 8 && selected != part.id) continue;
        final at = screen(part.center + offsets[i] * explosion);
        final text = TextPainter(
            text: TextSpan(
                text: part.label,
                style: const TextStyle(
                    fontFamily: 'Geist', fontSize: 12, color: KColors.text)),
            textDirection: TextDirection.ltr,
            maxLines: 1,
            ellipsis: '…')
          ..layout(maxWidth: math.max(20, math.min(130, size.width * .45)));
        var rect = Rect.fromLTWH(
            (at.dx + 10).clamp(4, math.max(4, size.width - text.width - 14)),
            (at.dy - 20).clamp(4, math.max(4, size.height - text.height - 12)),
            text.width + 10,
            text.height + 6);
        // Nudge a label down until it no longer sits on another one.
        for (var tries = 0;
            tries < 8 && placed.any((other) => other.overlaps(rect));
            tries++) {
          rect = rect.shift(Offset(0, rect.height + 4));
        }
        rect = Rect.fromLTWH(
            rect.left,
            rect.top.clamp(4, math.max(4, size.height - rect.height - 4)),
            rect.width,
            rect.height);
        placed.add(rect);
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
