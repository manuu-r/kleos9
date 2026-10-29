import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../../../data/models/json.dart';
import '../../../state/kleos_store.dart';
import '../../core/device_visual.dart';
import '../../core/human_task_card.dart';
import '../../core/widgets.dart';
import 'common.dart';

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
  String? _sourceLane;
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
  Widget build(BuildContext context) => switch (widget.stage) {
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

  Widget _chat(String text, {bool user = false}) => Align(
        alignment: user ? Alignment.centerRight : Alignment.centerLeft,
        child: Container(
          constraints: const BoxConstraints(maxWidth: 690),
          padding: const EdgeInsets.symmetric(horizontal: 17, vertical: 13),
          decoration: BoxDecoration(
              color: user ? KColors.accent.withValues(alpha: .1) : KColors.bg,
              border: Border.all(
                  color: user
                      ? KColors.accent.withValues(alpha: .2)
                      : KColors.border),
              borderRadius: BorderRadius.circular(12)),
          child: pText(text),
        ),
      );

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
    final pending = controller.isPending('project.answer');
    return pColumn([
      const KSectionHeader(
          title: 'Capture intent',
          description:
              'A few focused questions so the design matches what you actually want.'),
      KCard(
          child: pColumn([
        pActions([
          const Icon(Icons.smart_toy_outlined, size: 15, color: KColors.muted),
          pText('Agent', muted: true, small: true)
        ]),
        _chat(
            'Got it: “${'${p['brief']}'.replaceFirst(RegExp(r'[.\s]+$'), '')}”. I’ll ask a few quick questions before researching feasibility.'),
        for (final q
            in _questions.where((q) => answers.containsKey(q['key']))) ...[
          _chat('${q['ask']}'),
          _chat(joinValues(answers[q['key']]), user: true),
        ],
        if (current != null) ...[
          _chat('${current['ask']}'),
          Wrap(spacing: 8, runSpacing: 8, children: [
            for (final value in asList(current['options']))
              current['multi'] == true
                  ? FilterChip(
                      label: Text('$value'),
                      selected: _picked.contains(value),
                      onSelected: pending
                          ? null
                          : (selected) => setState(() {
                                if (selected) {
                                  _picked.add('$value');
                                } else {
                                  _picked.remove(value);
                                }
                              }),
                    )
                  : ActionChip(
                      label: Text('$value'),
                      onPressed:
                          pending ? null : () => _answer(current, ['$value'])),
          ]),
          if (current['multi'] == true)
            KButton(
                label: 'Continue',
                primary: true,
                compact: true,
                onPressed: _picked.isEmpty || pending
                    ? null
                    : () => _answer(current, _picked.toList())),
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
              KTable(headers: const [
                'Requirement',
                'Value'
              ], rows: [
                for (final requirement in asMaps(p['requirements']))
                  [
                    pText(requirement['label'], muted: true),
                    pText(requirement['value'])
                  ]
              ]),
              pColumn([
                pText('ASSUMPTIONS', muted: true, small: true),
                for (final item in asList(p['assumptions'])) pText('• $item'),
                const SizedBox(height: 4),
                pText('UNKNOWNS, CARRIED FORWARD', muted: true, small: true),
                for (final item in asList(p['unknowns']))
                  pActions(
                      [const KBadge('Unknown', tone: 'unknown'), pText(item)]),
              ], gap: 8),
            ])),
    ], gap: 24);
  }

  Widget _feasibility() {
    final research = asMap(p['research']);
    const tracks = [
      'Parts & availability',
      'Materials & printing',
      'Electronics & power budget',
      'Firmware & app support'
    ];
    return pColumn([
      KSectionHeader(
          title: 'Research feasibility',
          description:
              'Parts, materials, manufacturing and software are researched together before anything is designed.',
          trailing: p['feasibility'] == 'idle'
              ? _action('project.research', 'Run feasibility research',
                  icon: Icons.search)
              : null),
      if (p['feasibility'] != 'idle')
        KCard(
            title: 'Research tracks',
            child: KResponsive(children: [
              for (final track in tracks)
                pColumn([
                  Row(children: [
                    Expanded(child: pText(track)),
                    pText('${research[track] ?? 0}%',
                        muted: true, mono: true, small: true)
                  ]),
                  KProgress(number(research[track]).toDouble()),
                ], gap: 7)
            ])),
      if (p['feasibility'] == 'done') ...[
        KResponsive(children: [
          KCard(title: 'Concept', child: _device()),
          KCard(
              title: 'Features',
              child: pColumn([
                for (final feature in asMaps(p['features']))
                  pColumn([
                    pActions([
                      pText(feature['name'], strong: true),
                      KBadge('${feature['status']}',
                          tone: feature['status'] == 'supported'
                              ? 'pass'
                              : 'unknown')
                    ]),
                    pText(feature['note'], muted: true, small: true),
                  ], gap: 4)
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
        KResponsive(columns: 3, children: [
          KCard(
              title: 'Estimates',
              child: pColumn([
                pText('Kit estimate: \$95-125', strong: true),
                pText('Lead time: 9-14 days'),
                pText('Assembly: ~40 min, no soldering'),
                pText('Quotes commit later; these are estimates.',
                    muted: true, small: true)
              ], gap: 7)),
          KCard(
              title: 'Limitations',
              child: pColumn([
                pText('• Not a safety alarm'),
                pText('• CO2 is indicative until calibrated'),
                pText('• Printed shells show fine layer lines')
              ], gap: 7)),
          KCard(
              title: 'Next',
              child: pColumn([
                pText('Review the concept and confirm before design starts.'),
                KButton(
                    label: 'Review & confirm',
                    primary: true,
                    icon: Icons.arrow_forward,
                    onPressed: widget.onNext)
              ])),
        ]),
      ],
    ], gap: 24);
  }

  Widget _confirm() {
    final review =
        allTasks.where((t) => t['id'] == p['reviewTaskId']).firstOrNull;
    return pColumn([
      const KSectionHeader(
          title: 'Review concept & confirm',
          description:
              'Nothing is designed or spent until you confirm. Reduce scope or ask a human engineer first if you like.'),
      KResponsive(children: [
        KCard(
            title: 'Your monitor',
            child: pColumn([
              SizedBox(
                  height: 220,
                  child: DeviceVisual(exploded: false, finish: finishOf(p))),
              for (final requirement in asMaps(p['requirements'])
                  .where((r) => r['label'] != 'Features'))
                pText('${requirement['label']}: ${requirement['value']}',
                    small: true),
            ], gap: 5)),
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
      KCard(
          child: pColumn([
        p['confirmed'] == true
            ? pActions([
                const Icon(Icons.check_circle_outline,
                    color: KColors.pass, size: 18),
                pText('Confirmed. Design can start.', color: KColors.pass)
              ])
            : pText(
                'Estimated kit \$95-125  ·  9-14 days  ·  About 40 min to assemble',
                muted: true),
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
      ])),
    ], gap: 24);
  }

  Widget _interfaces() {
    final saved = asMaps(p['interfaces']);
    final rows = saved.isNotEmpty ? saved : asMaps(p['interfacePreview']);
    final running = rows.any((r) => r['state'] == 'RUNNING');
    const criteria = [
      'Every interface value has units and an owner',
      'Battery and core fit with ≥ 0.3 mm clearance',
      'Estimated battery life ≥ requirement',
      'All parts reachable for assembly without soldering',
      'Firmware pin map matches schematic net names'
    ];
    return pColumn([
      KSectionHeader(
          title: 'Establish interfaces',
          description:
              'Mechanics, electronics and firmware agree on shared contracts first, so they are designed to fit together.',
          trailing: p['interfacesAgreed'] != true
              ? _action(
                  'project.solveInterfaces', 'Run solver & agree interfaces',
                  enabled: !running, icon: Icons.functions)
              : null),
      KCard(
          title: 'Interface specification v1',
          child: KTable(headers: const [
            'Interface',
            'Contract',
            'Owner',
            'Solver'
          ], rows: [
            for (final item in rows)
              [
                pText(item['name'], strong: true),
                pText(item['value'], mono: true, small: true),
                KBadge('${item['owner']}'),
                evidence(item['state'])
              ]
          ])),
      KCard(
          title:
              'Acceptance criteria (owned by Quality, generators cannot change)',
          child:
              pColumn([for (final item in criteria) pText('• $item')], gap: 9)),
      if (p['interfacesAgreed'] == true) _next('Generate designs'),
    ], gap: 24);
  }

  Widget _generate() {
    final lanes = asList(p['generationLanes']).map((v) => '$v').toList();
    final progress = asMap(p['genProgress']);
    final tools = asMap(p['tools']);
    final selectedLane =
        lanes.contains(_sourceLane) ? _sourceLane : lanes.firstOrNull;
    final log = asList(p['genLog']);
    return pColumn([
      KSectionHeader(
          title: 'Generate in parallel',
          description:
              'Each domain generates code-defined files against the shared interface spec. Generators submit candidates; they cannot approve them.',
          trailing: p['generation'] == 'idle'
              ? _action('project.generate', 'Start generation')
              : null),
      KResponsive(columns: lanes.length.clamp(1, 4), children: [
        for (final lane in lanes)
          KCard(
              child: pColumn([
            Row(children: [
              Expanded(child: pText(lane, strong: true)),
              pText('${progress[lane] ?? 0}%',
                  muted: true, small: true, mono: true)
            ]),
            if (lane == 'App')
              pText('Kleos9 App (built in)', muted: true, small: true)
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
                return Semantics(
                    label: '$lane tool',
                    child: DropdownButton<String>(
                      value: value,
                      isExpanded: true,
                      hint: pText('Choose a tool', muted: true, small: true),
                      items: [
                        for (final candidate in candidates)
                          DropdownMenuItem(
                              value: '${candidate['id']}',
                              child: Text(
                                  '${candidate['name']}${candidate['builtIn'] == true ? ' (default)' : ''}',
                                  overflow: TextOverflow.ellipsis,
                                  style: const TextStyle(fontSize: 12)))
                      ],
                      onChanged: p['generation'] != 'idle' ||
                              controller.isPending('project.selectTool')
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
                    ));
              }),
            KProgress(number(progress[lane]).toDouble()),
          ]))
      ]),
      if (p['generation'] == 'idle')
        pActions([
          pText('Default Kleos9 tools run unless you pick another for a lane.',
              muted: true),
          KButton(
              label: 'Plug in more tools',
              compact: true,
              onPressed: () => context.go('/connections?tab=tools'))
        ]),
      if (log.isNotEmpty)
        KCard(
            title: 'Generation log',
            child: Container(
                width: double.infinity,
                constraints: const BoxConstraints(maxHeight: 310),
                child: SingleChildScrollView(
                    child: pColumn([
                  for (final line in log) pText(line, mono: true, small: true)
                ], gap: 5)))),
      if (p['generation'] == 'done') ...[
        KCard(
            title: 'Generated source (code-defined)',
            child: pColumn([
              Wrap(spacing: 8, runSpacing: 8, children: [
                for (final lane in lanes)
                  ChoiceChip(
                      label: Text(lane),
                      selected: selectedLane == lane,
                      onSelected: (_) => setState(() => _sourceLane = lane))
              ]),
              Container(
                  width: double.infinity,
                  padding: const EdgeInsets.all(16),
                  decoration: BoxDecoration(
                      color: KColors.bg,
                      borderRadius: BorderRadius.circular(8)),
                  child: SingleChildScrollView(
                      scrollDirection: Axis.horizontal,
                      child: SelectableText(
                          '${asMap(p['generatedSource'])[selectedLane] ?? ''}',
                          style: const TextStyle(
                              fontFamily: 'GeistMono',
                              fontSize: 12,
                              height: 1.65,
                              color: KColors.text)))),
            ])),
        KCard(
            title: 'Candidate artifacts',
            trailing: KButton(
                label: 'Verify',
                compact: true,
                primary: true,
                icon: Icons.arrow_forward,
                onPressed: widget.onNext),
            child: KTable(headers: const [
              'Artifact',
              'Files',
              'Depends on',
              'Version',
              'Status'
            ], rows: [
              for (final artifact in asMaps(p['artifacts']))
                [
                  pColumn([
                    pText(artifact['name'], strong: true),
                    pText(artifact['domain'], muted: true, small: true)
                  ], gap: 2),
                  pText(artifact['file'], mono: true, small: true),
                  pText(joinValues(artifact['deps']), muted: true, small: true),
                  pText('v${artifact['version']} ${artifact['hash']}',
                      mono: true, small: true),
                  evidence(artifact['status']),
                ]
            ])),
      ],
    ], gap: 24);
  }

  Widget _verify() {
    final checks = asMaps(p['checks']);
    final artifacts = asMaps(p['artifacts']);
    final busy = p['verifyRun'] == 'running' ||
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
    return pColumn([
      KSectionHeader(
          title: 'Verify and revise',
          description:
              'Validators inspect the candidate files. Anything they cannot prove stays unknown.',
          trailing: p['verifyRun'] == 'idle'
              ? _action('project.verify', 'Run verification',
                  icon: Icons.verified_user_outlined)
              : null),
      if (p['verifyRun'] == 'running')
        pColumn([
          pText('$finished of ${checks.length} checks done', muted: true),
          KProgress(checks.isEmpty ? 0 : finished / checks.length * 100)
        ], gap: 8),
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
        if (attention.isNotEmpty) pCheckList(attention),
        pDisclosure(
            title: 'All checks',
            count: checks.length,
            child: pColumn([
              for (final domain in checks.map((c) => '${c['domain']}').toSet())
                pColumn([
                  pText(domain.toUpperCase(), muted: true, small: true),
                  pCheckList(
                      checks.where((c) => c['domain'] == domain).toList())
                ], gap: 4),
            ], gap: 18)),
      ],
      if (settled && !accepted)
        pColumn([
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
        ]),
      if (accepted)
        pColumn([
          pText(
              'Release ${shortHash(p['manifestHash'])} covers ${artifacts.length} artifacts. ${unknowns.length} unknown result(s) go to physical testing.',
              muted: true),
          _next('Procure prototype')
        ]),
    ], gap: 24);
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
      KSectionHeader(
          title: 'Procure prototype',
          description:
              'Each item goes to the connector that fits best. Nothing is sent until you authorize.',
          trailing: p['procurement'] == 'idle'
              ? _action('project.requestQuotes', 'Request quotes',
                  icon: Icons.receipt_long_outlined)
              : null),
      if (p['procurement'] == 'quoting')
        KCard(
            child: pColumn([
          pText('Waiting for quotes…'),
          const LinearProgressIndicator(minHeight: 3)
        ])),
      if (quotes.isNotEmpty) ...[
        KTable(headers: const [
          'Item',
          'Routed to',
          'Price',
          'Lead time'
        ], rows: [
          for (final item in items)
            () {
              final candidates =
                  quotes.where((q) => q['item'] == item).toList();
              final quote =
                  candidates.where((q) => q['selected'] == true).firstOrNull ??
                      candidates.first;
              final home =
                  p['casePrint'] == 'home' && quote['id'] == caseQuote?['id'];
              return <Widget>[
                pText(item),
                pText(home ? 'Your printer' : _name(quote['connectorId']),
                    muted: true),
                pText(home ? '\$2.80' : '\$${quote['price']}', mono: true),
                pText(home ? 'Today' : '${quote['leadDays']} days', mono: true)
              ];
            }()
        ]),
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
          pText('Printed shells', strong: true),
          pText('Print them yourself, or order them.', muted: true),
          if (printer != null)
            KResponsive(children: [
              for (final mode in ['home', 'order'])
                InkWell(
                  onTap: p['procurement'] != 'quoted' ||
                          controller.isPending('project.selectPrint')
                      ? null
                      : () {
                          controller.execute('project.selectPrint',
                              {'projectId': p['id'], 'mode': mode});
                        },
                  borderRadius: BorderRadius.circular(12),
                  child: Container(
                      padding: const EdgeInsets.all(20),
                      decoration: BoxDecoration(
                          color: KColors.surface,
                          border: Border.all(
                              color: p['casePrint'] == mode
                                  ? KColors.accent
                                  : KColors.border,
                              width: p['casePrint'] == mode ? 1.5 : 1),
                          borderRadius: BorderRadius.circular(12)),
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
          pActions([
            pText('Mandate \$${mandate['limit']}', mono: true),
            pText('This order \$$total', mono: true),
            pText('Left after \$$remaining', mono: true),
            if (p['procurement'] == 'quoted')
              _action('project.placeOrders', 'Authorize and place orders',
                  enabled: total <= number(mandate['limit']),
                  icon: Icons.shopping_cart_outlined)
          ]),
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
        ]),
      if (p['procurement'] == 'done') _next('Physical tests'),
    ], gap: 24);
  }

  Widget _physical() {
    final tasks = _tasks('physicalTaskIds');
    final rah = connectors.where((c) => c['id'] == 'rentahuman').firstOrNull;
    final linked = rah?['linked'] == true && rah?['enabled'] == true;
    final allAccepted =
        tasks.isNotEmpty && tasks.every((t) => t['state'] == 'ACCEPTED');
    return pColumn([
      KSectionHeader(
          title: 'Test physically',
          description:
              'What the AI could not verify goes to people, with a fixed evidence format.',
          trailing: tasks.isEmpty
              ? _action('project.createPhysicalTasks',
                  'Create human tasks from UNKNOWN results',
                  icon: Icons.back_hand_outlined)
              : null),
      pActions([
        pText(
            linked
                ? 'Jobs are posted to RentAHuman as bounties.'
                : 'No human-task platform is linked. Tasks wait until you connect one or use the Kleos9 portal.',
            muted: true),
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
      ]),
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
      KSectionHeader(
          title: 'Prepare the DIY kit',
          description:
              'Tested subassemblies, labeled bags and a guide generated from the same accepted CAD as the parts.',
          trailing: tasks.isEmpty
              ? _action('project.prepareKit', 'Start kit preparation',
                  icon: Icons.inventory_2_outlined)
              : null),
      KResponsive(children: [
        KCard(
            title: 'Packing manifest',
            child: KTable(headers: const [
              'Bag',
              'Contents',
              'Count'
            ], rows: [
              for (final bag in _bags)
                [
                  KBadge('Bag ${bag['bag']}'),
                  pColumn([
                    pText(bag['contents'],
                        muted: home && ['A', 'F'].contains(bag['bag'])),
                    if (home && ['A', 'F'].contains(bag['bag']))
                      pText('Shell printed at home; feet and caps still packed',
                          muted: true, small: true)
                  ], gap: 3),
                  pText('×${bag['count']}', mono: true)
                ]
            ])),
        KCard(
            title: 'Guide preview (from CAD assembly graph)',
            child: pColumn([
              _device(),
              pText(
                  'Firmware on core module: fw-9c41e2, manifest ${shortHash(p['manifestHash'])}',
                  muted: true,
                  small: true,
                  mono: true)
            ])),
      ]),
      for (final task in tasks)
        HumanTaskCard(
            key: ValueKey(task['id']), controller: controller, task: task),
      if (tasks.isNotEmpty)
        p['kitAccepted'] == true
            ? _next('Deliver & activate')
            : pRight(_action('project.acceptKit', 'Accept final kit',
                enabled: allAccepted)),
    ], gap: 24);
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
      KSectionHeader(
          title: 'Deliver & activate',
          description:
              'Shipment, assembly and activation are tracked as separate records.',
          trailing: shipment.isEmpty
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
            child: Wrap(spacing: 22, runSpacing: 15, children: [
              for (var i = 0; i < _shipSteps.length; i++)
                pActions([
                  Icon(
                      i <= shipmentStep
                          ? Icons.check_circle
                          : Icons.circle_outlined,
                      color: i <= shipmentStep ? KColors.pass : KColors.faint,
                      size: 17),
                  pText(_shipSteps[i], muted: i > shipmentStep, small: true)
                ]),
            ])),
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
                : KResponsive(children: [
                    _device(guideStep: step),
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
                  ])),
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
                    pColumn([
                      for (final test in selfTests)
                        Row(children: [
                          Expanded(child: pText(test['name'])),
                          evidence(test['state'])
                        ])
                    ], gap: 12),
                    if (p['activation'] == 'done')
                      _device(exploded: false)
                    else
                      Center(child: pText('Testing…', muted: true)),
                  ])),
      if (p['activation'] == 'done')
        KCard(
            title: 'Device history',
            child: pColumn([
              pText(
                  'Manifest ${shortHash(p['manifestHash'], 16)}  ·  Firmware fw-9c41e2  ·  Battery ${p['batteryMah']} mAh',
                  mono: true,
                  small: true),
              pText(
                  'Shipment ${shipment['tracking']}  ·  Assembly completed  ·  Activation passed',
                  small: true),
              pText(
                  'Spares and rebuilds route back to the same providers when you mark them as preferred.',
                  muted: true),
            ])),
    ], gap: 24);
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
