import 'dart:convert';

import 'package:flutter/material.dart';

import '../../../data/models/json.dart';
import '../../core/concept_preview.dart';
import '../../core/widgets.dart';
import 'common.dart';

/// The frozen user-approved reference, kept separate from working AI drafts.
class ApprovedDesignReference extends StatelessWidget {
  const ApprovedDesignReference({
    super.key,
    required this.projectId,
    required this.approvedDesign,
    required this.designIntent,
    this.check = const {},
    this.status,
    this.onRequestReview,
  });
  final String projectId;
  final JsonMap approvedDesign, designIntent, check;
  final String? status;
  final VoidCallback? onRequestReview;

  @override
  Widget build(BuildContext context) {
    final scene = asMap(approvedDesign['scene']);
    final parts = asMaps(scene['parts']);
    final recorded = approvedDesign['schemaVersion'] == 'approved-design-v1' &&
        parts.isNotEmpty &&
        '${approvedDesign['sceneHash'] ?? ''}'.isNotEmpty;
    final enclosure = designIntent['sceneHash'] == approvedDesign['sceneHash']
        ? asMap(designIntent['enclosure'])
        : <String, dynamic>{};
    final superseded = status == 'superseded';
    final profile = switch (enclosure['profile']) {
      'circle' => 'Circular',
      'ellipse' => 'Elliptical',
      'rectangle' => 'Rectangular',
      'rounded_rectangle' => 'Rounded rectangular',
      _ => 'Approved concept shape',
    };
    final layout = parts
        .where(
            (part) => !const {'shell', 'cover', 'strap'}.contains(part['role']))
        .toList();
    final version = approvedDesign['version'];
    final details = asMap(check['details']);
    final pending = check['status'] == 'UNKNOWN' &&
        details['reasonCode'] == 'awaiting_engineering_checks';
    final conflict =
        check['status'] == 'FAIL' || (check['status'] == 'UNKNOWN' && !pending);
    return KCard(
      key:
          ValueKey('approved-design-$projectId-${approvedDesign['sceneHash']}'),
      title: 'Approved design reference',
      trailing: KBadge(
          superseded
              ? 'New approval needed'
              : recorded
                  ? 'Frozen at confirmation'
                  : 'Reference missing',
          tone: recorded && !superseded ? 'pass' : 'unknown'),
      child: pColumn([
        if (recorded) ...[
          pText('Body shape: $profile', strong: true),
          if (layout.isNotEmpty)
            pText(
                'Layout: ${layout.take(5).map((part) => part['label'] ?? part['id']).join(' · ')}${layout.length > 5 ? ' · +${layout.length - 5} more' : ''}',
                small: true,
                muted: true),
          pText(
              superseded
                  ? 'This is the previous approved reference. Review the new draft and confirm it before engineering continues. Proportions are relative.'
                  : 'Engineering uses this approved shape and arrangement. Proportions are relative; dimensions come from the accepted engineering interfaces.',
              small: true,
              muted: true),
          pDisclosure(
            title: 'View approved 3D reference',
            child: ConceptPreview(
              key: ValueKey(
                  'approved-scene-$projectId-${approvedDesign['sceneHash']}'),
              scene: scene,
              conceptVersion: (approvedDesign['sceneVersion'] as num?)?.toInt(),
              height: 230,
              caption:
                  'Approved shape and layout reference. Relative proportions are not millimetres or manufacturing geometry.',
            ),
          ),
        ] else
          pText(
              'No approved 3D reference was recorded for this build. A current draft cannot stand in for an approved design.',
              small: true,
              muted: true),
        if (pending && recorded && !superseded)
          pColumn([
            pText('Awaiting engineering checks', strong: true),
            pText(
                'Your approved reference is recorded. Engineering dimensions and independent checks are next.',
                small: true,
                muted: true),
          ], gap: 4),
        if (conflict)
          pCallout(Material(
              type: MaterialType.transparency,
              child: pColumn([
                pActions([
                  pText(
                      check['status'] == 'FAIL'
                          ? 'Engineering conflicts with the approved design'
                          : 'Design alignment needs a decision',
                      strong: true),
                  evidence(check['status']),
                ]),
                if (details['reason'] != null)
                  pText(details['reason'], small: true),
                pText(
                    'Engineering cannot silently replace this reference. Review the requested change and confirm the design before accepting new engineering output.',
                    small: true,
                    muted: true),
              ], gap: 8))),
        if ((!recorded || conflict || superseded) && onRequestReview != null)
          KButton(
              label: 'Review design reference',
              compact: true,
              icon: Icons.edit_outlined,
              onPressed: onRequestReview),
        if (recorded || check.isNotEmpty)
          pDisclosure(
            title: 'Reference details',
            child: pColumn([
              if (recorded) ...[
                pText(
                    'Approval version ${version ?? 'unavailable'} · concept version ${approvedDesign['sceneVersion'] ?? 'unavailable'}',
                    small: true),
                if (approvedDesign['engineeringRevision'] != null)
                  pText(
                      'Approved for engineering revision ${approvedDesign['engineeringRevision']}',
                      small: true,
                      muted: true),
                if (approvedDesign['approvedAt'] != null)
                  pText('Confirmed ${approvedDesign['approvedAt']}',
                      small: true, muted: true),
                if (approvedDesign['approvedBy'] != null)
                  pText('Confirmed by ${approvedDesign['approvedBy']}',
                      small: true, muted: true),
                pText('Source: the concept confirmed by the project user',
                    small: true, muted: true),
                SelectableText('Scene hash: ${approvedDesign['sceneHash']}',
                    style: const TextStyle(
                        fontFamily: 'GeistMono', fontSize: 12, height: 1.5)),
              ],
              if (designIntent.isNotEmpty)
                pDisclosure(
                    title: 'Shape and layout contract',
                    child: _json(designIntent)),
              if (check.isNotEmpty)
                pDisclosure(
                    title: 'Design alignment check', child: _json(check)),
            ], gap: 8),
          ),
      ], gap: 12),
    );
  }
}

Widget _json(JsonMap value) => SelectableText(
    const JsonEncoder.withIndent('  ').convert(value),
    style: const TextStyle(fontFamily: 'GeistMono', fontSize: 12, height: 1.5));
