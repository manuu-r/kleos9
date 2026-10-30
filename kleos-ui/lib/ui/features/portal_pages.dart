import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import '../../data/models/json.dart';
import '../../state/kleos_store.dart';
import '../core/human_task_card.dart';
import '../core/widgets.dart';
import 'production/provider_models.dart';

class HumanTasksPage extends StatefulWidget {
  const HumanTasksPage({super.key, required this.controller});
  final KleosController controller;
  @override
  State<HumanTasksPage> createState() => _HumanTasksPageState();
}

class _HumanTasksPageState extends State<HumanTasksPage> {
  String filter = 'All';
  @override
  Widget build(BuildContext context) {
    final all = asMaps(widget.controller.state['humanTasks']);
    final tasks = all
        .where((t) => switch (filter) {
              'Open' =>
                !['ACCEPTED', 'WAITING_FOR_CONNECTOR'].contains(t['state']),
              'Waiting for platform' => t['state'] == 'WAITING_FOR_CONNECTOR',
              'In review' =>
                ['EVIDENCE_SUBMITTED', 'REVIEW'].contains(t['state']),
              'Accepted' => t['state'] == 'ACCEPTED',
              _ => true
            })
        .toList();
    final rah = asMaps(widget.controller.state['connectors'])
            .where((c) => c['id'] == 'rentahuman')
            .firstOrNull ??
        {};
    return KPage(children: [
      const KSectionHeader(
          title: 'Human tasks',
          description:
              'Work the AI can’t do itself: physical checks, measurements, assembly trials and expert reviews.'),
      KStats(items: [
        (
          label: 'Open',
          value: '${all.where((t) => ![
                'ACCEPTED',
                'WAITING_FOR_CONNECTOR'
              ].contains(t['state'])).length}',
          sub: null
        ),
        (
          label: 'Waiting for a platform',
          value:
              '${all.where((t) => t['state'] == 'WAITING_FOR_CONNECTOR').length}',
          sub: null
        ),
        (
          label: 'Accepted',
          value: '${all.where((t) => t['state'] == 'ACCEPTED').length}',
          sub: null
        ),
        (
          label: 'RentAHuman',
          value: rah['linked'] == true ? 'Linked' : 'Not linked',
          sub: rah['linked'] == true
              ? 'Jobs post as bounties'
              : 'Connect it in Connections'
        )
      ]),
      KCard(
          title: 'When does the agent create a human task?',
          child: kColumn(const [
            KMuted(
                '1. A check returns UNKNOWN because it needs physical presence, human judgement or an instrument.'),
            KMuted(
                '2. No qualified automated connector covers the capability.'),
            KMuted(
                '3. Automated attempts hit their limit without PASS or FAIL.'),
            KMuted(
                '4. The workflow always requires a human (assembly trial, packing check).')
          ], gap: 7)),
      KSegmented<String>(
          selected: filter,
          options: [
            for (final f in [
              'All',
              'Open',
              'Waiting for platform',
              'In review',
              'Accepted'
            ])
              (value: f, label: f, icon: null)
          ],
          onSelected: (f) => setState(() => filter = f)),
      if (tasks.isEmpty) const KCard(child: KMuted('No tasks in this view.')),
      for (final task in tasks)
        HumanTaskCard(
            key: ValueKey(task['id']),
            controller: widget.controller,
            task: task,
            defaultOpen: false),
    ]);
  }
}

class DemandPage extends StatefulWidget {
  const DemandPage({super.key, required this.controller});
  final KleosController controller;
  @override
  State<DemandPage> createState() => _DemandPageState();
}

class _DemandPageState extends State<DemandPage> {
  String kind = 'all';
  @override
  Widget build(BuildContext context) {
    final rows = asMaps(widget.controller.state['demand'])
        .where((d) => kind == 'all' || d['kind'] == kind)
        .toList();
    return KPage(children: [
      KSectionHeader(
          title: 'Unmet demand',
          description:
              'What customers asked for in the last 30 days that no qualified connector covers well. Build one and that work routes to you.',
          trailing: KButton(
              label: 'Provider portal',
              compact: true,
              onPressed: () => context.go('/provider'))),
      KSegmented<String>(
          selected: kind,
          options: [
            for (final entry in {
              'all': 'All',
              'service': 'Services',
              'tool': 'Engineering tools',
              'printer': 'Printer drivers'
            }.entries)
              (value: entry.key, label: entry.value, icon: null)
          ],
          onSelected: (value) => setState(() => kind = value)),
      KCard(
          padding: const EdgeInsets.all(8),
          child: KTable(
              headers: const [
                'Capability',
                'What customers needed',
                'Type',
                'Requests',
                'Qualified',
                '30-day change',
                ''
              ],
              rows: rows
                  .map((d) => [
                        Text('${d['capability']}',
                            style: const TextStyle(
                                fontFamily: 'GeistMono', fontSize: 10)),
                        SizedBox(
                            width: 230,
                            child: Text('${d['description']}',
                                style: const TextStyle(fontSize: 12))),
                        KMuted('${d['kind']}'),
                        Text('${d['requests']}'),
                        KBadge(
                            d['qualified'] == 0 ? 'None' : '${d['qualified']}',
                            tone: d['qualified'] == 0 ? 'fail' : null),
                        Text('+${d['trend']}',
                            style: const TextStyle(color: KColors.pass)),
                        KButton(
                            label: 'Build',
                            compact: true,
                            icon: Icons.arrow_forward,
                            onPressed: () => context.go(
                                '/developer?kind=${d['kind']}&cap=${Uri.encodeComponent('${d['capability']}')}'))
                      ])
                  .toList())),
      const KResponsive(columns: 3, children: [
        KCard(
            title: 'Build what already has buyers',
            child: KMuted(
                'Each number is a simulated request that waited or was routed elsewhere.')),
        KCard(
            title: 'Jobs arrive ready to run',
            child: KMuted(
                'Exact files, revision, acceptance criteria and required evidence.')),
        KCard(
            title: 'Ranking is earned',
            child: KMuted(
                'Routing uses fit, cost, lead time and measured quality. There is no paid placement.'))
      ]),
    ]);
  }
}

const _ladder = [
  ('Listed', 'Visible in directory; no routing'),
  ('Schema-verified', 'Quote-only routing'),
  ('Qualified', 'Small orders within mandate'),
  ('Preferred', 'Full mandate, routing boost from measured performance')
];

class ProviderPage extends StatefulWidget {
  const ProviderPage(
      {super.key, required this.controller, this.tab, this.projectId});
  final KleosController controller;
  final String? tab, projectId;
  @override
  State<ProviderPage> createState() => _ProviderPageState();
}

class _ProviderPageState extends State<ProviderPage> {
  String? selected;
  late bool showModels = widget.tab == 'models';
  @override
  void didUpdateWidget(covariant ProviderPage oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.tab != widget.tab ||
        oldWidget.projectId != widget.projectId) {
      showModels = widget.tab == 'models';
    }
  }

  @override
  Widget build(BuildContext context) {
    final state = widget.controller.state;
    final summary = asMap(state['providerSummary']);
    final jobs = asMaps(state['providerJobs']);
    selected ??= jobs.firstOrNull?['id'] as String?;
    final job = jobs.where((j) => j['id'] == selected).firstOrNull;
    final level = _ladder.indexWhere((l) => l.$1 == state['providerLevel']);
    final production = state['production'] == true;
    final tabs = KSegmented<bool>(
        selected: showModels,
        options: const [
          (value: false, label: 'Services', icon: null),
          (value: true, label: 'AI models', icon: null),
        ],
        onSelected: (value) => setState(() => showModels = value));
    if (production && showModels) {
      return KPage(children: [
        tabs,
        ProviderModels(
            key: ValueKey(widget.projectId),
            controller: widget.controller,
            initialProjectId: widget.projectId)
      ]);
    }
    return KPage(children: [
      if (production) tabs,
      kColumn([
        const KMuted('Provider portal, signed in as', size: 11),
        KSectionHeader(
            title: '${summary['name'] ?? 'Provider portal'}',
            description:
                'Jobs arrive fully specified from customer builds. No marketing needed, and ranking is earned by measured performance.',
            trailing: const KBadge('Simulated provider view'))
      ], gap: 8),
      KStats(
          items: asMaps(summary['stats'])
              .map((s) => (
                    label: '${s['label']}',
                    value: '${s['value']}',
                    sub: s['sub'] as String?
                  ))
              .toList()),
      KResponsive(children: [
        KCard(
            title: 'Qualification level',
            child: kColumn([
              for (var i = 0; i < _ladder.length; i++)
                Opacity(
                    opacity: i <= level ? 1 : .5,
                    child: kColumn([
                      kRow([
                        Icon(
                            i <= level
                                ? Icons.check_circle
                                : Icons.circle_outlined,
                            size: 14,
                            color: i <= level ? KColors.pass : KColors.faint),
                        KBadge(_ladder[i].$1)
                      ]),
                      KMuted(_ladder[i].$2, size: 11)
                    ], gap: 4)),
              const KMuted(
                  'Next: Preferred after 50 jobs with ≥ 95% on-time and quality ≥ 92.',
                  size: 10)
            ])),
        KCard(
            title: 'Where your jobs came from',
            child: kColumn([
              for (final s in asMaps(summary['sources']))
                kColumn([
                  Row(children: [
                    Expanded(child: KMuted('${s['label']}')),
                    Text('${s['pct']}%')
                  ]),
                  KProgress((s['pct'] as num).toDouble())
                ], gap: 6)
            ], gap: 17))
      ]),
      KResponsive(children: [
        KCard(
            title: 'Routed jobs',
            child: kColumn([
              for (final j in jobs)
                InkWell(
                    onTap: () => setState(() => selected = '${j['id']}'),
                    borderRadius: BorderRadius.circular(8),
                    child: Container(
                        padding: const EdgeInsets.all(12),
                        decoration: BoxDecoration(
                            borderRadius: BorderRadius.circular(8),
                            color: j['id'] == selected
                                ? KColors.accent.withValues(alpha: .1)
                                : Colors.transparent),
                        child: kColumn([
                          kRow([
                            Text('${j['title']}',
                                style: const TextStyle(
                                    fontWeight: FontWeight.w500)),
                            KBadge('${j['state']}'.replaceAll('_', ' '),
                                tone: _jobTone(j))
                          ]),
                          KMuted('${j['projectRef']} · Due ${j['due']}',
                              size: 10)
                        ], gap: 5)))
            ])),
        if (job != null)
          _JobDetail(
              key: ValueKey(job['id']), controller: widget.controller, job: job)
      ]),
      KResponsive(children: [
        KCard(
            title: 'Why you won or lost',
            child: kColumn([
              for (final j in jobs.where((j) => j['state'] == 'lost'))
                KMuted('• ${j['title']}: ${j['lossReason']}')
            ])),
        KCard(
            title: 'Grow with unmet demand',
            child: kColumn([
              KMuted(
                  '${asMaps(state['demand']).where((d) => d['capability'] == 'fab.print.tpu_flex').firstOrNull?['requests'] ?? 0} recent requests needed fab.print.tpu_flex and no qualified provider covered it.'),
              KButton(
                  label: 'See unmet demand',
                  compact: true,
                  onPressed: () => context.go('/demand'))
            ]))
      ]),
    ]);
  }
}

String _jobTone(JsonMap job) =>
    ['won', 'done', 'in_progress'].contains(job['state'])
        ? 'pass'
        : job['state'] == 'lost'
            ? 'fail'
            : 'accent';

class _JobDetail extends StatefulWidget {
  const _JobDetail({super.key, required this.controller, required this.job});
  final KleosController controller;
  final JsonMap job;
  @override
  State<_JobDetail> createState() => _JobDetailState();
}

class _JobDetailState extends State<_JobDetail> {
  final price = TextEditingController(text: '24'),
      lead = TextEditingController(text: '3');
  @override
  void dispose() {
    price.dispose();
    lead.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final j = widget.job;
    return KCard(
        title: '${j['title']}',
        trailing:
            KBadge('${j['state']}'.replaceAll('_', ' '), tone: _jobTone(j)),
        child: kColumn([
          KMuted(
              '${j['capability']} · ${j['projectRef']} · Qty ${j['qty']} · Due ${j['due']}',
              size: 10),
          const Text('Why this was routed to you',
              style: TextStyle(fontWeight: FontWeight.w500)),
          for (final reason in asList(j['why']))
            kRow([
              const Icon(Icons.check_circle, size: 13, color: KColors.pass),
              KMuted('$reason')
            ]),
          KResponsive(children: [
            kColumn([
              const KMuted('Files (exact revision, minimum packet)', size: 10),
              for (final file in asList(j['files'])) KCode('$file')
            ], gap: 6),
            kColumn([
              const KMuted('Acceptance criteria', size: 10),
              for (final item in asList(j['acceptance'])) KMuted('• $item')
            ], gap: 6)
          ]),
          if (j['state'] == 'new') ...[
            Row(children: [
              Expanded(
                  child: TextField(
                      controller: price,
                      keyboardType: TextInputType.number,
                      decoration:
                          const InputDecoration(labelText: 'Price (USD)'))),
              const SizedBox(width: 12),
              Expanded(
                  child: TextField(
                      controller: lead,
                      keyboardType: TextInputType.number,
                      decoration:
                          const InputDecoration(labelText: 'Lead (days)')))
            ]),
            kRow([
              KButton(
                  label: 'Submit quote',
                  primary: true,
                  icon: Icons.send_outlined,
                  onPressed: widget.controller.isPending('provider.quote')
                      ? null
                      : () => widget.controller.execute('provider.quote', {
                            'jobId': j['id'],
                            'price': double.tryParse(price.text) ?? 0,
                            'lead': double.tryParse(lead.text) ?? 0
                          })),
              KButton(
                  label: 'Decline',
                  onPressed: () => widget.controller
                      .execute('provider.decline', {'jobId': j['id']}))
            ]),
          ],
          if (j['state'] == 'quoted')
            KMuted(
                'Quote submitted (\$${j['quote']}). Routing compares total cost, lead time and measured quality…'),
          if (j['state'] == 'lost')
            Text('Not selected. ${j['lossReason']}',
                style: const TextStyle(color: KColors.fail)),
          if (j['state'] == 'in_progress') ...[
            Text('Won at \$${j['quote']}. Upload evidence when done.',
                style: const TextStyle(color: KColors.pass)),
            KButton(
                label: 'Upload photos + dimension report',
                primary: true,
                icon: Icons.upload_outlined,
                onPressed: () => widget.controller
                    .execute('provider.uploadEvidence', {'jobId': j['id']}))
          ],
          if (j['state'] == 'evidence')
            const KMuted('Evidence received; independent validator checking…'),
          if (j['state'] == 'done')
            const Text(
                'Accepted. Counts toward your on-time and quality scores.',
                style: TextStyle(color: KColors.pass)),
        ], gap: 15));
  }
}
