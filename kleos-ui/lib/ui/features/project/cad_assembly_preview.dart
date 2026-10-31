import 'dart:convert';
import 'dart:math' as math;
import 'dart:typed_data';
import 'dart:ui' as ui;

import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';
import 'package:flutter/physics.dart';

import '../../../data/models/json.dart';
import '../../core/widgets.dart';

const _minZoom = .5, _maxZoom = 6.0;

/// Standard CAD views. Azimuth and elevation are in radians; +Z is up and the
/// model's front faces -Y, so Front looks along +Y.
enum _CadView {
  iso('Isometric', .75, .5),
  front('Front', -math.pi / 2, 0),
  back('Back', math.pi / 2, 0),
  left('Left', math.pi, 0),
  right('Right', 0, 0),
  top('Top', -math.pi / 2, math.pi / 2 - .002),
  bottom('Bottom', -math.pi / 2, -(math.pi / 2 - .002));

  const _CadView(this.label, this.azimuth, this.elevation);
  final String label;
  final double azimuth, elevation;
}

/// Display-only separation of the immutable part meshes in an assembly manifest.
///
/// Made to be handled: one finger orbits (with inertia), two fingers pinch to
/// zoom and drag to pan, the wheel zooms, tapping a part selects it (the rest
/// fade back), a views menu snaps to Front/Top/Right/Iso with an animated
/// camera, and the model opens full screen on a phone. Lit by a key and fill
/// light that follow the camera, standing on a ground grid with a soft contact
/// shadow, with an axis gizmo in the corner. Drawn with one batched call per
/// frame so orbiting stays smooth on a phone.
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
    with TickerProviderStateMixin {
  late List<_Part> parts;
  late _Scene scene;
  late String signature;
  double azimuth = .75, elevation = .5, zoom = 1, explosion = 0;
  Offset pan = Offset.zero;
  double _zoomAtStart = 1;
  String? selected;
  bool _interacting = false;
  DateTime? _lastTap;
  Offset _lastTapAt = Offset.zero;

  /// Inertial spin after a fling; unbounded so it can run past 1.
  late final AnimationController _spin =
      AnimationController.unbounded(vsync: this)..addListener(_onSpin);
  double _spinFrom = 0;

  /// Eased camera moves (views menu, reset).
  late final AnimationController _cam = AnimationController(
      vsync: this, duration: const Duration(milliseconds: 460))
    ..addListener(_onCam);
  double _a0 = 0, _a1 = 0, _e0 = 0, _e1 = 0, _z0 = 1, _z1 = 1;
  Offset _p0 = Offset.zero, _p1 = Offset.zero;

  @override
  void initState() {
    super.initState();
    read();
  }

  @override
  void dispose() {
    _spin.dispose();
    _cam.dispose();
    super.dispose();
  }

  void _onSpin() => setState(() => azimuth = _spinFrom + _spin.value);

  void _onCam() {
    final t = Curves.easeInOutCubic.transform(_cam.value);
    setState(() {
      azimuth = _a0 + (_a1 - _a0) * t;
      elevation = _e0 + (_e1 - _e0) * t;
      zoom = _z0 + (_z1 - _z0) * t;
      pan = Offset.lerp(_p0, _p1, t)!;
    });
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
    scene = _Scene(parts);
  }

  @override
  void didUpdateWidget(covariant CadAssemblyPreview oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!identical(oldWidget.assembly, widget.assembly) &&
        jsonEncode(widget.assembly) != signature) {
      read();
    }
  }

  /// Glide the camera to a pose along the shortest way round.
  void _goTo(
      {double? az, double? el, double zoomTo = 1, Offset panTo = Offset.zero}) {
    _spin.stop();
    var da = (az ?? azimuth) - azimuth;
    da = ((da + math.pi) % (2 * math.pi)) - math.pi;
    if (MediaQuery.disableAnimationsOf(context)) {
      setState(() {
        azimuth += da;
        elevation = el ?? elevation;
        zoom = zoomTo;
        pan = panTo;
      });
      return;
    }
    _a0 = azimuth;
    _a1 = azimuth + da;
    _e0 = elevation;
    _e1 = el ?? elevation;
    _z0 = zoom;
    _z1 = zoomTo;
    _p0 = pan;
    _p1 = panTo;
    _cam.forward(from: 0);
  }

  void _reset() {
    setState(() {
      explosion = 0;
      selected = null;
    });
    _goTo(az: .75, el: .5);
  }

  void _zoomBy(double factor) =>
      setState(() => zoom = (zoom * factor).clamp(_minZoom, _maxZoom));

  void _onScaleStart(ScaleStartDetails d) {
    _spin.stop();
    _cam.stop();
    _zoomAtStart = zoom;
    setState(() => _interacting = true);
  }

  void _onScaleUpdate(ScaleUpdateDetails d) => setState(() {
        if (d.pointerCount > 1) {
          // Two fingers: pinch to zoom, drag to pan.
          zoom = (_zoomAtStart * d.scale).clamp(_minZoom, _maxZoom);
          pan += d.focalPointDelta;
        } else {
          azimuth += d.focalPointDelta.dx * .011;
          elevation =
              (elevation + d.focalPointDelta.dy * .009).clamp(-1.5, 1.5);
        }
      });

  void _onScaleEnd(ScaleEndDetails d) {
    setState(() => _interacting = false);
    if (d.pointerCount > 0) return;
    final vx = d.velocity.pixelsPerSecond.dx;
    if (MediaQuery.disableAnimationsOf(context) || vx.abs() < 140) return;
    _spinFrom = azimuth;
    _spin.value = 0;
    _spin.animateWith(FrictionSimulation(.05, 0, (vx * .011).clamp(-10, 10)));
  }

  /// A tap selects the part under the finger at once (no double-tap delay); a
  /// second tap right after resets the view.
  void _onTapUp(TapUpDetails d) {
    final now = DateTime.now();
    if (_lastTap != null &&
        now.difference(_lastTap!) < const Duration(milliseconds: 320) &&
        (d.localPosition - _lastTapAt).distance < 32) {
      _lastTap = null;
      _reset();
      return;
    }
    _lastTap = now;
    _lastTapAt = d.localPosition;
    final id = scene.pick(d.localPosition);
    setState(() => selected = id == null || id == selected ? null : id);
  }

  Widget _control(
          {required IconData icon,
          required String tooltip,
          required VoidCallback? onPressed}) =>
      KToolButton(icon: icon, tooltip: tooltip, onPressed: onPressed);

  Widget _viewsMenu() => PopupMenuButton<_CadView>(
      tooltip: 'CAD views',
      onSelected: (v) {
        KHaptics.select();
        _goTo(az: v.azimuth, el: v.elevation);
      },
      itemBuilder: (_) => [
            for (final v in _CadView.values)
              PopupMenuItem(
                  value: v,
                  height: 44,
                  child: Text(v.label,
                      style:
                          const TextStyle(fontSize: 14, color: KColors.text))),
          ],
      child: Semantics(
          button: true,
          label: 'CAD views',
          child: ExcludeSemantics(
              child: KToolTile(icon: Icons.view_in_ar_outlined))));

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
              'Generated CAD assembly, ${parts.length} parts, ${explosion == 0 ? 'assembled' : 'exploded'} view. Drag to rotate, pinch to zoom, tap a part to select it.',
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
              onTapUp: _onTapUp,
              child: MouseRegion(
                  cursor: _interacting
                      ? SystemMouseCursors.grabbing
                      : SystemMouseCursors.grab,
                  child: CustomPaint(
                    key: const ValueKey('cad-assembly-canvas'),
                    size: Size.infinite,
                    painter: _ScenePainter(scene, azimuth, elevation, zoom, pan,
                        explosion, selected),
                  )),
            ),
          )),
        )),
        Positioned(
            left: 8,
            top: 8,
            child: Column(mainAxisSize: MainAxisSize.min, children: [
              if (!widget.fullscreen) ...[
                _control(
                    icon: Icons.open_in_full,
                    tooltip: 'Expand CAD viewer',
                    onPressed: _openFullscreen),
                const SizedBox(height: 8),
              ],
              _viewsMenu(),
            ])),
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
            left: 28,
            bottom: 16,
            right: 76,
            child: IgnorePointer(
                child: Text(
              chosen == null
                  ? parts.length > 1
                      ? '${parts.length} parts · drag to orbit'
                      : 'Single CAD body · drag to orbit'
                  : '${chosen.label}${chosen.material == null ? '' : ' · ${chosen.material}'}',
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: KType.monoSmall,
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
                return KChip(
                    key: ValueKey('cad-part-${part.id}'),
                    label:
                        '${part.label}${part.quantity > 1 ? ' ×${part.quantity}' : ''}',
                    semanticLabel: '${part.label}, quantity ${part.quantity}',
                    dot: part.color,
                    selected: on,
                    onTap: () =>
                        setState(() => selected = on ? null : part.id));
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
        KViz.parts[index % KViz.parts.length],
        triangles,
        (low + high) * .5);
  }
}

/// The assembly flattened into typed arrays once, so every frame is a tight
/// loop: triangle coordinates, face normals and the owning part.
class _Scene {
  _Scene(this.parts) {
    var n = 0;
    for (final p in parts) {
      n += p.triangles.length;
    }
    count = n;
    tri = Float64List(n * 9);
    nrm = Float64List(n * 3);
    owner = Int32List(n);
    var lo = _P.zero, hi = _P.zero, first = true;
    var t = 0;
    for (var i = 0; i < parts.length; i++) {
      for (final tr in parts[i].triangles) {
        for (var k = 0; k < 3; k++) {
          tri[t * 9 + k * 3] = tr[k].x;
          tri[t * 9 + k * 3 + 1] = tr[k].y;
          tri[t * 9 + k * 3 + 2] = tr[k].z;
          if (first) {
            lo = hi = tr[k];
            first = false;
          } else {
            lo = _P(math.min(lo.x, tr[k].x), math.min(lo.y, tr[k].y),
                math.min(lo.z, tr[k].z));
            hi = _P(math.max(hi.x, tr[k].x), math.max(hi.y, tr[k].y),
                math.max(hi.z, tr[k].z));
          }
        }
        final c = (tr[1] - tr[0]).cross(tr[2] - tr[0]);
        final len = c.length;
        nrm[t * 3] = len == 0 ? 0 : c.x / len;
        nrm[t * 3 + 1] = len == 0 ? 0 : c.y / len;
        nrm[t * 3 + 2] = len == 0 ? 1 : c.z / len;
        owner[t] = i;
        t++;
      }
    }
    center = (lo + hi) * .5;
    groundZ = lo.z;
    var r = 1.0;
    for (var i = 0; i < n * 3; i++) {
      final dx = tri[i * 3] - center.x,
          dy = tri[i * 3 + 1] - center.y,
          dz = tri[i * 3 + 2] - center.z;
      r = math.max(r, math.sqrt(dx * dx + dy * dy + dz * dz));
    }
    extent = r;
    // Each part's explosion direction: away from the assembly centre, or
    // stacked along Z when it sits on the centre.
    final partCentre = parts.fold<_P>(_P.zero, (p, part) => p + part.center) *
        (1 / math.max(1, parts.length));
    offsets = [
      for (var i = 0; i < parts.length; i++)
        if ((parts[i].center - partCentre).length > .01)
          (parts[i].center - partCentre) *
              (extent * .7 / (parts[i].center - partCentre).length)
        else
          _P(0, 0, (i - (parts.length - 1) / 2) * extent * .8)
    ];
  }

  final List<_Part> parts;
  late final int count;
  late final Float64List tri, nrm;
  late final Int32List owner;
  late final _P center;
  late final double extent, groundZ;
  late final List<_P> offsets;

  // The last frame's screen-space triangles, back to front, kept for picking.
  Float32List lastPos = Float32List(0);
  Int32List lastOwner = Int32List(0);

  /// The part whose nearest visible triangle contains [p], or null.
  String? pick(Offset p) {
    for (var k = lastOwner.length - 1; k >= 0; k--) {
      final i = k * 6;
      final ax = lastPos[i], ay = lastPos[i + 1];
      final bx = lastPos[i + 2], by = lastPos[i + 3];
      final cx = lastPos[i + 4], cy = lastPos[i + 5];
      final d1 = (p.dx - bx) * (ay - by) - (ax - bx) * (p.dy - by);
      final d2 = (p.dx - cx) * (by - cy) - (bx - cx) * (p.dy - cy);
      final d3 = (p.dx - ax) * (cy - ay) - (cx - ax) * (p.dy - ay);
      final neg = d1 < 0 || d2 < 0 || d3 < 0;
      final pos = d1 > 0 || d2 > 0 || d3 > 0;
      if (!(neg && pos)) return parts[lastOwner[k]].id;
    }
    return null;
  }
}

/// Draws the scene: ground, contact shadow, lit faces in one batched call,
/// callouts and the axis gizmo.
class _ScenePainter extends CustomPainter {
  _ScenePainter(this.scene, this.azimuth, this.elevation, this.zoom, this.pan,
      this.explosion, this.selected);
  final _Scene scene;
  final double azimuth, elevation, zoom, explosion;
  final Offset pan;
  final String? selected;

  @override
  void paint(Canvas canvas, Size size) {
    if (size.isEmpty || scene.count == 0) return;
    final sa = math.sin(azimuth), ca = math.cos(azimuth);
    final se = math.sin(elevation), ce = math.cos(elevation);
    // Camera basis: screen right, screen up, and the direction to the viewer.
    final rx = sa, ry = -ca, rz = 0.0;
    final ux = -ca * se, uy = -sa * se, uz = ce;
    final tx = ca * ce, ty = sa * ce, tz = se;
    final fitR = scene.extent * (1 + .7 * explosion);
    final scale = (math.min(size.width, size.height) - 52).clamp(60, 4000) /
        (2 * fitR) *
        zoom;
    final cx = size.width / 2 + pan.dx, cy = size.height / 2 + pan.dy;
    final c = scene.center;

    Offset proj(double x, double y, double z) {
      final qx = x - c.x, qy = y - c.y, qz = z - c.z;
      return Offset(cx + scale * (qx * rx + qy * ry + qz * rz),
          cy - scale * (qx * ux + qy * uy + qz * uz));
    }

    _ground(canvas, proj);

    // Lights follow the camera: a key from upper left, a fill from the right.
    _V3 norm(double x, double y, double z) {
      final l = math.sqrt(x * x + y * y + z * z);
      return _V3(x / l, y / l, z / l);
    }

    final key = norm(tx * .55 + ux * .7 - rx * .55,
        ty * .55 + uy * .7 - ry * .55, tz * .55 + uz * .7 - rz * .55);
    final fill = norm(tx * .45 - ux * .15 + rx * .8,
        ty * .45 - uy * .15 + ry * .8, tz * .45 - uz * .15 + rz * .8);
    final half = norm(key.x + tx, key.y + ty, key.z + tz);
    final n = scene.count;
    final pos = Float32List(n * 6);
    final depth = Float64List(n);
    final cols = Int32List(n * 3);
    final has = selected != null;
    final dim = KColors.surface;
    final off = scene.offsets;
    final selIndex = has ? scene.parts.indexWhere((p) => p.id == selected) : -1;
    final base = [for (final p in scene.parts) p.color];
    for (var t = 0; t < n; t++) {
      final i = scene.owner[t];
      final o = off[i];
      final ox = o.x * explosion, oy = o.y * explosion, oz = o.z * explosion;
      final b = t * 9;
      var dsum = 0.0;
      for (var k = 0; k < 3; k++) {
        final x = scene.tri[b + k * 3] + ox - c.x;
        final y = scene.tri[b + k * 3 + 1] + oy - c.y;
        final z = scene.tri[b + k * 3 + 2] + oz - c.z;
        pos[t * 6 + k * 2] = (cx + scale * (x * rx + y * ry + z * rz));
        pos[t * 6 + k * 2 + 1] = (cy - scale * (x * ux + y * uy + z * uz));
        dsum += x * tx + y * ty + z * tz;
      }
      depth[t] = dsum;
      var nx = scene.nrm[t * 3],
          ny = scene.nrm[t * 3 + 1],
          nz = scene.nrm[t * 3 + 2];
      if (nx * tx + ny * ty + nz * tz < 0) {
        nx = -nx;
        ny = -ny;
        nz = -nz;
      }
      final kd = math.max(0.0, nx * key.x + ny * key.y + nz * key.z);
      final fd = math.max(0.0, nx * fill.x + ny * fill.y + nz * fill.z);
      final hd = math.max(0.0, nx * half.x + ny * half.y + nz * half.z);
      final h2 = hd * hd, h4 = h2 * h2, h8 = h4 * h4;
      final spec = h8 * h8 * h8 * h4 * .26; // ~ pow(hd, 28)
      final shade = KViz.ambient + .62 * kd + .2 * fd;
      final bc = base[i];
      var r = bc.r * 255 * shade + spec * 255;
      var g = bc.g * 255 * shade + spec * 255;
      var bl = bc.b * 255 * shade + spec * 255;
      // Shadows lean cool, like skylight.
      final cool = (1 - shade.clamp(0.0, 1.0)) * .2;
      r += (KViz.shadowTint.r * 255 - r) * cool;
      g += (KViz.shadowTint.g * 255 - g) * cool;
      bl += (KViz.shadowTint.b * 255 - bl) * cool;
      if (has) {
        if (i == selIndex) {
          // Lift the part you are holding: brighter, with a breath of coral.
          r += (255 - r) * .14 + (KColors.accent.r * 255 - r) * .07;
          g += (255 - g) * .14 + (KColors.accent.g * 255 - g) * .07;
          bl += (255 - bl) * .14 + (KColors.accent.b * 255 - bl) * .07;
        } else {
          r += (dim.r * 255 - r) * .66;
          g += (dim.g * 255 - g) * .66;
          bl += (dim.b * 255 - bl) * .66;
        }
      }
      final argb = 0xff000000 |
          (r.clamp(0, 255).round() << 16) |
          (g.clamp(0, 255).round() << 8) |
          bl.clamp(0, 255).round();
      cols[t * 3] = argb;
      cols[t * 3 + 1] = argb;
      cols[t * 3 + 2] = argb;
    }
    // Painter's algorithm: far to near. Then one draw call for all faces.
    final order = List<int>.generate(n, (i) => i)
      ..sort((a, b) => depth[a].compareTo(depth[b]));
    final sortedPos = Float32List(n * 6);
    final sortedCols = Int32List(n * 3);
    final sortedOwner = Int32List(n);
    for (var k = 0; k < n; k++) {
      final t = order[k];
      sortedPos.setRange(k * 6, k * 6 + 6, pos, t * 6);
      sortedCols.setRange(k * 3, k * 3 + 3, cols, t * 3);
      sortedOwner[k] = scene.owner[t];
    }
    // Kept so a tap can ask which part is nearest under the finger.
    scene.lastPos = sortedPos;
    scene.lastOwner = sortedOwner;
    canvas.drawVertices(
        ui.Vertices.raw(ui.VertexMode.triangles, sortedPos, colors: sortedCols),
        BlendMode.dst,
        // No edge anti-aliasing: shared edges would otherwise let the surface
        // behind show through as hairline seams between neighbouring faces.
        Paint()..isAntiAlias = false);

    _callouts(canvas, size, proj);
    _gizmo(canvas, size, rx, ry, rz, ux, uy, uz, tx, ty, tz);
  }

  void _ground(Canvas canvas, Offset Function(double, double, double) proj) {
    final c = scene.center, e = scene.extent;
    final z = scene.groundZ - e * .004;
    final half = e * 1.7, step = half / 5;
    final grid = Path();
    for (var i = -5; i <= 5; i++) {
      final u = i * step;
      grid.moveTo(
          proj(c.x + u, c.y - half, z).dx, proj(c.x + u, c.y - half, z).dy);
      final a = proj(c.x + u, c.y + half, z);
      grid.lineTo(a.dx, a.dy);
      final b0 = proj(c.x - half, c.y + u, z);
      grid.moveTo(b0.dx, b0.dy);
      final b1 = proj(c.x + half, c.y + u, z);
      grid.lineTo(b1.dx, b1.dy);
    }
    canvas.drawPath(
        grid,
        Paint()
          ..style = PaintingStyle.stroke
          ..strokeWidth = 1
          ..color = KColors.borderStrong.withValues(alpha: .38));
    // Soft contact shadow under the assembly.
    final r = e * (.82 + .3 * explosion);
    final ring = Path();
    for (var k = 0; k <= 48; k++) {
      final a = k / 48 * math.pi * 2;
      final p = proj(c.x + math.cos(a) * r, c.y + math.sin(a) * r, z);
      k == 0 ? ring.moveTo(p.dx, p.dy) : ring.lineTo(p.dx, p.dy);
    }
    final bounds = ring.getBounds();
    if (bounds.isEmpty) return;
    canvas.drawPath(
        ring,
        Paint()
          ..shader = ui.Gradient.radial(
              bounds.center,
              math.max(bounds.width, bounds.height) / 2,
              [const Color(0x70000000), const Color(0x00000000)],
              [.15, 1]));
  }

  void _callouts(
      Canvas canvas, Size size, Offset Function(double, double, double) proj) {
    final parts = scene.parts;
    if (!(explosion > .05 || selected != null)) return;
    final placed = <Rect>[];
    for (var i = 0; i < parts.length; i++) {
      final part = parts[i];
      if (parts.length > 8 && selected != part.id) continue;
      final o = scene.offsets[i] * explosion;
      final at =
          proj(part.center.x + o.x, part.center.y + o.y, part.center.z + o.z);
      final text = TextPainter(
          text: TextSpan(
              text: part.label,
              style: const TextStyle(
                  fontFamily: 'Geist', fontSize: 12, color: KColors.text)),
          textDirection: TextDirection.ltr,
          maxLines: 1,
          ellipsis: '…')
        ..layout(maxWidth: math.max(20, math.min(140, size.width * .45)));
      var rect = Rect.fromLTWH(
          (at.dx + 12).clamp(4, math.max(4, size.width - text.width - 18)),
          (at.dy - 22).clamp(4, math.max(4, size.height - text.height - 14)),
          text.width + 12,
          text.height + 8);
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
          rect.centerLeft,
          Paint()
            ..color = part.color
            ..strokeWidth = 1);
      canvas.drawCircle(at, 2.5, Paint()..color = part.color);
      canvas.drawRect(
          rect, Paint()..color = KColors.surface.withValues(alpha: .94));
      canvas.drawRect(
          rect,
          Paint()
            ..style = PaintingStyle.stroke
            ..strokeWidth = 1
            ..color = KColors.borderStrong);
      text.paint(canvas, rect.topLeft + const Offset(6, 4));
    }
  }

  /// A small X/Y/Z triad in the corner, so you always know which way you face.
  void _gizmo(Canvas canvas, Size size, double rx, double ry, double rz,
      double ux, double uy, double uz, double tx, double ty, double tz) {
    final o = Offset(size.width - 38, size.height - 38);
    const len = 17.0;
    final axes = [
      ('X', 1.0, 0.0, 0.0, KColors.accent),
      ('Y', 0.0, 1.0, 0.0, KColors.pass),
      ('Z', 0.0, 0.0, 1.0, KColors.actionInk),
    ]..sort((a, b) => (a.$2 * tx + a.$3 * ty + a.$4 * tz)
        .compareTo(b.$2 * tx + b.$3 * ty + b.$4 * tz));
    for (final (name, x, y, z, color) in axes) {
      final end = o +
          Offset((x * rx + y * ry + z * rz) * len,
              -(x * ux + y * uy + z * uz) * len);
      canvas.drawLine(
          o,
          end,
          Paint()
            ..color = color
            ..strokeWidth = 1.6
            ..strokeCap = StrokeCap.round);
      final label = TextPainter(
          text: TextSpan(
              text: name,
              style: TextStyle(
                  fontFamily: 'GeistMono',
                  fontSize: 11,
                  fontWeight: FontWeight.w600,
                  color: color)),
          textDirection: TextDirection.ltr)
        ..layout();
      final dir = (end - o);
      final push = dir.distance == 0 ? Offset.zero : dir / dir.distance * 8;
      label.paint(
          canvas, end + push - Offset(label.width / 2, label.height / 2));
    }
  }

  @override
  bool shouldRepaint(covariant _ScenePainter old) =>
      scene != old.scene ||
      azimuth != old.azimuth ||
      elevation != old.elevation ||
      zoom != old.zoom ||
      pan != old.pan ||
      explosion != old.explosion ||
      selected != old.selected;
}

class _V3 {
  const _V3(this.x, this.y, this.z);
  final double x, y, z;
}
