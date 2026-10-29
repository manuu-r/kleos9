import 'package:flutter/material.dart';

import '../../../data/models/json.dart';
import '../../../state/kleos_store.dart';
import '../../core/widgets.dart';

const stageNames = <String>[
  'Capture intent',
  'Research feasibility',
  'Review & confirm',
  'Establish interfaces',
  'Generate in parallel',
  'Verify & revise',
  'Procure prototype',
  'Test physically',
  'Prepare DIY kit',
  'Deliver & activate',
];

Widget pText(Object? value,
        {bool muted = false,
        bool small = false,
        bool mono = false,
        bool strong = false,
        Color? color}) =>
    Text(
      '${value ?? ''}',
      style: TextStyle(
        color: color ?? (muted ? KColors.muted : KColors.text),
        fontSize: small ? 12 : 14,
        fontFamily: mono ? 'GeistMono' : null,
        fontWeight: strong ? FontWeight.w600 : FontWeight.w400,
        height: 1.55,
      ),
    );

Widget pColumn(List<Widget> children, {double gap = 14}) => Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        for (var i = 0; i < children.length; i++) ...[
          if (i > 0) SizedBox(height: gap),
          children[i]
        ]
      ],
    );

Widget pActions(List<Widget> children) => Wrap(
    spacing: 10,
    runSpacing: 10,
    crossAxisAlignment: WrapCrossAlignment.center,
    children: children);
Widget pRight(Widget child) =>
    Align(alignment: Alignment.centerRight, child: child);

Widget pCallout(Widget child) => Container(
      width: double.infinity,
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
          color: KColors.accent.withValues(alpha: .06),
          border: Border.all(color: KColors.border),
          borderRadius: BorderRadius.circular(12)),
      child: child,
    );

String evidenceTone(String state) => switch (state) {
      'PASS' || 'ACCEPTED' || 'CONFIRMED' || 'RECEIVED' || 'accepted' => 'pass',
      'FAIL' || 'lost' => 'fail',
      'UNKNOWN' ||
      'STALE' ||
      'UNCERTAIN' ||
      'RECONCILING' ||
      'WAITING_FOR_CONNECTOR' ||
      'REVISION_REQUESTED' =>
        'unknown',
      'RUNNING' ||
      'DISPATCHED' ||
      'SHIPPED' ||
      'generating' ||
      'ready' ||
      'EVIDENCE_SUBMITTED' ||
      'REVIEW' =>
        'accent',
      _ => '',
    };
Widget evidence(Object? state) =>
    KBadge('${state ?? 'PENDING'}', tone: evidenceTone('$state'));

Widget pDisclosure(
        {required String title,
        required Widget child,
        bool initiallyExpanded = false,
        int? count}) =>
    Builder(
        builder: (context) => Theme(
              data:
                  Theme.of(context).copyWith(dividerColor: Colors.transparent),
              child: ExpansionTile(
                tilePadding: EdgeInsets.zero,
                childrenPadding: const EdgeInsets.only(bottom: 12),
                initiallyExpanded: initiallyExpanded,
                title: pText(count == null ? title : '$title  $count',
                    muted: true, small: true),
                iconColor: KColors.muted,
                collapsedIconColor: KColors.muted,
                children: [child],
              ),
            ));

Widget pCheckList(List<JsonMap> checks) => pColumn([
      for (final check in checks)
        Padding(
          padding: const EdgeInsets.symmetric(vertical: 8),
          child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Expanded(
                child: pColumn([
              pText(check['name'], strong: true),
              if ('${check['detail'] ?? ''}'.isNotEmpty)
                pText(check['detail'], muted: true, small: true)
            ], gap: 2)),
            const SizedBox(width: 14),
            evidence(check['state']),
          ]),
        ),
    ], gap: 0);

Widget projectAction(KleosController controller, JsonMap project,
        String command, String label,
        {bool enabled = true,
        bool primary = true,
        IconData? icon,
        JsonMap extra = const {}}) =>
    KButton(
      label: label,
      primary: primary,
      icon: icon,
      onPressed: enabled && !controller.isPending(command)
          ? () {
              controller
                  .execute(command, {'projectId': project['id'], ...extra});
            }
          : null,
    );

String shortHash(Object? value, [int length = 12]) {
  final text = '${value ?? ''}';
  return text.length <= length ? text : text.substring(0, length);
}

String finishOf(JsonMap project) {
  final finishes = asList(asMap(project['answers'])['finish']);
  return finishes.isEmpty ? 'Matte black' : '${finishes.first}';
}

String joinValues(Object? values) => asList(values).join(', ');
num number(Object? value, [num fallback = 0]) =>
    value is num ? value : num.tryParse('$value') ?? fallback;
