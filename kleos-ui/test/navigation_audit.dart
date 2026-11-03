import 'package:flutter/material.dart';
import 'dart:math' as math;
import 'package:flutter/rendering.dart';
import 'package:flutter_test/flutter_test.dart';

/// A web semantics click must not extend outside the widget that owns it.
/// Ignore non-tappable/hidden semantics; explicit action tests cover activation.
void expectBoundedClickTargets(WidgetTester tester, {required String screen}) {
  final targets = find.byWidgetPredicate((widget) =>
      widget is InkResponse && widget.onTap != null ||
      widget is GestureDetector &&
          widget.onTap != null &&
          !widget.excludeFromSemantics);
  for (final element in targets.evaluate()) {
    var box = element.findRenderObject();
    // Switch/checkbox/radio list tiles intentionally make the label and
    // control one target. Respect their explicit merged region.
    element.visitAncestorElements((ancestor) {
      if (ancestor.widget is MergeSemantics) {
        box = ancestor.findRenderObject();
        return false;
      }
      if (ancestor.widget is Semantics &&
          (ancestor.widget as Semantics).container) {
        return false;
      }
      return true;
    });
    final bounds = box;
    if (bounds is! RenderBox || !bounds.hasSize) continue;
    final node =
        tester.getSemantics(find.byElementPredicate((e) => e == element));
    if (!node.getSemanticsData().hasAction(SemanticsAction.tap)) continue;
    // Material pads small buttons to a 48px touch target without altering
    // their visible dimensions. That intentional padding is not a leak.
    expect(
        node.rect.width, lessThanOrEqualTo(math.max(48, bounds.size.width) + 1),
        reason: '$screen: ${element.widget.runtimeType} click width escaped '
            'its control (${node.getSemanticsData().label})');
    expect(node.rect.height,
        lessThanOrEqualTo(math.max(48, bounds.size.height) + 1),
        reason: '$screen: ${element.widget.runtimeType} click height escaped '
            'its control (${node.getSemanticsData().label})');
  }
}
