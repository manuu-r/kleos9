import 'dart:convert';
import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../data/models/json.dart';
import '../../../state/production_store.dart';
import '../../core/concept_preview.dart';
import '../../core/widgets.dart';
import 'common.dart';
import 'companion_app_preview.dart';
import 'cad_assembly_preview.dart';
import 'engineering_views.dart';

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
    this.initialView = 'mechanics',
    this.sidebar,
  });

  final String projectId;
  final int revision;
  final bool updating;
  final JsonMap fallbackScene;
  final VoidCallback? onReviseCircuit;
  final String initialView;
  final Widget Function(bool bounded)? sidebar;

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
        SizedBox(
            height: 300,
            width: double.infinity,
            child: KGridBackdrop(
                child: Center(
                    child: pColumn([
              const Icon(Icons.view_in_ar_outlined,
                  color: KColors.muted, size: 32),
              pText(
                  result.hasError
                      ? 'Preview unavailable'
                      : 'Loading build preview…',
                  muted: true),
            ]))))
      else
        EngineeringPreviewPanel(
          key: ValueKey(widget.projectId),
          projectId: widget.projectId,
          data: data,
          updating: widget.updating || result.isLoading,
          stale: result.hasError ||
              (result.isLoading && number(data['revision']) < widget.revision),
          fallbackScene: widget.fallbackScene,
          onReviseCircuit: widget.onReviseCircuit,
          initialView: widget.initialView,
          sidebar: widget.sidebar,
        ),
    ]);
  }
}

/// Data-only views of engineering outputs. Generated HTML is never executed.
///
/// One studio for every lane: an explorer on the left, a viewport on the
/// right, and an overview that lays CAD, PCB and the companion app out side
/// by side. Narrow screens drop the explorer and keep a file disclosure.
class EngineeringPreviewPanel extends StatefulWidget {
  const EngineeringPreviewPanel({
    super.key,
    required this.data,
    this.projectId,
    this.updating = false,
    this.stale = false,
    this.fallbackScene = const {},
    this.onReviseCircuit,
    this.initialView = 'mechanics',
    this.sidebar,
    this.mechanicsBuilder,
  });

  final JsonMap data;
  final String? projectId;
  final bool updating;
  final bool stale;
  final JsonMap fallbackScene;
  final VoidCallback? onReviseCircuit;

  /// 'overview', 'mechanics', 'electronics', 'application' or 'firmware'.
  final String initialView;

  /// Replaces the file explorer, for example with the shared interface
  /// contracts. Receives whether it sits in a bounded-height column.
  final Widget Function(bool bounded)? sidebar;

  /// Draws the CAD view from something other than a mesh, such as the
  /// simulated assembly model.
  final Widget Function(double height)? mechanicsBuilder;

  @override
  State<EngineeringPreviewPanel> createState() =>
      _EngineeringPreviewPanelState();
}

class _LaneView {
  const _LaneView({
    required this.title,
    required this.actual,
    required this.status,
    required this.name,
    required this.visual,
    required this.code,
    required this.showingSource,
    required this.current,
    required this.useConcept,
    required this.runningApp,
    required this.assemblyParts,
  });
  final String title;
  final bool actual;
  final Object? status;
  final Object? name;
  final Widget visual;
  final String code;
  final bool showingSource;
  final JsonMap current;
  final bool useConcept;
  final bool runningApp;
  final int assemblyParts;
}

class _EngineeringPreviewPanelState extends State<EngineeringPreviewPanel> {
  late String lane = widget.initialView;
  bool showCode = false;

  _LaneView describe(String id,
      {required bool code, required double inner, bool tile = false}) {
    final current = asMap(widget.data[id]);
    final source = current['source'] == 'artifact';
    final text = current['code']?.toString() ?? '';
    final mesh = _mesh(asMap(current['mesh'])['triangles']);
    final assembly = asMap(current['assembly']);
    final assemblyParts = asMaps(assembly['parts']);
    final showingSource = (code || id == 'firmware') && text.isNotEmpty;
    final appArtifactId = '${current['previewArtifactId'] ?? ''}';
    final runningApp = id == 'application' &&
        !showingSource &&
        current['previewAvailable'] == true &&
        widget.projectId != null &&
        appArtifactId.isNotEmpty;
    final envelope = asMap(current['envelope']);
    final custom = id == 'mechanics' &&
        widget.mechanicsBuilder != null &&
        mesh.isEmpty &&
        assemblyParts.isEmpty;
    final useConcept = id == 'mechanics' &&
        !custom &&
        mesh.isEmpty &&
        assemblyParts.isEmpty &&
        !['width', 'height', 'depth']
            .every((key) => _positive(envelope[key]) != null) &&
        asList(widget.fallbackScene['parts']).isNotEmpty;
    final title = switch (id) {
      'mechanics' => (mesh.isNotEmpty || assemblyParts.isNotEmpty) && source
          ? assemblyParts.length > 1
              ? 'Generated CAD assembly'
              : 'Generated CAD mesh'
          : custom
              ? 'Assembly model'
              : useConcept
                  ? 'Planning concept'
                  : 'Shared size envelope',
      'electronics' =>
        source ? 'Generated PCB geometry' : 'Board layout sketch',
      'firmware' => 'Firmware source',
      _ => runningApp ? 'Built companion app' : 'Companion app sketch',
    };
    final actual = showingSource
        ? current['codeArtifactId'] != null || source
        : runningApp ||
            source &&
                ((id == 'mechanics' &&
                        (mesh.isNotEmpty || assemblyParts.isNotEmpty)) ||
                    (id == 'electronics' && asMap(current['pcb']).isNotEmpty));
    final Widget visual;
    if (showingSource) {
      visual = KeyedSubtree(
          key: const ValueKey('engineering-source-text'),
          child: CodePanel(
              code: text.length > 12000 ? text.substring(0, 12000) : text,
              maxHeight: inner));
    } else if (custom) {
      final model = SizedBox(
          height: inner,
          width: double.infinity,
          child: widget.mechanicsBuilder!(inner));
      // In an overview tile the tap belongs to the tile, which opens the view.
      visual = tile ? IgnorePointer(child: model) : model;
    } else if (useConcept) {
      visual = SingleChildScrollView(
          child: ConceptPreview(
              scene: widget.fallbackScene, height: math.max(180, inner - 110)));
    } else {
      visual = switch (id) {
        'mechanics' => CadAssemblyPreview(
            assembly: assembly,
            height: inner,
            fallback: _CadPreview(
                key: const ValueKey('native-cad-preview'),
                mesh: mesh,
                envelope: envelope,
                height: inner,
                actual: source && mesh.isNotEmpty)),
        'electronics' => _PcbPreview(pcb: asMap(current['pcb']), height: inner),
        'firmware' => const _WaitingPreview(
            icon: Icons.code,
            text: 'Generated firmware source will appear here.'),
        _ => runningApp && !tile
            ? CompanionAppPreview(
                key: ValueKey(appArtifactId),
                projectId: widget.projectId!,
                artifactId: appArtifactId,
                sha256: '${current['previewSha256'] ?? ''}')
            : _AppPreview(app: asMap(current['app']), height: inner),
      };
    }
    return _LaneView(
        title: title,
        actual: actual,
        status: showingSource
            ? current['codeStatus'] ?? current['status']
            : current['status'],
        name: runningApp
            ? current['previewName'] ?? 'Compiled Flutter web build'
            : showingSource
                ? current['codeName'] ?? current['name']
                : current['name'],
        visual: visual,
        code: text,
        showingSource: showingSource,
        current: current,
        useConcept: useConcept,
        runningApp: runningApp,
        assemblyParts: assemblyParts.length);
  }

  void openFile(JsonMap file) {
    final target = _previewLane('${file['lane']}');
    if (target == null) return;
    setState(() {
      lane = target;
      final data = asMap(widget.data[target]);
      showCode = (data['codeArtifactId'] ?? data['artifactId']) == file['id'] &&
          '${data['code'] ?? ''}'.isNotEmpty;
    });
  }

  Widget overviewTile(String id, String label, IconData icon) => KTap(
      radius: KRadius.card - 4,
      onTap: () => setState(() {
            lane = id;
            showCode = false;
          }),
      builder: (context, hovered) => AnimatedContainer(
          duration: KMotion.base,
          curve: KMotion.out,
          clipBehavior: Clip.antiAlias,
          decoration: BoxDecoration(
              color: KColors.bg.withValues(alpha: .5),
              borderRadius: BorderRadius.circular(KRadius.card - 4),
              border: Border.all(
                  color: hovered
                      ? KColors.accent.withValues(alpha: .5)
                      : KColors.border)),
          child: LayoutBuilder(builder: (context, c) {
            final inner = math.max(120.0, c.maxHeight - 50);
            final view = describe(id, code: false, inner: inner, tile: true);
            return Stack(children: [
              Positioned.fill(
                  top: 40,
                  child: Material(
                      type: MaterialType.transparency,
                      child: Align(
                          alignment: Alignment.center, child: view.visual))),
              Positioned(
                  top: 10,
                  left: 12,
                  right: 12,
                  child: Row(children: [
                    Icon(icon, size: 14, color: KColors.accent),
                    const SizedBox(width: 7),
                    Text(label,
                        style: const TextStyle(
                            fontSize: 12.5, fontWeight: FontWeight.w500)),
                    const SizedBox(width: 8),
                    Flexible(
                        child: FittedBox(
                            fit: BoxFit.scaleDown,
                            child: KBadge(
                                view.runningApp
                                    ? 'Built app available'
                                    : view.actual
                                        ? 'Actual file'
                                        : 'Approximation',
                                tone: view.actual ? 'accent' : null))),
                    const Spacer(),
                    Icon(Icons.north_east,
                        size: 14,
                        color: hovered ? KColors.accent : KColors.faint),
                  ])),
            ]);
          })));

  Widget overviewBody(bool wide) {
    final cad = overviewTile('mechanics', 'CAD', Icons.view_in_ar_outlined);
    final pcb = overviewTile('electronics', 'PCB', Icons.memory_outlined);
    final app =
        overviewTile('application', 'App', Icons.phone_android_outlined);
    return Padding(
        padding: const EdgeInsets.all(12),
        child: wide
            ? Row(children: [
                Expanded(flex: 6, child: cad),
                const SizedBox(width: 12),
                Expanded(
                    flex: 5,
                    child: Column(children: [
                      Expanded(child: pcb),
                      const SizedBox(height: 12),
                      Expanded(child: app),
                    ])),
              ])
            : Column(children: [
                Expanded(child: cad),
                const SizedBox(height: 12),
                Expanded(child: pcb),
                const SizedBox(height: 12),
                Expanded(child: app),
              ]));
  }

  @override
  Widget build(BuildContext context) {
    final overview = lane == 'overview';
    final files = asMaps(widget.data['files']);
    final narrow = MediaQuery.sizeOf(context).width < 700;
    return LayoutBuilder(builder: (context, c) {
      final wide = c.maxWidth >= 860;
      final viewport = overview
          ? (wide ? 600.0 : 760.0)
          : narrow
              ? 350.0
              : 460.0;
      final inner = viewport - 64;
      final view =
          describe(overview ? 'mechanics' : lane, code: showCode, inner: inner);
      final current = view.current;
      final notes = <String>[
        if (overview)
          'Open a view to inspect it. Labeled approximations are sketches, not generated files.'
        else ...[
          if (!view.showingSource && '${current['summary'] ?? ''}'.isNotEmpty)
            '${current['summary']}',
          if (widget.stale)
            'Showing the last loaded revision while new geometry arrives.',
          if (lane == 'application' && !showCode && !view.runningApp)
            'A safe native sketch of the screen. Open generated source to inspect the implementation.',
          if (view.runningApp)
            'The built companion app is interactive. Its screen does not establish live sensor or hardware operation.',
          if (lane == 'electronics' &&
              current['source'] == 'artifact' &&
              !view.showingSource)
            'Simplified footprints show placement and routing from the PCB file.',
          if (lane == 'mechanics' &&
              !view.showingSource &&
              asMap(current['mesh'])['sampled'] == true)
            'Mesh simplified for preview. Download the file for complete geometry.',
          if (view.useConcept && !view.showingSource)
            'The planning concept stays visible until shared dimensions are established.',
          if (lane == 'mechanics' &&
              view.assemblyParts > 1 &&
              !view.showingSource)
            'Exploding the view moves the displayed parts only. Downloaded geometry remains assembled and unchanged.',
          if (lane == 'mechanics' &&
              view.assemblyParts == 1 &&
              !view.showingSource)
            'This build contains one CAD body. An exploded view becomes available when separate parts are generated.',
          if (view.showingSource && view.code.length >= 12000)
            'Source excerpt · download the file for the complete version.',
          if (view.actual)
            'Independent checks and release status determine whether generated output can be accepted.',
        ]
      ];
      final tabs = <({String value, String label, IconData? icon})>[
        (
          value: 'overview',
          label: 'Overview',
          icon: Icons.dashboard_customize_outlined
        ),
        (value: 'mechanics', label: 'CAD', icon: Icons.view_in_ar_outlined),
        (value: 'electronics', label: 'PCB', icon: Icons.memory_outlined),
        (
          value: 'application',
          label: 'App',
          icon: Icons.phone_android_outlined
        ),
        if (asMap(widget.data['firmware']).isNotEmpty)
          (value: 'firmware', label: 'Code', icon: Icons.code),
      ];
      final heading = Wrap(
          spacing: 10,
          runSpacing: 8,
          crossAxisAlignment: WrapCrossAlignment.center,
          children: [
            Text('Build preview',
                style: Theme.of(context).textTheme.titleMedium),
            if (widget.updating)
              const KBadge('Updating from engineering work', tone: 'accent'),
            if (widget.stale) const KBadge('Previous preview', tone: 'unknown'),
          ]);
      final switcher = KSegmented<String>(
          compact: c.maxWidth < 520,
          keyPrefix: 'preview-tab-',
          options: tabs,
          selected: lane,
          onSelected: (id) => setState(() {
                lane = id;
                showCode = false;
              }));
      final stage = SizedBox(
          height: viewport,
          width: double.infinity,
          child: KGridBackdrop(
              child: overview
                  ? overviewBody(wide)
                  : Column(
                      crossAxisAlignment: CrossAxisAlignment.stretch,
                      children: [
                          Padding(
                              padding: const EdgeInsets.fromLTRB(18, 14, 18, 6),
                              child: Material(
                                  type: MaterialType.transparency,
                                  child: pColumn([
                                    Wrap(
                                        spacing: 8,
                                        runSpacing: 6,
                                        crossAxisAlignment:
                                            WrapCrossAlignment.center,
                                        children: [
                                          Text(
                                              view.showingSource &&
                                                      lane != 'firmware'
                                                  ? 'Generated source'
                                                  : view.title,
                                              style: const TextStyle(
                                                  fontSize: 14,
                                                  fontWeight: FontWeight.w600)),
                                          KBadge(
                                              view.actual
                                                  ? 'Actual file'
                                                  : 'Approximation',
                                              tone: view.actual
                                                  ? 'accent'
                                                  : null),
                                          if (view.status != null)
                                            evidence(view.status),
                                        ]),
                                    if (view.name != null)
                                      Text('${view.name}',
                                          maxLines: 1,
                                          overflow: TextOverflow.ellipsis,
                                          style: const TextStyle(
                                              fontFamily: 'GeistMono',
                                              fontSize: 11,
                                              color: KColors.faint)),
                                  ], gap: 4))),
                          Expanded(
                              child: Padding(
                                  padding:
                                      const EdgeInsets.fromLTRB(16, 4, 16, 8),
                                  child: Material(
                                      type: MaterialType.transparency,
                                      child: AnimatedSwitcher(
                                          duration: KMotion.base,
                                          switchInCurve: KMotion.out,
                                          child: KeyedSubtree(
                                              key: ValueKey(
                                                  '$lane-${view.showingSource}'),
                                              child: Align(
                                                  alignment: view.showingSource
                                                      ? Alignment.topCenter
                                                      : Alignment.center,
                                                  child: view.visual)))))),
                        ])));
      final side = widget.sidebar;
      final showSidebar = wide && (side != null || files.isNotEmpty);
      final framed = showSidebar
          ? Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
              SizedBox(
                  width: 288,
                  height: viewport,
                  child: side != null
                      ? side(true)
                      : _Explorer(
                          files: files,
                          lane: overview ? '' : lane,
                          onOpen: openFile)),
              const SizedBox(width: 14),
              Expanded(child: stage),
            ])
          : stage;
      return pColumn([
        wide
            ? Row(children: [Expanded(child: heading), switcher])
            : pColumn([heading, switcher], gap: 12),
        framed,
        if (lane == 'electronics' &&
            asMap(widget.data['circuitContractCheck']).isNotEmpty)
          CircuitContractNotice(
              check: asMap(widget.data['circuitContractCheck']),
              onRevise: widget.onReviseCircuit),
        if (notes.isNotEmpty ||
            (!overview && view.code.isNotEmpty && lane != 'firmware'))
          Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Expanded(
                child: pColumn([
              for (final note in notes) pText(note, small: true, muted: true)
            ], gap: 4)),
            if (!overview && view.code.isNotEmpty && lane != 'firmware') ...[
              const SizedBox(width: 16),
              KButton(
                  label: showCode
                      ? 'Show visual preview'
                      : 'Show generated source',
                  compact: true,
                  icon: showCode ? Icons.visibility_outlined : Icons.code,
                  onPressed: () => setState(() => showCode = !showCode)),
            ],
          ]),
        if (!overview && asList(current['limitations']).isNotEmpty)
          pDisclosure(
              title: 'Preview limitations',
              child: pColumn([
                for (final item in asList(current['limitations']).take(12))
                  pText(item, small: true, muted: true)
              ], gap: 6)),
        if (!wide && side != null) side(false),
        if (!showSidebar && files.isNotEmpty)
          pDisclosure(
            title: 'Files appearing in this build',
            count: files.length,
            child: pColumn([
              for (final file in files.take(20))
                ListTile(
                  dense: true,
                  contentPadding: EdgeInsets.zero,
                  leading: Icon(_fileIcon('${file['lane']}', '${file['name']}'),
                      size: 20, color: KColors.muted),
                  title: pText(file['name'], small: true),
                  subtitle: pText('${file['lane']} · ${file['status']}',
                      small: true, muted: true),
                  trailing: IconButton(
                    tooltip: 'Preview ${file['lane']} lane',
                    icon: const Icon(Icons.visibility_outlined, size: 19),
                    onPressed: _previewLane('${file['lane']}') == null
                        ? null
                        : () => openFile(file),
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
      ], gap: 16);
    });
  }
}

/// File tree for the studio: one group per lane, one row per file. Status is
/// carried by an icon and tooltip so the tree stays quiet at a glance.
class _Explorer extends StatelessWidget {
  const _Explorer(
      {required this.files, required this.lane, required this.onOpen});
  final List<JsonMap> files;
  final String lane;
  final ValueChanged<JsonMap> onOpen;

  static const _groups = [
    ('mechanics', 'Mechanical', Icons.view_in_ar_outlined),
    ('electronics', 'Electronics', Icons.memory_outlined),
    ('firmware', 'Firmware', Icons.developer_board_outlined),
    ('application', 'Companion app', Icons.phone_android_outlined),
  ];

  @override
  Widget build(BuildContext context) => Semantics(
      label: 'Build files',
      child: KPanel(
          padding: EdgeInsets.zero,
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Padding(
                padding: const EdgeInsets.fromLTRB(16, 14, 16, 12),
                child: Row(children: [
                  const Text('Files',
                      style:
                          TextStyle(fontSize: 13, fontWeight: FontWeight.w600)),
                  const SizedBox(width: 8),
                  Text('${files.length}', style: KType.monoSmall),
                ])),
            const Divider(height: 1, color: KColors.hairline),
            Expanded(
                child: ListView(
                    padding: const EdgeInsets.fromLTRB(8, 10, 8, 12),
                    children: [
                  for (final group in _groups) ...[
                    _group(group.$1, group.$2, group.$3),
                    const SizedBox(height: 8),
                  ]
                ])),
          ])));

  Widget _group(String id, String label, IconData icon) {
    final rows =
        files.where((f) => _previewLane('${f['lane']}') == id).toList();
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Padding(
          padding: const EdgeInsets.fromLTRB(8, 4, 8, 6),
          child: Row(children: [
            Icon(icon, size: 13, color: KColors.faint),
            const SizedBox(width: 7),
            Expanded(child: Text(label, style: KType.label)),
            Text('${rows.length}', style: KType.monoSmall),
          ])),
      if (rows.isEmpty)
        const Padding(
            padding: EdgeInsets.fromLTRB(29, 2, 8, 6),
            child: Text('No files yet',
                style: TextStyle(fontSize: 11.5, color: KColors.faint)))
      else
        for (final file in rows)
          KTap(
              flat: true,
              radius: 8,
              onTap: () => onOpen(file),
              builder: (context, hovered) => AnimatedContainer(
                  duration: KMotion.fast,
                  padding:
                      const EdgeInsets.symmetric(horizontal: 8, vertical: 8),
                  decoration: BoxDecoration(
                      color: lane == id
                          ? KColors.surface3
                          : hovered
                              ? KColors.surface2
                              : Colors.transparent,
                      borderRadius: BorderRadius.circular(8)),
                  child: Row(children: [
                    Icon(_fileIcon('${file['lane']}', '${file['name']}'),
                        size: 14,
                        color: lane == id ? KColors.accent : KColors.faint),
                    const SizedBox(width: 9),
                    Expanded(
                        child: Text(_baseName('${file['name']}'),
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: TextStyle(
                                fontFamily: 'GeistMono',
                                fontSize: 11.5,
                                color: lane == id
                                    ? KColors.text
                                    : KColors.muted))),
                    const SizedBox(width: 8),
                    _statusGlyph('${file['status']}'),
                  ]))),
    ]);
  }
}

/// One small glyph per file state: a check for something that exists, a
/// spinner for work in flight, a warning for anything out of date or failed.
Widget _statusGlyph(String status) {
  final key = status.toLowerCase();
  final (IconData icon, Color color) = switch (key) {
    'accepted' || 'pass' || 'received' => (Icons.check, KColors.pass),
    'generating' || 'running' || 'queued' => (Icons.sync, KColors.accent),
    'stale' || 'revision_requested' => (Icons.history, KColors.unknown),
    'fail' || 'failed' || 'lost' => (Icons.close, KColors.fail),
    'ready' || 'candidate' || 'succeeded' => (Icons.check, KColors.faint),
    _ => (Icons.remove, KColors.faint),
  };
  return Tooltip(message: status, child: Icon(icon, size: 13, color: color));
}

String _baseName(String path) {
  final i = path.lastIndexOf('/');
  return i < 0 ? path : path.substring(i + 1);
}

/// Small translucent chip that floats over a viewport.
Widget _overlayChip(Widget child) => Container(
    padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
    decoration: BoxDecoration(
        color: KColors.bg.withValues(alpha: .72),
        borderRadius: BorderRadius.circular(KRadius.pill),
        border: Border.all(color: KColors.border)),
    child: child);

class CircuitContractNotice extends StatelessWidget {
  const CircuitContractNotice({super.key, required this.check, this.onRevise});
  final JsonMap check;
  final VoidCallback? onRevise;

  @override
  Widget build(BuildContext context) {
    final passed = check['status'] == 'PASS';
    final details = asMap(check['details']);
    final color = passed ? KColors.pass : KColors.accent;
    return Container(
        padding: const EdgeInsets.fromLTRB(18, 16, 18, 8),
        decoration: BoxDecoration(
            color: color.withValues(alpha: .06),
            borderRadius: BorderRadius.circular(KRadius.card - 4),
            border: Border.all(color: color.withValues(alpha: .28))),
        child: Material(
            type: MaterialType.transparency,
            child: pColumn([
              Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Container(
                    width: 30,
                    height: 30,
                    decoration: BoxDecoration(
                        shape: BoxShape.circle,
                        color: color.withValues(alpha: .16)),
                    child: Icon(
                        passed ? Icons.check : Icons.electrical_services,
                        size: 16,
                        color: color)),
                const SizedBox(width: 14),
                Expanded(
                    child: pColumn([
                  Wrap(
                      spacing: 10,
                      runSpacing: 6,
                      crossAxisAlignment: WrapCrossAlignment.center,
                      children: [
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
                ], gap: 8)),
              ]),
              if (details.isNotEmpty)
                pDisclosure(
                  title: 'Circuit check details',
                  child: SelectableText(
                      const JsonEncoder.withIndent('  ').convert(details),
                      style: const TextStyle(
                          fontFamily: 'GeistMono', fontSize: 11, height: 1.5)),
                ),
            ], gap: 4)));
  }
}

String? _previewLane(String lane) => switch (lane.toLowerCase()) {
      'mechanics' || 'mechanical' => 'mechanics',
      'electronics' => 'electronics',
      'firmware' => 'firmware',
      'application' || 'software' || 'app' => 'application',
      _ => null,
    };

/// Icon for a file: its extension wins, then the lane it belongs to.
IconData _fileIcon(String lane, [String name = '']) {
  final ext = name.contains('.') ? name.split('.').last.toLowerCase() : '';
  if (const {'step', 'stp', 'stl', 'obj', '3mf', 'iges'}.contains(ext)) {
    return Icons.view_in_ar_outlined;
  }
  if (const {'kicad_pcb', 'kicad_sch', 'gbr', 'ato'}.contains(ext)) {
    return Icons.memory_outlined;
  }
  if (const {
    'c',
    'h',
    'cpp',
    'ino',
    'rs',
    'overlay',
    'conf',
    'py',
    'ts',
    'dart',
    'html',
    'js'
  }.contains(ext)) {
    return Icons.code;
  }
  return switch (_previewLane(lane)) {
    'mechanics' => Icons.view_in_ar_outlined,
    'electronics' => Icons.memory_outlined,
    'application' => Icons.phone_android_outlined,
    _ => Icons.code,
  };
}

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

/// Plays a one-shot entrance for a preview, skipped when the platform asks for
/// reduced motion. Shared by the CAD, PCB and app views so they arrive alike.
mixin _Entrance<T extends StatefulWidget>
    on State<T>, TickerProviderStateMixin<T> {
  late final AnimationController entrance =
      AnimationController(vsync: this, duration: KMotion.intro);
  late final Animation<double> arrive =
      CurvedAnimation(parent: entrance, curve: KMotion.out);
  bool _entered = false;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    if (_entered) return;
    _entered = true;
    if (MediaQuery.disableAnimationsOf(context)) {
      entrance.value = 1;
    } else {
      entrance.forward();
    }
  }

  @override
  void dispose() {
    entrance.dispose();
    super.dispose();
  }
}

class _CadPreview extends StatefulWidget {
  const _CadPreview(
      {super.key,
      required this.mesh,
      required this.envelope,
      this.height = 290,
      required this.actual});
  final double height;
  final List<List<_V>> mesh;
  final JsonMap envelope;
  final bool actual;
  @override
  State<_CadPreview> createState() => _CadPreviewState();
}

class _CadPreviewState extends State<_CadPreview>
    with TickerProviderStateMixin, _Entrance {
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
    final controls = Row(mainAxisSize: MainAxisSize.min, children: [
      if (MediaQuery.sizeOf(context).width >= 560)
        const Padding(
            padding: EdgeInsets.only(left: 6, right: 4),
            child: Text('Drag to orbit',
                style: TextStyle(fontSize: 11, color: KColors.muted))),
      IconButton(
          tooltip: 'Zoom out CAD',
          visualDensity: VisualDensity.compact,
          onPressed: zoom > .65
              ? () => setState(() => zoom = (zoom - .15).clamp(.65, 1.8))
              : null,
          icon: const Icon(Icons.remove, size: 16)),
      IconButton(
          tooltip: 'Zoom in CAD',
          visualDensity: VisualDensity.compact,
          onPressed: zoom < 1.8
              ? () => setState(() => zoom = (zoom + .15).clamp(.65, 1.8))
              : null,
          icon: const Icon(Icons.add, size: 16)),
      IconButton(
          tooltip: 'Reset CAD view',
          visualDensity: VisualDensity.compact,
          onPressed: () => setState(() {
                azimuth = .75;
                elevation = .5;
                zoom = 1;
              }),
          icon: const Icon(Icons.restart_alt, size: 16)),
    ]);
    return Semantics(
      label: widget.actual
          ? '3D preview of generated CAD file'
          : 'Approximate 3D envelope from shared dimensions',
      child: SizedBox(
        height: widget.height,
        width: double.infinity,
        child: triangles.isEmpty
            ? const _WaitingPreview(
                icon: Icons.view_in_ar_outlined,
                text: 'Shared dimensions will appear here.')
            : Stack(children: [
                Positioned(
                    left: 0,
                    right: 0,
                    top: 0,
                    bottom: 46,
                    child: ClipRect(
                      child: GestureDetector(
                        onPanUpdate: (details) => setState(() {
                          azimuth += details.delta.dx * .012;
                          elevation = (elevation + details.delta.dy * .008)
                              .clamp(.12, 1.35);
                        }),
                        child: MouseRegion(
                          cursor: SystemMouseCursors.grab,
                          // The model swings in from the side and settles, so
                          // the first look at the part reads as a reveal.
                          child: AnimatedBuilder(
                            animation: arrive,
                            builder: (context, _) => Opacity(
                              opacity: (arrive.value * 1.6).clamp(0, 1),
                              child: CustomPaint(
                                key: const ValueKey('cad-mesh-canvas'),
                                painter: _MeshPainter(
                                    triangles,
                                    azimuth - (1 - arrive.value) * 1.15,
                                    elevation,
                                    zoom * (.88 + .12 * arrive.value),
                                    widget.actual),
                              ),
                            ),
                          ),
                        ),
                      ),
                    )),
                Positioned(
                    left: 4,
                    right: 4,
                    bottom: 4,
                    child: Material(
                        type: MaterialType.transparency,
                        child: Row(children: [
                          if (dimensions)
                            Flexible(
                                child: _overlayChip(Text(
                                    '${widget.actual ? 'Shared envelope: ' : ''}${_dimension(w)} × ${_dimension(h)} × ${_dimension(d)} mm',
                                    overflow: TextOverflow.ellipsis,
                                    style: const TextStyle(
                                        fontFamily: 'GeistMono',
                                        fontSize: 11,
                                        color: KColors.muted)))),
                          const Spacer(),
                          _overlayChip(controls),
                        ]))),
              ]),
      ),
    );
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
    final centre = size.center(Offset.zero);
    // Contact shadow: a soft ellipse under the part grounds it on the grid.
    final footY = centre.dy + bounds.height * scale / 2 + 8;
    final footW = bounds.width * scale * .78;
    final shadow = Rect.fromCenter(
        center: Offset(centre.dx, footY), width: footW, height: 26);
    canvas.drawOval(
        shadow,
        Paint()
          ..shader = RadialGradient(colors: [
            const Color(0xff050507).withValues(alpha: .55),
            Colors.transparent
          ]).createShader(shadow));
    final faces = [...triangles]..sort((a, b) => a
        .fold<double>(0, (v, p) => v + p.dot(toward))
        .compareTo(b.fold<double>(0, (v, p) => v + p.dot(toward))));
    final base = actual ? const Color(0xffc9d0dd) : const Color(0xff7d93ad);
    for (final face in faces) {
      final normal = (face[1] - face[0]).cross(face[2] - face[0]);
      final facing =
          normal.length == 0 ? .5 : (normal.dot(toward) / normal.length).abs();
      final path = Path()
        ..addPolygon(
            face.map((p) => (at(p) - bounds.center) * scale + centre).toList(),
            true);
      final shade = Color.lerp(KColors.surface2, base, .3 + .7 * facing)!;
      canvas.drawPath(
          path,
          Paint()
            ..color = shade
            ..isAntiAlias = true);
      // Faces turned away from the viewer pick up a warm rim so edges read.
      if (facing < .35) {
        canvas.drawPath(
            path,
            Paint()
              ..color =
                  KColors.accent.withValues(alpha: .22 * (1 - facing / .35)));
      }
      if (!actual) {
        canvas.drawPath(
            path,
            Paint()
              ..color = const Color(0xffb4c8de).withValues(alpha: .34)
              ..style = PaintingStyle.stroke
              ..strokeJoin = StrokeJoin.round
              ..strokeWidth = .8);
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

class _PcbPreview extends StatefulWidget {
  const _PcbPreview({required this.pcb, this.height = 290});
  final JsonMap pcb;
  final double height;
  @override
  State<_PcbPreview> createState() => _PcbPreviewState();
}

class _PcbPreviewState extends State<_PcbPreview>
    with TickerProviderStateMixin, _Entrance {
  @override
  Widget build(BuildContext context) {
    final pcb = widget.pcb;
    final board = asMap(pcb['board']);
    final w = _positive(board['width']), h = _positive(board['height']);
    return SizedBox(
      height: widget.height,
      width: double.infinity,
      child: w == null || h == null
          ? const _WaitingPreview(
              icon: Icons.memory_outlined,
              text:
                  'Board dimensions and component placements will appear here.')
          : Stack(children: [
              Positioned(
                  left: 0,
                  right: 0,
                  top: 0,
                  bottom: 46,
                  child: Semantics(
                    label: 'PCB board and component placement preview',
                    child: CustomPaint(
                        key: const ValueKey('pcb-geometry-canvas'),
                        painter: _PcbPainter(pcb, w, h, arrive)),
                  )),
              Positioned(
                  left: 4,
                  bottom: 4,
                  child: _overlayChip(Text(
                      '${_dimension(w)} × ${_dimension(h)} mm · ${asMaps(pcb['components']).length} components',
                      style: const TextStyle(
                          fontFamily: 'GeistMono',
                          fontSize: 11,
                          color: KColors.muted)))),
            ]),
    );
  }
}

class _PcbPainter extends CustomPainter {
  _PcbPainter(this.pcb, this.width, this.height, this.progress)
      : super(repaint: progress);
  final JsonMap pcb;
  final double width, height;
  final Animation<double> progress;
  @override
  void paint(Canvas canvas, Size size) {
    if (size.isEmpty) return;
    final t = progress.value;
    final scale =
        math.min((size.width - 34) / width, (size.height - 32) / height) *
            (.94 + .06 * t);
    final rect = Rect.fromCenter(
        center: size.center(Offset.zero),
        width: width * scale,
        height: height * scale);
    final radius = Radius.circular(math.min(rect.shortestSide * .08, 10));
    final outline = RRect.fromRectAndRadius(rect, radius);
    canvas.saveLayer(rect.inflate(30),
        Paint()..color = Colors.white.withValues(alpha: t.clamp(0, 1)));
    // Soldermask with a faint diagonal sheen, then a bright copper edge.
    canvas.drawRRect(
        outline.shift(const Offset(0, 8)),
        Paint()
          ..color = const Color(0xff050507).withValues(alpha: .5)
          ..maskFilter = const MaskFilter.blur(BlurStyle.normal, 14));
    canvas.drawRRect(
        outline,
        Paint()
          ..shader = const LinearGradient(
                  begin: Alignment.topLeft,
                  end: Alignment.bottomRight,
                  colors: [Color(0xff17493a), Color(0xff0e2f26)])
              .createShader(rect));
    canvas.drawRRect(
        outline,
        Paint()
          ..color = const Color(0xff6fb08f).withValues(alpha: .7)
          ..style = PaintingStyle.stroke
          ..strokeWidth = 1.2);
    canvas.save();
    canvas.clipRRect(outline);
    Offset at(double x, double y) =>
        Offset(rect.left + x * scale, rect.top + y * scale);
    // Mounting holes in the corners.
    for (final c in [
      rect.topLeft + const Offset(9, 9),
      rect.topRight + const Offset(-9, 9),
      rect.bottomLeft + const Offset(9, -9),
      rect.bottomRight + const Offset(-9, -9),
    ]) {
      canvas.drawCircle(c, 3.2, Paint()..color = KColors.bg);
      canvas.drawCircle(
          c,
          3.2,
          Paint()
            ..color = const Color(0xff9fd0b5).withValues(alpha: .6)
            ..style = PaintingStyle.stroke
            ..strokeWidth = 1);
    }
    // Copper traces draw themselves from their first point.
    final drawn = Curves.easeOutCubic.transform(((t - .15) / .85).clamp(0, 1));
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
      final path = Path()..addPolygon(points, false);
      final metric =
          path.computeMetrics().fold<double>(0, (a, m) => a + m.length);
      final visible = Path();
      var remaining = metric * drawn;
      for (final m in path.computeMetrics()) {
        if (remaining <= 0) break;
        visible.addPath(
            m.extractPath(0, math.min(m.length, remaining)), Offset.zero);
        remaining -= m.length;
      }
      final copper = trace['layer'] == 'B.Cu'
          ? const Color(0xff72a9c6)
          : const Color(0xffe0b866);
      canvas.drawPath(
          visible,
          Paint()
            ..color = copper.withValues(alpha: .16)
            ..style = PaintingStyle.stroke
            ..strokeCap = StrokeCap.round
            ..strokeJoin = StrokeJoin.round
            ..strokeWidth = 5);
      canvas.drawPath(
          visible,
          Paint()
            ..color = copper
            ..style = PaintingStyle.stroke
            ..strokeCap = StrokeCap.round
            ..strokeJoin = StrokeJoin.round
            ..strokeWidth = 1.6);
    }
    for (final component in asMaps(pcb['components']).take(128)) {
      final x = _finite(component['x']), y = _finite(component['y']);
      final w = _positive(component['width']),
          h = _positive(component['height']);
      if (x == null || y == null || w == null || h == null) continue;
      final r = Rect.fromCenter(
          center: at(x, y), width: w * scale, height: h * scale);
      // Pads along the long edges give each package a footprint.
      final horizontal = r.width >= r.height;
      final pads = ((horizontal ? r.width : r.height) / 5).floor().clamp(0, 10);
      final pad = Paint()..color = const Color(0xffe0b866);
      for (var i = 0; i < pads; i++) {
        final f = (i + .5) / pads;
        if (horizontal) {
          final px = r.left + r.width * f;
          canvas.drawRect(Rect.fromLTWH(px - 1, r.top - 2.5, 2, 3), pad);
          canvas.drawRect(Rect.fromLTWH(px - 1, r.bottom - .5, 2, 3), pad);
        } else {
          final py = r.top + r.height * f;
          canvas.drawRect(Rect.fromLTWH(r.left - 2.5, py - 1, 3, 2), pad);
          canvas.drawRect(Rect.fromLTWH(r.right - .5, py - 1, 3, 2), pad);
        }
      }
      canvas.drawRRect(RRect.fromRectAndRadius(r, const Radius.circular(2.5)),
          Paint()..color = const Color(0xff262d34));
      canvas.drawRRect(
          RRect.fromRectAndRadius(r.deflate(.5), const Radius.circular(2.5)),
          Paint()
            ..color = const Color(0xffd7d3b4).withValues(alpha: .85)
            ..style = PaintingStyle.stroke
            ..strokeWidth = 1);
      if (r.width > 18 && r.height > 11) {
        final label = '${component['label'] ?? component['id'] ?? ''}';
        final text = TextPainter(
            text: TextSpan(
                text: label.length > 16 ? '${label.substring(0, 15)}…' : label,
                style: const TextStyle(
                    color: Color(0xffefecd8),
                    fontSize: 9,
                    fontFamily: 'GeistMono')),
            textDirection: TextDirection.ltr,
            maxLines: 1,
            ellipsis: '…')
          ..layout(maxWidth: math.max(1, r.width - 3));
        text.paint(canvas, r.center - Offset(text.width / 2, text.height / 2));
      }
    }
    canvas.restore();
    canvas.restore();
  }

  @override
  bool shouldRepaint(covariant _PcbPainter old) =>
      old.pcb != pcb || old.width != width || old.height != height;
}

class _AppPreview extends StatefulWidget {
  const _AppPreview({required this.app, this.height = 330});
  final JsonMap app;
  final double height;
  @override
  State<_AppPreview> createState() => _AppPreviewState();
}

class _AppPreviewState extends State<_AppPreview>
    with TickerProviderStateMixin, _Entrance {
  @override
  Widget build(BuildContext context) {
    final app = widget.app;
    final widgets = asMaps(app['widgets']).take(6).toList();
    if (app.isEmpty) {
      return const SizedBox(
          height: 290,
          child: _WaitingPreview(
              icon: Icons.phone_android_outlined,
              text:
                  'The companion screen will appear as its interface is defined.'));
    }
    return SizedBox(
        height: widget.height,
        child: AnimatedBuilder(
          animation: arrive,
          builder: (context, child) => Opacity(
              opacity: arrive.value.clamp(0, 1),
              child: Transform.translate(
                  offset: Offset(0, (1 - arrive.value) * 22), child: child)),
          child: FittedBox(
            fit: BoxFit.scaleDown,
            child: Container(
              key: const ValueKey('native-app-sketch'),
              width: 250,
              height: 330,
              padding: const EdgeInsets.fromLTRB(14, 12, 14, 12),
              decoration: BoxDecoration(
                  gradient: const LinearGradient(
                      begin: Alignment.topLeft,
                      end: Alignment.bottomRight,
                      colors: [Color(0xff4a4a53), Color(0xff2a2a31)]),
                  borderRadius: BorderRadius.circular(34),
                  boxShadow: KShadow.stage),
              child: Container(
                padding: const EdgeInsets.fromLTRB(14, 10, 14, 12),
                decoration: BoxDecoration(
                    color: KColors.bg, borderRadius: BorderRadius.circular(24)),
                child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Center(
                          child: Container(
                              width: 60,
                              height: 5,
                              decoration: BoxDecoration(
                                  color: KColors.surface3,
                                  borderRadius: BorderRadius.circular(8)))),
                      const SizedBox(height: 18),
                      Text('${app['title'] ?? 'Your device'}',
                          maxLines: 2,
                          overflow: TextOverflow.ellipsis,
                          style: const TextStyle(
                              fontSize: 18,
                              letterSpacing: -.4,
                              fontWeight: FontWeight.w600)),
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
                                borderRadius: BorderRadius.circular(12),
                                border: Border.all(color: KColors.border)),
                            child: pColumn([
                              pText(item['label'] ?? item['kind'],
                                  small: true, muted: true),
                              if (item['value'] != null)
                                pText(item['value'], strong: true),
                            ], gap: 3),
                          ),
                        if (widgets.isEmpty)
                          pText('Connected device', small: true, muted: true),
                      ], gap: 9))),
                      const SizedBox(height: 8),
                      pText('Preview values are illustrative',
                          small: true, muted: true),
                    ]),
              ),
            ),
          ),
        ));
  }
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
          Container(
              width: 64,
              height: 64,
              decoration: BoxDecoration(
                  shape: BoxShape.circle,
                  color: KColors.surface2.withValues(alpha: .7),
                  border: Border.all(color: KColors.border)),
              child: Icon(icon, size: 26, color: KColors.faint)),
          const SizedBox(height: 16),
          ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 300),
              child: Text(text,
                  textAlign: TextAlign.center,
                  style: const TextStyle(
                      color: KColors.muted, fontSize: 13, height: 1.5))),
        ]),
      ));
}
