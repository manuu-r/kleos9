import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../../data/models/json.dart';
import '../../state/kleos_store.dart';
import '../features/project/common.dart';
import 'widgets.dart';

class HumanTaskCard extends StatefulWidget {
  const HumanTaskCard(
      {super.key,
      required this.controller,
      required this.task,
      this.defaultOpen = true});
  final KleosController controller;
  final JsonMap task;
  final bool defaultOpen;

  @override
  State<HumanTaskCard> createState() => _HumanTaskCardState();
}

class _HumanTaskCardState extends State<HumanTaskCard> {
  late bool _open = widget.defaultOpen;
  static const _words = <String, String>{
    'DRAFT': 'Draft',
    'SCOPED': 'Scoped',
    'AUTHORIZED': 'Authorized',
    'POSTED': 'Posted',
    'APPLICATIONS': 'Choose a worker',
    'ASSIGNED': 'Assigned',
    'IN_PROGRESS': 'In progress',
    'EVIDENCE_SUBMITTED': 'Evidence in',
    'REVIEW': 'In review',
    'ACCEPTED': 'Accepted',
    'REVISION_REQUESTED': 'Revision requested',
    'WAITING_FOR_CONNECTOR': 'Needs a platform',
  };
  static const _flow = [
    'DRAFT',
    'SCOPED',
    'AUTHORIZED',
    'POSTED',
    'APPLICATIONS',
    'ASSIGNED',
    'IN_PROGRESS',
    'EVIDENCE_SUBMITTED',
    'REVIEW',
    'ACCEPTED'
  ];

  Widget _action(String command, String label,
          {bool enabled = true,
          bool primary = false,
          IconData? icon,
          JsonMap extra = const {}}) =>
      KButton(
        label: label,
        primary: primary,
        compact: true,
        icon: icon,
        onPressed: enabled && !widget.controller.isPending(command)
            ? () {
                widget.controller
                    .execute(command, {'taskId': widget.task['id'], ...extra});
              }
            : null,
      );

  void _connect() {
    final current = GoRouterState.of(context).uri.toString();
    context.go(Uri(path: '/connections', queryParameters: {
      'tab': 'human',
      'connect': 'rentahuman',
      'back': current
    }).toString());
  }

  @override
  Widget build(BuildContext context) {
    final task = widget.task;
    final state = '${task['state']}';
    final applicants = asMaps(task['applicants']);
    final assignee =
        applicants.where((a) => a['id'] == task['assigneeId']).firstOrNull;
    final validation = asMaps(task['validation']);
    final validated =
        validation.isNotEmpty && validation.every((v) => v['ok'] == true);
    final evidenceData = asMap(task['evidence']);
    final photos = asList(evidenceData['photos']);
    final history = asMaps(task['history']);
    final flowIndex =
        _flow.indexOf(state == 'REVISION_REQUESTED' ? 'IN_PROGRESS' : state);
    return KCard(
        child: pColumn([
      InkWell(
          onTap: () => setState(() => _open = !_open),
          borderRadius: BorderRadius.circular(8),
          child: Semantics(
            button: true,
            expanded: _open,
            label: '${task['title']}, ${_words[state] ?? state}',
            child: Padding(
                padding: const EdgeInsets.symmetric(vertical: 2),
                child: Row(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Expanded(
                          child: pColumn([
                        pActions([
                          pText(task['title'], strong: true),
                          KBadge(_words[state] ?? state,
                              tone: evidenceTone(state))
                        ]),
                        pText(
                            '${task['projectName']}  ·  ${task['platform'] ?? 'No platform yet'}  ·  \$${task['fee']}',
                            muted: true,
                            small: true),
                      ], gap: 5)),
                      Icon(
                          _open
                              ? Icons.keyboard_arrow_up
                              : Icons.keyboard_arrow_down,
                          size: 20,
                          color: KColors.muted),
                    ])),
          )),
      if (_open) ...[
        pText(task['trigger'], muted: true),
        if (state != 'WAITING_FOR_CONNECTOR' && state != 'ACCEPTED')
          Wrap(spacing: 5, runSpacing: 5, children: [
            for (var i = 0; i < _flow.length; i++)
              Container(
                width: 22,
                height: 4,
                decoration: BoxDecoration(
                    color: i <= flowIndex ? KColors.accent : KColors.border,
                    borderRadius: BorderRadius.circular(3)),
              ),
          ]),
        if (state == 'WAITING_FOR_CONNECTOR')
          pCallout(pColumn([
            pText(
                'Connect RentAHuman or a similar platform to post this job. Or use the Kleos9 portal.'),
            pActions([
              KButton(
                  label: 'Connect RentAHuman',
                  compact: true,
                  primary: true,
                  icon: Icons.link,
                  onPressed: _connect),
              _action('task.usePortal', 'Use the portal'),
            ]),
          ])),
        if (state == 'APPLICATIONS')
          pColumn([
            pText('APPLICANTS', muted: true, small: true),
            for (final applicant in applicants)
              Container(
                padding: const EdgeInsets.symmetric(vertical: 10),
                decoration: const BoxDecoration(
                    border: Border(bottom: BorderSide(color: KColors.border))),
                child: LayoutBuilder(
                    builder: (context, constraints) => pActions([
                          SizedBox(
                              width: constraints.maxWidth > 430
                                  ? constraints.maxWidth - 110
                                  : constraints.maxWidth,
                              child: pColumn([
                                pText(applicant['name'], strong: true),
                                pText(
                                    '${applicant['rating']} stars, ${applicant['jobs']} jobs${applicant['meets'] == true ? '' : ', missing: ${applicant['missing']}'}',
                                    muted: true,
                                    small: true),
                              ], gap: 1)),
                          _action('task.assign', 'Assign',
                              enabled: applicant['meets'] == true,
                              icon: Icons.person_add_alt_1,
                              extra: {'applicantId': applicant['id']}),
                        ])),
              ),
          ], gap: 0),
        if (assignee != null && !['APPLICATIONS', 'ACCEPTED'].contains(state))
          pText(
              '${assignee['name']} ${task['filesShared'] == true ? 'has the design files, shared with an expiring link.' : 'is assigned. Files unlock next.'}',
              muted: true,
              small: true),
        if (state == 'IN_PROGRESS')
          _action('task.submitEvidence',
              'Simulate evidence from ${assignee?['name'] ?? 'the worker'}',
              primary: true),
        if (evidenceData.isNotEmpty && state != 'ACCEPTED')
          pColumn([
            pText(
                'EVIDENCE${number(task['revisionCount']) > 0 ? ', REVISION ${task['revisionCount']}' : ''}',
                muted: true,
                small: true),
            if (photos.isNotEmpty)
              Wrap(spacing: 10, runSpacing: 10, children: [
                for (final photo in photos)
                  Container(
                    width: 154,
                    height: 100,
                    padding: const EdgeInsets.all(12),
                    decoration: BoxDecoration(
                        color: KColors.bg,
                        border: Border.all(color: KColors.border),
                        borderRadius: BorderRadius.circular(9)),
                    child: Center(
                        child: pColumn([
                      const Icon(Icons.photo_outlined,
                          size: 20, color: KColors.faint),
                      pText(photo, muted: true, small: true)
                    ], gap: 4)),
                  )
              ]),
            if (asMaps(evidenceData['readings']).isNotEmpty)
              KTable(headers: const [
                'Reading',
                'Value'
              ], rows: [
                for (final reading in asMaps(evidenceData['readings']))
                  [pText(reading['label']), pText(reading['value'], mono: true)]
              ]),
            pText(evidenceData['statement']),
            if ('${evidenceData['notes'] ?? ''}'.isNotEmpty)
              pText(evidenceData['notes'], muted: true, small: true),
          ]),
        if (validation.isNotEmpty && state != 'ACCEPTED')
          pColumn([
            for (final item in validation)
              Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Expanded(child: pText(item['label'], muted: true, small: true)),
                const SizedBox(width: 10),
                Icon(item['ok'] == true ? Icons.check_circle : Icons.cancel,
                    color: item['ok'] == true ? KColors.pass : KColors.fail,
                    size: 16,
                    semanticLabel: item['ok'] == true ? 'Passed' : 'Failed'),
              ]),
          ], gap: 9),
        if (state == 'REVIEW')
          pActions([
            _action('task.accept', 'Accept and release \$${task['fee']}',
                primary: true, enabled: validated),
            _action('task.requestRevision', 'Ask for a revision'),
          ]),
        if (state == 'ACCEPTED')
          pActions([
            const Icon(Icons.check_circle, color: KColors.pass, size: 17),
            pText('Accepted. \$${task['fee']} released.', color: KColors.pass)
          ]),
        pDisclosure(
            title: 'Task details',
            child: pColumn([
              pText(task['publicDescription'], muted: true),
              pText('Needs', muted: true, small: true),
              pText(joinValues(task['requirements'])),
              pText('Evidence required', muted: true, small: true),
              pText(joinValues(task['evidenceSchema'])),
              pText(
                  '${task['capability']}${task['externalRef'] == null ? '' : ', ${task['externalRef']}'}',
                  muted: true,
                  small: true,
                  mono: true),
            ], gap: 8)),
        if (history.isNotEmpty)
          pDisclosure(
              title: 'History',
              count: history.length,
              child: pColumn([
                for (final item in history)
                  Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                    SizedBox(
                        width: 72,
                        child: pText(item['t'],
                            muted: true, small: true, mono: true)),
                    Expanded(child: pText(item['text'], small: true)),
                  ]),
              ], gap: 8)),
      ],
    ], gap: 16));
  }
}
