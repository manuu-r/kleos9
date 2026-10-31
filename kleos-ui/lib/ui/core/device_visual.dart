import 'dart:convert';
import 'dart:math' as math;
import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

/// Native Flutter rendering of the prototype's original vector device geometry.
/// Only presentation animation lives here; all engineering results come from the API.
class DeviceVisual extends StatefulWidget {
  const DeviceVisual({
    super.key,
    this.exploded = true,
    this.interactive = true,
    this.finish,
    this.guideStep,
  });

  final bool exploded;
  final bool interactive;
  final String? finish;
  final int? guideStep;

  @override
  State<DeviceVisual> createState() => _DeviceVisualState();
}

class _DeviceVisualState extends State<DeviceVisual>
    with SingleTickerProviderStateMixin {
  static final Future<_DeviceScene> _scene = _DeviceScene.load();
  static const _assemblyOrder = [
    'base',
    'battery',
    'board',
    'sensor',
    'display',
    'lid'
  ];
  late final AnimationController _animation;
  String? _selected;
  bool _open = true;

  @override
  void initState() {
    super.initState();
    _open = widget.exploded;
    _animation = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 650),
      value: _open ? 1 : 0,
    );
  }

  @override
  void didUpdateWidget(DeviceVisual oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.exploded != widget.exploded) _setOpen(widget.exploded);
  }

  void _setOpen(bool open) {
    _open = open;
    if (MediaQuery.disableAnimationsOf(context)) {
      _animation.value = open ? 1 : 0;
    } else {
      _animation.animateTo(open ? 1 : 0, curve: Curves.easeInOutCubic);
    }
  }

  void _cycle(int direction) {
    final current = _assemblyOrder.indexOf(_selected ?? 'base');
    setState(() => _selected =
        _assemblyOrder[(current + direction) % _assemblyOrder.length]);
  }

  @override
  void dispose() {
    _animation.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => FutureBuilder<_DeviceScene>(
        future: _scene,
        builder: (context, snapshot) {
          if (!snapshot.hasData) {
            return const AspectRatio(
              aspectRatio: 560 / 680,
              child: Center(
                  child: Icon(Icons.memory_outlined,
                      size: 64, color: Color(0xff646470))),
            );
          }
          final scene = snapshot.requireData;
          final selected = widget.guideStep == null
              ? _selected
              : _assemblyOrder[widget.guideStep!.clamp(0, 5)];
          final detail = selected == null ? null : scene.pieces[selected];
          final description = detail == null
              ? 'An air-quality monitor: six assemblies, ${scene.parts} parts. '
                  'A printed base, battery, main board, sensor, display and top shell.'
              : '${detail['label']}, ${detail['parts']} parts, from ${detail['from']}';
          return Semantics(
            label: description,
            image: true,
            button: widget.interactive,
            hint: widget.interactive
                ? 'Tap or press Space to assemble or take apart. Arrow keys select assemblies.'
                : null,
            onTap: widget.interactive ? () => _setOpen(!_open) : null,
            child: FocusableActionDetector(
              shortcuts: widget.interactive
                  ? const {
                      SingleActivator(LogicalKeyboardKey.space):
                          ActivateIntent(),
                      SingleActivator(LogicalKeyboardKey.arrowRight):
                          _SelectAssemblyIntent(1),
                      SingleActivator(LogicalKeyboardKey.arrowLeft):
                          _SelectAssemblyIntent(-1),
                    }
                  : const {},
              actions: {
                ActivateIntent: CallbackAction<ActivateIntent>(onInvoke: (_) {
                  _setOpen(!_open);
                  return null;
                }),
                _SelectAssemblyIntent:
                    CallbackAction<_SelectAssemblyIntent>(onInvoke: (intent) {
                  _cycle(intent.direction);
                  return null;
                }),
              },
              child: AnimatedBuilder(
                animation: _animation,
                builder: (context, _) => AspectRatio(
                  aspectRatio: 560 / 680,
                  child: LayoutBuilder(builder: (context, constraints) {
                    final size =
                        Size(constraints.maxWidth, constraints.maxHeight);
                    final painter =
                        _DevicePainter(scene, _animation.value, selected);
                    return MouseRegion(
                      cursor: widget.interactive
                          ? SystemMouseCursors.click
                          : MouseCursor.defer,
                      onHover: widget.interactive
                          ? (event) {
                              final piece =
                                  painter.pieceAt(event.localPosition, size);
                              if (piece != _selected) {
                                setState(() => _selected = piece);
                              }
                            }
                          : null,
                      onExit: widget.interactive
                          ? (_) {
                              setState(() => _selected = null);
                            }
                          : null,
                      child: GestureDetector(
                        excludeFromSemantics: true,
                        onTap:
                            widget.interactive ? () => _setOpen(!_open) : null,
                        child: Stack(children: [
                          Positioned.fill(
                              child: RepaintBoundary(
                                  child: CustomPaint(painter: painter))),
                          if (widget.interactive)
                            Positioned(
                              left: 16,
                              right: 16,
                              bottom: 12,
                              child: IgnorePointer(
                                  child: Text(
                                detail == null
                                    ? (_open
                                        ? 'Tap to assemble · 6 assemblies, ${scene.parts} parts'
                                        : 'Tap to take apart')
                                    : '${detail['label']} · ${detail['parts']} parts',
                                textAlign: TextAlign.center,
                                style: const TextStyle(
                                    fontFamily: 'GeistMono',
                                    fontSize: 12,
                                    color: Color(0xffa3a3ad)),
                              )),
                            ),
                        ]),
                      ),
                    );
                  }),
                ),
              ),
            ),
          );
        },
      );
}

class _SelectAssemblyIntent extends Intent {
  const _SelectAssemblyIntent(this.direction);
  final int direction;
}

class _DeviceScene {
  _DeviceScene(this.items, this.vectors, this.pieces, this.parts);
  final List<_DeviceItem> items;
  final Map<String, Offset> vectors;
  final Map<String, Map<String, dynamic>> pieces;
  final int parts;

  static Future<_DeviceScene> load() async {
    final json =
        jsonDecode(await rootBundle.loadString('assets/device.generated.json'))
            as Map<String, dynamic>;
    final vectors = (json['vectors'] as Map<String, dynamic>).map((key, value) {
      final point = value as List<dynamic>;
      return MapEntry(key,
          Offset((point[0] as num).toDouble(), (point[1] as num).toDouble()));
    });
    final items = (json['items'] as List<dynamic>).map((raw) {
      final item = raw as Map<String, dynamic>;
      return _DeviceItem(
          item['piece'] as String,
          (item['key'] as num).toDouble(),
          (item['lift'] as num).toDouble(),
          _parseShapes(item['svg'] as String));
    }).toList(growable: false);
    final pieces = (json['pieces'] as Map<String, dynamic>).map(
        (key, value) => MapEntry(key, Map<String, dynamic>.from(value as Map)));
    return _DeviceScene(items, vectors, pieces, json['parts'] as int);
  }
}

class _DeviceItem {
  const _DeviceItem(this.piece, this.depth, this.lift, this.shapes);
  final String piece;
  final double depth;
  final double lift;
  final List<_DeviceShape> shapes;
}

class _DeviceShape {
  const _DeviceShape(this.path, this.attributes,
      {this.dots = false, this.fade = false, this.text});
  final Path path;
  final Map<String, String> attributes;
  final bool dots;
  final bool fade;
  final String? text;
}

Map<String, String> _attributes(String tag) => {
      for (final match in RegExp(r'([\w-]+)="([^"]*)"').allMatches(tag))
        match[1]!: match[2]!,
    };

List<_DeviceShape> _parseShapes(String svg) {
  final shapes = <_DeviceShape>[];
  final stack = <Map<String, String>>[];
  for (final token in RegExp(
          r'<g\b[^>]*>|</g>|<path\b[^>]*/>|<circle\b[^>]*/>|<text\b[^>]*>[^<]*</text>')
      .allMatches(svg)) {
    final tag = token[0]!;
    if (tag.startsWith('</g')) {
      if (stack.isNotEmpty) stack.removeLast();
      continue;
    }
    final attributes = _attributes(tag);
    if (tag.startsWith('<g')) {
      stack.add(attributes);
      continue;
    }
    final dots = stack.any((g) => (g['class'] ?? '').contains('iso-dots'));
    final fade = stack.any((g) => g.containsKey('data-fade')) ||
        tag.contains('data-fade');
    if (!attributes.containsKey('data-tone')) {
      for (final parent in stack.reversed) {
        if (parent.containsKey('data-tone')) {
          attributes['data-tone'] = parent['data-tone']!;
          break;
        }
      }
    }
    final path = Path();
    if (tag.startsWith('<circle')) {
      path.addOval(Rect.fromCircle(
          center: Offset(
              double.parse(attributes['cx']!), double.parse(attributes['cy']!)),
          radius: double.parse(attributes['r']!)));
    } else if (attributes['d'] != null) {
      final points = RegExp(r'[MLZ]|-?(?:\d+(?:\.\d*)?|\.\d+)')
          .allMatches(attributes['d']!)
          .map((m) => m[0]!)
          .toList();
      var index = 0;
      var command = 'M';
      while (index < points.length) {
        final token = points[index];
        if (token == 'Z') {
          path.close();
          index++;
          continue;
        }
        if (token == 'M' || token == 'L') {
          command = token;
          index++;
          continue;
        }
        if (index + 1 >= points.length) break;
        final x = double.parse(points[index++]);
        final y = double.parse(points[index++]);
        if (command == 'M') {
          path.moveTo(x, y);
          command = 'L';
        } else {
          path.lineTo(x, y);
        }
      }
    }
    final text = tag.startsWith('<text')
        ? tag
            .substring(tag.indexOf('>') + 1, tag.indexOf('</text>'))
            .replaceAll('&amp;', '&')
        : null;
    shapes.add(
        _DeviceShape(path, attributes, dots: dots, fade: fade, text: text));
  }
  return shapes;
}

class _DevicePainter extends CustomPainter {
  const _DevicePainter(this.scene, this.expansion, this.selected);
  final _DeviceScene scene;
  final double expansion;
  final String? selected;
  static const _paper = Color(0xff17171b);
  static const _tones = {
    'hi': Color(0xffececf1),
    'mid': Color(0xff9d9da8),
    'lo': Color(0xff646470),
    'faint': Color(0xff3c3c45),
    'lit': Color(0xffef8656),
    'red': Color(0xffff6b6b),
    'green': Color(0xff6be0a0),
    'blue': Color(0xff6b9bff),
  };

  Rect get _view => Rect.lerp(const Rect.fromLTWH(20, 365, 520, 320),
      const Rect.fromLTWH(0, 0, 560, 680), expansion)!;
  double _scale(Size size) =>
      math.min(size.width / _view.width, (size.height - 25) / _view.height);
  Offset _origin(Size size) {
    final scale = _scale(size);
    return Offset((size.width - _view.width * scale) / 2 - _view.left * scale,
        (size.height - 25 - _view.height * scale) / 2 - _view.top * scale);
  }

  List<_DeviceItem> get _sorted => [...scene.items]..sort((a, b) =>
      (a.depth + a.lift * expansion).compareTo(b.depth + b.lift * expansion));

  String? pieceAt(Offset position, Size size) {
    final scale = _scale(size);
    final point = (position - _origin(size)) / scale;
    for (final item in _sorted.reversed) {
      final local =
          point - (scene.vectors[item.piece] ?? Offset.zero) * expansion;
      if (item.shapes.any((shape) => shape.path.contains(local))) {
        return item.piece;
      }
    }
    return null;
  }

  @override
  void paint(Canvas canvas, Size size) {
    final scale = _scale(size);
    final origin = _origin(size);
    canvas.save();
    canvas.translate(origin.dx, origin.dy);
    canvas.scale(scale);
    for (final item in _sorted) {
      final vector = scene.vectors[item.piece] ?? Offset.zero;
      final highlighted = item.piece == selected;
      final dim = selected != null && !highlighted ? .38 : 1.0;
      canvas.save();
      canvas.translate(vector.dx * expansion, vector.dy * expansion);
      for (final shape in item.shapes) {
        final attributes = shape.attributes;
        final classes = attributes['class'] ?? '';
        final tone = _tones[attributes['data-tone']] ?? _tones['lo']!;
        final opacity =
            dim * (shape.fade ? (1 - expansion * 6).clamp(0.0, 1.0) : 1.0);
        if (shape.text != null) {
          _paintText(canvas, shape, tone.withValues(alpha: opacity));
          continue;
        }
        final paint = Paint()..isAntiAlias = true;
        if (classes.contains('iso-line') || classes.contains('iso-wire-end')) {
          paint
            ..style = PaintingStyle.stroke
            ..strokeWidth = .7 / scale
            ..strokeJoin = StrokeJoin.round
            ..strokeCap = StrokeCap.round;
          paint.color = (highlighted && classes.contains('iso-edge')
                  ? _tones['lit']!
                  : tone)
              .withValues(alpha: opacity);
          if (classes.contains('iso-wire-end')) {
            canvas.drawPath(
                shape.path, Paint()..color = _paper.withValues(alpha: opacity));
          }
        } else {
          Color color;
          if (shape.dots) {
            color = tone;
            final dotOpacity = switch (attributes['data-tone']) {
              'hi' => .85,
              'mid' => .45,
              'lo' => .22,
              'faint' => .12,
              _ => .32
            };
            paint.color = color.withValues(alpha: opacity * dotOpacity);
            canvas.drawPath(shape.path, paint);
            continue;
          } else if (classes.contains('iso-top')) {
            color =
                highlighted ? const Color(0xff3b2a22) : const Color(0xff28282e);
          } else if (classes.contains('iso-shade')) {
            const shades = [
              Color(0xff131316),
              Color(0xff18181c),
              Color(0xff1d1d22),
              Color(0xff232329)
            ];
            color = highlighted
                ? const Color(0xff2b2019)
                : shades[int.tryParse(attributes['data-shade'] ?? '') ?? 0];
          } else if (classes.contains('iso-halo')) {
            color = const Color(0xff050507);
            paint.maskFilter = const MaskFilter.blur(BlurStyle.normal, 9);
          } else {
            color = _paper;
          }
          paint.color = color.withValues(alpha: opacity);
        }
        canvas.drawPath(shape.path, paint);
      }
      canvas.restore();
    }
    canvas.restore();
  }

  void _paintText(Canvas canvas, _DeviceShape shape, Color color) {
    final painter = TextPainter(
        text: TextSpan(
            text: shape.text,
            style: TextStyle(
                fontFamily: 'GeistMono',
                fontSize:
                    double.tryParse(shape.attributes['font-size'] ?? '') ?? 2.8,
                color: color)),
        textDirection: TextDirection.ltr)
      ..layout();
    final transform = shape.attributes['transform'];
    canvas.save();
    if (transform != null) {
      final values = RegExp(r'-?\d+(?:\.\d+)?')
          .allMatches(transform)
          .map((m) => double.parse(m[0]!))
          .toList();
      if (values.length == 6) {
        canvas.transform(Float64List.fromList([
          values[0],
          values[1],
          0,
          0,
          values[2],
          values[3],
          0,
          0,
          0,
          0,
          1,
          0,
          values[4],
          values[5],
          0,
          1
        ]));
      }
    }
    painter.paint(
        canvas,
        Offset(
            shape.attributes['text-anchor'] == 'middle'
                ? -painter.width / 2
                : 0,
            -painter.height * .8));
    canvas.restore();
    painter.dispose();
  }

  @override
  bool shouldRepaint(_DevicePainter oldDelegate) =>
      scene != oldDelegate.scene ||
      expansion != oldDelegate.expansion ||
      selected != oldDelegate.selected;
}
