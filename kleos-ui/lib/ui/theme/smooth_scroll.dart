import 'package:flutter/material.dart';

/// A scroll controller with premium wheel scrolling.
///
/// Flutter jumps a fixed distance per mouse-wheel notch on web and desktop,
/// which feels steppy. This controller eases each notch toward a target with a
/// decelerating curve, and notches that arrive mid-flight extend the same
/// target, so a fast spin glides instead of stuttering. Trackpads send small
/// continuous deltas and are passed through untouched; touch scrolling keeps
/// the platform's native momentum.
///
/// Give it to the page's scroll view: `SingleChildScrollView(controller: c)`.
class KSmoothController extends ScrollController {
  KSmoothController({super.initialScrollOffset});

  @override
  ScrollPosition createScrollPosition(ScrollPhysics physics,
          ScrollContext context, ScrollPosition? oldPosition) =>
      _SmoothPosition(
          physics: physics,
          context: context,
          initialPixels: initialScrollOffset,
          oldPosition: oldPosition,
          debugLabel: debugLabel);
}

class _SmoothPosition extends ScrollPositionWithSingleContext {
  _SmoothPosition(
      {required super.physics,
      required super.context,
      super.initialPixels,
      super.oldPosition,
      super.debugLabel});

  /// Where the current eased scroll is heading, while one is in flight.
  double? _target;

  /// Deltas at or above this are wheel notches (a notch is about 100px); the
  /// small continuous deltas of a trackpad are below it.
  static const _notch = 40.0;

  @override
  void pointerScroll(double delta) {
    if (delta == 0) {
      goBallistic(0);
      return;
    }
    if (delta.abs() < _notch) {
      _target = null;
      super.pointerScroll(delta);
      return;
    }
    final from = _target ?? pixels;
    final target = (from + delta).clamp(minScrollExtent, maxScrollExtent);
    if (target == pixels && _target == null) return;
    _target = target;
    // Longer travel gets slightly longer, so a hard spin still reads as one
    // continuous glide rather than a series of short hops.
    final distance = (target - pixels).abs();
    final ms = (200 + distance * .18).clamp(220, 520).round();
    animateTo(target,
            duration: Duration(milliseconds: ms), curve: Curves.easeOutCubic)
        .whenComplete(() {
      if (_target == target) _target = null;
    });
  }

  @override
  void beginActivity(ScrollActivity? newActivity) {
    // A drag or fling takes over from an eased scroll.
    if (newActivity is! DrivenScrollActivity) _target = null;
    super.beginActivity(newActivity);
  }
}
