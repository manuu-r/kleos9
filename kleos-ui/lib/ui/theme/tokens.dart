import 'package:flutter/material.dart';
import 'package:flutter/services.dart' show HapticFeedback;

// ---------------------------------------------------------------------------
// Kleos9 design tokens, "Signal" direction.
//
// This is the one file to edit to change the colour, spacing, type and motion
// of the whole app. Shapes live in shape.dart, decoration in ornaments.dart,
// widget visuals in components.dart, and Flutter's ThemeData in theme_data.dart.
// Preview any change with `flutter run -t lib/theme_gallery.dart`.
//
// Direction: a flat engineering console. Neutral near-black panels outlined by
// 1px lines, sharp corners with cut (chamfered) panel corners, small mono
// readout labels, and two hues with separate jobs:
//   * blue  (action)  = what you can do and what you have selected;
//   * coral (accent)  = the product and live work: the part you are touching,
//                       running jobs, the brand dot.
// Status keeps its own hues. Every text pair clears 4.5:1 on the surfaces it
// is used on; test/design_system_test.dart enforces it.
// ---------------------------------------------------------------------------

abstract final class KColors {
  /// Page ground.
  static const bg = Color(0xff141414);

  /// Panels and cards: one step darker than the ground, drawn by a 1px line.
  static const surface = Color(0xff0f0f10);

  /// Nested wells, fields and hover fills.
  static const surface2 = Color(0xff1b1b1c);

  /// Raised and selected fills.
  static const surface3 = Color(0xff262627);

  /// Panel and control outlines.
  static const border = Color(0xff2c2c2e);
  static const borderStrong = Color(0xff444447);

  /// Warm off-white, as on the HUD reference, never pure white.
  static const text = Color(0xffede9e2);
  static const muted = Color(0xffa5a29a);
  static const faint = Color(0xff94918a);

  /// Action blue: the primary button fill, switches, sliders.
  static const action = Color(0xff2f5bea);

  /// Ink for text on [action].
  static const onAction = Color(0xffffffff);

  /// Action blue for lines and glyphs on dark: selection, focus, links.
  static const actionInk = Color(0xff7b9dff);

  /// Coral: the product and live work. Never a button or a selection mark.
  static const accent = Color(0xffff7a59);

  /// Ink for text that sits on [accent].
  static const onAccent = Color(0xff1a0c08);

  // Status. Each is always paired with a word or glyph, never colour alone.
  static const pass = Color(0xff56c28a);
  static const fail = Color(0xfff46277);
  static const unknown = Color(0xffb8b4ab);
  static const warn = Color(0xffe3b341);

  /// "Needs a person".
  static const human = Color(0xff47c2c9);

  /// Row rules inside panels.
  static const hairline = Color(0x1affffff);

  /// Kept for older call sites; there is no lit edge in a flat design.
  static const highlight = Color(0x00ffffff);

  /// Pale status tint for pills inside rows.
  static Color tint(Color c) => c.withValues(alpha: .14);
}

/// Corner language: sharp. Controls are 3px, small marks 2px, and panels are
/// not rounded at all: two opposite corners are cut at 45 degrees by [panelCut]
/// (see shape.dart).
abstract final class KRadius {
  /// Panel chamfer size (the diagonal cut), not a radius.
  static const card = 10.0;

  /// Buttons, fields, tabs, chips.
  static const control = 3.0;
  static const small = 2.0;

  /// Kept for older call sites: chips and badges are sharp rectangles now.
  static const pill = control;
}

/// One 4pt-based spacing scale and one gutter per breakpoint.
abstract final class KSpace {
  static const xs = 4.0;
  static const sm = 8.0;
  static const md = 12.0;
  static const lg = 16.0;
  static const xl = 24.0;
  static const xxl = 32.0;

  /// Smallest comfortable touch target.
  static const touch = 44.0;

  static bool phone(BuildContext context) =>
      MediaQuery.sizeOf(context).width < 600;

  /// Side margin of the page canvas.
  static double gutter(BuildContext context) {
    final w = MediaQuery.sizeOf(context).width;
    return w < 600
        ? 16
        : w < 1000
            ? 24
            : 32;
  }
}

/// Flat design: nothing casts a shadow at rest, and floating things use a
/// line, not blur.
abstract final class KShadow {
  static const lift = <BoxShadow>[];
  static const stage = <BoxShadow>[];
}

/// Motion tokens. Quick, linear-feeling ease-outs; one soft settle for the tab
/// indicator. Nothing bounces.
abstract final class KMotion {
  static const out = Cubic(.2, .8, .2, 1);
  static const spring = Cubic(.25, 1.08, .4, 1);
  static const fast = Duration(milliseconds: 100);
  static const base = Duration(milliseconds: 160);
  static const slow = Duration(milliseconds: 260);
  static const intro = Duration(milliseconds: 700);
}

/// Type scale. Geist for prose, Geist Mono for ids, figures and the small
/// uppercase readout labels ([hud]). Titles are 22-30px, body 15px, nothing
/// under 11.5px.
abstract final class KType {
  static const display = TextStyle(
      fontFamily: 'Geist',
      color: KColors.text,
      fontSize: 32,
      fontWeight: FontWeight.w600,
      letterSpacing: -1,
      height: 1.1);
  static const h1 = TextStyle(
      fontFamily: 'Geist',
      color: KColors.text,
      fontSize: 28,
      fontWeight: FontWeight.w600,
      letterSpacing: -.8,
      height: 1.12);
  static const h2 = TextStyle(
      fontFamily: 'Geist',
      color: KColors.text,
      fontSize: 20,
      fontWeight: FontWeight.w600,
      letterSpacing: -.4,
      height: 1.22);
  static const h3 = TextStyle(
      fontFamily: 'Geist',
      color: KColors.text,
      fontSize: 16,
      fontWeight: FontWeight.w600,
      letterSpacing: -.2,
      height: 1.3);
  static const body = TextStyle(
      fontFamily: 'Geist',
      color: KColors.text,
      fontSize: 15,
      letterSpacing: -.1,
      height: 1.47);
  static const small = TextStyle(
      fontFamily: 'Geist', color: KColors.muted, fontSize: 13.5, height: 1.45);
  static const label = TextStyle(
      fontFamily: 'Geist',
      color: KColors.muted,
      fontSize: 12.5,
      fontWeight: FontWeight.w500,
      height: 1.3);

  /// The HUD readout: small, mono, tracked, uppercase by convention. Use it for
  /// meta lines and stat labels, a few per screen at most.
  static const hud = TextStyle(
      fontFamily: 'GeistMono',
      color: KColors.muted,
      fontSize: 11.5,
      fontWeight: FontWeight.w500,
      letterSpacing: .9,
      height: 1.3);
  static const mono = TextStyle(
      fontFamily: 'GeistMono', color: KColors.text, fontSize: 13, height: 1.5);
  static const monoSmall = TextStyle(
      fontFamily: 'GeistMono', color: KColors.muted, fontSize: 12, height: 1.5);
  static const numeral = TextStyle(
      fontFamily: 'GeistMono',
      color: KColors.text,
      fontSize: 24,
      fontWeight: FontWeight.w400,
      letterSpacing: -.8,
      height: 1);
}

/// Light haptics for native; a no-op on web.
abstract final class KHaptics {
  static void select() => HapticFeedback.selectionClick();
  static void tap() => HapticFeedback.lightImpact();
}
