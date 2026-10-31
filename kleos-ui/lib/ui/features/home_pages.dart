import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../data/services/command_journal.dart';
import '../../state/production_store.dart';
import 'package:go_router/go_router.dart';
import '../../data/models/json.dart';
import '../../state/kleos_store.dart';
import '../core/device_visual.dart';
import '../core/widgets.dart';
import '../core/build_actions.dart';
import 'project/engineering_views.dart' show TerminalPanel;

const stageNames = [
  'Capture intent',
  'Research feasibility',
  'Review & confirm',
  'Establish interfaces',
  'Generate in parallel',
  'Verify & revise',
  'Procure prototype',
  'Test physically',
  'Prepare DIY kit',
  'Deliver & activate'
];
const sampleBriefs = [
  'A desk air-quality monitor that shows CO2 on a small screen and warns me when a room gets stuffy.',
  'A pocket CO2 meter with two weeks of battery and no screen glare, that my kid can assemble.',
  'A humidity and temperature logger for a grow tent, with two buttons and a bright display.',
];

class ProjectsPage extends StatelessWidget {
  const ProjectsPage({super.key, required this.controller});
  final KleosController controller;
  @override
  Widget build(BuildContext context) {
    final projects = asMaps(controller.state['projects']);
    final connectors = asMaps(controller.state['connectors']);
    final tools = connectors.where((c) => c['kind'] == 'tool').toList();
    final wide = MediaQuery.sizeOf(context).width >= 900;
    return KPage(gap: 36, children: [
      _Hero(showStage: wide),
      if (controller.state['production'] == true) const _PendingRequests(),
      if (projects.isNotEmpty)
        _Builds(projects: projects, controller: controller),
      // On a phone the device visual follows the builds instead of pushing
      // them below the fold.
      if (!wide && projects.isNotEmpty) const _Stage(height: 240),
      if (projects.isEmpty) const _Pipeline(),
      _PlugIns(tools: tools, connectors: connectors),
    ]);
  }
}

/// The exploded device on its drafting grid. Flat, no shadow.
class _Stage extends StatelessWidget {
  const _Stage({required this.height});
  final double height;
  @override
  Widget build(BuildContext context) => SizedBox(
      width: double.infinity,
      height: height,
      child: const KGridBackdrop(child: DeviceVisual()));
}

/// The screen's title row: a noun-sized title, one sentence, the two actions.
/// On wide screens the device visual sits beside it.
class _Hero extends StatelessWidget {
  const _Hero({required this.showStage});
  final bool showStage;
  @override
  Widget build(BuildContext context) => LayoutBuilder(builder: (context, c) {
        final phone = c.maxWidth < 600;
        final copy = kColumn([
          Text('Build hardware from a sentence.',
              style: KType.h1.copyWith(
                  fontSize: phone ? 24 : 30, letterSpacing: phone ? -.5 : -.9)),
          ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 480),
              child: const KMuted(
                  'Describe a device. Agents design the enclosure, board and firmware, then route every step to a connector or a person.',
                  size: 14.5)),
          kRow([
            KButton(
                label: 'New build',
                icon: Icons.add,
                primary: true,
                onPressed: () => context.go('/new')),
            KButton(
                label: 'Browse connectors',
                onPressed: () => context.go('/connections'))
          ], gap: 10),
        ], gap: 14);
        if (!showStage) return copy;
        return Row(children: [
          Expanded(flex: 5, child: copy),
          const SizedBox(width: 48),
          const Expanded(flex: 6, child: _Stage(height: 340)),
        ]);
      });
}

/// Ten slim segments, one per stage: finished stages are quiet, the current
/// stage carries the one accent mark, and what is left stays dark.
class StageTrack extends StatelessWidget {
  const StageTrack({super.key, required this.stage, this.finished = false});
  final int stage;
  final bool finished;
  @override
  Widget build(BuildContext context) => Row(children: [
        for (var i = 0; i < 10; i++) ...[
          if (i > 0) const SizedBox(width: 3),
          Expanded(
              child: AnimatedContainer(
                  duration: KMotion.slow,
                  curve: KMotion.out,
                  height: 4,
                  decoration: BoxDecoration(
                      borderRadius: BorderRadius.circular(KRadius.control),
                      color: finished || i < stage
                          ? KColors.text.withValues(alpha: .42)
                          : i == stage
                              ? KColors.accent
                              : KColors.surface3)))
        ]
      ]);
}

class _Builds extends StatelessWidget {
  const _Builds({required this.projects, required this.controller});
  final List<JsonMap> projects;
  final KleosController controller;
  @override
  Widget build(BuildContext context) => kColumn([
        Row(
            crossAxisAlignment: CrossAxisAlignment.baseline,
            textBaseline: TextBaseline.alphabetic,
            children: [
              Flexible(
                  child: Text('Your builds',
                      style: Theme.of(context).textTheme.headlineSmall)),
              const SizedBox(width: 10),
              Text('${projects.length}', style: KType.monoSmall),
            ]),
        LayoutBuilder(builder: (context, c) {
          String stageLabel(JsonMap p, int stage, int done) => done == 10
              ? p['production'] == true
                  ? 'Simulated delivery and activation'
                  : 'Delivered and activated'
              : stageNames[stage];
          int stageOf(JsonMap p) =>
              (p['stage'] as num?)?.toInt().clamp(0, 9) ?? 0;
          int doneOf(JsonMap p) =>
              asList(p['done']).where((x) => x == true).length;
          Widget status(JsonMap p) => p['production'] == true
              ? KBadge('${p['status'] ?? 'draft'}',
                  tone: p['status'] == 'blocked' ? 'unknown' : null)
              : const SizedBox.shrink();
          if (c.maxWidth >= 720) {
            // One list, one row per build: name and brief, then where it is.
            return KPanel(
                padding: EdgeInsets.zero,
                child: Column(children: [
                  for (var i = 0; i < projects.length; i++) ...[
                    if (i > 0)
                      const Divider(height: 1, color: KColors.hairline),
                    KTap(
                        flat: true,
                        radius: 0,
                        onTap: () =>
                            context.go('/projects/${projects[i]['id']}'),
                        builder: (context, hovered) => AnimatedContainer(
                            duration: KMotion.base,
                            color:
                                hovered ? KColors.surface2 : Colors.transparent,
                            padding: const EdgeInsets.fromLTRB(20, 16, 8, 16),
                            child: Row(children: [
                              Expanded(
                                  flex: 5,
                                  child: kColumn([
                                    Text('${projects[i]['name']}',
                                        maxLines: 1,
                                        overflow: TextOverflow.ellipsis,
                                        style: Theme.of(context)
                                            .textTheme
                                            .titleMedium),
                                    Text('${projects[i]['brief']}',
                                        maxLines: 1,
                                        overflow: TextOverflow.ellipsis,
                                        style: const TextStyle(
                                            color: KColors.muted,
                                            fontSize: 13.5)),
                                  ], gap: 4)),
                              const SizedBox(width: 24),
                              Expanded(
                                  flex: 4,
                                  child: kColumn([
                                    StageTrack(
                                        stage: stageOf(projects[i]),
                                        finished: doneOf(projects[i]) == 10),
                                    Text(
                                        stageLabel(
                                            projects[i],
                                            stageOf(projects[i]),
                                            doneOf(projects[i])),
                                        style: KType.monoSmall),
                                  ], gap: 8)),
                              const SizedBox(width: 12),
                              status(projects[i]),
                              BuildActionsMenu(
                                  project: projects[i], controller: controller),
                              const Icon(Icons.chevron_right,
                                  size: 18, color: KColors.faint),
                              const SizedBox(width: 8),
                            ]))),
                  ]
                ]));
          }
          return kColumn([
            for (final p in projects)
              KCard(
                  onTap: () => context.go('/projects/${p['id']}'),
                  child: kColumn([
                    Row(children: [
                      Expanded(
                          child: Text('${p['name']}',
                              maxLines: 2,
                              overflow: TextOverflow.ellipsis,
                              style: Theme.of(context).textTheme.titleMedium)),
                      BuildActionsMenu(project: p, controller: controller),
                      const Icon(Icons.chevron_right,
                          size: 18, color: KColors.faint)
                    ]),
                    Text('${p['brief']}',
                        maxLines: 2,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(
                            color: KColors.muted, fontSize: 13.5, height: 1.5)),
                    StageTrack(stage: stageOf(p), finished: doneOf(p) == 10),
                    Row(children: [
                      Expanded(
                          child: Text(stageLabel(p, stageOf(p), doneOf(p)),
                              style: KType.monoSmall)),
                      status(p),
                    ])
                  ], gap: 14)),
          ]);
        }),
      ], gap: 16);
}

/// A sample build, shown as a pipeline of finished stages beside the log the
/// agents would have written. It makes the product legible before a first run
/// and only appears while there are no builds.
class _Pipeline extends StatelessWidget {
  const _Pipeline();
  static const _log = [
    'Intent       7 answers recorded, 2 unknowns carried forward',
    'PartsDirect  Core module, display and battery in stock',
    'You          Confirmed concept, generation unlocked',
    'Solver       All 9 interface constraints satisfiable',
    'Mechanical   shells.py exported, 2 parts, PETG profile',
    'Validators   USB-C notch clearance FAIL, repair task created',
  ];
  @override
  Widget build(BuildContext context) => kColumn([
        Text('How a build runs',
            style: Theme.of(context).textTheme.headlineSmall),
        const KMuted(
            'Fabrication, parts, machines and people plug in the same way, so the agent can route each step to whichever qualified one fits.',
            size: 14),
        const _Stage(height: 240),
        KCard(
            title: 'Air Quality Monitor',
            trailing: const KBadge('simulated'),
            child: KResponsive(children: [
              kColumn([
                for (var i = 0; i < 6; i++)
                  Row(children: [
                    Container(
                        width: 20,
                        height: 20,
                        decoration: BoxDecoration(
                            shape: BoxShape.circle,
                            color: i < 4
                                ? KColors.pass.withValues(alpha: .14)
                                : Colors.transparent,
                            border: Border.all(
                                color: i < 4
                                    ? KColors.pass.withValues(alpha: .5)
                                    : KColors.borderStrong)),
                        child: i < 4
                            ? const Icon(Icons.check,
                                size: 12, color: KColors.pass)
                            : null),
                    const SizedBox(width: 12),
                    Expanded(
                        child: Text(stageNames[i],
                            style: TextStyle(
                                fontSize: 14,
                                color: i < 4 ? KColors.text : KColors.faint)))
                  ])
              ], gap: 14),
              const TerminalPanel(lines: _log, maxHeight: 220),
            ])),
      ], gap: 16);
}

class _PlugIns extends StatelessWidget {
  const _PlugIns({required this.tools, required this.connectors});
  final List<JsonMap> tools, connectors;
  @override
  Widget build(BuildContext context) {
    final toolsTile = _HomeLink(
        title: 'Engineering tools, built in or your own',
        description:
            'Every lane has a default that works out of the box. Plug in another and choose which one runs.',
        route: '/connections?tab=tools',
        child: Wrap(spacing: 24, runSpacing: 18, children: [
          for (final lane in [
            'Mechanical',
            'Electronics',
            'Firmware',
            'Verification'
          ])
            SizedBox(
                width: 170,
                child: kColumn([
                  KMuted(lane),
                  for (final tool in tools
                      .where((t) => t['domain'] == lane && t['linked'] == true))
                    KBadge('${tool['name']}',
                        tone: tool['builtIn'] == true ? null : 'accent'),
                  const KMuted('Your tool')
                ], gap: 8))
        ]));
    final servicesTile = _HomeLink(
        title: 'Services and machines',
        description:
            'Print farms, assemblers, parts, your own printer: one contract.',
        route: '/connections',
        child: kColumn([
          for (final item in [
            ('Print farms', 'Fabrication'),
            ('PCB assembly', 'PCB'),
            ('Components', 'Components'),
            ('Kit prep and shipping', 'Logistics'),
            ('Human tasks', 'Human tasks'),
            ('Your own printer', 'Printers')
          ])
            Row(children: [
              Expanded(
                  child: Text(item.$1, style: const TextStyle(fontSize: 13))),
              Text(
                  '${connectors.where((c) => c['category'] == item.$2 && c['linked'] == true).length} of ${connectors.where((c) => c['category'] == item.$2).length}',
                  style: KType.monoSmall)
            ])
        ], gap: 11));
    final peopleTile = _HomeLink(
        title: "People for what AI can't do",
        description:
            'Fit checks and assembly trials post to RentAHuman or the hosted portal.',
        route: '/human-tasks',
        child: kColumn([
          kRow([
            const Text('Fit check, printed case'),
            const KBadge('In review', tone: 'accent')
          ]),
          const KProgress(70),
          const KMuted('Evidence received, validator running')
        ]));
    const devTile = _HomeLink(
        title: 'Build a connector in minutes',
        description:
            'One manifest for a service, a tool or a printer driver. Test locally, qualify, and start receiving routed work.',
        route: '/developer',
        child: KCode(
            '{\n  "kind": "engineering_tool",\n  "display_name": "Vent Forge",\n  "tool": { "lane": "Mechanical",\n    "operations": { "generate": "supported", "approve": "unsupported" }\n  }\n}'));
    return kColumn([
      Text('What plugs in', style: Theme.of(context).textTheme.headlineSmall),
      LayoutBuilder(builder: (context, c) {
        if (c.maxWidth < 860) {
          return kColumn([toolsTile, servicesTile, peopleTile, devTile],
              gap: 16);
        }
        Widget row(Widget a, int fa, Widget b, int fb) => IntrinsicHeight(
                child: Row(
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                  Expanded(flex: fa, child: a),
                  const SizedBox(width: 16),
                  Expanded(flex: fb, child: b),
                ]));
        return kColumn([
          row(toolsTile, 3, servicesTile, 2),
          row(peopleTile, 2, devTile, 3),
        ], gap: 16);
      }),
    ], gap: 24);
  }
}

class _HomeLink extends StatelessWidget {
  const _HomeLink(
      {required this.title,
      required this.description,
      required this.route,
      required this.child});
  final String title, description, route;
  final Widget child;
  @override
  Widget build(BuildContext context) => InkWell(
      onTap: () => context.go(route),
      borderRadius: BorderRadius.circular(KRadius.control),
      child: KCard(
          child: kColumn([
        Row(children: [
          Expanded(
              child:
                  Text(title, style: Theme.of(context).textTheme.titleMedium)),
          const Icon(Icons.north_east, size: 15, color: KColors.faint)
        ]),
        KMuted(description, size: 12.5),
        child
      ], gap: 18)));
}

class NewBuildPage extends StatefulWidget {
  const NewBuildPage({super.key, required this.controller});
  final KleosController controller;
  @override
  State<NewBuildPage> createState() => _NewBuildPageState();
}

class _NewBuildPageState extends State<NewBuildPage> {
  final brief = TextEditingController();
  bool _simulate = false;
  @override
  void dispose() {
    brief.dispose();
    super.dispose();
  }

  Future<void> start() async {
    if (brief.text.trim().isEmpty ||
        widget.controller.isPending('project.create')) {
      return;
    }
    FocusManager.instance.primaryFocus?.unfocus();
    final result = await widget.controller.execute('project.create', {
      'brief': brief.text.trim(),
      if (_simulate) 'simulation': true,
      if (widget.controller.state['production'] == true && !_simulate)
        'autoStart': true,
    });
    if (mounted && result.ok) {
      context.go('/projects/${result.result['projectId']}');
    }
  }

  @override
  Widget build(BuildContext context) => LayoutBuilder(builder: (context, c) {
        final wide = c.maxWidth >= 900;
        final composer = kColumn([
          KSectionHeader(
              title: 'Describe the device you want to build.',
              description: _simulate
                  ? 'Walk through the complete build flow with simulated results. No AI credits or real orders are used.'
                  : widget.controller.state['production'] == true
                      ? 'Plain language is fine. The agent asks a few questions, then designs the case, electronics and firmware together. Physical fulfillment remains simulated.'
                      : 'Plain language is fine. The agent asks a few questions, then designs the case, electronics and firmware together and ships you a kit.'),
          KPanel(
              padding: const EdgeInsets.all(18),
              child: kColumn([
                if (widget.controller.state['production'] == true &&
                    widget.controller.state['simulationAvailable'] == true)
                  SwitchListTile.adaptive(
                      contentPadding: EdgeInsets.zero,
                      title: const Text('Simulated demo'),
                      subtitle: const Text(
                          'Use sample results to explore all stages.'),
                      value: _simulate,
                      onChanged: widget.controller.isPending('project.create')
                          ? null
                          : (value) => setState(() => _simulate = value)),
                CallbackShortcuts(
                    bindings: {
                      const SingleActivator(LogicalKeyboardKey.enter,
                          meta: true): start,
                      const SingleActivator(LogicalKeyboardKey.enter,
                          control: true): start
                    },
                    child: TextField(
                        controller: brief,
                        autofocus: true,
                        minLines: 6,
                        maxLines: 10,
                        style: const TextStyle(fontSize: 15, height: 1.55),
                        onChanged: (_) => setState(() {}),
                        decoration: const InputDecoration(
                            labelText: 'Your request',
                            contentPadding: EdgeInsets.all(16),
                            hintText:
                                'A desk air-quality monitor with a small screen…'))),
                LayoutBuilder(builder: (context, constraints) {
                  final help = KMuted(widget.controller.state['production'] ==
                              true &&
                          !_simulate
                      ? 'AI models are configured in Providers.'
                      : 'Scripted around an air-quality monitor in this prototype.');
                  final submit = KButton(
                      label: _simulate ? 'Start demo build' : 'Start build',
                      primary: true,
                      icon: Icons.arrow_forward,
                      onPressed: brief.text.trim().isEmpty ||
                              widget.controller.isPending('project.create')
                          ? null
                          : start);
                  if (constraints.maxWidth < 400) {
                    return kColumn([help, submit]);
                  }
                  return Row(children: [
                    Expanded(child: help),
                    const SizedBox(width: 12),
                    submit,
                  ]);
                }),
              ], gap: 14)),
          kColumn([
            const KMuted('Or start from a sample'),
            for (final sample in sampleBriefs)
              KTap(
                  radius: KRadius.control,
                  onTap: () => setState(() => brief.text = sample),
                  builder: (context, hovered) => AnimatedContainer(
                      duration: KMotion.base,
                      curve: KMotion.out,
                      width: double.infinity,
                      padding: const EdgeInsets.symmetric(
                          horizontal: 18, vertical: 16),
                      decoration: KShape.surface(
                          hovered ? KColors.surface2 : KColors.surface,
                          radius: KRadius.control),
                      child: Row(children: [
                        Expanded(
                            child: Text(sample,
                                style: const TextStyle(
                                    fontSize: 12.5,
                                    height: 1.5,
                                    color: KColors.muted))),
                        const SizedBox(width: 12),
                        Icon(Icons.north_west,
                            size: 15,
                            color: hovered ? KColors.text : KColors.faint),
                      ]))),
          ], gap: 10),
        ], gap: 28);
        final next = kColumn([
          _Stage(height: wide ? 300 : 240),
          Text('What happens next',
              style: Theme.of(context).textTheme.titleMedium),
          _Timeline(steps: stageNames),
        ], gap: 22);
        return wide
            ? Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Expanded(flex: 6, child: composer),
                const SizedBox(width: 56),
                Expanded(flex: 4, child: next),
              ])
            : kColumn([composer, next], gap: 40);
      });
}

/// Vertical sequence with a single hairline running through numbered nodes.
class _Timeline extends StatelessWidget {
  const _Timeline({required this.steps});
  final List<String> steps;
  @override
  Widget build(BuildContext context) => Column(children: [
        for (var i = 0; i < steps.length; i++)
          IntrinsicHeight(
              child: Row(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                SizedBox(
                    width: 28,
                    child: Column(children: [
                      Container(
                          width: 22,
                          height: 22,
                          alignment: Alignment.center,
                          decoration: BoxDecoration(
                              shape: BoxShape.circle,
                              color: KColors.surface2,
                              border: Border.all(color: KColors.borderStrong)),
                          child: Text('${i + 1}',
                              style: KType.monoSmall.copyWith(fontSize: 12))),
                      if (i < steps.length - 1)
                        Expanded(
                            child: Container(width: 1, color: KColors.border)),
                    ])),
                const SizedBox(width: 12),
                Expanded(
                    child: Padding(
                        padding: const EdgeInsets.only(top: 2, bottom: 14),
                        child: Text(steps[i],
                            style:
                                const TextStyle(fontSize: 13, height: 1.5)))),
              ])),
      ]);
}

class _PendingRequests extends ConsumerStatefulWidget {
  const _PendingRequests();
  @override
  ConsumerState<_PendingRequests> createState() => _PendingRequestsState();
}

class _PendingRequestsState extends ConsumerState<_PendingRequests> {
  late Future<List<PendingCommand>> pending =
      ref.read(productionRepositoryProvider).journal.pending();
  String? busy;
  String? error;
  Future<void> retry(PendingCommand command) async {
    setState(() {
      busy = command.key;
      error = null;
    });
    try {
      final result =
          await ref.read(productionRepositoryProvider).retry(command);
      await ref.read(kleosProvider).refresh();
      if (mounted) {
        setState(() =>
            pending = ref.read(productionRepositoryProvider).journal.pending());
        context.go('/projects/${result.projectId}');
      }
    } catch (e) {
      if (mounted) setState(() => error = e.toString());
    } finally {
      if (mounted) setState(() => busy = null);
    }
  }

  @override
  Widget build(BuildContext context) => FutureBuilder<List<PendingCommand>>(
        future: pending,
        builder: (context, snapshot) {
          final requests = snapshot.data ?? const <PendingCommand>[];
          if (requests.isEmpty) return const SizedBox.shrink();
          return KCard(
              title: 'Requests awaiting confirmation',
              child: kColumn([
                const KMuted(
                    'A previous request may already have been accepted. Checking reuses its original identifier to avoid creating duplicate work.'),
                if (error != null) KMuted(error!),
                for (final command in requests)
                  kRow([
                    Text('${command.body['type']}'),
                    KButton(
                        label:
                            busy == command.key ? 'Checking…' : 'Check request',
                        onPressed: busy == null ? () => retry(command) : null),
                  ]),
              ]));
        },
      );
}
