import 'dart:math' as math;

import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';
import 'package:flutter/physics.dart';

import '../../../data/models/json.dart';
import '../../core/widgets.dart';

const _minZoom = 1.0, _maxZoom = 40.0;

double? _num(Object? raw) {
  final v = raw is num ? raw.toDouble() : double.tryParse('$raw');
  return v != null && v.isFinite && v.abs() <= 100000 ? v : null;
}

double? _size(Object? raw) {
  final v = _num(raw);
  return v != null && v > 0 ? v : null;
}

String _mm(double v) =>
    v == v.roundToDouble() ? v.toStringAsFixed(0) : v.toStringAsFixed(1);

/// One placed component, in board millimetres (x, y is its centre).
class _Comp {
  _Comp(this.id, this.label, this.rect);
  final String id, label;
  final Rect rect;
  bool get isIc =>
      math.max(rect.width, rect.height) >= 5 &&
      math.min(rect.width, rect.height) >= 3;
  bool get isPassive => math.max(rect.width, rect.height) < 4.5;
}

class _Trace {
  _Trace(this.top, this.points, this.width) : path = Path() {
    path.addPolygon(points, false);
    for (final m in path.computeMetrics()) {
      length += m.length;
    }
  }
  final bool top;
  final List<Offset> points;
  final double width;
  final Path path;
  double length = 0;

  /// Whether either end of the trace lands on [c] (with a little reach, since
  /// traces end on pads at the package edge).
  bool touches(_Comp c) {
    final r = c.rect.inflate(.9);
    return r.contains(points.first) || r.contains(points.last);
  }
}

/// The board model parsed once from the preview data, capped so malformed data
/// cannot make it expensive.
class _Board {
  _Board(JsonMap pcb)
      : width = _size(asMap(pcb['board'])['width']),
        height = _size(asMap(pcb['board'])['height']),
        comps = [],
        traces = [] {
    for (final raw in asMaps(pcb['components']).take(128)) {
      final x = _num(raw['x']), y = _num(raw['y']);
      final w = _size(raw['width']), h = _size(raw['height']);
      if (x == null || y == null || w == null || h == null) continue;
      final id = '${raw['id'] ?? raw['label'] ?? 'part${comps.length + 1}'}';
      comps.add(_Comp(id, '${raw['label'] ?? ''}',
          Rect.fromCenter(center: Offset(x, y), width: w, height: h)));
    }
    for (final raw in asMaps(pcb['traces']).take(256)) {
      final pts = [
        for (final p in asMaps(raw['points']).take(256))
          if (_num(p['x']) != null && _num(p['y']) != null)
            Offset(_num(p['x'])!, _num(p['y'])!)
      ];
      if (pts.length < 2) continue;
      traces
          .add(_Trace(raw['layer'] != 'B.Cu', pts, _size(raw['width']) ?? .25));
    }
  }
  final double? width, height;
  final List<_Comp> comps;
  final List<_Trace> traces;
  bool get valid => width != null && height != null;

  /// The smallest component under [p] (board millimetres), with a little slack.
  _Comp? hit(Offset p, double slack) {
    _Comp? best;
    for (final c in comps) {
      if (c.rect.inflate(slack).contains(p) &&
          (best == null ||
              c.rect.width * c.rect.height <
                  best.rect.width * best.rect.height)) {
        best = c;
      }
    }
    return best;
  }
}

/// A PCB viewer made for a phone: pinch to zoom, drag to pan (with inertia),
/// wheel to zoom at the cursor, double-tap to zoom in and out, tap a component
/// to select it (its traces light up), pick one from the chip row to fly to
/// it, and toggle layers from the menu. Drawn in board millimetres with a dim
/// grid, dimensions and a scale bar.
class PcbViewer extends StatefulWidget {
  const PcbViewer(
      {super.key,
      required this.pcb,
      required this.fallback,
      this.height = 330,
      this.fullscreen = false});
  final JsonMap pcb;
  final Widget fallback;
  final double height;

  /// Fill the available space instead of using [height]; hides the expand
  /// button. Used by the full-screen route.
  final bool fullscreen;
  @override
  State<PcbViewer> createState() => _PcbViewerState();
}

class _PcbViewerState extends State<PcbViewer> with TickerProviderStateMixin {
  late _Board board = _Board(widget.pcb);
  double zoom = 1;
  Offset pan = Offset.zero;
  Size canvas = Size.zero;
  String? selected;
  bool showTop = true, showBottom = true, showParts = true, showLabels = true;
  bool _interacting = false;

  // Pinch/drag bookkeeping.
  double _zoom0 = 1;
  Offset _pan0 = Offset.zero, _focal0 = Offset.zero;
  DateTime? _lastTap;
  Offset _lastTapAt = Offset.zero;

  late final AnimationController _arrive = AnimationController(
      vsync: this, duration: const Duration(milliseconds: 1000));
  late final AnimationController _fling =
      AnimationController.unbounded(vsync: this)..addListener(_onFling);
  Offset _flingFrom = Offset.zero, _flingDir = Offset.zero;
  late final AnimationController _cam = AnimationController(
      vsync: this, duration: const Duration(milliseconds: 420))
    ..addListener(_onCam);
  double _z1 = 1;
  Offset _p1 = Offset.zero;
  double _zFrom = 1;
  Offset _pFrom = Offset.zero;
  bool _started = false;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    if (_started) return;
    _started = true;
    // Traces draw themselves once, on first show; reduced motion skips it.
    if (MediaQuery.disableAnimationsOf(context)) {
      _arrive.value = 1;
    } else {
      _arrive.forward();
    }
  }

  @override
  void dispose() {
    _arrive.dispose();
    _fling.dispose();
    _cam.dispose();
    super.dispose();
  }

  @override
  void didUpdateWidget(covariant PcbViewer old) {
    super.didUpdateWidget(old);
    if (!identical(old.pcb, widget.pcb)) {
      board = _Board(widget.pcb);
      if (!board.comps.any((c) => c.id == selected)) selected = null;
    }
  }

  /// On a tall canvas the board sits below the toolbar tiles, so it can use
  /// the full width; on a short one it keeps clear of the tile columns.
  bool get _tall => canvas.height >= 460;
  double get _topInset => _tall ? 96 : 0;

  double get _fit {
    final w = board.width ?? 1, h = board.height ?? 1;
    final margin = _tall ? 24.0 : 56.0;
    return math.max(
        .1,
        math.min((canvas.width - margin * 2) / w,
            (canvas.height - 64 - _topInset) / h));
  }

  Offset get _centre => canvas.center(Offset.zero) + Offset(0, _topInset / 2);

  void _onFling() =>
      setState(() => pan = _flingFrom + _flingDir * _fling.value);

  void _onCam() {
    final t = Curves.easeInOutCubic.transform(_cam.value);
    setState(() {
      zoom = _zFrom + (_z1 - _zFrom) * t;
      pan = Offset.lerp(_pFrom, _p1, t)!;
    });
  }

  void _flyTo(double z, Offset p) {
    _fling.stop();
    z = z.clamp(_minZoom, _maxZoom);
    if (MediaQuery.disableAnimationsOf(context)) {
      setState(() {
        zoom = z;
        pan = p;
      });
      return;
    }
    _zFrom = zoom;
    _pFrom = pan;
    _z1 = z;
    _p1 = p;
    _cam.forward(from: 0);
  }

  void _fitAll() => _flyTo(1, Offset.zero);

  /// Zoom by [factor] keeping the point under [focal] where it is.
  void _zoomAt(Offset focal, double factor, {bool animate = false}) {
    final z = (zoom * factor).clamp(_minZoom, _maxZoom);
    final k = z / zoom;
    final p = focal - _centre - (focal - _centre - pan) * k;
    if (animate) {
      _flyTo(z, p);
    } else {
      setState(() {
        zoom = z;
        pan = p;
      });
    }
  }

  /// Fly the camera so [c] fills about a third of the view.
  void _focus(_Comp c) {
    final w = board.width!, h = board.height!;
    final fit = _fit;
    final target = (canvas.shortestSide *
            .34 /
            (math.max(c.rect.width, c.rect.height) * fit))
        .clamp(1.6, _maxZoom);
    final s = fit * target;
    final boardCentre = Offset(w / 2, h / 2);
    _flyTo(target, -(c.rect.center - boardCentre) * s);
  }

  void _select(String? id) {
    setState(() => selected = id == selected ? null : id);
    final c = board.comps.where((c) => c.id == selected).firstOrNull;
    if (c != null) _focus(c);
  }

  void _onScaleStart(ScaleStartDetails d) {
    _fling.stop();
    _cam.stop();
    _zoom0 = zoom;
    _pan0 = pan;
    _focal0 = d.localFocalPoint;
    setState(() => _interacting = true);
  }

  void _onScaleUpdate(ScaleUpdateDetails d) {
    final z = (_zoom0 * d.scale).clamp(_minZoom, _maxZoom);
    final k = z / _zoom0;
    setState(() {
      zoom = z;
      pan = d.localFocalPoint - _centre - (_focal0 - _centre - _pan0) * k;
    });
  }

  void _onScaleEnd(ScaleEndDetails d) {
    setState(() => _interacting = false);
    final v = d.velocity.pixelsPerSecond;
    if (d.pointerCount > 0 ||
        MediaQuery.disableAnimationsOf(context) ||
        v.distance < 160) {
      return;
    }
    _flingFrom = pan;
    _flingDir = v / v.distance;
    _fling.value = 0;
    _fling.animateWith(
        FrictionSimulation(.05, 0, math.min(v.distance, 4000) * .5));
  }

  Offset _toBoard(Offset screen) {
    final s = _fit * zoom;
    final c = Offset(board.width! / 2, board.height! / 2);
    return (screen - _centre - pan) / s + c;
  }

  /// A tap selects at once (no double-tap delay); a second tap right after
  /// zooms in on that spot, or back out to the whole board.
  void _onTapUp(TapUpDetails d) {
    final now = DateTime.now();
    if (_lastTap != null &&
        now.difference(_lastTap!) < const Duration(milliseconds: 320) &&
        (d.localPosition - _lastTapAt).distance < 32) {
      _lastTap = null;
      if (zoom < 2) {
        _zoomAt(d.localPosition, 3, animate: true);
      } else {
        _fitAll();
      }
      return;
    }
    _lastTap = now;
    _lastTapAt = d.localPosition;
    final slack = 8 / (_fit * zoom);
    final hit = board.hit(_toBoard(d.localPosition), slack);
    setState(() => selected = hit?.id == selected ? null : hit?.id);
  }

  void _openFullscreen() => Navigator.of(context).push(MaterialPageRoute<void>(
      fullscreenDialog: true,
      builder: (_) => Scaffold(
          backgroundColor: KColors.bg,
          appBar: AppBar(
              backgroundColor: KColors.bg,
              scrolledUnderElevation: 0,
              title: Text(
                  'PCB · ${_mm(board.width!)} × ${_mm(board.height!)} mm')),
          body: SafeArea(
              child: Padding(
                  padding: const EdgeInsets.fromLTRB(12, 4, 12, 12),
                  child: PcbViewer(
                      pcb: widget.pcb,
                      fallback: widget.fallback,
                      fullscreen: true))))));

  Widget _layersMenu() => PopupMenuButton<String>(
      tooltip: 'PCB layers',
      onSelected: (v) => setState(() {
            switch (v) {
              case 'top':
                showTop = !showTop;
              case 'bottom':
                showBottom = !showBottom;
              case 'parts':
                showParts = !showParts;
              case 'labels':
                showLabels = !showLabels;
            }
          }),
      itemBuilder: (_) => [
            for (final (id, label, on) in [
              ('top', 'Top copper', showTop),
              ('bottom', 'Bottom copper', showBottom),
              ('parts', 'Components', showParts),
              ('labels', 'Labels', showLabels),
            ])
              CheckedPopupMenuItem(
                  value: id,
                  checked: on,
                  height: 44,
                  child: Text(label,
                      style:
                          const TextStyle(fontSize: 14, color: KColors.text))),
          ],
      child: Semantics(
          button: true,
          label: 'PCB layers',
          child: const ExcludeSemantics(
              child: KToolTile(icon: Icons.layers_outlined))));

  @override
  Widget build(BuildContext context) {
    if (!board.valid) return widget.fallback;
    final comps = board.comps;
    final chosen = comps.where((c) => c.id == selected).firstOrNull;
    final touching = chosen == null
        ? 0
        : board.traces.where((t) => t.touches(chosen)).length;
    final stage = KGridBackdrop(
        grid: false,
        child: Stack(children: [
          Positioned.fill(
              child: Semantics(
                  label:
                      'PCB board and component placement preview. Drag to pan, pinch to zoom, tap a component to select it.',
                  child: ClipRect(
                      child: Listener(
                          onPointerSignal: (e) {
                            if (e is PointerScrollEvent) {
                              _zoomAt(e.localPosition,
                                  math.exp(-e.scrollDelta.dy * .0015));
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
                                  child: LayoutBuilder(builder: (context, c) {
                                    canvas = c.biggest;
                                    return CustomPaint(
                                        key: const ValueKey(
                                            'pcb-geometry-canvas'),
                                        size: Size.infinite,
                                        painter: _PcbPainter(
                                            board: board,
                                            fit: _fit,
                                            centre: _centre,
                                            zoom: zoom,
                                            pan: pan,
                                            selected: selected,
                                            showTop: showTop,
                                            showBottom: showBottom,
                                            showParts: showParts,
                                            showLabels: showLabels,
                                            arrive: _arrive));
                                  }))))))),
          Positioned(
              left: 8,
              top: 8,
              child: Column(mainAxisSize: MainAxisSize.min, children: [
                if (!widget.fullscreen) ...[
                  KToolButton(
                      icon: Icons.open_in_full,
                      tooltip: 'Expand PCB viewer',
                      onPressed: _openFullscreen),
                  const SizedBox(height: 8),
                ],
                _layersMenu(),
              ])),
          Positioned(
              right: 8,
              top: 8,
              child: Column(mainAxisSize: MainAxisSize.min, children: [
                KToolButton(
                    icon: Icons.add,
                    tooltip: 'Zoom in PCB',
                    onPressed: zoom < _maxZoom
                        ? () => _zoomAt(_centre, 1.4, animate: true)
                        : null),
                const SizedBox(height: 8),
                KToolButton(
                    icon: Icons.remove,
                    tooltip: 'Zoom out PCB',
                    onPressed: zoom > _minZoom
                        ? () => _zoomAt(_centre, 1 / 1.4, animate: true)
                        : null),
                const SizedBox(height: 8),
                KToolButton(
                    icon: Icons.fit_screen_outlined,
                    tooltip: 'Fit PCB to view',
                    onPressed: _fitAll),
              ])),
          Positioned(
              left: 28,
              bottom: 16,
              right: 16,
              child: IgnorePointer(
                  child: Text(
                chosen == null
                    ? '${_mm(board.width!)} × ${_mm(board.height!)} mm · ${comps.length} ${comps.length == 1 ? 'part' : 'parts'} · ${board.traces.length} ${board.traces.length == 1 ? 'trace' : 'traces'}'
                    : '${chosen.id}${chosen.label.isEmpty ? '' : ' · ${chosen.label}'} · ${_mm(chosen.rect.width)} × ${_mm(chosen.rect.height)} mm · $touching ${touching == 1 ? 'trace' : 'traces'}',
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: KType.monoSmall,
              ))),
        ]));
    final body = Column(children: [
      Expanded(child: stage),
      if (comps.isNotEmpty) ...[
        const SizedBox(height: 10),
        SizedBox(
            height: 40,
            child: ListView.separated(
                scrollDirection: Axis.horizontal,
                itemCount: comps.length,
                separatorBuilder: (_, __) => const SizedBox(width: 8),
                itemBuilder: (context, i) {
                  final c = comps[i];
                  return KChip(
                      key: ValueKey('pcb-part-${c.id}'),
                      label: c.label.isEmpty ? c.id : '${c.id} · ${c.label}',
                      selected: c.id == selected,
                      onTap: () => _select(c.id));
                })),
      ],
    ]);
    if (widget.fullscreen) return SizedBox.expand(child: body);
    return SizedBox(height: widget.height, width: double.infinity, child: body);
  }
}

class _PcbPainter extends CustomPainter {
  _PcbPainter(
      {required this.board,
      required this.fit,
      required this.centre,
      required this.zoom,
      required this.pan,
      required this.selected,
      required this.showTop,
      required this.showBottom,
      required this.showParts,
      required this.showLabels,
      required this.arrive})
      : super(repaint: arrive);
  final _Board board;
  final double fit, zoom;
  final Offset centre, pan;
  final String? selected;
  final bool showTop, showBottom, showParts, showLabels;
  final Animation<double> arrive;

  @override
  void paint(Canvas canvas, Size size) {
    if (size.isEmpty || !board.valid) return;
    final w = board.width!, h = board.height!;
    final s = fit * zoom;
    final origin = centre + pan - Offset(w * s / 2, h * s / 2);
    double px(double v) => v / s; // screen pixels -> board millimetres
    Offset toScreen(Offset mm) => origin + mm * s;
    final t = arrive.value;
    final chosen = board.comps.where((c) => c.id == selected).firstOrNull;
    final lit = chosen == null
        ? const <_Trace>{}
        : {
            for (final tr in board.traces)
              if (tr.touches(chosen)) tr
          };

    canvas.save();
    canvas.translate(origin.dx, origin.dy);
    canvas.scale(s);

    // Workspace grid, in board units: 1 mm when there is room, 5 mm always.
    final inv = Rect.fromLTRB(-origin.dx / s, -origin.dy / s,
        (size.width - origin.dx) / s, (size.height - origin.dy) / s);
    void gridLines(double step, double alpha) {
      final line = Paint()
        ..color = Colors.white.withValues(alpha: alpha)
        ..strokeWidth = px(1);
      final x0 = (inv.left / step).floor(), x1 = (inv.right / step).ceil();
      final y0 = (inv.top / step).floor(), y1 = (inv.bottom / step).ceil();
      if ((x1 - x0) + (y1 - y0) > 260) return;
      for (var i = x0; i <= x1; i++) {
        canvas.drawLine(
            Offset(i * step, inv.top), Offset(i * step, inv.bottom), line);
      }
      for (var j = y0; j <= y1; j++) {
        canvas.drawLine(
            Offset(inv.left, j * step), Offset(inv.right, j * step), line);
      }
    }

    if (s >= 7) gridLines(1, .028);
    gridLines(5, .05);

    // Board: solder mask, edge, inner keep-out line, mounting holes.
    final r = math.min(1.2, math.min(w, h) / 10);
    final outline =
        RRect.fromRectAndRadius(Rect.fromLTWH(0, 0, w, h), Radius.circular(r));
    canvas.drawRRect(outline, Paint()..color = KViz.mask);
    canvas.drawRRect(
        outline.deflate(.5),
        Paint()
          ..color = KViz.maskEdge.withValues(alpha: .28)
          ..style = PaintingStyle.stroke
          ..strokeWidth = px(1));
    canvas.drawRRect(
        outline,
        Paint()
          ..color = KViz.maskEdge
          ..style = PaintingStyle.stroke
          ..strokeWidth = px(1.6));
    if (w >= 24 && h >= 18) {
      for (final c in [
        Offset(3, 3),
        Offset(w - 3, 3),
        Offset(3, h - 3),
        Offset(w - 3, h - 3)
      ]) {
        canvas.drawCircle(c, 1.5, Paint()..color = KColors.surface);
        canvas.drawCircle(
            c,
            2.3,
            Paint()
              ..color = KViz.pad.withValues(alpha: .7)
              ..style = PaintingStyle.stroke
              ..strokeWidth = .32);
      }
    }
    canvas.save();
    canvas.clipRRect(outline);

    // Copper. Traces draw themselves once, on first show.
    final drawn =
        Curves.easeOutCubic.transform(((t - .1) / .9).clamp(0.0, 1.0));
    void trace(_Trace tr, Color color, double extra, {bool animate = true}) {
      final width = math.max(tr.width, px(1.4)) + extra;
      Path path = tr.path;
      if (animate && drawn < 1) {
        path = Path();
        var remaining = tr.length * drawn;
        for (final m in tr.path.computeMetrics()) {
          if (remaining <= 0) break;
          path.addPath(
              m.extractPath(0, math.min(m.length, remaining)), Offset.zero);
          remaining -= m.length;
        }
      }
      canvas.drawPath(
          path,
          Paint()
            ..color = color
            ..style = PaintingStyle.stroke
            ..strokeCap = StrokeCap.round
            ..strokeJoin = StrokeJoin.round
            ..strokeWidth = width);
    }

    final fade = chosen == null ? 1.0 : .32;
    if (showBottom) {
      for (final tr in board.traces.where((e) => !e.top && !lit.contains(e))) {
        trace(tr,
            KViz.copperBottom.withValues(alpha: (showTop ? .8 : 1) * fade), 0);
      }
    }
    if (showTop) {
      for (final tr in board.traces.where((e) => e.top && !lit.contains(e))) {
        trace(tr, KViz.copperTop.withValues(alpha: fade), 0);
      }
    }
    // The selected part's traces, in coral and a touch wider.
    for (final tr in lit) {
      if ((tr.top && showTop) || (!tr.top && showBottom)) {
        trace(tr, KColors.accent, px(.8), animate: false);
      }
    }

    // Components: pads, body, silk outline, pin-1 mark.
    if (showParts) {
      for (final c in board.comps) {
        final on = c.id == selected;
        final rc = c.rect;
        final horizontal = rc.width >= rc.height;
        final long = horizontal ? rc.width : rc.height;
        final short = horizontal ? rc.height : rc.width;
        final padPaint = Paint()
          ..color = KViz.pad.withValues(alpha: chosen != null && !on ? .5 : 1);
        final padW = math.max(.55, px(1.6)), padH = math.min(1.2, short * .3);
        if (c.isPassive) {
          for (final f in [0.0, 1.0]) {
            final cx = horizontal ? rc.left + rc.width * f : rc.center.dx;
            final cy = horizontal ? rc.center.dy : rc.top + rc.height * f;
            canvas.drawRect(
                Rect.fromCenter(
                    center: Offset(cx, cy),
                    width: horizontal ? padH * 1.1 : short * .8,
                    height: horizontal ? short * .8 : padH * 1.1),
                padPaint);
          }
        } else {
          final n = (long / 1.27).floor().clamp(2, 24);
          for (var i = 0; i < n; i++) {
            final f = (i + .5) / n;
            for (final side in [-1.0, 1.0]) {
              final cx = horizontal
                  ? rc.left + rc.width * f
                  : rc.center.dx + side * (rc.width / 2);
              final cy = horizontal
                  ? rc.center.dy + side * (rc.height / 2)
                  : rc.top + rc.height * f;
              canvas.drawRect(
                  Rect.fromCenter(
                      center: Offset(cx, cy),
                      width: horizontal ? padW : padH,
                      height: horizontal ? padH : padW),
                  padPaint);
            }
          }
        }
        final body = RRect.fromRectAndRadius(rc, const Radius.circular(.25));
        canvas.drawRRect(body, Paint()..color = KViz.partBody);
        if (on) {
          canvas.drawRRect(
              body, Paint()..color = KColors.accent.withValues(alpha: .16));
        }
        canvas.drawRRect(
            body,
            Paint()
              ..color = on
                  ? KColors.accent
                  : KViz.silk.withValues(alpha: chosen != null ? .35 : .55)
              ..style = PaintingStyle.stroke
              ..strokeWidth = px(on ? 2 : 1));
        if (c.isIc) {
          canvas.drawCircle(
              rc.topLeft + const Offset(.9, .9),
              math.max(.3, px(1.6)),
              Paint()..color = KViz.silk.withValues(alpha: .8));
        }
      }
    }
    canvas.restore(); // board clip
    canvas.restore(); // mm space

    // Dimensions along the top and left edges.
    final dim =
        TextStyle(fontFamily: 'GeistMono', fontSize: 11, color: KColors.muted);
    void dimension(String text, Offset at, {bool vertical = false}) {
      final tp = TextPainter(
          text: TextSpan(text: text, style: dim),
          textDirection: TextDirection.ltr)
        ..layout();
      canvas.save();
      canvas.translate(at.dx, at.dy);
      if (vertical) canvas.rotate(-math.pi / 2);
      tp.paint(canvas, Offset(-tp.width / 2, -tp.height / 2));
      canvas.restore();
    }

    final tl = toScreen(Offset.zero), br = toScreen(Offset(w, h));
    if (tl.dy > 20) {
      dimension('${_mm(w)} mm', Offset((tl.dx + br.dx) / 2, tl.dy - 12));
    }
    if (tl.dx > 20) {
      dimension('${_mm(h)} mm', Offset(tl.dx - 12, (tl.dy + br.dy) / 2),
          vertical: true);
    }

    // Reference designators; the selected one gets a tag above it.
    if (showParts && showLabels) {
      for (final c in board.comps) {
        final on = c.id == selected;
        final rect = Rect.fromCenter(
            center: toScreen(c.rect.center),
            width: c.rect.width * s,
            height: c.rect.height * s);
        final tp = TextPainter(
            text: TextSpan(
                text: c.id,
                style: TextStyle(
                    fontFamily: 'GeistMono',
                    fontSize: 11,
                    fontWeight: FontWeight.w500,
                    color: on
                        ? KColors.accent
                        : KColors.text
                            .withValues(alpha: chosen != null ? .45 : .9))),
            textDirection: TextDirection.ltr,
            maxLines: 1)
          ..layout();
        if (on) {
          final tag = Rect.fromCenter(
              center: rect.topCenter + const Offset(0, -16),
              width: tp.width + 14,
              height: 22);
          canvas.drawRect(tag, Paint()..color = KColors.surface);
          canvas.drawRect(
              tag,
              Paint()
                ..style = PaintingStyle.stroke
                ..strokeWidth = 1
                ..color = KColors.accent);
          tp.paint(canvas, tag.center - Offset(tp.width / 2, tp.height / 2));
        } else if (rect.width >= tp.width + 6 && rect.height >= tp.height + 2) {
          tp.paint(canvas, rect.center - Offset(tp.width / 2, tp.height / 2));
        }
      }
    }

    // Scale bar, bottom right: the largest round length that fits.
    const nice = <double>[.5, 1, 2, 5, 10, 20, 50, 100];
    var length = nice.first;
    for (final n in nice) {
      if (n * s <= 96) length = n;
    }
    final barPx = length * s;
    final y = size.height - 50;
    final x1 = size.width - 20, x0 = x1 - barPx;
    final bar = Paint()
      ..color = KColors.muted
      ..strokeWidth = 1.4;
    canvas.drawLine(Offset(x0, y), Offset(x1, y), bar);
    canvas.drawLine(Offset(x0, y - 4), Offset(x0, y + 4), bar);
    canvas.drawLine(Offset(x1, y - 4), Offset(x1, y + 4), bar);
    final label = TextPainter(
        text: TextSpan(text: '${_mm(length)} mm', style: dim),
        textDirection: TextDirection.ltr)
      ..layout();
    label.paint(canvas, Offset(x1 - label.width, y - 8 - label.height));
  }

  @override
  bool shouldRepaint(covariant _PcbPainter old) =>
      board != old.board ||
      fit != old.fit ||
      centre != old.centre ||
      zoom != old.zoom ||
      pan != old.pan ||
      selected != old.selected ||
      showTop != old.showTop ||
      showBottom != old.showBottom ||
      showParts != old.showParts ||
      showLabels != old.showLabels;
}
