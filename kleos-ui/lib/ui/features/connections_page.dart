import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import '../../data/models/json.dart';
import '../../state/kleos_store.dart';
import '../core/widgets.dart';
import 'production/engineering_tools.dart';
import 'production/rentahuman.dart';

const _tabs = {
  'tools': 'Engineering tools',
  'printers': 'Printers',
  'services': 'Services',
  'human': 'Human tasks'
};
String _tabOf(JsonMap c) => c['kind'] == 'tool'
    ? 'tools'
    : c['kind'] == 'printer'
        ? 'printers'
        : c['category'] == 'Human tasks'
            ? 'human'
            : 'services';

class ConnectionsPage extends StatefulWidget {
  const ConnectionsPage(
      {super.key,
      required this.controller,
      this.tab = 'tools',
      this.connectId,
      this.back});
  final KleosController controller;
  final String tab;
  final String? connectId, back;
  @override
  State<ConnectionsPage> createState() => _ConnectionsPageState();
}

class _ConnectionsPageState extends State<ConnectionsPage> {
  bool dialogOpen = false;
  @override
  void initState() {
    super.initState();
    _schedule();
  }

  @override
  void didUpdateWidget(ConnectionsPage old) {
    super.didUpdateWidget(old);
    if (old.connectId != widget.connectId) _schedule();
  }

  void _schedule() {
    if (widget.connectId != null) {
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (mounted) _connect();
      });
    }
  }

  Future<void> _connect() async {
    if (dialogOpen) return;
    final matches = asMaps(widget.controller.state['connectors']).where((c) =>
        c['id'] == widget.connectId &&
        c['linked'] != true &&
        c['production'] != true);
    if (matches.isEmpty) return;
    dialogOpen = true;
    final saved = await showDialog<bool>(
        context: context,
        builder: (context) => ConnectorDialog(
            controller: widget.controller, connector: matches.first));
    dialogOpen = false;
    if (mounted) {
      context.go((saved ?? false) && widget.back != null
          ? widget.back!
          : '/connections?tab=${widget.tab}');
    }
  }

  @override
  Widget build(BuildContext context) {
    final tab = _tabs.containsKey(widget.tab) ? widget.tab : 'tools';
    final list = asMaps(widget.controller.state['connectors'])
        .where((c) => _tabOf(c) == tab)
        .toList();
    final description = switch (tab) {
      'tools' =>
        'Kleos9 ships default generators and validators that work out of the box. Anyone can build a tool and plug it in; you choose which one runs for each lane.',
      'printers' =>
        'Have a 3D printer? Connect it and Kleos9 can print parts at home, no order needed. Without one, prints are ordered from qualified providers.',
      'human' =>
        widget.controller.state['production'] == true
          ? 'Find people and prepare work for RentAHuman.ai. Hiring and payment happen on RentAHuman; simulated connectors remain separate.'
          : "When the AI can't do something (fit checks, functional tests, assembly trials), jobs are posted to a linked platform such as RentAHuman, or to the hosted portal.",
      _ =>
        'Fabrication, PCB assembly, components and logistics. Work is routed to qualified services by measured performance.'
    };
    return KPage(children: [
      const KSectionHeader(
          title: 'Connections',
          description:
              'What the agent can use to design, build and check your device. Only qualified capabilities are routed to.'),
      KSegmented<String>(
          selected: tab,
          options: [
            for (final entry in _tabs.entries)
              (value: entry.key, label: entry.value, icon: null)
          ],
          onSelected: (value) => context.go('/connections?tab=$value')),
      Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Expanded(
            child: kColumn([
          Text(tab == 'printers' ? 'Your printers' : _tabs[tab]!,
              style: Theme.of(context).textTheme.headlineSmall),
          KMuted(description)
        ], gap: 8)),
        if (tab != 'human') ...[
          const SizedBox(width: 16),
          KButton(
              label: tab == 'tools'
                  ? 'Build a tool'
                  : tab == 'printers'
                      ? 'Write a printer driver'
                      : 'List your service',
              compact: true,
              icon: Icons.north_east,
              onPressed: () => context.go(
                  '/developer?kind=${tab == 'tools' ? 'tool' : tab == 'printers' ? 'printer' : 'service'}'))
        ]
      ]),
      if (tab == 'tools' && widget.controller.state['production'] == true)
        EngineeringTools(controller: widget.controller),
      if (tab == 'human' && widget.controller.state['production'] == true)
        const RentAHumanDirectory(),
      KResponsive(
          columns: 3,
          children: list
              .map((c) => _ConnectorTile(
                  controller: widget.controller,
                  connector: c,
                  onConnect: () => context.go(
                      '/connections?tab=$tab&connect=${c['id']}${widget.back != null ? '&back=${Uri.encodeComponent(widget.back!)}' : ''}')))
              .toList()),
    ]);
  }
}

class _ConnectorTile extends StatelessWidget {
  const _ConnectorTile(
      {required this.controller,
      required this.connector,
      required this.onConnect});
  final KleosController controller;
  final JsonMap connector;
  final VoidCallback onConnect;
  @override
  Widget build(BuildContext context) {
    final c = connector;
    final printer = asMap(c['printer']);
    final run = asMap(asMap(controller.state['connectionRuns'])[c['id']]);
    return Opacity(
        opacity: c['linked'] == true && c['enabled'] != true ? .55 : 1,
        child: KCard(
            child: kColumn([
          Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Padding(
                padding: const EdgeInsets.only(top: 2),
                child: Icon(
                    c['kind'] == 'printer'
                        ? Icons.print_outlined
                        : c['kind'] == 'tool'
                            ? Icons.construction_outlined
                            : Icons.hub_outlined,
                    size: 20,
                    color:
                        c['linked'] == true ? KColors.muted : KColors.faint)),
            const SizedBox(width: 12),
            Expanded(
                child: kColumn([
              Text('${c['name']}',
                  style: Theme.of(context).textTheme.titleMedium),
              KMuted([
                c['domain'],
                c['builtIn'] == true
                    ? 'Built in'
                    : c['kind'] == 'printer'
                        ? 'Printer driver'
                        : c['author'] != null
                            ? 'by ${c['author']}'
                            : c['integration'],
                if (c['builtIn'] != true && c['kind'] != 'printer') c['level']
              ].where((x) => x != null).join(', ')),
            ], gap: 2)),
          ]),
          KMuted(printer.isNotEmpty
              ? '${printer['buildVolume']}, ${printer['material']}. Calibrated.'
              : '${c['description']}'),
          if (c['production'] == true)
            kRow([
              KBadge('${c['level'] ?? 'unqualified'}'),
              KBadge('${c['mode'] ?? 'unavailable'}'),
              KMuted(c['enabled'] == true ? 'Enabled' : 'Not enabled'),
            ])
          else if (c['linked'] != true)
            KButton(
                label: c['kind'] == 'printer'
                    ? 'Connect printer'
                    : c['kind'] == 'tool'
                        ? 'Plug in'
                        : 'Connect',
                primary: true,
                compact: true,
                icon: c['kind'] == 'printer' ? Icons.print : Icons.power,
                onPressed: onConnect)
          else if (c['builtIn'] == true || c['integration'] == 'Built-in')
            const KMuted('On by default')
          else
            kRow([
              Text(
                  c['health'] == 'degraded'
                      ? 'Needs attention'
                      : printer.isNotEmpty
                          ? 'Online'
                          : 'Connected',
                  style: TextStyle(
                      color: c['health'] == 'degraded'
                          ? KColors.muted
                          : KColors.pass,
                      fontSize: 12)),
              KButton(
                  label: run['status'] == 'running' ? 'Testing…' : 'Test',
                  compact: true,
                  icon: Icons.refresh,
                  onPressed: run['status'] == 'running'
                      ? null
                      : () => controller
                          .execute('connector.test', {'connectorId': c['id']})),
              KButton(
                  label: c['enabled'] == true ? 'Disable' : 'Enable',
                  compact: true,
                  onPressed: () => controller.execute('connector.setEnabled', {
                        'connectorId': c['id'],
                        'enabled': c['enabled'] != true
                      })),
              KButton(
                  label: 'Disconnect',
                  compact: true,
                  onPressed: () => controller.execute(
                      'connector.disconnect', {'connectorId': c['id']}))
            ]),
        ])));
  }
}

class ConnectorDialog extends StatefulWidget {
  const ConnectorDialog(
      {super.key, required this.controller, required this.connector});
  final KleosController controller;
  final JsonMap connector;
  @override
  State<ConnectorDialog> createState() => _ConnectorDialogState();
}

class _ConnectorDialogState extends State<ConnectorDialog> {
  int step = 0;
  late Set<String> capabilities;
  late TextEditingController scope, host;
  final apiKey = TextEditingController();
  bool confirmEach = true;
  bool get printer => widget.connector['kind'] == 'printer';
  @override
  void initState() {
    super.initState();
    capabilities =
        asList(widget.connector['capabilities']).cast<String>().toSet();
    scope = TextEditingController(
        text: widget.connector['category'] == 'Human tasks'
            ? '60'
            : widget.connector['kind'] == 'tool'
                ? '0'
                : '120');
    final p = asMap(asMap(widget.controller.state['printerProfiles'])[
        widget.connector['id']]);
    host = TextEditingController(text: '${p['host'] ?? ''}');
  }

  @override
  void dispose() {
    scope.dispose();
    host.dispose();
    apiKey.dispose();
    super.dispose();
  }

  Future<void> action(String type, JsonMap payload,
      {int? next, bool save = false}) async {
    final result = await widget.controller
        .execute(type, {'connectorId': widget.connector['id'], ...payload});
    if (!mounted) return;
    if (result.ok) {
      if (save) {
        Navigator.of(context).pop(true);
      } else {
        setState(() => step = next ?? step);
      }
    }
  }

  @override
  Widget build(BuildContext context) => Consumer(builder: (context, ref, _) {
        final controller = ref.watch(kleosProvider);
        final c = widget.connector;
        final run = asMap(asMap(controller.state['connectionRuns'])[c['id']]);
        final progress = (run['progress'] as num?)?.toInt() ?? 0;
        final profile =
            asMap(asMap(controller.state['printerProfiles'])[c['id']]);
        const tests = [
          'Authenticate',
          'Discover capabilities',
          'Read-only status lookup',
          'Webhook signature check',
          'Schema conformance'
        ];
        const cal = [
          'Heat bed and nozzle',
          'Print 20 mm calibration cube',
          'Measure with camera and calipers prompt',
          'Save tolerance profile'
        ];
        final labels = printer
            ? ['Find', 'Profile', 'Calibrate', 'Use']
            : ['Authenticate', 'Capabilities', 'Tests', 'Scope'];
        return Dialog(
            alignment: Alignment.centerRight,
            insetPadding: const EdgeInsets.all(20),
            child: ConstrainedBox(
                constraints: BoxConstraints(
                    maxWidth: 520,
                    maxHeight: MediaQuery.sizeOf(context).height - 40),
                child: SingleChildScrollView(
                    padding: const EdgeInsets.all(24),
                    child: kColumn([
                      Row(children: [
                        Expanded(
                            child: Text('Connect ${c['name']}',
                                style:
                                    Theme.of(context).textTheme.headlineSmall)),
                        IconButton(
                            tooltip: 'Close',
                            onPressed: () => Navigator.of(context).pop(false),
                            icon: const Icon(Icons.close, size: 18))
                      ]),
                      kRow([
                        for (var i = 0; i < labels.length; i++)
                          KBadge('${i + 1}. ${labels[i]}',
                              tone: i == step
                                  ? 'accent'
                                  : i < step
                                      ? 'pass'
                                      : null)
                      ]),
                      if (step == 0) ...[
                        KMuted(printer
                            ? 'Jobs go straight from Kleos9 to your printer, so you can print parts at home instead of ordering them. This connection is simulated.'
                            : '${c['integration']} authorization is simulated. Credentials never enter model context, and connecting never grants spending by itself.'),
                        if (printer)
                          TextField(
                              controller: host,
                              decoration: const InputDecoration(
                                  labelText: 'Printer address')),
                        if (!printer && c['integration'] == 'REST')
                          TextField(
                              controller: apiKey,
                              obscureText: true,
                              decoration: const InputDecoration(
                                  labelText: 'Test API key (demo, not stored)',
                                  hintText: 'demo-key-…')),
                        KButton(
                            label: printer
                                ? 'Connect (simulated)'
                                : 'Authorize (simulated)',
                            primary: true,
                            onPressed: () => action(
                                printer
                                    ? 'connector.discover'
                                    : 'connector.authorize',
                                printer
                                    ? {'host': host.text}
                                    : {
                                        if (apiKey.text.isNotEmpty)
                                          'apiKey': apiKey.text
                                      },
                                next: 1)),
                      ],
                      if (step == 1 && !printer) ...[
                        const Text(
                            'Discovered capabilities. Choose which to map to the Kleos9 contract.'),
                        for (final cap in asList(c['capabilities']))
                          CheckboxListTile(
                              contentPadding: EdgeInsets.zero,
                              dense: true,
                              title: Text('$cap',
                                  style: const TextStyle(
                                      fontFamily: 'GeistMono', fontSize: 12)),
                              value: capabilities.contains(cap),
                              onChanged: (v) => setState(() => v ?? false
                                  ? capabilities.add('$cap')
                                  : capabilities.remove(cap))),
                        const KMuted(
                            'Tool descriptions are untrusted data. Any change triggers requalification.'),
                        KButton(
                            label: 'Run read-only tests',
                            primary: true,
                            onPressed: capabilities.isEmpty
                                ? null
                                : () => action('connector.test', {}, next: 2)),
                      ],
                      if (step == 1 && printer) ...[
                        if (progress < 3 && run['phase'] == 'discover')
                          kRow([
                            KSpinner(size: 15),
                            KMuted('Reading printer profile from ${host.text}…')
                          ])
                        else
                          KCard(
                              child: kColumn([
                            Text('${profile['model'] ?? c['name']}',
                                style: Theme.of(context).textTheme.titleMedium),
                            const KBadge('Online', tone: 'pass'),
                            KMuted(
                                'Build volume ${profile['buildVolume'] ?? ''} · Nozzle ${profile['nozzle'] ?? ''}\nLoaded: ${profile['material'] ?? ''}')
                          ])),
                        KButton(
                            label: 'Run calibration print',
                            primary: true,
                            onPressed: progress < 3
                                ? null
                                : () =>
                                    action('connector.calibrate', {}, next: 2)),
                      ],
                      if (step == 2) ...[
                        for (var i = 0; i < (printer ? cal : tests).length; i++)
                          kRow([
                            i < progress
                                ? const Icon(Icons.check_circle,
                                    size: 16, color: KColors.pass)
                                : i == progress
                                    ? KSpinner(size: 15)
                                    : const Icon(Icons.circle_outlined,
                                        size: 12, color: KColors.faint),
                            ExpandedText((printer ? cal : tests)[i])
                          ]),
                        if (printer && progress >= cal.length)
                          const KCard(
                              child: KMuted(
                                  'Measured offsets: X +0.06, Y +0.04, holes −0.12 mm. Kleos9 compensates for these when it sends parts to this printer.')),
                        KButton(
                            label: 'Continue',
                            primary: true,
                            onPressed: progress < (printer ? cal : tests).length
                                ? null
                                : () => setState(() => step = 3)),
                      ],
                      if (step == 3) ...[
                        DropdownButtonFormField<String>(
                            initialValue: 'all',
                            decoration: InputDecoration(
                                labelText: printer ? 'Use for' : 'Projects'),
                            items: const [
                              DropdownMenuItem(
                                  value: 'all', child: Text('All my projects'))
                            ],
                            onChanged: (_) {}),
                        if (printer)
                          CheckboxListTile(
                              contentPadding: EdgeInsets.zero,
                              dense: true,
                              value: confirmEach,
                              onChanged: (v) =>
                                  setState(() => confirmEach = v ?? true),
                              title: const Text(
                                  'Ask me to confirm on the printer before each job starts',
                                  style: TextStyle(fontSize: 12)))
                        else
                          kRow([
                            const KBadge('Demo', tone: 'accent'),
                            const KBadge(
                                'Live (not available in this simulation)')
                          ]),
                        if (!printer && c['kind'] != 'tool')
                          TextField(
                              controller: scope,
                              keyboardType: TextInputType.number,
                              decoration: const InputDecoration(
                                  labelText: 'Spending scope per job (USD)')),
                        KMuted(printer
                            ? 'When a project needs printed parts that fit this printer and its loaded material, you can choose to print at home instead of ordering.'
                            : c['kind'] == 'tool'
                                ? 'Once saved, you can pick this tool for its lane in any project. Its output is a candidate; Kleos9 validators still decide.'
                                : 'Human tasks in your projects will be posted to ${c['name']}. Design files are shared only after a worker is assigned.'),
                        KButton(
                            label: printer ? 'Save printer' : 'Save connection',
                            primary: true,
                            icon: printer ? Icons.print : Icons.power,
                            onPressed: () => action(
                                printer
                                    ? 'connector.savePrinter'
                                    : 'connector.save',
                                printer
                                    ? {
                                        'host': host.text,
                                        'confirmEach': confirmEach
                                      }
                                    : {
                                        'capabilities': capabilities.toList(),
                                        'scope':
                                            double.tryParse(scope.text) ?? 0
                                      },
                                save: true)),
                      ],
                    ], gap: 18))));
      });
}

class ExpandedText extends StatelessWidget {
  const ExpandedText(this.text, {super.key});
  final String text;
  @override
  Widget build(BuildContext context) =>
      Text(text, style: const TextStyle(fontSize: 12));
}
