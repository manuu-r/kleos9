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
import '../../core/concept_preview.dart';
import 'common.dart';
import 'requirements_interview.dart';
import 'planning_conversation.dart';

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
  final battery = List.generate(4, (_) => TextEditingController());
  String? downloadError;
  String? downloading;
  bool refreshing = false;
  String? selectedPart;
  JsonMap get workspace => asMap(widget.project['productionWorkspace']);
  JsonMap get project => asMap(workspace['project']);
  JsonMap get state => asMap(project['state']);
  JsonMap get proposal => asMap(state['proposal']);
  List<JsonMap> get runs => asMaps(workspace['runs']);
  bool get active =>
      const {'queued', 'running', 'waiting'}.contains(project['status']);
  bool get planningActive =>
      active &&
      (runs.any((run) =>
              const {'queued', 'running', 'waiting'}.contains(run['status']) &&
              const {
                'project.capture',
                'project.research',
                'project.requestReview'
              }.contains(run['kind'])) ||
          (asMap(state['planning']).isNotEmpty &&
              number(project['stage']) <= 2));
  bool get planningPending => asMap(state['planning'])['pending'] == true;
  String? get selectedPartId =>
      asMaps(asMap(asMap(state['concept'])['scene'])['parts'])
              .any((part) => part['id'] == selectedPart)
          ? selectedPart
          : null;
  String? get selectedPartLabel =>
      asMaps(asMap(asMap(state['concept'])['scene'])['parts'])
          .where((part) => part['id'] == selectedPartId)
          .firstOrNull?['label']
          ?.toString();
  bool get canWrite =>
      ref.watch(accountProvider).asData?.value?.canWrite == true;

  @override
  void dispose() {
    for (final field in battery) {
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
      return asMap(state['planning']).isEmpty
          ? [
              action('project.capture', 'Start planning',
                  data: {'autoResume': true})
            ]
          : [];
    }
    if (state['confirmed'] != true) return [];
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

  Widget concept() => LayoutBuilder(builder: (context, constraints) {
        final concept = asMap(state['concept']);
        final scene = asMap(concept['scene']);
        final clarification = asMap(state['clarification']);
        final conversation = asMaps(state['conversation']);
        final questions = [
          ...asMaps(clarification['questions']),
          ...asMaps(clarification['resolvedQuestions'])
        ];
        final pending = planningPending || planningActive;
        final chat = PlanningConversation(
          key: ValueKey('planning-chat-${project['id']}'),
          messages: conversation.isEmpty
              ? [
                  {
                    'id': 'original-brief',
                    'role': 'user',
                    'text': project['brief']
                  },
                ]
              : conversation,
          canWrite: canWrite,
          busy: active,
          pending: planningPending,
          allowSendWhileBusy: planningActive,
          phase:
              '${asMap(state['planning'])['phase'] ?? (widget.stage == 1 ? 'research' : 'capture')}',
          selectedPartLabel: selectedPartLabel,
          selectedPartId: selectedPartId,
          onClearPart: () => setState(() => selectedPart = null),
          onSend: (text, clientId) => command('project.message', {
            'text': text,
            'clientMessageId': clientId,
            'autoResume': true,
            if (selectedPartId != null) ...{
              'selectedPartId': selectedPartId,
              'conceptVersion': concept['version'],
            },
          }),
          question: proposal.isEmpty
              ? null
              : RequirementsInterview(
                  key: ValueKey(project['id']),
                  clarification: clarification,
                  proposal: proposal,
                  legacyAnswers: asMap(state['answers']),
                  canWrite: canWrite,
                  active: active,
                  confirmed: state['confirmed'] == true,
                  autoResume: true,
                  acceptWhileActive: planningActive,
                  updatePending: planningPending,
                  onAnswer: (id, values, legacy) => command('project.answer', {
                    if (legacy) 'questionKey': id else 'questionId': id,
                    'values': values,
                    'autoResume': true,
                    if (questions
                            .where((q) => q['id'] == id)
                            .firstOrNull?['questionHash'] !=
                        null)
                      'questionHash': questions
                          .where((q) => q['id'] == id)
                          .first['questionHash'],
                  }),
                  onReview: () =>
                      command('project.requestReview', {'autoResume': true}),
                  onConfirm: () => command('project.confirm'),
                ),
        );
        final preview = KCard(
          title: 'Live concept',
          trailing: const KBadge('Concept preview'),
          child: ConceptPreview(
            scene: scene,
            conceptVersion: (concept['version'] as num?)?.toInt(),
            updating: pending,
            height: constraints.maxWidth < 600 ? 270 : 390,
            onPartSelected: (id) => setState(() => selectedPart = id),
          ),
        );
        final details = pDisclosure(
          title: 'Concept and engineering details',
          child: pColumn([
            if (proposal['summary'] != null) pText(proposal['summary']),
            for (final limitation in asList(proposal['limitations']))
              pCallout(pText(limitation, small: true)),
            if (asMap(proposal['requirements']).isNotEmpty)
              record('Proposed requirements', proposal['requirements']),
            if (asList(proposal['sources']).isNotEmpty)
              record('Research sources', proposal['sources']),
            if (asMap(project['requirements']).isNotEmpty)
              record('Accepted requirements', project['requirements']),
          ], gap: 16),
        );
        return pColumn([
          if (constraints.maxWidth >= 900)
            Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Expanded(flex: 6, child: chat),
              const SizedBox(width: 24),
              Expanded(flex: 5, child: preview),
            ])
          else ...[preview, chat],
          if (proposal.isNotEmpty) details,
        ], gap: 24);
      });

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
            0 => 'Shape your device',
            1 => 'Shape your device',
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
      if (widget.stage <= 2 && state['confirmed'] != true)
        pDisclosure(title: 'Project settings', child: settings())
      else
        settings(),
    ], gap: 24);
  }
}
