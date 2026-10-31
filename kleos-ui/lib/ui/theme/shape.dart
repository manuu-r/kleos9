import 'dart:math' as math;
import 'package:flutter/material.dart';
import 'tokens.dart';

/// The corner language of the app, in one place.
///
/// * Panels (cards, stages, sheets) are never rounded. Two opposite corners,
///   top-right and bottom-left, are cut at 45 degrees. That asymmetric cut is
///   the signature of the look; change [panelCut] / [panel] to change it
///   everywhere.
/// * Controls (buttons, fields, tabs, chips) are rectangles with a 3px radius.
abstract final class KShape {
  /// A panel: opposite corners chamfered, outlined by a 1px line by default.
  static BeveledRectangleBorder panel(
          {double cut = KRadius.card, BorderSide? side}) =>
      BeveledRectangleBorder(
          borderRadius: BorderRadius.only(
              topRight: Radius.circular(cut), bottomLeft: Radius.circular(cut)),
          side: side ?? const BorderSide(color: KColors.border));

  /// A control: a sharp rectangle with a tiny radius.
  static RoundedRectangleBorder control(
          {double radius = KRadius.control, BorderSide? side}) =>
      RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(radius),
          side: side ?? BorderSide.none);

  /// The shape for a given nominal radius: anything larger than 8 is a panel
  /// (chamfered), anything smaller is a control. Lets older call sites that
  /// pass a radius pick up the right language without changes.
  static ShapeBorder border(double radius, {BorderSide? side}) => radius > 8
      ? panel(cut: math.min(14, math.max(6, radius)), side: side)
      : control(radius: radius, side: side);

  /// A filled surface in the right shape. Panels get the outline by default;
  /// pass `side: BorderSide.none` for a borderless fill.
  static ShapeDecoration surface(Color color,
          {double radius = KRadius.card, BorderSide? side}) =>
      ShapeDecoration(color: color, shape: border(radius, side: side));
}
