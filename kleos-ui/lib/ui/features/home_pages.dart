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
    return KPage(gap: 56, children: [
      KResponsive(children: [
        ConstrainedBox(
            constraints: BoxConstraints(
                minHeight: MediaQuery.sizeOf(context).width < 650 ? 0 : 380),
            child: Center(
                child: kColumn([
              LayoutBuilder(
                  builder: (context, constraints) => Text(
                      'Build hardware from a sentence.',
                      style: TextStyle(
                          fontSize:
                              (constraints.maxWidth * .115).clamp(32.0, 60.0),
                          fontWeight: FontWeight.w600,
                          letterSpacing: -1.6,
                          height: 1.05))),
              const KMuted(
                  'Describe a device. Agents design the enclosure, board and firmware, then route every step to a connector or a person.',
                  size: 16),
              kRow([
                KButton(
                    label: 'New build',
                    icon: Icons.add,
                    primary: true,
                    onPressed: () => context.go('/new')),
                KButton(
                    label: 'Browse connectors',
                    onPressed: () => context.go('/connections'))
              ]),
            ], gap: 22))),
        Container(
            height: MediaQuery.sizeOf(context).width < 650 ? 270 : 380,
            decoration: BoxDecoration(
                borderRadius: BorderRadius.circular(18),
                gradient: const RadialGradient(
                    center: Alignment(.4, .2),
                    radius: .85,
                    colors: [
                      Color(0x775c2e22),
                      Color(0xff101014),
                      KColors.bg
                    ])),
            child: const DeviceVisual())
      ]),
      if (controller.state['production'] == true) const _PendingRequests(),
      if (projects.isNotEmpty)
        kColumn([
          Text('Your builds', style: Theme.of(context).textTheme.headlineSmall),
          KResponsive(
              children: projects.map((p) {
            final done = asList(p['done']).where((x) => x == true).length;
            return InkWell(
                borderRadius: BorderRadius.circular(14),
                onTap: () => context.go('/projects/${p['id']}'),
                child: KCard(
                    child: kColumn([
                  Row(children: [
                    Expanded(
                        child: Text('${p['name']}',
                            style: Theme.of(context).textTheme.titleMedium)),
                    const Icon(Icons.arrow_forward,
                        size: 16, color: KColors.faint)
                  ]),
                  if (p['production'] == true) ...[
                    Text('${p['brief']}',
                        maxLines: 3,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(
                            color: KColors.muted, fontSize: 12)),
                    KBadge('${p['status'] ?? 'draft'}',
                        tone: p['status'] == 'blocked' ? 'unknown' : null),
                  ] else
                    KMuted('${p['brief']}'),
                  KProgress(done * 10.0),
                  KMuted(
                      done == 10
                          ? p['production'] == true
                              ? 'Simulated delivery and activation'
                              : 'Delivered and activated'
                          : stageNames[(p['stage'] as num).toInt().clamp(0, 9)],
                      size: 11)
                ])));
          }).toList())
        ], gap: 20),
      Text.rich(
          TextSpan(children: const [
            TextSpan(
                text: 'Every capability is a connector. ',
                style: TextStyle(color: KColors.text)),
            TextSpan(
                text:
                    'Fabrication, parts, machines and people plug in the same way, so the agent can route each step to whichever qualified one fits.',
                style: TextStyle(color: KColors.faint))
          ]),
          style: const TextStyle(
              fontSize: 30,
              fontWeight: FontWeight.w500,
              letterSpacing: -.8,
              height: 1.3)),
      KCard(
          title: 'Air Quality Monitor',
          trailing: const KBadge('simulated'),
          child: KResponsive(children: [
            kColumn([
              for (var i = 0; i < 6; i++)
                kRow([
                  Icon(i < 4 ? Icons.check : Icons.circle_outlined,
                      size: 13, color: i < 4 ? KColors.pass : KColors.faint),
                  Text(stageNames[i],
                      style: TextStyle(
                          color: i < 4 ? KColors.text : KColors.faint))
                ])
            ]),
            const KCode(
                'Intent       7 answers recorded, 2 unknowns carried forward\nPartsDirect  Core module, display and battery in stock\nYou          Confirmed concept, generation unlocked\nSolver       All 9 interface constraints satisfiable\nMechanical   shells.py exported, 2 parts, PETG profile\nValidators   USB-C notch clearance FAIL, repair task created')
          ])),
      kColumn([
        Text('What plugs in', style: Theme.of(context).textTheme.headlineSmall),
        KResponsive(children: [
          _HomeLink(
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
                      width: 190,
                      child: kColumn([
                        KMuted(lane, size: 10),
                        for (final tool in tools.where(
                            (t) => t['domain'] == lane && t['linked'] == true))
                          KBadge('${tool['name']}',
                              tone: tool['builtIn'] == true ? null : 'accent'),
                        const KMuted('Your tool', size: 11)
                      ], gap: 8))
              ])),
          _HomeLink(
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
                    Expanded(child: Text(item.$1)),
                    KMuted(
                        '${connectors.where((c) => c['category'] == item.$2 && c['linked'] == true).length} of ${connectors.where((c) => c['category'] == item.$2).length}',
                        size: 11)
                  ])
              ])),
          _HomeLink(
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
                const KMuted('Evidence received, validator running', size: 11)
              ])),
          const _HomeLink(
              title: 'Build a connector in minutes',
              description:
                  'One manifest for a service, a tool or a printer driver. Test locally, qualify, and start receiving routed work.',
              route: '/developer',
              child: KCode(
                  '{\n  "kind": "engineering_tool",\n  "display_name": "Vent Forge",\n  "tool": { "lane": "Mechanical",\n    "operations": { "generate": "supported", "approve": "unsupported" }\n  }\n}')),
        ])
      ], gap: 24),
    ]);
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
      borderRadius: BorderRadius.circular(14),
      child: KCard(
          child: kColumn([
        Row(children: [
          Expanded(
              child:
                  Text(title, style: Theme.of(context).textTheme.titleMedium)),
          const Icon(Icons.north_east, size: 15, color: KColors.faint)
        ]),
        KMuted(description),
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
    final result = await widget.controller
        .execute('project.create', {'brief': brief.text.trim()});
    if (mounted && result.ok) {
      context.go('/projects/${result.result['projectId']}');
    }
  }

  @override
  Widget build(BuildContext context) => KResponsive(children: [
        kColumn([
          KSectionHeader(
              title: 'Describe the device you want to build.',
              description: widget.controller.state['production'] == true
                  ? 'Plain language is fine. The agent asks a few questions, then designs the case, electronics and firmware together. Physical fulfillment remains simulated.'
                  : 'Plain language is fine. The agent asks a few questions, then designs the case, electronics and firmware together and ships you a kit.'),
          CallbackShortcuts(
              bindings: {
                const SingleActivator(LogicalKeyboardKey.enter, meta: true):
                    start,
                const SingleActivator(LogicalKeyboardKey.enter, control: true):
                    start
              },
              child: TextField(
                  controller: brief,
                  autofocus: true,
                  minLines: 5,
                  maxLines: 8,
                  onChanged: (_) => setState(() {}),
                  decoration: const InputDecoration(
                      labelText: 'Your request',
                      hintText:
                          'A desk air-quality monitor with a small screen…'))),
          kRow([
            KMuted(
                widget.controller.state['production'] == true
                    ? 'AI models are configured in Providers.'
                    : 'Scripted around an air-quality monitor in this prototype.',
                size: 10),
            KButton(
                label: 'Start build',
                primary: true,
                icon: Icons.arrow_forward,
                onPressed: brief.text.trim().isEmpty ||
                        widget.controller.isPending('project.create')
                    ? null
                    : start)
          ]),
          const KMuted('Or start from a sample', size: 11),
          for (final sample in sampleBriefs)
            InkWell(
                onTap: () => setState(() => brief.text = sample),
                borderRadius: BorderRadius.circular(9),
                child: Container(
                    width: double.infinity,
                    padding: const EdgeInsets.all(14),
                    decoration: BoxDecoration(
                        border: Border.all(color: KColors.border),
                        borderRadius: BorderRadius.circular(9)),
                    child: Text(sample, style: const TextStyle(fontSize: 12)))),
        ], gap: 20),
        kColumn([
          Container(
              height: 320,
              decoration: BoxDecoration(
                  color: KColors.surface,
                  borderRadius: BorderRadius.circular(14)),
              child: const DeviceVisual()),
          Text('What happens next',
              style: Theme.of(context).textTheme.titleMedium),
          for (var i = 0; i < stageNames.length; i++)
            kRow([
              SizedBox(width: 20, child: KMuted('${i + 1}', size: 11)),
              Text(stageNames[i])
            ])
        ], gap: 13)
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
