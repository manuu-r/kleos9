import 'dart:convert';
import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../data/models/concept_scene.dart';
import '../../data/models/json.dart';
import 'concept_geometry.dart';
import 'widgets.dart';

/// A data-only, native 3D illustration of the current planning concept.
///
/// The scene is produced by the planning agent using the pinned Anatomy skill.
/// Relative proportions are intentionally kept separate from engineering files.
class ConceptPreview extends StatefulWidget {
  const ConceptPreview({
    super.key,
    required this.scene,
    this.conceptVersion,
    this.updating = false,
    this.emptyMessage =
        'Your device preview will appear here as we shape the idea.',
    this.stale = false,
    this.onPartSelected,
    this.height,
    this.caption =
        'Early visual concept. Shape and arrangement will be refined during engineering.',
  });

  final JsonMap scene;
  final int? conceptVersion;
  final bool updating;
  final String emptyMessage;
  final bool stale;
  final ValueChanged<String?>? onPartSelected;
  final double? height;
  final String caption;

  @override
  State<ConceptPreview> createState() => _ConceptPreviewState();
}

class _ConceptPreviewState extends State<ConceptPreview>
    with SingleTickerProviderStateMixin {
  late ConceptScene _scene;
  late ConceptGeometry _geometry;
  late String _signature;
  late final AnimationController _explode;
  final _focus = FocusNode(debugLabel: 'Concept preview');
  String? _selected;
  double _azimuth = 40;
  double _elevation = 28;
  double _zoom = 1;
  double _startZoom = 1;

  @override
  void initState() {
    super.initState();
    _readScene();
    _explode = AnimationController(
        vsync: this, value: .65, duration: const Duration(milliseconds: 420));
  }

  void _readScene() {
    _signature = jsonEncode(widget.scene);
    _scene = ConceptScene.fromJson(widget.scene);
    _geometry = ConceptGeometry(_scene);
    if (!_scene.parts.any((part) => part.id == _selected)) _selected = null;
  }

  @override
  void didUpdateWidget(covariant ConceptPreview oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!identical(oldWidget.scene, widget.scene) &&
        jsonEncode(widget.scene) != _signature) {
      _readScene();
    }
  }

  @override
  void dispose() {
    _explode.dispose();
    _focus.dispose();
    super.dispose();
  }

  void _spread(double amount) {
    if (MediaQuery.disableAnimationsOf(context)) {
      _explode.value = amount;
    } else {
      _explode.animateTo(amount, curve: Curves.easeInOutCubic);
    }
  }

  void _select(String? id) {
    if (_selected == id) id = null;
    setState(() => _selected = id);
    widget.onPartSelected?.call(id);
  }

  void _reset() {
    setState(() {
      _azimuth = 40;
      _elevation = 28;
      _zoom = 1;
    });
  }

  KeyEventResult _key(FocusNode _, KeyEvent event) {
    if (event is! KeyDownEvent && event is! KeyRepeatEvent) {
      return KeyEventResult.ignored;
    }
    final key = event.logicalKey;
    if (key == LogicalKeyboardKey.space) {
      _spread(_explode.value > .5 ? 0 : 1);
    } else if (key == LogicalKeyboardKey.arrowLeft) {
      setState(() => _azimuth -= 10);
    } else if (key == LogicalKeyboardKey.arrowRight) {
      setState(() => _azimuth += 10);
    } else if (key == LogicalKeyboardKey.arrowUp) {
      setState(() => _elevation = (_elevation + 5).clamp(8, 78));
    } else if (key == LogicalKeyboardKey.arrowDown) {
      setState(() => _elevation = (_elevation - 5).clamp(8, 78));
    } else if (key == LogicalKeyboardKey.keyR) {
      _reset();
    } else {
      return KeyEventResult.ignored;
    }
    return KeyEventResult.handled;
  }

  @override
  Widget build(BuildContext context) {
    final geometryError = _scene.validationError ?? _geometry.validationError;
    final hasGeometry = _scene.parts.isNotEmpty && geometryError == null;
    final selected =
        _scene.parts.where((part) => part.id == _selected).firstOrNull;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Row(children: [
          const Expanded(
              child: Text('Device concept',
                  style: TextStyle(fontSize: 13, fontWeight: FontWeight.w600))),
          if (widget.updating && hasGeometry) ...[
            const KSpinner(size: 13),
            const SizedBox(width: 7),
            const Text('Updating',
                style: TextStyle(color: KColors.muted, fontSize: 12)),
          ] else if (widget.conceptVersion != null && hasGeometry)
            Text('Version ${widget.conceptVersion}',
                style: const TextStyle(color: KColors.muted, fontSize: 12)),
        ]),
        if (widget.stale && hasGeometry) ...[
          const SizedBox(height: 9),
          const Text(
              'Showing your previous concept. The latest update is not available.',
              style: TextStyle(color: KColors.muted, fontSize: 12)),
        ],
        if (_scene.title.isNotEmpty) ...[
          const SizedBox(height: 9),
          Text(_scene.title, style: Theme.of(context).textTheme.headlineSmall),
        ],
        if (_scene.summary.isNotEmpty) ...[
          const SizedBox(height: 7),
          Text(_scene.summary,
              style: const TextStyle(
                  color: KColors.muted, fontSize: 13.5, height: 1.5)),
        ],
        const SizedBox(height: 14),
        LayoutBuilder(builder: (context, constraints) {
          final height =
              widget.height ?? (constraints.maxWidth < 480 ? 290.0 : 360.0);
          return SizedBox(
            height: height,
            child: DecoratedBox(
              decoration: BoxDecoration(
                  color: KColors.surface,
                  borderRadius: BorderRadius.circular(KRadius.control),
                  border: Border.all(color: KColors.border)),
              child: !hasGeometry
                  ? Center(
                      child: Padding(
                          padding: const EdgeInsets.all(24),
                          child: Text(
                              geometryError == null
                                  ? widget.emptyMessage
                                  : 'This concept geometry could not be displayed: $geometryError Update the concept and try again.',
                              textAlign: TextAlign.center,
                              style: const TextStyle(color: KColors.muted))))
                  : AnimatedBuilder(
                      animation: _explode,
                      builder: (context, _) {
                        final size = Size(constraints.maxWidth, height);
                        final drawing = _geometry.project(size,
                            azimuth: _azimuth,
                            elevation: _elevation,
                            exploded: _explode.value,
                            zoom: _zoom,
                            selected: _selected);
                        return Focus(
                          focusNode: _focus,
                          onKeyEvent: _key,
                          child: Semantics(
                            label:
                                'Interactive 3D concept: ${_scene.title}. ${_scene.parts.length} parts.',
                            hint:
                                'Drag to rotate. Pinch or use zoom controls. Select a part below. Arrow keys rotate; Space assembles or separates.',
                            image: true,
                            child: ClipRRect(
                              borderRadius:
                                  BorderRadius.circular(KRadius.control),
                              child: Stack(children: [
                                Positioned.fill(
                                  child: GestureDetector(
                                    key: const ValueKey('concept-canvas'),
                                    behavior: HitTestBehavior.opaque,
                                    excludeFromSemantics: true,
                                    onTapDown: (_) => _focus.requestFocus(),
                                    onTapUp: (event) => _select(
                                        drawing.partAt(event.localPosition)),
                                    onScaleStart: (_) {
                                      _focus.requestFocus();
                                      _startZoom = _zoom;
                                    },
                                    onScaleUpdate: (event) => setState(() {
                                      if (event.pointerCount > 1) {
                                        _zoom = (_startZoom * event.scale)
                                            .clamp(.65, 1.8);
                                      } else {
                                        _azimuth +=
                                            event.focalPointDelta.dx * .45;
                                        _elevation = (_elevation -
                                                event.focalPointDelta.dy * .3)
                                            .clamp(8, 78);
                                      }
                                    }),
                                    child: RepaintBoundary(
                                        child: CustomPaint(
                                            painter: _ConceptPainter(
                                                drawing, _selected))),
                                  ),
                                ),
                                Positioned(
                                  left: 14,
                                  top: 12,
                                  child: IgnorePointer(
                                      child: Text(
                                          '${_scene.parts.length} concept parts',
                                          style: const TextStyle(
                                              color: KColors.faint,
                                              fontSize: 12))),
                                ),
                                Positioned(
                                  right: 4,
                                  top: 4,
                                  child: Row(
                                      mainAxisSize: MainAxisSize.min,
                                      children: [
                                        for (final c in [
                                          (
                                            'Zoom out',
                                            Icons.remove,
                                            () => setState(() => _zoom =
                                                (_zoom - .15).clamp(.65, 1.8))
                                          ),
                                          (
                                            'Zoom in',
                                            Icons.add,
                                            () => setState(() => _zoom =
                                                (_zoom + .15).clamp(.65, 1.8))
                                          ),
                                          (
                                            'Reset view',
                                            Icons.restart_alt_rounded,
                                            _reset
                                          ),
                                        ])
                                          SizedBox(
                                              width: 44,
                                              height: 44,
                                              child: IconButton(
                                                  tooltip: c.$1,
                                                  onPressed: c.$3,
                                                  icon: Icon(c.$2, size: 19))),
                                      ]),
                                ),
                                const Positioned(
                                    left: 14,
                                    right: 14,
                                    bottom: 12,
                                    child: IgnorePointer(
                                        child: Text(
                                            'Drag to rotate · tap a part',
                                            textAlign: TextAlign.center,
                                            style: TextStyle(
                                                fontSize: 12,
                                                color: KColors.faint)))),
                              ]),
                            ),
                          ),
                        );
                      }),
            ),
          );
        }),
        if (hasGeometry) ...[
          const SizedBox(height: 12),
          AnimatedBuilder(
              animation: _explode,
              builder: (context, _) => Wrap(
                      crossAxisAlignment: WrapCrossAlignment.center,
                      spacing: 8,
                      runSpacing: 8,
                      children: [
                        ChoiceChip(
                            key: const ValueKey('concept-assembled'),
                            label: const Text('Assembled'),
                            selected: _explode.value < .05,
                            onSelected: (_) => _spread(0)),
                        ChoiceChip(
                            key: const ValueKey('concept-exploded'),
                            label: const Text('Exploded view'),
                            selected: _explode.value > .95,
                            onSelected: (_) => _spread(1)),
                      ])),
          AnimatedBuilder(
              animation: _explode,
              builder: (context, _) => Slider(
                  key: const ValueKey('concept-spread'),
                  value: _explode.value,
                  semanticFormatterCallback: (value) =>
                      '${(value * 100).round()} percent separated',
                  label: 'Separate parts',
                  onChanged: (value) => _explode.value = value)),
          SingleChildScrollView(
            scrollDirection: Axis.horizontal,
            child: Row(children: [
              for (final part in _scene.parts)
                Padding(
                    padding: const EdgeInsets.only(right: 7),
                    child: ChoiceChip(
                        key: ValueKey('concept-part-${part.id}'),
                        label: Text(part.label),
                        selected: part.id == _selected,
                        onSelected: (_) => _select(part.id))),
            ]),
          ),
          const SizedBox(height: 9),
          AnimatedSize(
            duration: MediaQuery.disableAnimationsOf(context)
                ? Duration.zero
                : const Duration(milliseconds: 180),
            alignment: Alignment.topLeft,
            child: Align(
              alignment: Alignment.centerLeft,
              child: selected == null
                  ? const Text('Select a part to explore the arrangement.',
                      style: TextStyle(color: KColors.faint, fontSize: 12.5))
                  : Text(
                      '${selected.label}${selected.detail.isEmpty ? '' : ' · ${selected.detail}'}',
                      key: const ValueKey('concept-part-detail'),
                      style:
                          const TextStyle(color: KColors.text, fontSize: 13)),
            ),
          ),
        ],
        const SizedBox(height: 12),
        Text(widget.caption,
            style: const TextStyle(
                color: KColors.faint, fontSize: 12, height: 1.5)),
      ],
    );
  }
}

class _ConceptPainter extends CustomPainter {
  _ConceptPainter(this.drawing, this.selected);
  final ConceptDrawing drawing;
  final String? selected;

  @override
  void paint(Canvas canvas, Size size) {
    if (drawing.faces.isEmpty) return;
    final halo = Rect.fromCenter(
        center: Offset(size.width / 2,
            math.min(size.height - 34, drawing.bounds.bottom + 8)),
        width: drawing.bounds.width * .92,
        height: math.min(35, drawing.bounds.height * .14));
    canvas.drawOval(
        halo,
        Paint()
          ..shader = const RadialGradient(colors: [
            Color(0x35000000),
            Color(0x00000000),
          ]).createShader(halo));
    final fill = Paint()..style = PaintingStyle.fill;
    final edge = Paint()
      ..style = PaintingStyle.stroke
      ..strokeWidth = .65
      ..strokeJoin = StrokeJoin.round;
    for (final face in drawing.faces) {
      fill.color = face.color;
      // Feature triangles share exact edges. Independent antialias coverage
      // otherwise exposes hairline seams; legacy polygon surfaces keep AA.
      fill.isAntiAlias = face.points.length != 3 || face.outline;
      canvas.drawPath(face.path, fill);
      if (face.outline) {
        edge.color = face.partId == selected
            ? KColors.accent.withValues(alpha: .88)
            : Color.lerp(face.color, Colors.white, .15)!;
        canvas.drawPath(face.path, edge);
      }
    }
  }

  @override
  bool shouldRepaint(covariant _ConceptPainter oldDelegate) =>
      oldDelegate.drawing != drawing || oldDelegate.selected != selected;
}
