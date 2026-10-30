import 'dart:convert';
import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../data/models/json.dart';
import '../../../state/production_store.dart';
import '../../core/concept_preview.dart';
import '../../core/widgets.dart';
import 'common.dart';

typedef EngineeringPreviewRequest = ({String projectId, int revision});

/// A scoped read of existing interfaces and files; this never starts AI work.
final engineeringPreviewProvider = FutureProvider.autoDispose
    .family<JsonMap, EngineeringPreviewRequest>((ref, request) {
  final repository = ref.watch(productionRepositoryProvider);
  return repository.api.engineeringPreview(request.projectId,
      organizationId: repository.organizationId);
}, retry: (_, __) => null);

class EngineeringPreview extends ConsumerStatefulWidget {
  const EngineeringPreview({
    super.key,
    required this.projectId,
    required this.revision,
    this.updating = false,
    this.fallbackScene = const {},
    this.onReviseCircuit,
  });

  final String projectId;
  final int revision;
  final bool updating;
  final JsonMap fallbackScene;
  final VoidCallback? onReviseCircuit;

  @override
  ConsumerState<EngineeringPreview> createState() => _EngineeringPreviewState();
}

class _EngineeringPreviewState extends ConsumerState<EngineeringPreview> {
  JsonMap? previous;

  @override
  void didUpdateWidget(covariant EngineeringPreview oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.projectId != widget.projectId) previous = null;
  }

  @override
  Widget build(BuildContext context) {
    final request = (projectId: widget.projectId, revision: widget.revision);
    final provider = engineeringPreviewProvider(request);
    ref.listen(provider, (_, next) {
      if (next.asData?.value case final data?) previous = data;
    });
    final result = ref.watch(provider);
    final data = result.asData?.value ?? previous;
    return pColumn([
      if (result.hasError)
        pCallout(pColumn([
          pText('Preview update interrupted', strong: true),
          pText('Your files and shared interfaces are still available.',
              small: true, muted: true),
          KButton(
              label: 'Retry preview',
              compact: true,
              icon: Icons.refresh,
              onPressed: () => ref.invalidate(provider)),
        ], gap: 8)),
      if (data == null)
        Container(
          height: 220,
          alignment: Alignment.center,
          decoration: _surface(),
          child: pColumn([
            const Icon(Icons.view_in_ar_outlined,
                color: KColors.muted, size: 32),
            pText(
                result.hasError
                    ? 'Preview unavailable'
                    : 'Loading build preview…',
                muted: true),
          ]),
        )
      else
        EngineeringPreviewPanel(
          key: ValueKey(widget.projectId),
          data: data,
          updating: widget.updating || result.isLoading,
          stale: result.hasError ||
              (result.isLoading && number(data['revision']) < widget.revision),
          fallbackScene: widget.fallbackScene,
          onReviseCircuit: widget.onReviseCircuit,
        ),
    ]);
  }
}

/// Data-only views of engineering outputs. Generated HTML is never executed.
class EngineeringPreviewPanel extends StatefulWidget {
  const EngineeringPreviewPanel({
    super.key,
    required this.data,
    this.updating = false,
    this.stale = false,
    this.fallbackScene = const {},
    this.onReviseCircuit,
  });

  final JsonMap data;
  final bool updating;
  final bool stale;
  final JsonMap fallbackScene;
  final VoidCallback? onReviseCircuit;

  @override
  State<EngineeringPreviewPanel> createState() =>
      _EngineeringPreviewPanelState();
}

class _EngineeringPreviewPanelState extends State<EngineeringPreviewPanel> {
  String lane = 'mechanics';
  bool showCode = false;

  @override
  Widget build(BuildContext context) {
    final current = asMap(widget.data[lane]);
    final source = current['source'] == 'artifact';
    final code = current['code']?.toString() ?? '';
    final mesh = _mesh(asMap(current['mesh'])['triangles']);
    final showingSource = (showCode || lane == 'firmware') && code.isNotEmpty;
    final envelope = asMap(current['envelope']);
    final useConcept = lane == 'mechanics' &&
        mesh.isEmpty &&
        !['width', 'height', 'depth']
            .every((key) => _positive(envelope[key]) != null) &&
        asList(widget.fallbackScene['parts']).isNotEmpty;
    final title = switch (lane) {
      'mechanics' => mesh.isNotEmpty && source
          ? 'Generated CAD mesh'
          : useConcept
              ? 'Planning concept'
              : 'Shared size envelope',
      'electronics' =>
        source ? 'Generated PCB geometry' : 'Board layout sketch',
      'firmware' => 'Firmware source',
      _ => 'Companion app sketch',
    };
    final actual = showingSource
        ? current['codeArtifactId'] != null || source
        : source &&
            ((lane == 'mechanics' && mesh.isNotEmpty) ||
                (lane == 'electronics' && asMap(current['pcb']).isNotEmpty));
    final status = showingSource
        ? current['codeStatus'] ?? current['status']
        : current['status'];
    final name = showingSource
        ? current['codeName'] ?? current['name']
        : current['name'];
    final files = asMaps(widget.data['files']);
    return pColumn([
      pActions([
        pText('Build preview', strong: true),
        if (widget.updating)
          const KBadge('Updating from engineering work', tone: 'accent'),
        if (widget.stale) const KBadge('Previous preview', tone: 'unknown'),
      ]),
      pText(
          'Shared dimensions appear first. Generated files replace the sketches as each lane finishes.',
          small: true,
          muted: true),
      pActions([
        for (final item in [
          ('mechanics', 'CAD', Icons.view_in_ar_outlined),
          ('electronics', 'PCB', Icons.memory_outlined),
          ('application', 'App', Icons.phone_android_outlined),
          if (asMap(widget.data['firmware']).isNotEmpty)
            ('firmware', 'Code', Icons.code),
        ])
          KButton(
              key: ValueKey('preview-tab-${item.$1}'),
              label: item.$2,
              icon: item.$3,
              compact: true,
              primary: lane == item.$1,
              onPressed: () => setState(() {
                    lane = item.$1;
                    showCode = false;
                  })),
      ]),
      Container(
        width: double.infinity,
        decoration: _surface(),
        padding: const EdgeInsets.all(16),
        child: Material(
            type: MaterialType.transparency,
            child: pColumn([
              pActions([
                pText(
                    showingSource && lane != 'firmware'
                        ? 'Generated source'
                        : title,
                    strong: true),
                KBadge(actual ? 'Actual file' : 'Approximation',
                    tone: actual ? 'accent' : null),
                if (status != null) evidence(status),
              ]),
              if (!showingSource && '${current['summary'] ?? ''}'.isNotEmpty)
                pText(current['summary'], small: true, muted: true),
              if (widget.stale)
                pText(
                    'Showing the last loaded revision while new geometry arrives.',
                    small: true,
                    color: KColors.unknown),
              if (lane == 'electronics' &&
                  asMap(widget.data['circuitContractCheck']).isNotEmpty)
                CircuitContractNotice(
                    check: asMap(widget.data['circuitContractCheck']),
                    onRevise: widget.onReviseCircuit),
              if (showingSource)
                _SourcePreview(code: code)
              else if (useConcept)
                ConceptPreview(scene: widget.fallbackScene, height: 290)
              else
                switch (lane) {
                  'mechanics' => _CadPreview(
                      key: const ValueKey('native-cad-preview'),
                      mesh: mesh,
                      envelope: asMap(current['envelope']),
                      actual: source && mesh.isNotEmpty),
                  'electronics' => _PcbPreview(pcb: asMap(current['pcb'])),
                  'firmware' => const SizedBox(
                      height: 290,
                      child: _WaitingPreview(
                          icon: Icons.code,
                          text: 'Generated firmware source will appear here.')),
                  _ => _AppPreview(app: asMap(current['app'])),
                },
              if (lane == 'application' && !showCode)
                pText(
                    'A safe native sketch of the screen. Open generated source to inspect the implementation.',
                    small: true,
                    muted: true),
              if (lane == 'electronics' && source && !showingSource)
                pText(
                    'Simplified footprints show placement and routing from the PCB file.',
                    small: true,
                    muted: true),
              if (lane == 'mechanics' &&
                  !showingSource &&
                  asMap(current['mesh'])['sampled'] == true)
                pText(
                    'Mesh simplified for preview. Download the file for complete geometry.',
                    small: true,
                    muted: true),
              if (useConcept && !showingSource)
                pText(
                    'The planning concept stays visible until shared dimensions are established.',
                    small: true,
                    muted: true),
              if (name != null)
                pText(name, mono: true, small: true, muted: true),
              if (showingSource && code.length >= 12000)
                pText(
                    'Source excerpt · download the file for the complete version.',
                    small: true,
                    muted: true),
              if (code.isNotEmpty && lane != 'firmware')
                KButton(
                    label: showCode
                        ? 'Show visual preview'
                        : 'Show generated source',
                    compact: true,
                    icon: showCode ? Icons.visibility_outlined : Icons.code,
                    onPressed: () => setState(() => showCode = !showCode)),
              if (actual)
                pText(
                    'Independent checks and release status determine whether generated output can be accepted.',
                    small: true,
                    muted: true),
              if (asList(current['limitations']).isNotEmpty)
                pDisclosure(
                    title: 'Preview limitations',
                    child: pColumn([
                      for (final item
                          in asList(current['limitations']).take(12))
                        pText(item, small: true, muted: true)
                    ], gap: 6)),
            ], gap: 12)),
      ),
      if (files.isNotEmpty)
        pDisclosure(
          title: 'Files appearing in this build',
          count: files.length,
          child: pColumn([
            for (final file in files.take(20))
              ListTile(
                dense: true,
                contentPadding: EdgeInsets.zero,
                leading: Icon(_fileIcon('${file['lane']}'),
                    size: 20, color: KColors.muted),
                title: pText(file['name'], small: true),
                subtitle: pText('${file['lane']} · ${file['status']}',
                    small: true, muted: true),
                trailing: IconButton(
                  tooltip: 'Preview ${file['lane']} lane',
                  icon: const Icon(Icons.visibility_outlined, size: 19),
                  onPressed: _previewLane('${file['lane']}') == null
                      ? null
                      : () => setState(() {
                            lane = _previewLane('${file['lane']}')!;
                            final target = asMap(widget.data[lane]);
                            showCode = (target['codeArtifactId'] ??
                                        target['artifactId']) ==
                                    file['id'] &&
                                '${target['code'] ?? ''}'.isNotEmpty;
                          }),
                ),
              ),
            pText('Downloads and the complete file list are below.',
                small: true, muted: true),
          ], gap: 2),
        ),
      if (asList(widget.data['limitations']).isNotEmpty)
        pDisclosure(
            title: 'What this preview can show',
            child: pColumn([
              for (final limitation
                  in asList(widget.data['limitations']).take(12))
                pText(limitation, small: true, muted: true),
            ], gap: 6)),
    ]);
  }
}

class CircuitContractNotice extends StatelessWidget {
  const CircuitContractNotice({super.key, required this.check, this.onRevise});
  final JsonMap check;
  final VoidCallback? onRevise;

  @override
  Widget build(BuildContext context) {
    final passed = check['status'] == 'PASS';
    final details = asMap(check['details']);
    return pCallout(Material(
        type: MaterialType.transparency,
        child: pColumn([
          pActions([
            pText('Circuit definition check', strong: true),
            evidence(check['status']),
          ]),
          pText(
              passed
                  ? 'Component identities and pin connections are defined. Electrical performance still needs independent validation.'
                  : 'The circuit needs complete component and pin connections before electronics can pass validation.',
              small: true,
              muted: true),
          if (!passed && onRevise != null) ...[
            pText(
                'Revising interfaces starts a new engineering revision; existing files remain in history.',
                small: true,
                muted: true),
            KButton(
                label: 'Revise circuit interfaces',
                compact: true,
                onPressed: onRevise),
          ],
          if (details.isNotEmpty)
            pDisclosure(
              title: 'Circuit check details',
              child: SelectableText(
                  const JsonEncoder.withIndent('  ').convert(details),
                  style: const TextStyle(
                      fontFamily: 'GeistMono', fontSize: 11, height: 1.5)),
            ),
        ], gap: 8)));
  }
}

String? _previewLane(String lane) => switch (lane) {
      'mechanics' || 'mechanical' => 'mechanics',
      'electronics' => 'electronics',
      'firmware' => 'firmware',
      'application' || 'software' => 'application',
      _ => null,
    };

IconData _fileIcon(String lane) => switch (_previewLane(lane)) {
      'mechanics' => Icons.view_in_ar_outlined,
      'electronics' => Icons.memory_outlined,
      _ => Icons.code,
    };

BoxDecoration _surface() => BoxDecoration(
    color: KColors.bg,
    borderRadius: BorderRadius.circular(12),
    border: Border.all(color: KColors.border));

double? _finite(Object? value) {
  final n = value is num ? value.toDouble() : double.tryParse('$value');
  return n != null && n.isFinite && n.abs() <= 1000000 ? n : null;
}

double? _positive(Object? value) {
  final n = _finite(value);
  return n != null && n > 0 ? n : null;
}

List<List<_V>> _mesh(Object? value) {
  final triangles = <List<_V>>[];
  for (final item in asList(value).take(1800)) {
    final triangle = asList(item);
    if (triangle.length != 3) continue;
    final points = <_V>[];
    for (final vertex in triangle) {
      final coordinates = asList(vertex);
      if (coordinates.length != 3) break;
      final x = _finite(coordinates[0]);
      final y = _finite(coordinates[1]);
      final z = _finite(coordinates[2]);
      if (x == null || y == null || z == null) break;
      points.add(_V(x, y, z));
    }
    if (points.length == 3 &&
        (points[1] - points[0]).cross(points[2] - points[0]).length > 1e-10) {
      triangles.add(points);
    }
  }
  return triangles;
}

List<List<_V>> _box(double x, double y, double z) {
  final v = [
    _V(0, 0, 0),
    _V(x, 0, 0),
    _V(x, y, 0),
    _V(0, y, 0),
    _V(0, 0, z),
    _V(x, 0, z),
    _V(x, y, z),
    _V(0, y, z),
  ];
  return [
    for (final f in const [
      [0, 2, 1],
      [0, 3, 2],
      [4, 5, 6],
      [4, 6, 7],
      [0, 1, 5],
      [0, 5, 4],
      [1, 2, 6],
      [1, 6, 5],
      [2, 3, 7],
      [2, 7, 6],
      [3, 0, 4],
      [3, 4, 7],
    ])
      f.map((index) => v[index]).toList(growable: false)
  ];
}

class _CadPreview extends StatefulWidget {
  const _CadPreview(
      {super.key,
      required this.mesh,
      required this.envelope,
      required this.actual});
  final List<List<_V>> mesh;
  final JsonMap envelope;
  final bool actual;
  @override
  State<_CadPreview> createState() => _CadPreviewState();
}

class _CadPreviewState extends State<_CadPreview> {
  double azimuth = .75, elevation = .5, zoom = 1;
  @override
  Widget build(BuildContext context) {
    final w = _positive(widget.envelope['width']);
    final h = _positive(widget.envelope['height']);
    final d = _positive(widget.envelope['depth']);
    final dimensions = w != null && h != null && d != null;
    final triangles = widget.mesh.isNotEmpty
        ? widget.mesh
        : dimensions
            ? _box(w, h, d)
            : <List<_V>>[];
    return pColumn([
      Semantics(
        label: widget.actual
            ? '3D preview of generated CAD file'
            : 'Approximate 3D envelope from shared dimensions',
        child: SizedBox(
          height: 290,
          width: double.infinity,
          child: triangles.isEmpty
              ? const _WaitingPreview(
                  icon: Icons.view_in_ar_outlined,
                  text: 'Shared dimensions will appear here.')
              : ClipRect(
                  child: GestureDetector(
                    onPanUpdate: (details) => setState(() {
                      azimuth += details.delta.dx * .012;
                      elevation = (elevation + details.delta.dy * .008)
                          .clamp(.12, 1.35);
                    }),
                    child: MouseRegion(
                      cursor: SystemMouseCursors.grab,
                      child: CustomPaint(
                        key: const ValueKey('cad-mesh-canvas'),
                        painter: _MeshPainter(
                            triangles, azimuth, elevation, zoom, widget.actual),
                      ),
                    ),
                  ),
                ),
        ),
      ),
      if (triangles.isNotEmpty)
        pActions([
          pText('Drag to orbit', small: true, muted: true),
          IconButton(
              tooltip: 'Zoom out CAD',
              onPressed: zoom > .65
                  ? () => setState(() => zoom = (zoom - .15).clamp(.65, 1.8))
                  : null,
              icon: const Icon(Icons.remove, size: 18)),
          IconButton(
              tooltip: 'Zoom in CAD',
              onPressed: zoom < 1.8
                  ? () => setState(() => zoom = (zoom + .15).clamp(.65, 1.8))
                  : null,
              icon: const Icon(Icons.add, size: 18)),
          IconButton(
              tooltip: 'Reset CAD view',
              onPressed: () => setState(() {
                    azimuth = .75;
                    elevation = .5;
                    zoom = 1;
                  }),
              icon: const Icon(Icons.restart_alt, size: 18)),
        ]),
      if (dimensions)
        pText(
            '${widget.actual ? 'Shared envelope: ' : ''}${_dimension(w)} × ${_dimension(h)} × ${_dimension(d)} mm',
            mono: true,
            small: true,
            muted: true),
    ], gap: 6);
  }
}

String _dimension(double value) => value == value.roundToDouble()
    ? '${value.toInt()}'
    : value.toStringAsFixed(1);

class _V {
  const _V(this.x, this.y, this.z);
  final double x, y, z;
  _V operator -(_V b) => _V(x - b.x, y - b.y, z - b.z);
  double dot(_V b) => x * b.x + y * b.y + z * b.z;
  _V cross(_V b) => _V(y * b.z - z * b.y, z * b.x - x * b.z, x * b.y - y * b.x);
  double get length => math.sqrt(dot(this));
}

class _MeshPainter extends CustomPainter {
  _MeshPainter(
      this.triangles, this.azimuth, this.elevation, this.zoom, this.actual);
  final List<List<_V>> triangles;
  final double azimuth, elevation, zoom;
  final bool actual;
  @override
  void paint(Canvas canvas, Size size) {
    if (size.isEmpty) return;
    final right = _V(math.sin(azimuth), -math.cos(azimuth), 0);
    final up = _V(-math.cos(azimuth) * math.sin(elevation),
        -math.sin(azimuth) * math.sin(elevation), math.cos(elevation));
    final toward = _V(math.cos(azimuth) * math.cos(elevation),
        math.sin(azimuth) * math.cos(elevation), math.sin(elevation));
    Offset at(_V p) => Offset(p.dot(right), -p.dot(up));
    final points = [
      for (final t in triangles)
        for (final p in t) at(p)
    ];
    if (points.isEmpty) return;
    var left = points.first.dx,
        top = points.first.dy,
        rightEdge = left,
        bottom = top;
    for (final p in points) {
      left = math.min(left, p.dx);
      rightEdge = math.max(rightEdge, p.dx);
      top = math.min(top, p.dy);
      bottom = math.max(bottom, p.dy);
    }
    final bounds = Rect.fromLTRB(left, top, rightEdge, bottom);
    final scale = math.min((size.width - 44) / math.max(.01, bounds.width),
            (size.height - 42) / math.max(.01, bounds.height)) *
        zoom;
    final faces = [...triangles]..sort((a, b) => a
        .fold<double>(0, (v, p) => v + p.dot(toward))
        .compareTo(b.fold<double>(0, (v, p) => v + p.dot(toward))));
    final base = actual ? const Color(0xffc7cedb) : const Color(0xff71869f);
    for (final face in faces) {
      final normal = (face[1] - face[0]).cross(face[2] - face[0]);
      final brightness =
          normal.length == 0 ? .5 : (normal.dot(toward) / normal.length).abs();
      final path = Path()
        ..addPolygon(
            face
                .map((p) =>
                    (at(p) - bounds.center) * scale + size.center(Offset.zero))
                .toList(),
            true);
      canvas.drawPath(
          path,
          Paint()
            ..color =
                Color.lerp(KColors.surface2, base, .36 + .64 * brightness)!);
      if (!actual) {
        canvas.drawPath(
            path,
            Paint()
              ..color = KColors.bg.withValues(alpha: .25)
              ..style = PaintingStyle.stroke
              ..strokeWidth = .6);
      }
    }
  }

  @override
  bool shouldRepaint(covariant _MeshPainter old) =>
      old.triangles != triangles ||
      old.azimuth != azimuth ||
      old.elevation != elevation ||
      old.zoom != zoom ||
      old.actual != actual;
}

class _PcbPreview extends StatelessWidget {
  const _PcbPreview({required this.pcb});
  final JsonMap pcb;
  @override
  Widget build(BuildContext context) {
    final board = asMap(pcb['board']);
    final w = _positive(board['width']), h = _positive(board['height']);
    return pColumn([
      SizedBox(
        height: 290,
        width: double.infinity,
        child: w == null || h == null
            ? const _WaitingPreview(
                icon: Icons.memory_outlined,
                text:
                    'Board dimensions and component placements will appear here.')
            : Semantics(
                label: 'PCB board and component placement preview',
                child: CustomPaint(
                    key: const ValueKey('pcb-geometry-canvas'),
                    painter: _PcbPainter(pcb, w, h)),
              ),
      ),
      if (w != null && h != null)
        pText(
            '${_dimension(w)} × ${_dimension(h)} mm · ${asMaps(pcb['components']).length} components',
            mono: true,
            small: true,
            muted: true),
    ], gap: 6);
  }
}

class _PcbPainter extends CustomPainter {
  _PcbPainter(this.pcb, this.width, this.height);
  final JsonMap pcb;
  final double width, height;
  @override
  void paint(Canvas canvas, Size size) {
    if (size.isEmpty) return;
    final scale =
        math.min((size.width - 34) / width, (size.height - 32) / height);
    final rect = Rect.fromCenter(
        center: size.center(Offset.zero),
        width: width * scale,
        height: height * scale);
    canvas.drawRRect(RRect.fromRectAndRadius(rect, const Radius.circular(9)),
        Paint()..color = const Color(0xff173f32));
    canvas.drawRRect(
        RRect.fromRectAndRadius(rect, const Radius.circular(9)),
        Paint()
          ..color = const Color(0xff5c9a7b)
          ..style = PaintingStyle.stroke
          ..strokeWidth = 1.2);
    canvas.save();
    canvas.clipRect(rect);
    Offset at(double x, double y) =>
        Offset(rect.left + x * scale, rect.top + y * scale);
    for (final trace in asMaps(pcb['traces']).take(256)) {
      final points = asMaps(trace['points'])
          .take(256)
          .map((p) {
            final x = _finite(p['x']), y = _finite(p['y']);
            return x == null || y == null ? null : at(x, y);
          })
          .whereType<Offset>()
          .toList();
      if (points.length < 2) continue;
      canvas.drawPath(
          Path()..addPolygon(points, false),
          Paint()
            ..color = trace['layer'] == 'B.Cu'
                ? const Color(0xff6ea0ba)
                : const Color(0xffc7a763)
            ..style = PaintingStyle.stroke
            ..strokeWidth = 1.5);
    }
    for (final component in asMaps(pcb['components']).take(128)) {
      final x = _finite(component['x']), y = _finite(component['y']);
      final w = _positive(component['width']),
          h = _positive(component['height']);
      if (x == null || y == null || w == null || h == null) continue;
      final r = Rect.fromCenter(
          center: at(x, y), width: w * scale, height: h * scale);
      canvas.drawRRect(RRect.fromRectAndRadius(r, const Radius.circular(2)),
          Paint()..color = const Color(0xff30373e));
      canvas.drawRect(
          r,
          Paint()
            ..color = const Color(0xffbfbd9e)
            ..style = PaintingStyle.stroke
            ..strokeWidth = 1);
      if (r.width > 18 && r.height > 11) {
        final label = '${component['label'] ?? component['id'] ?? ''}';
        final text = TextPainter(
            text: TextSpan(
                text: label.length > 16 ? '${label.substring(0, 15)}…' : label,
                style: const TextStyle(color: Color(0xffefecd8), fontSize: 9)),
            textDirection: TextDirection.ltr,
            maxLines: 1,
            ellipsis: '…')
          ..layout(maxWidth: math.max(1, r.width - 3));
        text.paint(canvas, r.center - Offset(text.width / 2, text.height / 2));
      }
    }
    canvas.restore();
  }

  @override
  bool shouldRepaint(covariant _PcbPainter old) =>
      old.pcb != pcb || old.width != width || old.height != height;
}

class _AppPreview extends StatelessWidget {
  const _AppPreview({required this.app});
  final JsonMap app;
  @override
  Widget build(BuildContext context) {
    final widgets = asMaps(app['widgets']).take(6).toList();
    if (app.isEmpty) {
      return const SizedBox(
          height: 290,
          child: _WaitingPreview(
              icon: Icons.phone_android_outlined,
              text:
                  'The companion screen will appear as its interface is defined.'));
    }
    return Align(
      alignment: Alignment.center,
      child: Container(
        key: const ValueKey('native-app-sketch'),
        width: 250,
        height: 330,
        padding: const EdgeInsets.fromLTRB(17, 20, 17, 12),
        decoration: BoxDecoration(
            color: KColors.surface,
            borderRadius: BorderRadius.circular(26),
            border: Border.all(color: const Color(0xff4c4c54), width: 3)),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Center(
              child: Container(
                  width: 52,
                  height: 4,
                  decoration: BoxDecoration(
                      color: KColors.border,
                      borderRadius: BorderRadius.circular(8)))),
          const SizedBox(height: 20),
          Text('${app['title'] ?? 'Your device'}',
              maxLines: 2,
              overflow: TextOverflow.ellipsis,
              style:
                  const TextStyle(fontSize: 18, fontWeight: FontWeight.w600)),
          const SizedBox(height: 12),
          Expanded(
              child: SingleChildScrollView(
                  child: pColumn([
            for (final item in widgets)
              Container(
                width: double.infinity,
                padding: const EdgeInsets.all(12),
                decoration: BoxDecoration(
                    color: KColors.surface2,
                    borderRadius: BorderRadius.circular(9)),
                child: pColumn([
                  pText(item['label'] ?? item['kind'],
                      small: true, muted: true),
                  if (item['value'] != null) pText(item['value'], strong: true),
                ], gap: 3),
              ),
            if (widgets.isEmpty)
              pText('Connected device', small: true, muted: true),
          ], gap: 9))),
          const SizedBox(height: 10),
          pText('Preview values are illustrative', small: true, muted: true),
        ]),
      ),
    );
  }
}

class _SourcePreview extends StatelessWidget {
  const _SourcePreview({required this.code});
  final String code;
  @override
  Widget build(BuildContext context) => Container(
        key: const ValueKey('engineering-source-text'),
        width: double.infinity,
        height: 290,
        padding: const EdgeInsets.all(12),
        decoration: BoxDecoration(
            color: KColors.surface, borderRadius: BorderRadius.circular(8)),
        child: SingleChildScrollView(
            child: SelectableText(
                code.length > 12000 ? code.substring(0, 12000) : code,
                style: const TextStyle(
                    fontFamily: 'GeistMono', fontSize: 11, height: 1.6))),
      );
}

class _WaitingPreview extends StatelessWidget {
  const _WaitingPreview({required this.icon, required this.text});
  final IconData icon;
  final String text;
  @override
  Widget build(BuildContext context) => Center(
          child: Padding(
        padding: const EdgeInsets.all(20),
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          Icon(icon, size: 38, color: KColors.faint),
          const SizedBox(height: 16),
          Text(text,
              textAlign: TextAlign.center,
              style: const TextStyle(
                  color: KColors.muted, fontSize: 13, height: 1.5)),
        ]),
      ));
}
