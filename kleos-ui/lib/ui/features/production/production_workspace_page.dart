import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:share_plus/share_plus.dart';
import '../../../data/models/json.dart';
import '../../../data/models/production.dart';
import '../../../state/production_store.dart';
import '../../core/widgets.dart';
import '../home_pages.dart' show stageNames;
import 'production_widgets.dart';
import 'model_policy_editor.dart';

class ProductionWorkspacePage extends ConsumerWidget {
  const ProductionWorkspacePage({super.key, required this.projectId});
  final String projectId;
  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final view = ref.watch(productionProjectProvider(projectId));
    return view.when(
      loading: () => const ProductionPage(children: [ProductionLoading()]),
      error: (e, _) => ProductionPage(children: [
        ProductionRetry(e.toString(),
            retry: () => ref.invalidate(productionProjectProvider(projectId)))
      ]),
      data: (value) => _workspace(context, ref, value),
    );
  }

  Widget _workspace(BuildContext context, WidgetRef ref, ProjectView value) {
    final workspace = value.workspace;
    final project = workspace.project;
    final controller = ref.read(productionProjectProvider(projectId).notifier);
    final canWrite =
        ref.watch(accountProvider).asData?.value?.canWrite ?? false;
    final proposal = asMap(project.state['proposal']);
    final proposedRequirements = asMap(proposal['requirements']);
    return ProductionPage(children: [
      KSectionHeader(
          title: project.name,
          description: project.brief,
          trailing: Wrap(spacing: 8, runSpacing: 8, children: [
            KBadge(project.status,
                tone: project.status == 'blocked' ? 'unknown' : null),
            KBadge(value.connected ? 'Live updates' : 'Waiting for updates'),
            IconButton(
                tooltip: 'Refresh workspace',
                onPressed: controller.refresh,
                icon: const Icon(Icons.refresh)),
          ])),
      const ProductionNotice(
          'Real design and digital checks · Simulated physical fulfillment'),
      if (value.error != null) ProductionNotice(value.error!, error: true),
      KCard(
          title: stageNames[project.stage.clamp(0, 9)],
          child: kColumn([
            KProgress((project.stage + 1) * 10.0),
            KMuted('Engineering revision ${project.engineeringRevision}'),
            if (workspace.runs.isNotEmpty &&
                workspace.runs.first.blocker != null)
              ProductionNotice(workspace.runs.first.blocker!, error: true),
            if (canWrite)
              Wrap(spacing: 10, runSpacing: 10, children: [
                for (final action in _actions(workspace))
                  KButton(
                      key: ValueKey('command-${action.$1}'),
                      label: action.$2,
                      primary: action.$3,
                      onPressed: value.busy
                          ? null
                          : () => controller.execute(action.$1)),
                if (project.isActive)
                  KButton(
                      label: 'Cancel run',
                      onPressed: value.busy
                          ? null
                          : () => controller.execute('project.cancel')),
                if (!project.isActive &&
                    workspace.runs.isNotEmpty &&
                    const {'blocked', 'failed'}
                        .contains(workspace.runs.first.status))
                  KButton(
                      label: 'Retry last run',
                      onPressed: value.busy
                          ? null
                          : () => controller.execute('project.retry',
                              {'runId': workspace.runs.first.id})),
              ]),
            if (value.busy) const KLineLoader(),
          ])),
      if (proposal.isNotEmpty)
        KCard(
            title: 'Concept and findings',
            child: kColumn([
              SelectionArea(child: Text(proposal['summary']?.toString() ?? '')),
              if (asList(proposedRequirements['excludedCoverage']).isNotEmpty)
                ProductionNotice('Excluded from digital validation: '
                    '${asList(proposedRequirements['excludedCoverage']).map(_coverageLabel).join(', ')}. '
                    'These capabilities remain unverified.'),
              if (asList(proposedRequirements['requiredPeripheralCoverage'])
                  .isNotEmpty)
                Text('Required peripheral checks: '
                    '${asList(proposedRequirements['requiredPeripheralCoverage']).map(_coverageLabel).join(', ')}.'),
              if (proposedRequirements.isNotEmpty)
                ProductionJsonDetails(
                    'Proposed requirements and validation scope',
                    proposedRequirements),
              for (final question in asList(proposal['questions']))
                _AnswerQuestion(
                    projectId: projectId,
                    question: question.toString(),
                    enabled: canWrite && !value.busy),
              if (asList(proposal['limitations']).isNotEmpty)
                ProductionNotice(asList(proposal['limitations']).join('\n')),
            ], gap: 16)),
      if (workspace.checks.isNotEmpty)
        KCard(
            title: 'Independent checks',
            child: kColumn([
              for (final raw in workspace.checks)
                Builder(builder: (context) {
                  final check = asMap(raw);
                  return kColumn([
                    Wrap(spacing: 10, runSpacing: 8, children: [
                      KBadge(check['status'].toString(),
                          tone: check['status'] == 'PASS' ? 'pass' : 'unknown'),
                      Text(check['name'].toString()),
                      KMuted(check['evidenceKind'].toString()),
                    ]),
                    ProductionJsonDetails(
                        'Check evidence and limitations', check['details'])
                  ]);
                }),
            ])),
      _ModelSettings(
          projectId: projectId,
          project: project,
          enabled: canWrite && !value.busy),
      if (project.data['acceptedReleaseId'] != null)
        _ReleaseDownload(
            releaseId: project.data['acceptedReleaseId'] as String,
            previous: project.stage < 6),
      _EngineeringSettings(
          projectId: projectId,
          project: project,
          enabled: canWrite && !value.busy),
      if (project.interfaces.isNotEmpty)
        ProductionJsonDetails(
            'Agreed engineering interfaces', project.interfaces),
      if (asMap(project.data['requirements']).isNotEmpty)
        ProductionJsonDetails(
            'Accepted requirements', project.data['requirements']),
      if (project.stage >= 6)
        KCard(
            title: 'Physical fulfillment · simulated',
            child: kColumn([
              const KMuted(
                  'These receipts represent synthetic outcomes. No purchases, printing, shipping, or physical acceptance occur.'),
              for (final entry in project.state.entries
                  .where((e) => asMap(e.value)['evidenceKind'] == 'synthetic'))
                ProductionJsonDetails(
                    entry.key.replaceAll('_', ' '), entry.value),
              if (canWrite)
                DropdownButtonFormField<String>(
                    key: ValueKey(project.state['printMode']),
                    initialValue: project.state['printMode'] as String?,
                    decoration:
                        const InputDecoration(labelText: 'Printing preference'),
                    items: const [
                      DropdownMenuItem(
                          value: 'home', child: Text('My home printer')),
                      DropdownMenuItem(
                          value: 'order',
                          child: Text('Order printed parts (simulated)'))
                    ],
                    onChanged: value.busy
                        ? null
                        : (mode) => controller
                            .execute('project.selectPrint', {'mode': mode})),
              if (project.stage >= 8 && canWrite)
                KButton(
                    label: 'Record next assembly step (simulated)',
                    onPressed: value.busy
                        ? null
                        : () => controller.execute('project.assemblyStep', {
                              'step':
                                  ((project.state['assemblyStep'] as int?) ??
                                          -1) +
                                      1,
                            })),
            ])),
      KCard(
          title: 'Usage allowance',
          child: Wrap(spacing: 30, runSpacing: 12, children: [
            ProductionMetric(
                'Allowance',
                project.budget['unlimited'] == true
                    ? 'Unlimited in development'
                    : productionMoney(project.budget['allowanceUsd'])),
            ProductionMetric('Allowance used',
                productionMoney(project.budget['spentEstimateUsd'])),
            ProductionMetric(
                'Reserved', productionMoney(project.budget['reservedUsd'])),
          ])),
      KCard(
          title: 'Engineering artifacts',
          child: workspace.artifacts.isEmpty
              ? const KMuted(
                  'Generated sources, builds, exports, and evidence will appear here.')
              : kColumn([
                  for (final artifact in workspace.artifacts)
                    _ArtifactRow(artifact: artifact)
                ])),
      KCard(
          title: 'Runs',
          child: workspace.runs.isEmpty
              ? const KMuted('Start research to create the first run.')
              : kColumn([
                  for (final run in workspace.runs)
                    ListTile(
                        key: ValueKey('open-run-${run.id}'),
                        contentPadding: EdgeInsets.zero,
                        title: Text(run.kind.replaceFirst('project.', '')),
                        subtitle: Text(run.blocker ?? run.status),
                        trailing: const Icon(Icons.chevron_right),
                        onTap: () => context.go('/runs/${run.id}'))
                ])),
      KCard(
          title: 'Activity',
          child: value.events.isEmpty
              ? const KMuted(
                  'New activity will appear here as work progresses.')
              : kColumn([
                  for (final event in value.events.reversed.take(30))
                    kColumn([
                      Text(event.type.replaceAll('.', ' ')),
                      if (event.data['reason'] != null)
                        KMuted(event.data['reason'].toString()),
                      if (event.data['summary'] != null)
                        KMuted(event.data['summary'].toString()),
                      if (event.data['evidenceKind'] == 'synthetic')
                        const KBadge('Simulated'),
                    ], gap: 4)
                ])),
      KCard(
          title: 'Training data choice',
          child: SwitchListTile.adaptive(
            key: const ValueKey('production-training-consent'),
            contentPadding: EdgeInsets.zero,
            title: const Text('Contribute validated examples'),
            subtitle: const Text(
                'Optional. Allow eligible project examples to be considered for future model training. Turning this off excludes them from future datasets.'),
            value: workspace.trainingOptedIn,
            onChanged: canWrite && !value.busy
                ? (value) => controller
                    .execute('project.setTrainingConsent', {'optedIn': value})
                : null,
          )),
    ]);
  }
}

List<(String, String, bool)> _actions(Workspace workspace) {
  final p = workspace.project;
  if (p.isActive) return [];
  final state = p.state;
  if (state['proposal'] == null) {
    return [('project.research', 'Research the concept', true)];
  }
  if (state['confirmed'] != true) {
    return [
      ('project.requestReview', 'Refine concept', false),
      ('project.confirm', 'Confirm concept', true),
    ];
  }
  if (p.interfaces.isEmpty) {
    return [('project.solveInterfaces', 'Establish interfaces', true)];
  }
  if (p.stage < 5) {
    return [('project.generate', 'Generate engineering files', true)];
  }
  if (p.stage == 5) {
    return [
      ('project.verify', 'Run independent checks', true),
      ('project.repair', 'Repair candidates', false),
      if (workspace.checks.isNotEmpty)
        ('project.acceptRelease', 'Accept digital release', false),
    ];
  }
  if (state['quotes'] == null) {
    return [('project.requestQuotes', 'Simulate quotes', true)];
  }
  if (state['prototype_order'] == null) {
    return [('project.placeOrders', 'Simulate prototype order', true)];
  }
  if (state['physical_tasks'] == null) {
    return [('project.createPhysicalTasks', 'Simulate physical tasks', true)];
  }
  if (state['physical_acceptance'] == null) {
    return [
      ('project.acceptPhysical', 'Record simulated physical review', true)
    ];
  }
  if (state['kit'] == null) {
    return [('project.prepareKit', 'Prepare kit package', true)];
  }
  if (state['kit_acceptance'] == null) {
    return [('project.acceptKit', 'Accept simulated kit', true)];
  }
  if (state['shipment'] == null) {
    return [('project.ship', 'Simulate shipment', true)];
  }
  if (state['activation'] == null) {
    return [('project.activate', 'Simulate activation', true)];
  }
  return [];
}

class _AnswerQuestion extends ConsumerStatefulWidget {
  const _AnswerQuestion(
      {required this.projectId, required this.question, required this.enabled});
  final String projectId;
  final String question;
  final bool enabled;
  @override
  ConsumerState<_AnswerQuestion> createState() => _AnswerQuestionState();
}

class _AnswerQuestionState extends ConsumerState<_AnswerQuestion> {
  final answer = TextEditingController();
  @override
  void dispose() {
    answer.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => kColumn([
        Text(widget.question),
        TextField(
            key: ValueKey('answer-${widget.question}'),
            controller: answer,
            enabled: widget.enabled,
            decoration: const InputDecoration(labelText: 'Your answer'),
            onChanged: (_) => setState(() {})),
        KButton(
            key: ValueKey('save-answer-${widget.question}'),
            label: 'Save answer',
            onPressed: !widget.enabled || answer.text.trim().isEmpty
                ? null
                : () => ref
                        .read(productionProjectProvider(widget.projectId)
                            .notifier)
                        .execute('project.answer', {
                      'questionKey': widget.question,
                      'values': [answer.text.trim()]
                    })),
      ]);
}

class _ModelSettings extends ConsumerStatefulWidget {
  const _ModelSettings(
      {required this.projectId, required this.project, required this.enabled});
  final String projectId;
  final ProjectRecord project;
  final bool enabled;
  @override
  ConsumerState<_ModelSettings> createState() => _ModelSettingsState();
}

class _ModelSettingsState extends ConsumerState<_ModelSettings> {
  Map<String, ModelChoice>? draft;
  @override
  Widget build(BuildContext context) => ref.watch(modelCatalogProvider).when(
        loading: () => const ProductionLoading(),
        error: (e, _) => ProductionRetry(e.toString(),
            retry: () => ref.invalidate(modelCatalogProvider)),
        data: (catalog) => ExpansionTile(
          tilePadding: EdgeInsets.zero,
          title: const Text('AI provider settings'),
          subtitle: const Text('Muse models by engineering lane'),
          children: [
            ModelPolicyEditor(
                catalog: catalog,
                policy: draft ?? widget.project.modelPolicy,
                enabled: widget.enabled,
                onChanged: (value) => setState(() => draft = value)),
            if (draft != null)
              Padding(
                  padding: const EdgeInsets.symmetric(vertical: 16),
                  child: KButton(
                      label: 'Save provider choices',
                      primary: true,
                      onPressed: !widget.enabled
                          ? null
                          : () async {
                              final result = await ref
                                  .read(productionProjectProvider(
                                          widget.projectId)
                                      .notifier)
                                  .execute('project.setModelPolicy', {
                                'modelPolicy': draft!
                                    .map((k, v) => MapEntry(k, v.toJson()))
                              });
                              if (result != null && mounted) {
                                setState(() => draft = null);
                              }
                            })),
          ],
        ),
      );
}

class _EngineeringSettings extends ConsumerStatefulWidget {
  const _EngineeringSettings(
      {required this.projectId, required this.project, required this.enabled});
  final String projectId;
  final ProjectRecord project;
  final bool enabled;
  @override
  ConsumerState<_EngineeringSettings> createState() =>
      _EngineeringSettingsState();
}

class _EngineeringSettingsState extends ConsumerState<_EngineeringSettings> {
  final capacity = TextEditingController();
  final width = TextEditingController();
  final length = TextEditingController();
  final height = TextEditingController();
  @override
  void dispose() {
    for (final field in [capacity, width, length, height]) {
      field.dispose();
    }
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final fields = {
      capacity: 'Capacity (mAh)',
      width: 'Width (mm)',
      length: 'Length (mm)',
      height: 'Height (mm)'
    };
    final valid = fields.keys.every((f) {
      final value = double.tryParse(f.text);
      return value != null && value.isFinite && value > 0;
    });
    return ExpansionTile(
        tilePadding: EdgeInsets.zero,
        title: const Text('Revise battery requirements'),
        children: [
          kColumn([
            const KMuted(
                'Changing the battery starts a new engineering revision and invalidates dependent designs and checks. The previous accepted baseline remains available.'),
            for (final field in fields.entries)
              TextField(
                  key: ValueKey('battery-${field.value}'),
                  controller: field.key,
                  enabled: widget.enabled,
                  keyboardType:
                      const TextInputType.numberWithOptions(decimal: true),
                  decoration: InputDecoration(labelText: field.value),
                  onChanged: (_) => setState(() {})),
            KButton(
                label: 'Apply battery revision',
                onPressed: !widget.enabled || !valid
                    ? null
                    : () => ref
                            .read(productionProjectProvider(widget.projectId)
                                .notifier)
                            .execute('project.swapBattery', {
                          'capacityMah': double.parse(capacity.text),
                          'dimensionsMm': {
                            'x': double.parse(width.text),
                            'y': double.parse(length.text),
                            'z': double.parse(height.text)
                          },
                        })),
          ])
        ]);
  }
}

class _ArtifactRow extends ConsumerStatefulWidget {
  const _ArtifactRow({required this.artifact});
  final ArtifactRecord artifact;
  @override
  ConsumerState<_ArtifactRow> createState() => _ArtifactRowState();
}

class _ArtifactRowState extends ConsumerState<_ArtifactRow> {
  bool busy = false;
  String? error;
  @override
  Widget build(BuildContext context) => kColumn([
        ListTile(
            contentPadding: EdgeInsets.zero,
            title: Text(widget.artifact.name),
            subtitle:
                Text('${widget.artifact.lane} · ${widget.artifact.status}'),
            trailing: IconButton(
                key: ValueKey('download-artifact-${widget.artifact.id}'),
                tooltip: 'Download or share artifact',
                icon: busy
                    ? KSpinner(size: 18)
                    : const Icon(Icons.download_outlined),
                onPressed: busy
                    ? null
                    : () async {
                        setState(() {
                          busy = true;
                          error = null;
                        });
                        try {
                          final bytes = await ref
                              .read(productionApiProvider)
                              .artifactBytes(widget.artifact.id);
                          if (!context.mounted) return;
                          final box = context.findRenderObject() as RenderBox;
                          await SharePlus.instance.share(ShareParams(
                            files: [
                              XFile.fromData(bytes,
                                  mimeType: widget.artifact.mediaType)
                            ],
                            fileNameOverrides: [
                              widget.artifact.name.split('/').last
                            ],
                            sharePositionOrigin:
                                box.localToGlobal(Offset.zero) & box.size,
                          ));
                        } catch (e) {
                          if (mounted) setState(() => error = e.toString());
                        } finally {
                          if (mounted) setState(() => busy = false);
                        }
                      })),
        if (error != null) ProductionNotice(error!, error: true),
      ]);
}

class _ReleaseDownload extends ConsumerStatefulWidget {
  const _ReleaseDownload({required this.releaseId, required this.previous});
  final String releaseId;
  final bool previous;
  @override
  ConsumerState<_ReleaseDownload> createState() => _ReleaseDownloadState();
}

class _ReleaseDownloadState extends ConsumerState<_ReleaseDownload> {
  bool busy = false;
  String? error;
  @override
  Widget build(BuildContext context) => kColumn([
        KButton(
            label: busy
                ? 'Preparing package…'
                : widget.previous
                    ? 'Download previous accepted release'
                    : 'Download accepted release',
            icon: Icons.folder_zip_outlined,
            onPressed: busy
                ? null
                : () async {
                    setState(() {
                      busy = true;
                      error = null;
                    });
                    try {
                      final bytes = await ref
                          .read(productionApiProvider)
                          .releaseBytes(widget.releaseId);
                      if (!context.mounted) return;
                      final box = context.findRenderObject() as RenderBox;
                      await SharePlus.instance.share(ShareParams(
                          files: [
                            XFile.fromData(bytes, mimeType: 'application/zip')
                          ],
                          fileNameOverrides: [
                            'kleos-release-${widget.releaseId}.zip'
                          ],
                          sharePositionOrigin:
                              box.localToGlobal(Offset.zero) & box.size));
                    } catch (e) {
                      if (mounted) setState(() => error = e.toString());
                    } finally {
                      if (mounted) setState(() => busy = false);
                    }
                  }),
        if (error != null) ProductionNotice(error!, error: true),
      ]);
}

String _coverageLabel(Object? value) => switch (value) {
      'ble' => 'Bluetooth (BLE)',
      'sensor' => 'sensors',
      'display' => 'display interaction',
      'radio' => 'radio operation',
      'thermal' => 'thermal behavior',
      'physicalOperation' => 'physical operation',
      _ => value.toString(),
    };
