import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import '../../data/models/json.dart';
import '../../state/kleos_store.dart';
import '../core/widgets.dart';

class DeveloperPage extends ConsumerStatefulWidget {
  const DeveloperPage(
      {super.key, required this.controller, this.kind, this.capability});
  final KleosController controller;
  final String? kind, capability;
  @override
  ConsumerState<DeveloperPage> createState() => _DeveloperPageState();
}

class _DeveloperPageState extends ConsumerState<DeveloperPage> {
  late TextEditingController manifest;
  final focus = FocusNode();
  final terminal = ScrollController();
  String? previousKind;
  int logLength = 0;
  @override
  void initState() {
    super.initState();
    manifest = TextEditingController(
        text: '${asMap(widget.controller.state['dev'])['manifest'] ?? ''}');
    previousKind = asMap(widget.controller.state['dev'])['kind'] as String?;
    focus.addListener(_focusChanged);
    ref.listenManual(kleosProvider, (_, __) => _changed());
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (widget.kind != null || widget.capability != null) {
        widget.controller.execute('developer.configure', {
          'kind': widget.kind,
          if (widget.capability != null) 'capability': widget.capability
        });
      }
    });
  }

  void _focusChanged() {
    if (!focus.hasFocus) _save();
  }

  void _changed() {
    final dev = asMap(widget.controller.state['dev']);
    if (previousKind != dev['kind'] || !focus.hasFocus) {
      if (manifest.text != dev['manifest']) {
        manifest.text = '${dev['manifest'] ?? ''}';
      }
      previousKind = '${dev['kind']}';
    }
    final count = asList(dev['terminal']).length;
    if (count != logLength) {
      logLength = count;
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (terminal.hasClients) {
          terminal.jumpTo(terminal.position.maxScrollExtent);
        }
      });
    }
  }

  Future<bool> _save() async {
    if (manifest.text == asMap(widget.controller.state['dev'])['manifest']) {
      return true;
    }
    final result = await widget.controller
        .execute('developer.manifest', {'manifest': manifest.text});
    return result.ok;
  }

  Future<void> run(String step) async {
    if (await _save()) {
      await widget.controller.execute('developer.run', {'step': step});
    }
  }

  @override
  void dispose() {
    focus.removeListener(_focusChanged);
    focus.dispose();
    manifest.dispose();
    terminal.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final dev = asMap(widget.controller.state['dev']);
    final kind = '${dev['kind'] ?? 'service'}';
    final steps = asMap(dev['steps']);
    final running = steps.values.contains('running');
    bool valid = true;
    JsonMap parsed = {};
    String parseError = '';
    try {
      final value = jsonDecode(manifest.text);
      if (value is Map) parsed = asMap(value);
    } catch (e) {
      valid = false;
      parseError = e.toString();
    }
    final operations = <String>[];
    final groups = [...asMaps(parsed['services'])];
    if (parsed['tool'] is Map) {
      groups.add({
        'service_id': parsed['provider_slug'],
        'operations': asMap(parsed['tool'])['operations']
      });
    }
    if (parsed['driver'] is Map) {
      groups.add({
        'service_id': parsed['provider_slug'],
        'operations': asMap(parsed['driver'])['operations']
      });
    }
    for (final group in groups) {
      for (final entry in asMap(group['operations']).entries) {
        if (entry.value != 'unsupported') {
          operations
              .add('${group['service_id']}.${entry.key} (${entry.value})');
        }
      }
    }
    final hint = asList(asMap(
        asMap(widget.controller.state['developerMeta'])['fixHints'])[kind]);
    const order = ['init', 'fixtures', 'conformance', 'submit', 'qualify'];
    const labels = [
      'kleos init',
      'kleos fixtures run',
      'kleos conformance',
      'kleos submit',
      'kleos qualify'
    ];
    return KPage(children: [
      KSectionHeader(
          title: 'Connector kit',
          description:
              'Bring your service, your engineering tool or your printer to Kleos9. Describe it once, test it against a local fake platform, then qualify.',
          trailing: kRow([
            KButton(
                label: 'Platform MCP server',
                compact: true,
                onPressed: () => context.go('/agent-access')),
            KBadge('${dev['level'] ?? 'Draft'}')
          ])),
      KSegmented<String>(
          selected: kind,
          options: [
            for (final entry in {
              'service': 'Service provider',
              'tool': 'Engineering tool',
              'printer': 'Printer driver'
            }.entries)
              (value: entry.key, label: entry.value, icon: null)
          ],
          onSelected: running
              ? null
              : (value) => widget.controller
                  .execute('developer.configure', {'kind': value})),
      KResponsive(children: [
        KCard(
            title: 'kleos.manifest.json',
            trailing: KBadge(valid ? 'Valid JSON' : 'Invalid',
                tone: valid ? 'pass' : 'fail'),
            child: kColumn([
              TextField(
                  controller: manifest,
                  focusNode: focus,
                  maxLines: 22,
                  minLines: 15,
                  enabled: !running,
                  onChanged: (_) => setState(() {}),
                  style: const TextStyle(
                      fontFamily: 'GeistMono', fontSize: 12, height: 1.7),
                  decoration: const InputDecoration(
                      border: InputBorder.none,
                      enabledBorder: InputBorder.none,
                      focusedBorder: InputBorder.none)),
              if (!valid)
                Text(parseError,
                    style: const TextStyle(color: KColors.fail, fontSize: 12)),
              const KMuted('Operations Kleos9 will call', size: 12),
              KCode(valid
                  ? operations.isEmpty
                      ? '(no operations)'
                      : operations.join('\n')
                  : 'Fix the manifest to preview operations'),
            ])),
        kColumn([
          KCard(
              title: 'CLI',
              trailing: const KBadge('Simulated'),
              child: kColumn([
                kRow([
                  for (var i = 0; i < order.length; i++)
                    KButton(
                        label:
                            '${labels[i]}${steps[order[i]] == 'pass' ? ' ✔' : steps[order[i]] == 'fail' ? ' ✘' : ''}',
                        compact: true,
                        primary: steps[order[i]] != 'pass',
                        icon: Icons.play_arrow,
                        onPressed: running ||
                                widget.controller.isPending('developer.run') ||
                                !valid ||
                                (i > 0 && steps[order[i - 1]] != 'pass')
                            ? null
                            : () => run(order[i]))
                ]),
                if (steps['fixtures'] == 'fail' && dev['fixApplied'] != true)
                  KCard(
                      child: kColumn([
                    KMuted('Suggested fix: ${hint.length > 1 ? hint[1] : ''}'),
                    KButton(
                        label: 'Apply fix',
                        compact: true,
                        icon: Icons.build_outlined,
                        onPressed: () =>
                            widget.controller.execute('developer.applyFix'))
                  ])),
                Container(
                    height: 320,
                    width: double.infinity,
                    padding: const EdgeInsets.all(15),
                    decoration: BoxDecoration(
                        color: KColors.bg,
                        borderRadius: BorderRadius.circular(KRadius.control),
                        border: Border.all(color: KColors.border)),
                    child: SingleChildScrollView(
                        controller: terminal,
                        child: kColumn([
                          if (asList(dev['terminal']).isEmpty)
                            const KMuted('Run kleos init to start.', size: 12),
                          for (final line in asList(dev['terminal']))
                            SelectableText(
                                '$line'
                                    .replaceFirst(
                                        RegExp(r'^(ok:|err:|dim:)'), '')
                                    .trimLeft(),
                                style: TextStyle(
                                    fontFamily: 'GeistMono',
                                    fontSize: 12,
                                    height: 1.8,
                                    color: '$line'.startsWith('ok:')
                                        ? KColors.pass
                                        : '$line'.startsWith('err:')
                                            ? KColors.fail
                                            : KColors.muted))
                        ], gap: 2))),
              ], gap: 16)),
          KCard(
              title: 'Qualification',
              child: KMuted(
                  'Listed, then schema-verified after conformance, then qualified after ${kind == 'tool' ? 'benchmark briefs pass Kleos9 validators' : kind == 'printer' ? 'a calibration round-trip' : 'a paid qualification job'}. Any change to the connector triggers requalification.')),
        ], gap: 16),
      ]),
    ]);
  }
}

class AgentAccessPage extends StatelessWidget {
  const AgentAccessPage({super.key, required this.controller});
  final KleosController controller;
  @override
  Widget build(BuildContext context) {
    final exchange = asMaps(controller.state['agentExchange']);
    final total =
        (controller.state['agentExchangeTotal'] as num?)?.toInt() ?? 0;
    return KPage(children: [
      const KSectionHeader(
          title: 'Platform MCP server',
          description:
              'Kleos9 is itself an MCP server, so external AI assistants can bring build requests in. Those requests route to the same qualified connectors, so providers get demand from every assistant through one integration.',
          trailing: KBadge('MCP 2026-07-28, Tasks extension', tone: 'accent')),
      KCard(
          title: 'Exposed tools',
          child: KTable(headers: const [
            'Tool',
            'Description'
          ], rows: const [
            [
              KCode('start_build_request'),
              SizedBox(
                  width: 650,
                  child: KMuted(
                      'Start a text-to-kit build from a plain-language brief. Returns a project ID and a link for the customer to confirm.\n{ brief: string, contact_ref: string }'))
            ],
            [
              KCode('get_build_status'),
              SizedBox(
                  width: 650,
                  child: KMuted(
                      'Current stage, open questions and evidence summary for a build.\n{ project_id: string }'))
            ],
            [
              KCode('list_capabilities'),
              SizedBox(
                  width: 650,
                  child: KMuted(
                      'Capabilities the platform can currently deliver through qualified connectors.\n{}'))
            ],
          ])),
      KCard(
          title: 'Example: an external assistant starts a build',
          trailing: const KBadge('Simulated'),
          child: kColumn([
            if (exchange.isEmpty)
              const KMuted('Press play to simulate the exchange.'),
            for (final message in exchange)
              kColumn(
                  [KBadge('${message['who']}'), KCode('${message['text']}')],
                  gap: 5),
            KButton(
                label: exchange.isEmpty ? 'Play' : 'Next message',
                primary: true,
                icon: Icons.send_outlined,
                onPressed: exchange.length >= total
                    ? null
                    : () => controller.execute('agent.nextMessage')),
            const KMuted(
                'The customer still confirms the concept on Kleos9 before any design or spending happens.',
                size: 12),
          ], gap: 17)),
    ]);
  }
}
