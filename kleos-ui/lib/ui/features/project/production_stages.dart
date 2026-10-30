import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:share_plus/share_plus.dart';

import '../../../data/models/json.dart';
import '../../../data/models/production.dart';
import '../../../state/kleos_store.dart';
import '../../../state/production_store.dart';
import '../../core/widgets.dart';
import 'common.dart';

/// Real engineering records presented inside the original stage workspace.
class ProductionStage extends ConsumerStatefulWidget {
  const ProductionStage(
      {super.key,
      required this.controller,
      required this.project,
      required this.stage,
      required this.onNext});
  final KleosController controller;
  final JsonMap project;
  final int stage;
  final VoidCallback onNext;
  @override
  ConsumerState<ProductionStage> createState() => _ProductionStageState();
}

class _ProductionStageState extends ConsumerState<ProductionStage> {
  final Map<String, TextEditingController> answers = {};
  final battery = List.generate(4, (_) => TextEditingController());
  String? downloadError;
  String? downloading;
  bool refreshing = false;
  JsonMap get workspace => asMap(widget.project['productionWorkspace']);
  JsonMap get project => asMap(workspace['project']);
  JsonMap get state => asMap(project['state']);
  JsonMap get proposal => asMap(state['proposal']);
  List<JsonMap> get runs => asMaps(workspace['runs']);
  bool get active =>
      const {'queued', 'running', 'waiting'}.contains(project['status']);
  bool get canWrite =>
      ref.watch(accountProvider).asData?.value?.canWrite == true;

  @override
  void dispose() {
    for (final field in [...answers.values, ...battery]) {
      field.dispose();
    }
    super.dispose();
  }

  Future<CommandResult> command(String type, [JsonMap data = const {}]) {
    FocusManager.instance.primaryFocus?.unfocus();
    return widget.controller
        .execute(type, {'projectId': widget.project['id'], ...data});
  }

  Widget action(String type, String label,
          {JsonMap data = const {},
          bool primary = true,
          bool allowActive = false}) =>
      KButton(
          label: label,
          primary: primary,
          onPressed: canWrite &&
                  (!active || allowActive) &&
                  !widget.controller.isPending(type)
              ? () => command(type, data)
              : null);

  Widget record(String title, Object? value) => pDisclosure(
      title: title,
      child: SizedBox(
          width: double.infinity,
          child: SelectableText(
              const JsonEncoder.withIndent('  ').convert(value),
              style: const TextStyle(
                  fontFamily: 'GeistMono', fontSize: 12, height: 1.5))));

  List<Widget> nextActions() {
    if (active) {
      return [
        action('project.cancel', 'Cancel run',
            allowActive: true, primary: false)
      ];
    }
    if (proposal.isEmpty) {
      return [action('project.research', 'Research feasibility')];
    }
    if (state['confirmed'] != true) {
      return [
        action('project.requestReview', 'Refine concept', primary: false),
        action('project.confirm', 'Confirm and start design')
      ];
    }
    if (asMap(project['interfaces']).isEmpty) {
      return [action('project.solveInterfaces', 'Establish interfaces')];
    }
    final stage = number(project['stage']).toInt();
    if (stage < 5) {
      return [action('project.generate', 'Generate engineering files')];
    }
    if (stage == 5) {
      {
        return [
          action('project.verify', 'Run independent checks'),
          action('project.repair', 'Repair candidates', primary: false),
          if (asList(workspace['checks']).isNotEmpty)
            action('project.acceptRelease', 'Accept digital release',
                primary: false)
        ];
      }
    }
    for (final step in [
      ('quotes', 'project.requestQuotes', 'Simulate quotes'),
      ('prototype_order', 'project.placeOrders', 'Simulate prototype order'),
      (
        'physical_tasks',
        'project.createPhysicalTasks',
        'Simulate physical tasks'
      ),
      (
        'physical_acceptance',
        'project.acceptPhysical',
        'Record simulated physical review'
      ),
      ('kit', 'project.prepareKit', 'Prepare kit package'),
      ('kit_acceptance', 'project.acceptKit', 'Accept simulated kit'),
      ('shipment', 'project.ship', 'Simulate shipment'),
      ('activation', 'project.activate', 'Simulate activation'),
    ]) {
      if (state[step.$1] == null) return [action(step.$2, step.$3)];
    }
    return [];
  }

  Widget concept() => pColumn([
        if (proposal.isEmpty)
          pText(
              'Your brief is ready. Start research to receive a concept, engineering scope, and any questions that need your input.',
              muted: true)
        else ...[
          if (proposal['summary'] != null) pText(proposal['summary']),
          for (final limitation in asList(proposal['limitations']))
            pCallout(pText(limitation, small: true)),
          if (asMap(proposal['requirements']).isNotEmpty)
            record('Proposed requirements', proposal['requirements']),
          if (asList(proposal['sources']).isNotEmpty)
            record('Research sources', proposal['sources']),
          for (final question in asList(proposal['questions']))
            questionField('$question'),
        ],
        if (asMap(project['requirements']).isNotEmpty)
          record('Accepted requirements', project['requirements']),
      ], gap: 18);

  Widget questionField(String question) {
    final field = answers.putIfAbsent(question, TextEditingController.new);
    final saved = asMap(state['answers'])[question];
    return pColumn([
      pText(question, strong: true),
      if (saved != null)
        pText('Saved answer: ${asList(saved).join(', ')}', muted: true),
      TextField(
          controller: field,
          enabled: canWrite && !active,
          minLines: 1,
          maxLines: 4,
          decoration: const InputDecoration(labelText: 'Your answer'),
          onChanged: (_) => setState(() {})),
      KButton(
          label: 'Save answer',
          compact: true,
          onPressed: !canWrite ||
                  active ||
                  field.text.trim().isEmpty ||
                  widget.controller.isPending('project.answer')
              ? null
              : () => command('project.answer', {
                    'questionKey': question,
                    'values': [field.text.trim()]
                  })),
    ], gap: 10);
  }

  Widget interfaceRecords() {
    final interfaces = asMap(project['interfaces']);
    return pColumn([
      pText(
          'Mechanics, electronics and firmware share the same accepted interface revision. Changes invalidate dependent files and checks.',
          muted: true),
      if (interfaces.isEmpty)
        pText('No interface baseline has been established yet.')
      else
        for (final item in interfaces.entries)
          record(item.key.replaceAll('_', ' '), item.value),
    ]);
  }

  Widget generation() {
    final policy = asMap(project['modelPolicy']);
    return pColumn([
      pText(
          'Each lane generates candidate files against the shared interfaces. Only independent checks and release gates can accept them.',
          muted: true),
      KResponsive(columns: 3, children: [
        for (final lane in engineeringLanes.entries)
          KCard(
              title: lane.value,
              child: pColumn([
                pText(
                    providerLabel(
                        '${asMap(policy[lane.key])['provider'] ?? ''}'),
                    strong: true),
                pText(asMap(policy[lane.key])['model'],
                    small: true, muted: true),
                pText(
                    '${asMaps(workspace['artifacts']).where((a) => a['lane'] == lane.key).length} recorded artifacts',
                    small: true),
              ], gap: 5)),
      ]),
    ]);
  }

  Widget validation() => pColumn([
        pText(
            'Results come from independent engineering checks. UNKNOWN means a capability is unverified; it does not count as a pass.',
            muted: true),
        if (asMaps(workspace['checks']).isEmpty)
          pText('No validation results have been recorded yet.')
        else
          for (final check in asMaps(workspace['checks']))
            KCard(
                title: '${check['name']}',
                trailing: evidence(check['status']),
                child: pColumn([
                  pText(check['evidenceKind'], muted: true, small: true),
                  record('Evidence and limitations', check['details']),
                ])),
      ]);

  Widget fulfillment() {
    final keys = switch (widget.stage) {
      6 => ['quotes', 'prototype_order'],
      7 => ['physical_tasks', 'physical_acceptance'],
      8 => ['kit', 'kit_acceptance'],
      _ => ['shipment', 'activation'],
    };
    return pColumn([
      pCallout(pColumn([
        const KBadge('Simulated physical workflow', tone: 'sim'),
        pText(
            'No live purchases, printing, shipping, or physical acceptance occur. Synthetic receipts cannot satisfy a live physical gate.',
            small: true),
      ])),
      if (keys.every((key) => state[key] == null))
        pText(
            'No records for this stage yet. Complete the preceding engineering gates first.',
            muted: true),
      for (final key in keys)
        if (state[key] != null) record(key.replaceAll('_', ' '), state[key]),
      if (widget.stage >= 8 && canWrite)
        action('project.assemblyStep', 'Record next assembly step (simulated)',
            primary: false,
            data: {'step': number(state['assemblyStep'], -1).toInt() + 1}),
      if (widget.stage == 6 && canWrite)
        DropdownButtonFormField<String>(
            initialValue: state['printMode'] as String?,
            decoration: const InputDecoration(labelText: 'Printing preference'),
            items: const [
              DropdownMenuItem(value: 'home', child: Text('My home printer')),
              DropdownMenuItem(
                  value: 'order',
                  child: Text('Order printed parts (simulated)'))
            ],
            onChanged: active
                ? null
                : (mode) {
                    if (mode != null) {
                      command('project.selectPrint', {'mode': mode});
                    }
                  }),
    ]);
  }

  Future<void> download(JsonMap artifact, {bool release = false}) async {
    setState(() {
      downloading = '${artifact['id']}';
      downloadError = null;
    });
    try {
      final api = ref.read(productionApiProvider);
      final bytes = release
          ? await api.releaseBytes('${artifact['id']}')
          : await api.artifactBytes('${artifact['id']}');
      if (!mounted) return;
      final box = context.findRenderObject() as RenderBox;
      await SharePlus.instance.share(ShareParams(files: [
        XFile.fromData(bytes,
            mimeType: '${artifact['mediaType'] ?? 'application/octet-stream'}')
      ], fileNameOverrides: [
        '${artifact['name']}'
      ], sharePositionOrigin: box.localToGlobal(Offset.zero) & box.size));
    } catch (error) {
      if (mounted) setState(() => downloadError = error.toString());
    } finally {
      if (mounted) setState(() => downloading = null);
    }
  }

  Widget artifacts() => KCard(
      title: 'Engineering files',
      child: pColumn([
        if (downloadError != null)
          pCallout(pText(downloadError, color: KColors.fail)),
        if (asMaps(workspace['artifacts']).isEmpty)
          pText(
              'Generated sources, builds, exports, and evidence will appear here.',
              muted: true),
        for (final artifact in asMaps(workspace['artifacts']))
          ListTile(
              contentPadding: EdgeInsets.zero,
              title: pText(artifact['name']),
              subtitle: pText('${artifact['lane']} · ${artifact['status']}',
                  small: true, muted: true),
              trailing: IconButton(
                  tooltip: 'Download artifact',
                  icon: const Icon(Icons.download_outlined),
                  onPressed:
                      downloading != null ? null : () => download(artifact))),
        if (project['acceptedReleaseId'] != null)
          KButton(
              label: 'Download accepted release',
              icon: Icons.folder_zip_outlined,
              onPressed: downloading != null
                  ? null
                  : () => download({
                        'id': project['acceptedReleaseId'],
                        'name':
                            'kleos-release-${project['acceptedReleaseId']}.zip',
                        'mediaType': 'application/zip'
                      }, release: true)),
      ]));

  Widget settings() => KCard(
      title: 'Project settings',
      child: pColumn([
        KButton(
            label: 'AI providers',
            compact: true,
            onPressed: () => context
                .go('/provider?tab=models&projectId=${widget.project['id']}')),
        pDisclosure(
            title: 'Revise battery requirements',
            child: pColumn([
              pText(
                  'A battery change creates a new engineering revision and invalidates dependent designs and checks. Previous accepted files remain available.',
                  muted: true,
                  small: true),
              for (var i = 0; i < battery.length; i++)
                TextField(
                    controller: battery[i],
                    enabled: canWrite && !active,
                    keyboardType:
                        const TextInputType.numberWithOptions(decimal: true),
                    decoration: InputDecoration(
                        labelText: [
                      'Capacity (mAh)',
                      'Width (mm)',
                      'Length (mm)',
                      'Height (mm)'
                    ][i]),
                    onChanged: (_) => setState(() {})),
              KButton(
                  label: 'Apply battery revision',
                  onPressed: !canWrite ||
                          active ||
                          widget.controller.isPending('project.swapBattery') ||
                          !battery.every((f) {
                            final n = double.tryParse(f.text);
                            return n != null && n.isFinite && n > 0;
                          })
                      ? null
                      : () => command('project.swapBattery', {
                            'capacityMah': double.parse(battery[0].text),
                            'dimensionsMm': {
                              'x': double.parse(battery[1].text),
                              'y': double.parse(battery[2].text),
                              'z': double.parse(battery[3].text)
                            }
                          })),
            ])),
        pDisclosure(
            title: 'Usage allowance',
            child: pColumn([
              pText(
                  'Project allowance: \$${asMap(project['budget'])['allowanceUsd'] ?? 0}'),
              pText(
                  'Used: \$${asMap(project['budget'])['spentEstimateUsd'] ?? 0} · Reserved: \$${asMap(project['budget'])['reservedUsd'] ?? 0}',
                  muted: true),
            ])),
        SwitchListTile.adaptive(
            contentPadding: EdgeInsets.zero,
            title: const Text('Contribute validated examples'),
            subtitle: const Text(
                'Optional. Allow eligible examples to be considered for future model training. Turning this off excludes this project from future datasets.'),
            value: workspace['trainingOptedIn'] == true,
            onChanged: canWrite &&
                    !widget.controller.isPending('project.setTrainingConsent')
                ? (value) =>
                    command('project.setTrainingConsent', {'optedIn': value})
                : null),
      ]));

  Widget connectionError(String message) => pCallout(pColumn([
        pText('Workspace updates interrupted', strong: true),
        pText(message, muted: true, small: true),
        KButton(
            label: refreshing ? 'Refreshing…' : 'Refresh workspace',
            compact: true,
            icon: Icons.refresh,
            onPressed: refreshing
                ? null
                : () async {
                    setState(() => refreshing = true);
                    try {
                      await widget.controller.refresh();
                    } finally {
                      if (mounted) setState(() => refreshing = false);
                    }
                  }),
      ]));

  @override
  Widget build(BuildContext context) {
    final liveError = widget.controller.state['liveError']?.toString() ??
        widget.controller.error;
    if (project.isEmpty) {
      return liveError != null
          ? connectionError(liveError)
          : const KPageSkeleton();
    }
    final latest = runs.firstOrNull;
    return pColumn([
      KSectionHeader(
          title: stageNames[widget.stage],
          description:
              'Engineering revision ${project['engineeringRevision']}'),
      KCard(
          child: pColumn([
        pActions([
          evidence(project['status']),
          KBadge(
              widget.controller.state['liveConnected'] == true
                  ? 'Live updates'
                  : 'Reconnecting updates',
              tone: widget.controller.state['liveConnected'] == true
                  ? 'pass'
                  : 'unknown'),
          const KBadge('Real engineering'),
          const KBadge('Physical steps simulated', tone: 'sim')
        ]),
        if (liveError != null) connectionError(liveError),
        if (latest?['blocker'] != null)
          pCallout(pText(latest!['blocker'], color: KColors.fail)),
        pActions([
          ...nextActions(),
          if (!active &&
              latest != null &&
              const {'blocked', 'failed'}.contains(latest['status']))
            action('project.retry', 'Retry last run',
                primary: false, data: {'runId': latest['id']}),
        ]),
        if (active) const LinearProgressIndicator(minHeight: 3),
      ])),
      KCard(
          title: switch (widget.stage) {
            0 => 'Your brief and questions',
            1 => 'Research findings',
            2 => 'Concept review',
            3 => 'Shared interfaces',
            4 => 'Engineering lanes',
            5 => 'Independent validation',
            _ => 'Stage records',
          },
          child: switch (widget.stage) {
            0 || 1 || 2 => concept(),
            3 => interfaceRecords(),
            4 => generation(),
            5 => validation(),
            _ => fulfillment(),
          }),
      if (widget.stage >= 3 || asMaps(workspace['artifacts']).isNotEmpty)
        artifacts(),
      if (runs.isNotEmpty)
        KCard(
            title: 'Runs',
            child: pColumn([
              for (final run in runs)
                ListTile(
                    contentPadding: EdgeInsets.zero,
                    title: pText('${run['kind']}'.replaceFirst('project.', '')),
                    subtitle: pText(run['blocker'] ?? run['status'],
                        muted: true, small: true),
                    trailing: const Icon(Icons.chevron_right),
                    onTap: () => context.go('/runs/${run['id']}')),
            ])),
      if (widget.stage < number(project['stage']).toInt())
        pRight(KButton(
            label: 'Continue to ${stageNames[(widget.stage + 1).clamp(0, 9)]}',
            primary: true,
            icon: Icons.arrow_forward,
            onPressed: widget.onNext)),
      settings(),
    ], gap: 24);
  }
}
