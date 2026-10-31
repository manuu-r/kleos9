import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../../../data/models/json.dart';
import '../../../state/kleos_store.dart';
import '../../core/device_visual.dart';
import '../../core/human_task_card.dart';
import '../../core/widgets.dart';
import 'common.dart';
import 'engineering_preview.dart' show EngineeringPreviewPanel;
import 'engineering_views.dart';
import 'mock_preview.dart';
import 'stage_kit.dart';
import 'production_stages.dart';

const _questions = <JsonMap>[
  {
    'key': 'features',
    'ask': 'What should it do day to day? Pick all that apply.',
    'options': [
      'CO2 level',
      'Temperature and humidity',
      'Fine dust (PM2.5)',
      'Alerts on your phone',
      'Hourly history'
    ],
    'multi': true
  },
  {
    'key': 'display',
    'ask': 'Which screen suits you?',
    'options': [
      'Small OLED (simple, sips power)',
      '2-inch color screen (richer)',
      'E-paper (calm, slow refresh)'
    ]
  },
  {
    'key': 'size',
    'ask': 'Roughly what case size?',
    'options': ['Pocket (90 mm)', 'Desk (140 mm)', 'Wall (180 mm)']
  },
  {
    'key': 'battery',
    'ask': 'How should it be powered?',
    'options': ['USB powered', '3 days on battery', '2 weeks on battery']
  },
  {
    'key': 'finish',
    'ask': 'Look and finish?',
    'options': ['Matte black', 'Sand', 'White']
  },
  {
    'key': 'assembly',
    'ask': 'How comfortable are you assembling electronics?',
    'options': [
      'First-timer: snap-fit, no soldering',
      'Some experience: screws are fine',
      'Happy to solder'
    ]
  },
  {
    'key': 'budget',
    'ask': 'What budget range should I design for?',
    'options': ['Under \$80', '\$80-150', '\$150+']
  },
];

const _bags = <JsonMap>[
  {'bag': 'A', 'contents': 'Base shell with four rubber feet', 'count': 5},
  {'bag': 'B', 'contents': 'Battery pack with its lead', 'count': 1},
  {
    'bag': 'C',
    'contents': 'Main board, pre-tested and flashed, with 4 screws',
    'count': 5
  },
  {'bag': 'D', 'contents': 'CO2 sensor card', 'count': 1},
  {'bag': 'E', 'contents': 'Display module on its ribbon', 'count': 1},
  {
    'bag': 'F',
    'contents': 'Top shell, two button caps, light pipe',
    'count': 4
  },
];
const _shipSteps = [
  'Label created',
  'Picked up',
  'In transit',
  'Out for delivery',
  'Delivered'
];
const _guide = <JsonMap>[
  {
    'title': 'Base shell',
    'text':
        'Set the base shell (bag A) on the table with the battery cradle on the left.'
  },
  {
    'title': 'Battery',
    'text':
        'Lay the battery (bag B) in the cradle and tuck its lead toward the middle.'
  },
  {
    'title': 'Main board',
    'text':
        'Lower the main board (bag C) onto the four posts, USB-C port on the right, and fit the four screws.'
  },
  {
    'title': 'Sensor card',
    'text':
        'Press the sensor card (bag D) into the header near the front edge. It only fits one way.'
  },
  {
    'title': 'Display',
    'text':
        'Rest the display (bag E) on the four pillars and seat its ribbon in the connector.'
  },
  {
    'title': 'Top shell',
    'text':
        'Fit the top shell (bag F) over everything and press the edges until it clicks.'
  },
];

class ProjectStage extends StatefulWidget {
  const ProjectStage(
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
  State<ProjectStage> createState() => _ProjectStageState();
}

class _ProjectStageState extends State<ProjectStage> {
  final Set<String> _picked = {};
  JsonMap get p => widget.project;
  KleosController get controller => widget.controller;
  List<JsonMap> get connectors => asMaps(controller.state['connectors']);
  List<JsonMap> get allTasks => asMaps(controller.state['humanTasks']);
  bool _done(int index) =>
      index < asList(p['done']).length && asList(p['done'])[index] == true;
  String _name(Object? id) =>
      '${connectors.where((c) => c['id'] == id).firstOrNull?['name'] ?? id ?? ''}';
  Widget _action(String command, String label,
          {bool enabled = true,
          bool primary = true,
          IconData? icon,
          JsonMap extra = const {}}) =>
      projectAction(controller, p, command, label,
          enabled: enabled, primary: primary, icon: icon, extra: extra);
  Widget _next(String label) => pRight(KButton(
      label: label,
      primary: true,
      icon: Icons.arrow_forward,
      onPressed: widget.onNext));
  Widget _device({bool exploded = true, int? guideStep}) => SizedBox(
      height: 300,
      child: DeviceVisual(
          exploded: exploded, finish: finishOf(p), guideStep: guideStep));
  List<JsonMap> _tasks(String field) =>
      allTasks.where((t) => asList(p[field]).contains(t['id'])).toList();

  @override
  Widget build(BuildContext context) => p['production'] == true
      ? ProductionStage(
          controller: controller,
          project: p,
          stage: widget.stage,
          onNext: widget.onNext)
      : switch (widget.stage) {
          0 => _intent(),
          1 => _feasibility(),
          2 => _confirm(),
          3 => _interfaces(),
          4 => _generate(),
          5 => _verify(),
          6 => _procure(),
          7 => _physical(),
          8 => _kit(),
          _ => _deliver(),
        };

  Widget _chat(String text, {bool user = false}) => Row(
          mainAxisAlignment:
              user ? MainAxisAlignment.end : MainAxisAlignment.start,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            if (!user) ...[
              Container(
                  width: 28,
                  height: 28,
                  margin: const EdgeInsets.only(top: 2),
                  decoration: const BoxDecoration(
                      shape: BoxShape.circle, color: KColors.surface3),
                  child: const Icon(Icons.auto_awesome,
                      size: 14, color: KColors.text)),
              const SizedBox(width: 12),
            ],
            Flexible(
                child: Container(
              constraints: const BoxConstraints(maxWidth: 620),
              padding: const EdgeInsets.symmetric(horizontal: 17, vertical: 13),
              decoration: BoxDecoration(
                  color: user ? KColors.surface3 : KColors.surface,
                  borderRadius: BorderRadius.only(
                      topLeft: Radius.circular(user ? 16 : 4),
                      topRight: Radius.circular(user ? 4 : 16),
                      bottomLeft: const Radius.circular(16),
                      bottomRight: const Radius.circular(16))),
              child: pText(text),
            )),
          ]);

  Future<void> _answer(JsonMap question, List<String> values) async {
    final result = await controller.execute('project.answer', {
      'projectId': p['id'],
      'questionKey': question['key'],
      'values': values
    });
    if (mounted && result.ok) setState(_picked.clear);
  }

  Widget _intent() {
    final answers = asMap(p['answers']);
    final current =
        _questions.where((q) => !answers.containsKey(q['key'])).firstOrNull;
    final answered =
        _questions.where((q) => answers.containsKey(q['key'])).length;
    final pending = controller.isPending('project.answer');
    return pColumn([
      StageHeader(
          title: 'Capture intent',
          description:
              'A few focused questions so the design matches what you actually want.',
          status: _done(0)
              ? const KBadge('Requirements captured', tone: 'pass')
              : KBadge(
                  'Question ${(answered + 1).clamp(1, _questions.length)} of ${_questions.length}',
                  tone: 'accent')),
      KPanel(
          padding: const EdgeInsets.all(24),
          child: pColumn([
            SegmentBar(tones: [
              for (var i = 0; i < _questions.length; i++)
                i < answered ? 'pass' : (i == answered ? 'accent' : '')
            ]),
            const SizedBox(height: 4),
            _chat(
                'Got it: “${'${p['brief']}'.replaceFirst(RegExp(r'[.\s]+$'), '')}”. I’ll ask a few quick questions before researching feasibility.'),
            for (final q
                in _questions.where((q) => answers.containsKey(q['key']))) ...[
              _chat('${q['ask']}'),
              _chat(joinValues(answers[q['key']]), user: true),
            ],
            if (current != null) ...[
              _chat('${current['ask']}'),
              Padding(
                  padding: const EdgeInsets.only(left: 40),
                  child: pColumn([
                    Wrap(spacing: 8, runSpacing: 8, children: [
                      for (final value in asList(current['options']))
                        ChoicePill(
                            label: '$value',
                            multi: current['multi'] == true,
                            selected: _picked.contains('$value'),
                            onTap: pending
                                ? null
                                : current['multi'] == true
                                    ? () => setState(() {
                                          if (!_picked.add('$value')) {
                                            _picked.remove('$value');
                                          }
                                        })
                                    : () => _answer(current, ['$value'])),
                    ]),
                    if (current['multi'] == true)
                      KButton(
                          label: 'Continue',
                          primary: true,
                          compact: true,
                          onPressed: _picked.isEmpty || pending
                              ? null
                              : () => _answer(current, _picked.toList())),
                  ], gap: 14)),
            ],
            if (current == null && !_done(0))
              _chat('Summarizing your requirements…'),
          ], gap: 16)),
      if (_done(0))
        KCard(
            title: 'Requirements v1',
            trailing: KButton(
                label: 'Research feasibility',
                primary: true,
                icon: Icons.arrow_forward,
                onPressed: widget.onNext),
            child: KResponsive(children: [
              KeyValueRows(rows: [
                for (final requirement in asMaps(p['requirements']))
                  ('${requirement['label']}', pText(requirement['value']))
              ]),
              pColumn([
                const Text('Assumptions', style: KType.label),
                for (final item in asList(p['assumptions'])) pText('• $item'),
                const SizedBox(height: 4),
                const Text('Unknowns, carried forward', style: KType.label),
                for (final item in asList(p['unknowns']))
                  pActions(
                      [const KBadge('Unknown', tone: 'unknown'), pText(item)]),
              ], gap: 8),
            ])),
    ], gap: 28);
  }

  Widget _feasibility() {
    final research = asMap(p['research']);
    const tracks = [
      'Parts & availability',
      'Materials & printing',
      'Electronics & power budget',
      'Firmware & app support'
    ];
    final idle = p['feasibility'] == 'idle';
    final done = p['feasibility'] == 'done';
    return pColumn([
      StageHeader(
          title: 'Research feasibility',
          description:
              'Parts, materials, manufacturing and software are researched together before anything is designed.',
          status: done
              ? const KBadge('Research complete', tone: 'pass')
              : idle
                  ? null
                  : const KBadge('Researching', tone: 'accent'),
          action: idle
              ? _action('project.research', 'Run feasibility research',
                  icon: Icons.search)
              : null),
      if (!idle)
        MeterList(items: [
          for (final track in tracks)
            (label: track, value: number(research[track]).toDouble())
        ]),
      if (done) ...[
        KResponsive(children: [
          KCard(
              title: 'Concept',
              child: SizedBox(
                  width: double.infinity,
                  child: KGridBackdrop(child: _device()))),
          KCard(
              title: 'Features',
              child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    for (final (i, feature)
                        in asMaps(p['features']).indexed) ...[
                      if (i > 0)
                        const Divider(height: 1, color: KColors.hairline),
                      Padding(
                          padding: const EdgeInsets.symmetric(vertical: 12),
                          child: pColumn([
                            pActions([
                              pText(feature['name'], strong: true),
                              KBadge('${feature['status']}',
                                  tone: feature['status'] == 'supported'
                                      ? 'pass'
                                      : 'unknown')
                            ]),
                            pText(feature['note'], muted: true, small: true),
                          ], gap: 4)),
                    ]
                  ])),
        ]),
        KCard(
            title: 'Candidate parts',
            child: KTable(headers: const [
              'Role',
              'Part',
              'Source',
              'Availability'
            ], rows: [
              for (final part in asMaps(p['candidateParts']))
                [
                  pText(part['role'], muted: true),
                  pText(part['name']),
                  pText(part['source']),
                  pText(part['note'], muted: true)
                ]
            ])),
        const KStats(items: [
          (
            label: 'Kit estimate',
            value: '\$95-125',
            sub: 'Quotes commit later; these are estimates.'
          ),
          (label: 'Lead time', value: '9-14 days', sub: null),
          (label: 'Assembly', value: '~40 min', sub: 'No soldering'),
        ]),
        KResponsive(children: [
          StagePanel(
              title: 'Limitations',
              child: pColumn([
                pText('• Not a safety alarm'),
                pText('• CO2 is indicative until calibrated'),
                pText('• Printed shells show fine layer lines')
              ], gap: 7)),
          StagePanel(
              title: 'Next',
              child: pColumn([
                pText('Review the concept and confirm before design starts.'),
                KButton(
                    label: 'Review & confirm',
                    primary: true,
                    icon: Icons.arrow_forward,
                    onPressed: widget.onNext)
              ], gap: 14)),
        ]),
      ],
    ], gap: 28);
  }

  Widget _confirm() {
    final review =
        allTasks.where((t) => t['id'] == p['reviewTaskId']).firstOrNull;
    final confirmed = p['confirmed'] == true;
    return pColumn([
      StageHeader(
          title: 'Review concept & confirm',
          description:
              'Nothing is designed or spent until you confirm. Reduce scope or ask a human engineer first if you like.',
          status: confirmed
              ? const KBadge('Confirmed', tone: 'pass')
              : review != null
                  ? const KBadge('With an engineer', tone: 'accent')
                  : null),
      KResponsive(children: [
        KCard(
            title: 'Your monitor',
            child: pColumn([
              SizedBox(
                  width: double.infinity,
                  height: 240,
                  child: KGridBackdrop(
                      child:
                          DeviceVisual(exploded: false, finish: finishOf(p)))),
              KeyValueRows(rows: [
                for (final requirement in asMaps(p['requirements'])
                    .where((r) => r['label'] != 'Features'))
                  (
                    '${requirement['label']}',
                    pText(requirement['value'], small: true)
                  ),
              ]),
            ], gap: 12)),
        KCard(
            title: 'Features (click to reduce scope)',
            child: pColumn([
              for (final feature in asMaps(p['features']))
                CheckboxListTile(
                  contentPadding: EdgeInsets.zero,
                  dense: true,
                  controlAffinity: ListTileControlAffinity.leading,
                  value: feature['status'] != 'dropped',
                  onChanged: p['confirmed'] == true ||
                          controller.isPending('project.toggleFeature')
                      ? null
                      : (_) {
                          controller.execute('project.toggleFeature',
                              {'projectId': p['id'], 'name': feature['name']});
                        },
                  title: Text('${feature['name']}',
                      style: TextStyle(
                          fontSize: 14,
                          decoration: feature['status'] == 'dropped'
                              ? TextDecoration.lineThrough
                              : null)),
                  subtitle: Padding(
                      padding: const EdgeInsets.only(top: 3),
                      child: Align(
                          alignment: Alignment.centerLeft,
                          child: KBadge('${feature['status']}',
                              tone: feature['status'] == 'supported'
                                  ? 'pass'
                                  : feature['status'] == 'limited'
                                      ? 'unknown'
                                      : ''))),
                ),
            ], gap: 1)),
      ]),
      if (review != null)
        HumanTaskCard(
            key: ValueKey(review['id']), controller: controller, task: review),
      KPanel(
          child: pColumn([
        confirmed
            ? pActions([
                const Icon(Icons.check_circle_outline,
                    color: KColors.pass, size: 18),
                pText('Confirmed. Design can start.', color: KColors.pass)
              ])
            : const Wrap(spacing: 28, runSpacing: 8, children: [
                KMuted('Estimated kit \$95-125', size: 13),
                KMuted('9-14 days', size: 13),
                KMuted('About 40 min to assemble', size: 13),
              ]),
        pActions([
          if (p['confirmed'] != true && review == null)
            _action('project.requestReview', 'Ask a human engineer to review',
                primary: false, icon: Icons.person_outline),
          if (p['confirmed'] == true)
            KButton(
                label: 'Establish interfaces',
                primary: true,
                icon: Icons.arrow_forward,
                onPressed: widget.onNext)
          else
            _action('project.confirm', 'Confirm and start design',
                icon: Icons.check_circle_outline),
        ]),
      ], gap: 16)),
    ], gap: 28);
  }

  Widget _interfaces() {
    final saved = asMaps(p['interfaces']);
    final rows = saved.isNotEmpty ? saved : asMaps(p['interfacePreview']);
    final running = rows.any((r) => r['state'] == 'RUNNING');
    final agreed = p['interfacesAgreed'] == true;
    final tones = [for (final r in rows) evidenceTone('${r['state']}')];
    final passed = tones.where((t) => t == 'pass').length;
    const criteria = [
      'Every interface value has units and an owner',
      'Battery and core fit with at least 0.3 mm clearance',
      'Estimated battery life meets the requirement',
      'All parts reachable for assembly without soldering',
      'Firmware pin map matches schematic net names'
    ];
    return pColumn([
      StageHeader(
          title: 'Establish interfaces',
          description:
              'Mechanics, electronics and firmware agree on shared contracts first, so they are designed to fit together.',
          status: agreed
              ? const KBadge('Interfaces agreed', tone: 'pass')
              : running
                  ? const KBadge('Solver running', tone: 'accent')
                  : null,
          action: !agreed
              ? _action('project.solveInterfaces', 'Run solver and agree',
                  enabled: !running, icon: Icons.functions)
              : null),
      SolverBand(
          passed: passed, total: rows.length, tones: tones, running: running),
      EngineeringPreviewPanel(
          key: ValueKey('studio-${p['id']}-contracts'),
          data: mockPreviewData(p),
          initialView: 'overview',
          mechanicsBuilder: (height) => mockAssembly(p, height),
          sidebar: (bounded) => ContractExplorer(
                  title: 'Interface contracts',
                  bounded: bounded,
                  items: [
                    for (final r in rows)
                      (
                        name: '${r['name']}',
                        value: '${r['value']}',
                        state: '${r['state']}',
                        group: '${r['owner']}',
                        footer: null
                      )
                  ])),
      KPanel(
          child: pColumn([
        GroupLabel('Acceptance criteria',
            trailing: const KBadge('Owned by Quality')),
        const KMuted('Generators cannot change these.', size: 12),
        CriteriaList(items: criteria, satisfied: agreed),
      ], gap: 14)),
      if (agreed) _next('Generate designs'),
    ], gap: 36);
  }

  Widget _laneCard(String lane, JsonMap progress, JsonMap tools) {
    final pct = number(progress[lane]).toDouble();
    final generating = p['generation'] == 'running' && pct < 100;
    final status = pct >= 100
        ? 'Candidate ready'
        : generating
            ? 'Generating'
            : 'Ready';
    final compact = MediaQuery.sizeOf(context).width < 600;
    return KCard(
        tint: generating ? KColors.accent : null,
        padding: EdgeInsets.all(compact ? 16 : 22),
        child: pColumn([
          Row(children: [
            ProgressRing(
                value: pct,
                size: compact ? 42 : 52,
                running: generating,
                child: pct >= 100
                    ? const Icon(Icons.check, size: 20, color: KColors.pass)
                    : Text('${pct.round()}',
                        style: const TextStyle(
                            fontFamily: 'GeistMono',
                            fontSize: 13,
                            fontWeight: FontWeight.w500))),
            const SizedBox(width: 16),
            Expanded(
                child: pColumn([
              Text(lane, style: Theme.of(context).textTheme.titleMedium),
              KMuted(status, size: 12),
            ], gap: 2)),
          ]),
          if (lane == 'App')
            const KMuted('Kleos9 App (built in)', size: 12)
          else
            Builder(builder: (context) {
              final candidates = connectors
                  .where((c) =>
                      c['kind'] == 'tool' &&
                      c['domain'] == lane &&
                      c['linked'] == true &&
                      c['enabled'] == true)
                  .toList();
              final value = candidates.any((c) => c['id'] == tools[lane])
                  ? '${tools[lane]}'
                  : null;
              final locked = p['generation'] != 'idle' ||
                  controller.isPending('project.selectTool');
              return Semantics(
                  label: '$lane tool',
                  child: Container(
                      constraints: const BoxConstraints(minHeight: 48),
                      padding: const EdgeInsets.symmetric(horizontal: 16),
                      alignment: Alignment.center,
                      decoration: KShape.surface(KColors.surface2,
                          radius: KRadius.control),
                      child: DropdownButton<String>(
                        value: value,
                        isExpanded: true,
                        underline: const SizedBox.shrink(),
                        dropdownColor: KColors.surface3,
                        borderRadius: BorderRadius.circular(KRadius.control),
                        icon: const Icon(Icons.unfold_more,
                            size: 16, color: KColors.faint),
                        hint: const KMuted('Choose a tool', size: 14),
                        items: [
                          for (final candidate in candidates)
                            DropdownMenuItem(
                                value: '${candidate['id']}',
                                child: Text(
                                    '${candidate['name']}${candidate['builtIn'] == true ? ' (default)' : ''}',
                                    overflow: TextOverflow.ellipsis,
                                    style: const TextStyle(fontSize: 14)))
                        ],
                        onChanged: locked
                            ? null
                            : (value) {
                                if (value != null) {
                                  controller.execute('project.selectTool', {
                                    'projectId': p['id'],
                                    'lane': lane,
                                    'toolId': value
                                  });
                                }
                              },
                      )));
            }),
        ], gap: 16));
  }

  Widget _generate() {
    final lanes = asList(p['generationLanes']).map((v) => '$v').toList();
    final progress = asMap(p['genProgress']);
    final tools = asMap(p['tools']);
    final log = asList(p['genLog']).map((l) => '$l').toList();
    final running = p['generation'] == 'running';
    final done = p['generation'] == 'done';
    final overall = lanes.isEmpty
        ? 0.0
        : lanes.fold<double>(0, (a, l) => a + number(progress[l]).toDouble()) /
            lanes.length;
    return pColumn([
      StageHeader(
          title: 'Generate in parallel',
          description:
              'Each domain generates code-defined files against the shared interface spec. Generators submit candidates; they cannot approve them.',
          status: done
              ? const KBadge('Candidates ready', tone: 'pass')
              : running
                  ? KBadge('Generating ${overall.round()}%', tone: 'accent')
                  : null,
          action: p['generation'] == 'idle'
              ? _action('project.generate', 'Start generation',
                  icon: Icons.play_arrow_rounded)
              : null),
      pColumn([
        SpecFan(label: 'Interface spec v1', count: lanes.length),
        EngineeringGrid(minWidth: 250, children: [
          for (final lane in lanes) _laneCard(lane, progress, tools)
        ]),
        if (p['generation'] == 'idle')
          pActions([
            const KMuted(
                'Default Kleos9 tools run unless you pick another for a lane.',
                size: 12),
            KButton(
                label: 'Plug in more tools',
                compact: true,
                onPressed: () => context.go('/connections?tab=tools'))
          ]),
      ], gap: 14),
      EngineeringPreviewPanel(
          key: ValueKey('studio-${p['id']}-files'),
          data: mockPreviewData(p),
          initialView: 'overview',
          mechanicsBuilder: (height) => mockAssembly(p, height)),
      if (log.isNotEmpty)
        pColumn([
          GroupLabel('Generation log',
              trailing: running ? const KBadge('Live', tone: 'accent') : null),
          TerminalPanel(lines: log, live: running),
        ], gap: 14),
      if (done)
        pColumn([
          GroupLabel('Candidate artifacts',
              count: asMaps(p['artifacts']).length,
              trailing: KButton(
                  label: 'Verify',
                  compact: true,
                  primary: true,
                  icon: Icons.arrow_forward,
                  onPressed: widget.onNext)),
          KPanel(
              padding: EdgeInsets.zero,
              child: Column(children: [
                for (final (i, artifact) in asMaps(p['artifacts']).indexed) ...[
                  if (i > 0) const Divider(height: 1, color: KColors.hairline),
                  Padding(
                      padding: const EdgeInsets.symmetric(
                          horizontal: 20, vertical: 16),
                      child: LayoutBuilder(builder: (context, c) {
                        final head = pColumn([
                          pText(artifact['name'], strong: true),
                          pText('${artifact['domain']}  ${artifact['file']}',
                              mono: true, small: true, muted: true),
                        ], gap: 3);
                        final meta = pColumn([
                          pText('v${artifact['version']}  ${artifact['hash']}',
                              mono: true, small: true, muted: true),
                          if (joinValues(artifact['deps']).isNotEmpty)
                            pText('Depends on ${joinValues(artifact['deps'])}',
                                small: true, muted: true),
                        ], gap: 3);
                        return c.maxWidth < 560
                            ? pColumn([
                                Row(children: [
                                  Expanded(child: head),
                                  evidence(artifact['status'])
                                ]),
                                meta
                              ], gap: 10)
                            : Row(children: [
                                Expanded(flex: 5, child: head),
                                Expanded(flex: 5, child: meta),
                                evidence(artifact['status'])
                              ]);
                      }))
                ]
              ])),
        ], gap: 14),
    ], gap: 36);
  }

  Widget _verify() {
    final checks = asMaps(p['checks']);
    final artifacts = asMaps(p['artifacts']);
    final running = p['verifyRun'] == 'running';
    final busy = running ||
        artifacts.any((a) => a['status'] == 'generating') ||
        checks.any((c) => c['state'] == 'RUNNING');
    final blocking = checks
        .where(
            (c) => ['FAIL', 'STALE', 'PENDING', 'RUNNING'].contains(c['state']))
        .toList();
    final unknowns = checks.where((c) => c['state'] == 'UNKNOWN').toList();
    final failed = checks.where((c) => c['state'] == 'FAIL').toList();
    final stale = checks.where((c) => c['state'] == 'STALE').toList();
    final passed = checks.where((c) => c['state'] == 'PASS').toList();
    final attention = checks
        .where((c) => ['FAIL', 'STALE', 'UNKNOWN'].contains(c['state']))
        .toList();
    final accepted = '${p['manifestHash'] ?? ''}'.isNotEmpty;
    final settled = p['verifyRun'] == 'done';
    final hasStale = stale.isNotEmpty ||
        failed.isNotEmpty ||
        artifacts.any((a) => a['status'] == 'stale');
    final finished = checks
        .where((c) => !['PENDING', 'RUNNING'].contains(c['state']))
        .length;
    final tones = [
      for (final c in checks)
        switch ('${c['state']}') {
          'PASS' => 'pass',
          'FAIL' => 'fail',
          'RUNNING' => 'accent',
          'PENDING' => '',
          _ => 'unknown',
        }
    ];
    return pColumn([
      StageHeader(
          title: 'Verify and revise',
          description:
              'Validators inspect the candidate files. Anything they cannot prove stays unknown.',
          status: accepted
              ? const KBadge('Release accepted', tone: 'pass')
              : running
                  ? KBadge('$finished of ${checks.length} checks done',
                      tone: 'accent')
                  : null,
          action: p['verifyRun'] == 'idle'
              ? _action('project.verify', 'Run verification',
                  icon: Icons.verified_user_outlined)
              : null),
      if ((running || settled) && checks.isNotEmpty)
        SolverBand(
            passed: passed.length,
            total: checks.length,
            tones: tones,
            running: running,
            label: running ? 'checks passed so far' : 'checks passed'),
      if (settled) ...[
        pActions([
          KBadge('${passed.length} passed', tone: 'pass'),
          if (failed.isNotEmpty)
            KBadge('${failed.length} failed', tone: 'fail'),
          if (stale.isNotEmpty)
            KBadge('${stale.length} out of date', tone: 'unknown'),
          if (unknowns.isNotEmpty)
            KBadge('${unknowns.length} need a person', tone: 'unknown')
        ]),
        if (attention.isNotEmpty)
          StagePanel(
              title: 'Needs attention',
              trailing: Text('${attention.length}', style: KType.monoSmall),
              child: pCheckList(attention)),
        pDisclosure(
            title: 'All checks',
            count: checks.length,
            child: pColumn([
              for (final domain in checks.map((c) => '${c['domain']}').toSet())
                pColumn([
                  Text(domain, style: KType.label),
                  pCheckList(
                      checks.where((c) => c['domain'] == domain).toList())
                ], gap: 4),
            ], gap: 18)),
      ],
      if (settled && !accepted)
        KPanel(
            child: pColumn([
          pText(
              hasStale
                  ? 'Only the affected work is regenerated; the rest is reused.'
                  : unknowns.isNotEmpty
                      ? 'Unknown results move to physical testing, where people can check them.'
                      : 'Nothing is blocking this release.',
              muted: true),
          pActions([
            if (hasStale)
              _action('project.repair', 'Fix and re-check',
                  enabled: !busy, icon: Icons.refresh)
            else ...[
              _action('project.swapBattery',
                  'Try a ${number(p['batteryMah']) + 500} mAh battery',
                  enabled: !busy,
                  primary: false,
                  icon: Icons.battery_charging_full),
              _action('project.acceptRelease', 'Accept digital release',
                  enabled: !busy && blocking.isEmpty),
            ],
          ]),
        ], gap: 14)),
      if (accepted)
        KPanel(
            child: pColumn([
          pText(
              'Release ${shortHash(p['manifestHash'])} covers ${artifacts.length} artifacts. ${unknowns.length} unknown result(s) go to physical testing.',
              muted: true),
          _next('Procure prototype')
        ], gap: 14)),
    ], gap: 28);
  }

  Widget _procure() {
    final quotes = asMaps(p['quotes']);
    final printer = connectors
        .where((c) =>
            c['kind'] == 'printer' &&
            c['linked'] == true &&
            c['enabled'] == true)
        .firstOrNull;
    final caseQuote = quotes
        .where((q) =>
            q['selected'] == true && '${q['item']}'.startsWith('Shells print'))
        .firstOrNull;
    final selected = quotes
        .where((q) =>
            q['selected'] == true &&
            !(p['casePrint'] == 'home' && q['id'] == caseQuote?['id']))
        .toList();
    final total = selected.fold<num>(0, (sum, q) => sum + number(q['price']));
    final mandate = asMap(p['mandate']);
    final used = number(mandate['spent']) + number(mandate['reserved']);
    final remaining = number(mandate['limit']) - (used == 0 ? total : used);
    final items = quotes.map((q) => '${q['item']}').toSet().toList();
    final print = asMap(p['homePrint']);
    final orders = asMaps(p['orders']);
    return pColumn([
      StageHeader(
          title: 'Procure prototype',
          description:
              'Each item goes to the connector that fits best. Nothing is sent until you authorize.',
          status: p['procurement'] == 'quoting'
              ? const KBadge('Waiting for quotes', tone: 'accent')
              : p['procurement'] == 'done'
                  ? const KBadge('Orders received', tone: 'pass')
                  : null,
          action: p['procurement'] == 'idle'
              ? _action('project.requestQuotes', 'Request quotes',
                  icon: Icons.receipt_long_outlined)
              : null),
      if (p['procurement'] == 'quoting')
        KPanel(
            child:
                pColumn([pText('Waiting for quotes…'), const KLineLoader()])),
      if (quotes.isNotEmpty) ...[
        KPanel(
            padding: const EdgeInsets.all(8),
            child: KTable(headers: const [
              'Item',
              'Routed to',
              'Price',
              'Lead time'
            ], rows: [
              for (final item in items)
                () {
                  final candidates =
                      quotes.where((q) => q['item'] == item).toList();
                  final quote = candidates
                          .where((q) => q['selected'] == true)
                          .firstOrNull ??
                      candidates.first;
                  final home = p['casePrint'] == 'home' &&
                      quote['id'] == caseQuote?['id'];
                  return <Widget>[
                    pText(item),
                    pText(home ? 'Your printer' : _name(quote['connectorId']),
                        muted: true),
                    pText(home ? '\$2.80' : '\$${quote['price']}', mono: true),
                    pText(home ? 'Today' : '${quote['leadDays']} days',
                        mono: true)
                  ];
                }()
            ])),
        pDisclosure(
            title: 'Compare all quotes',
            count: quotes.length,
            child: KTable(headers: const [
              'Item',
              'Provider',
              'Price',
              'Lead',
              'Quality',
              'On time',
              'Why'
            ], rows: [
              for (final quote in quotes)
                [
                  pText(quote['item'], small: true),
                  pColumn([
                    pText(_name(quote['connectorId']), small: true),
                    if (quote['selected'] == true)
                      const KBadge('selected', tone: 'accent')
                  ], gap: 3),
                  pText('\$${quote['price']}', mono: true),
                  pText('${quote['leadDays']}d', mono: true),
                  pText(quote['quality'], mono: true),
                  pText('${quote['onTime']}%', mono: true),
                  SizedBox(
                      width: 220,
                      child: pText(quote['reason'], muted: true, small: true)),
                ]
            ])),
      ],
      if (caseQuote != null)
        pColumn([
          Text('Printed shells',
              style: Theme.of(context).textTheme.titleMedium),
          pText('Print them yourself, or order them.', muted: true),
          if (printer != null)
            KResponsive(children: [
              for (final mode in ['home', 'order'])
                KTap(
                  onTap: p['procurement'] != 'quoted' ||
                          controller.isPending('project.selectPrint')
                      ? null
                      : () {
                          controller.execute('project.selectPrint',
                              {'projectId': p['id'], 'mode': mode});
                        },
                  builder: (context, hovered) => AnimatedContainer(
                      duration: KMotion.base,
                      curve: KMotion.out,
                      padding: const EdgeInsets.all(20),
                      decoration: BoxDecoration(
                          color: p['casePrint'] == mode
                              ? KColors.surface3
                              : hovered
                                  ? KColors.surface2
                                  : KColors.surface,
                          border: Border.all(
                              color: p['casePrint'] == mode
                                  ? KColors.text.withValues(alpha: .85)
                                  : Colors.transparent,
                              width: 1.5),
                          borderRadius: BorderRadius.circular(KRadius.card)),
                      child: pColumn([
                        pActions([
                          Icon(
                              mode == 'home'
                                  ? Icons.home_outlined
                                  : Icons.local_shipping_outlined,
                              size: 20),
                          pText(
                              mode == 'home'
                                  ? 'Print at home on ${asMap(printer['printer'])['model'] ?? printer['name']}'
                                  : 'Order from ${_name(caseQuote['connectorId'])}',
                              strong: true),
                          if (p['casePrint'] == mode)
                            const KBadge('Chosen', tone: 'accent')
                        ]),
                        pText(
                            mode == 'home'
                                ? 'About \$2.80 of filament  ·  About 6 h 30 min  ·  Ready today'
                                : '\$${caseQuote['price']}  ·  ${caseQuote['leadDays']} days  ·  Shipped to kit prep',
                            muted: true,
                            small: true),
                        pText(
                            mode == 'home'
                                ? 'Tolerance profile applied: ${asMap(printer['printer'])['tolerance']}'
                                : 'Routed by measured quality and on-time history',
                            muted: true,
                            small: true),
                      ])),
                ),
            ])
          else
            pCallout(pColumn([
              pText(
                  'Have a 3D printer? Connect it and print the shells at home instead of ordering them.'),
              KButton(
                  label: 'Connect a printer',
                  compact: true,
                  icon: Icons.print_outlined,
                  onPressed: () => context.go(Uri(
                          path: '/connections',
                          queryParameters: {
                            'tab': 'printers',
                            'back': '/projects/${p['id']}?s=6'
                          }).toString())),
            ])),
        ]),
      if (print.isNotEmpty)
        KCard(
            title:
                'Home print on ${asMap(printer?['printer'])['model'] ?? 'your printer'}',
            trailing: const KBadge('Simulated', tone: 'sim'),
            child: KResponsive(children: [
              Semantics(
                  image: true,
                  label:
                      'Printed shells on the home printer bed, ${print['progress']} percent complete',
                  child: SizedBox(
                      height: 155,
                      child: CustomPaint(
                          painter: _PrintBedPainter(
                              number(print['progress']).toDouble())))),
              pColumn([
                pActions([
                  pText(
                      switch (print['state']) {
                        'slicing' => 'Slicing',
                        'sending' => 'Sending to printer',
                        'printing' => 'Printing',
                        'done' => 'Finished',
                        _ => 'Waiting'
                      },
                      strong: true),
                  pText('${print['progress']}%', mono: true)
                ]),
                KProgress(number(print['progress']).toDouble()),
                pText(
                    'Layer ${print['layer']} of ${print['layers']}  ·  Nozzle ${print['nozzle']} °C',
                    muted: true,
                    small: true),
                pText(
                    print['state'] == 'done'
                        ? 'Take the parts off the bed'
                        : 'Top and bottom shells, PETG',
                    muted: true,
                    small: true),
              ])
            ])),
      if (!['idle', 'quoting'].contains(p['procurement']))
        pColumn([
          KStats(items: [
            (
              label: 'Mandate',
              value: '\$${mandate['limit']}',
              sub: 'Spending limit for this build'
            ),
            (label: 'This order', value: '\$$total', sub: null),
            (label: 'Left after', value: '\$$remaining', sub: null),
          ]),
          if (p['procurement'] == 'quoted')
            pRight(_action('project.placeOrders', 'Authorize and place orders',
                enabled: total <= number(mandate['limit']),
                icon: Icons.shopping_cart_outlined)),
          if (orders.isNotEmpty)
            pDisclosure(
                title: 'Orders',
                count: orders.length,
                initiallyExpanded: true,
                child: KTable(headers: const [
                  'Provider / reference',
                  'Amount',
                  'State'
                ], rows: [
                  for (final order in orders)
                    [
                      pText(
                          '${_name(order['connectorId'])}${order['externalRef'] == null ? '' : '  ${order['externalRef']}'}'),
                      pText('\$${order['amount']}', mono: true),
                      evidence(order['state'])
                    ]
                ])),
        ], gap: 16),
      if (p['procurement'] == 'done') _next('Physical tests'),
    ], gap: 28);
  }

  Widget _physical() {
    final tasks = _tasks('physicalTaskIds');
    final rah = connectors.where((c) => c['id'] == 'rentahuman').firstOrNull;
    final linked = rah?['linked'] == true && rah?['enabled'] == true;
    final allAccepted =
        tasks.isNotEmpty && tasks.every((t) => t['state'] == 'ACCEPTED');
    final accepted = tasks.where((t) => t['state'] == 'ACCEPTED').length;
    return pColumn([
      StageHeader(
          title: 'Test physically',
          description:
              'What the AI could not verify goes to people, with a fixed evidence format.',
          status: tasks.isEmpty
              ? null
              : KBadge('$accepted of ${tasks.length} accepted',
                  tone: allAccepted ? 'pass' : 'accent'),
          action: tasks.isEmpty
              ? _action('project.createPhysicalTasks',
                  'Create human tasks from UNKNOWN results',
                  icon: Icons.back_hand_outlined)
              : null),
      KPanel(
          child: pActions([
        Icon(linked ? Icons.link : Icons.link_off,
            size: 18, color: linked ? KColors.pass : KColors.faint),
        ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 560),
            child: pText(
                linked
                    ? 'Jobs are posted to RentAHuman as bounties.'
                    : 'No human-task platform is linked. Tasks wait until you connect one or use the Kleos9 portal.',
                muted: true)),
        if (!linked)
          KButton(
              label: 'Connect RentAHuman',
              compact: true,
              icon: Icons.link,
              onPressed: () => context.go(Uri(
                      path: '/connections',
                      queryParameters: {
                        'tab': 'human',
                        'connect': 'rentahuman',
                        'back': '/projects/${p['id']}?s=7'
                      }).toString())),
      ])),
      for (final task in tasks)
        HumanTaskCard(
            key: ValueKey(task['id']), controller: controller, task: task),
      if (tasks.isNotEmpty)
        _done(7)
            ? _next('Prepare DIY kit')
            : pRight(_action(
                'project.acceptPhysical', 'Accept physical evidence',
                enabled: allAccepted)),
    ], gap: 24);
  }

  Widget _kit() {
    final home = p['casePrint'] == 'home';
    final tasks = _tasks('kitTaskIds');
    final allAccepted =
        tasks.length == 2 && tasks.every((t) => t['state'] == 'ACCEPTED');
    return pColumn([
      StageHeader(
          title: 'Prepare the DIY kit',
          description:
              'Tested subassemblies, labeled bags and a guide generated from the same accepted CAD as the parts.',
          status: p['kitAccepted'] == true
              ? const KBadge('Kit accepted', tone: 'pass')
              : null,
          action: tasks.isEmpty
              ? _action('project.prepareKit', 'Start kit preparation',
                  icon: Icons.inventory_2_outlined)
              : null),
      KResponsive(children: [
        StagePanel(
            title: 'Packing manifest',
            trailing: Text('${_bags.length} bags', style: KType.monoSmall),
            child: Column(children: [
              for (final (i, bag) in _bags.indexed) ...[
                if (i > 0) const Divider(height: 1, color: KColors.hairline),
                Padding(
                    padding: const EdgeInsets.symmetric(vertical: 10),
                    child: Row(children: [
                      LetterTile('${bag['bag']}'),
                      const SizedBox(width: 14),
                      Expanded(
                          child: pColumn([
                        pText(bag['contents'],
                            muted: home && ['A', 'F'].contains(bag['bag'])),
                        if (home && ['A', 'F'].contains(bag['bag']))
                          pText(
                              'Shell printed at home; feet and caps still packed',
                              muted: true,
                              small: true)
                      ], gap: 3)),
                      const SizedBox(width: 12),
                      pText('×${bag['count']}', mono: true),
                    ])),
              ]
            ])),
        StagePanel(
            title: 'Guide preview (from CAD assembly graph)',
            child: pColumn([
              SizedBox(
                  width: double.infinity,
                  height: 300,
                  child: KGridBackdrop(child: _device())),
              pText(
                  'Firmware on core module: fw-9c41e2, manifest ${shortHash(p['manifestHash'])}',
                  muted: true,
                  small: true,
                  mono: true)
            ], gap: 12)),
      ]),
      for (final task in tasks)
        HumanTaskCard(
            key: ValueKey(task['id']), controller: controller, task: task),
      if (tasks.isNotEmpty)
        p['kitAccepted'] == true
            ? _next('Deliver & activate')
            : pRight(_action('project.acceptKit', 'Accept final kit',
                enabled: allAccepted)),
    ], gap: 28);
  }

  Widget _deliver() {
    final shipment = asMap(p['shipment']);
    final shipmentStep = number(shipment['step']).toInt();
    final delivered =
        shipment.isNotEmpty && shipmentStep == _shipSteps.length - 1;
    final step = number(p['guideStep']).toInt().clamp(0, _guide.length - 1);
    final assembled = p['assemblyDone'] == true;
    final selfTests = asMaps(p['selfTest']);
    return pColumn([
      StageHeader(
          title: 'Deliver & activate',
          description:
              'Shipment, assembly and activation are tracked as separate records.',
          status: p['activation'] == 'done'
              ? const KBadge('Activated', tone: 'pass')
              : null,
          action: shipment.isEmpty
              ? _action('project.ship', 'Ship kit',
                  icon: Icons.local_shipping_outlined)
              : null),
      if (shipment.isNotEmpty)
        KCard(
            title: 'Shipment',
            trailing: pActions([
              pText(shipment['tracking'], mono: true, small: true),
              const KBadge('Simulated', tone: 'sim')
            ]),
            child: StepTrack(steps: _shipSteps, reached: shipmentStep)),
      if (delivered)
        KCard(
            title:
                'Assembly guide, step ${assembled ? _guide.length : step + 1} of ${_guide.length}',
            trailing:
                assembled ? const KBadge('Assembled', tone: 'pass') : null,
            child: assembled
                ? pActions([
                    const Icon(Icons.check_circle_outline,
                        size: 18, color: KColors.pass),
                    pText('All 6 steps completed.', color: KColors.pass)
                  ])
                : pColumn([
                    SegmentBar(tones: [
                      for (var i = 0; i < _guide.length; i++)
                        i < step ? 'pass' : (i == step ? 'accent' : '')
                    ]),
                    KResponsive(children: [
                      SizedBox(
                          height: 300,
                          child:
                              KGridBackdrop(child: _device(guideStep: step))),
                      pColumn([
                        Text('${_guide[step]['title']}',
                            style: Theme.of(context).textTheme.headlineSmall),
                        pText(_guide[step]['text']),
                        pActions([
                          _action('project.assemblyStep', 'Back',
                              enabled: step > 0,
                              primary: false,
                              icon: Icons.arrow_back,
                              extra: {'step': step - 1}),
                          _action(
                              'project.assemblyStep',
                              step == _guide.length - 1
                                  ? 'Finish assembly'
                                  : 'Done, next step',
                              icon: Icons.arrow_forward,
                              extra: {'step': step + 1}),
                        ]),
                        pText(
                            'Stuck? A human helper can be requested as a human task.',
                            muted: true,
                            small: true),
                      ]),
                    ]),
                  ], gap: 18)),
      if (assembled)
        KCard(
            title: 'Activation',
            trailing: p['activation'] == 'idle'
                ? _action('project.activate', 'Power on & run self-test',
                    icon: Icons.power_settings_new)
                : null,
            child: p['activation'] == 'idle'
                ? pText(
                    'Hold the top button for 3 seconds, then run the self-test.',
                    muted: true)
                : KResponsive(children: [
                    Column(children: [
                      for (final (i, test) in selfTests.indexed) ...[
                        if (i > 0)
                          const Divider(height: 1, color: KColors.hairline),
                        Padding(
                            padding: const EdgeInsets.symmetric(vertical: 11),
                            child: Row(children: [
                              Expanded(child: pText(test['name'])),
                              evidence(test['state'])
                            ])),
                      ]
                    ]),
                    if (p['activation'] == 'done')
                      SizedBox(
                          height: 260,
                          child: KGridBackdrop(child: _device(exploded: false)))
                    else
                      Center(child: pText('Testing…', muted: true)),
                  ])),
      if (p['activation'] == 'done')
        StagePanel(
            title: 'Device history',
            child: pColumn([
              KeyValueRows(rows: [
                (
                  'Manifest',
                  pText(shortHash(p['manifestHash'], 16),
                      mono: true, small: true)
                ),
                ('Firmware', pText('fw-9c41e2', mono: true, small: true)),
                ('Battery', pText('${p['batteryMah']} mAh', small: true)),
                (
                  'Shipment',
                  pText('${shipment['tracking']}', mono: true, small: true)
                ),
                ('Assembly', pText('Completed', small: true)),
                ('Activation', pText('Passed', small: true)),
              ]),
              pText(
                  'Spares and rebuilds route back to the same providers when you mark them as preferred.',
                  muted: true),
            ], gap: 14)),
    ], gap: 28);
  }
}

class _PrintBedPainter extends CustomPainter {
  const _PrintBedPainter(this.progress);
  final double progress;

  @override
  void paint(Canvas canvas, Size size) {
    final a = Offset(size.width * .10, size.height * .46);
    final b = Offset(size.width * .61, size.height * .16);
    final c = Offset(size.width * .93, size.height * .53);
    final d = Offset(size.width * .42, size.height * .85);
    final bed = Path()
      ..moveTo(a.dx, a.dy)
      ..lineTo(b.dx, b.dy)
      ..lineTo(c.dx, c.dy)
      ..lineTo(d.dx, d.dy)
      ..close();
    canvas.drawPath(bed, Paint()..color = KColors.bg);
    canvas.drawPath(
        bed,
        Paint()
          ..color = KColors.border
          ..style = PaintingStyle.stroke
          ..strokeWidth = 1.2);
    final grid = Paint()
      ..color = KColors.border.withValues(alpha: .65)
      ..strokeWidth = .7;
    for (var i = 1; i < 10; i++) {
      canvas.drawLine(
          Offset.lerp(a, b, i / 10)!, Offset.lerp(d, c, i / 10)!, grid);
      canvas.drawLine(
          Offset.lerp(a, d, i / 10)!, Offset.lerp(b, c, i / 10)!, grid);
    }
    final height = 5 + progress.clamp(0, 100) / 100 * 27;
    for (var layer = 0; layer < height; layer += 3) {
      final shell = RRect.fromRectAndRadius(
          Rect.fromCenter(
              center: Offset(size.width * .51, size.height * .52 - layer),
              width: size.width * .33,
              height: size.height * .26),
          const Radius.circular(15));
      canvas.drawRRect(shell, Paint()..color = const Color(0xff29292d));
      canvas.drawRRect(
          shell,
          Paint()
            ..color = KColors.faint.withValues(alpha: .38)
            ..style = PaintingStyle.stroke
            ..strokeWidth = .6);
    }
  }

  @override
  bool shouldRepaint(covariant _PrintBedPainter oldDelegate) =>
      oldDelegate.progress != progress;
}
