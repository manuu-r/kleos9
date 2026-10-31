import 'package:flutter/gestures.dart';
import 'package:flutter/cupertino.dart' show CupertinoPageTransitionsBuilder;
import 'package:flutter/material.dart';
import 'package:flutter/services.dart' show HapticFeedback;

/// Kleos9 design tokens: tonal depth on a warm near-black ground.
///
/// Elevation is lightness alone: ground, then card, then nested well, then
/// raised control. Nothing is outlined and nothing casts a shadow at rest.
/// Text is Apple-style off-white with two greys. The copper [accent] marks
/// live activity only (work running, the part you are touching, focus); status
/// keeps its own hues. Every text pair clears 4.5:1 on the surfaces it is used
/// on, and test/design_system_test.dart enforces it.
abstract final class KColors {
  /// Page ground: a warm near-black.
  static const bg = Color(0xff0b0b0c);

  /// Cards and panels, one step above the ground.
  static const surface = Color(0xff161617);

  /// Nested wells, fields and hover fills.
  static const surface2 = Color(0xff1f1f21);

  /// Raised controls, pressed and selected fills.
  static const surface3 = Color(0xff2a2a2d);

  /// Hairline for the few places that still need an edge (inputs, outlines).
  static const border = Color(0xff262629);
  static const borderStrong = Color(0xff3d3d41);
  static const text = Color(0xfff5f5f7);
  static const muted = Color(0xffa1a1a6);
  static const faint = Color(0xff949499);

  /// PCB copper: live activity and focus, never a button, tab or nav fill.
  static const accent = Color(0xffec7a45);
  static const pass = Color(0xff5fc39c);
  static const fail = Color(0xffee6a80);
  static const unknown = Color(0xffb2adb7);
  static const warn = Color(0xffd9a441);

  /// "Needs a person". A cool blue so it never reads as violet.
  static const human = Color(0xff6ea5e8);

  /// Row rules inside panels: about 8% white.
  static const hairline = Color(0x14ffffff);

  /// Kept for older call sites; there is no lit top edge any more.
  static const highlight = Color(0x00ffffff);

  /// Ink for text that sits on an accent fill.
  static const onAccent = Color(0xff160a04);

  /// Pale status tints with dark-enough ink, for pills inside rows.
  static Color tint(Color c) => c.withValues(alpha: .12);
}

/// One concentric radius scale. Outer surfaces are 20, what sits inside them
/// is 14, small marks are 10, controls are full pills. All of it is drawn with
/// continuous (superellipse) corners via [KShape], not circular arcs.
abstract final class KRadius {
  static const card = 20.0;
  static const control = 14.0;
  static const small = 10.0;
  static const pill = 999.0;
}

/// Continuous-curvature corners, the way Apple draws them: the curve eases
/// into the straight edge instead of meeting it at a hard tangent.
abstract final class KShape {
  static RoundedSuperellipseBorder border(double radius, {BorderSide? side}) =>
      RoundedSuperellipseBorder(
          borderRadius: BorderRadius.circular(radius),
          side: side ?? BorderSide.none);

  /// A filled, borderless surface.
  static ShapeDecoration surface(Color color,
          {double radius = KRadius.card, BorderSide? side}) =>
      ShapeDecoration(color: color, shape: border(radius, side: side));
}

/// Light haptics for native; a no-op on web. Selection is a tick, a primary
/// action is a light tap.
abstract final class KHaptics {
  static void select() => HapticFeedback.selectionClick();
  static void tap() => HapticFeedback.lightImpact();
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

abstract final class KShadow {
  /// Resting cards cast nothing; only things that truly float do.
  static const lift = <BoxShadow>[];

  /// A floating sheet or viewport frame.
  static const stage = <BoxShadow>[
    BoxShadow(color: Color(0x33000000), blurRadius: 24, offset: Offset(0, 8)),
  ];
}

/// Motion tokens. Feedback is fast, state changes medium; nothing performs.
abstract final class KMotion {
  static const out = Cubic(.2, .8, .2, 1);

  /// A touch of overshoot, for things that settle: the tab indicator, a press
  /// returning to rest.
  static const spring = Cubic(.3, 1.28, .5, 1);
  static const fast = Duration(milliseconds: 110);
  static const base = Duration(milliseconds: 180);
  static const slow = Duration(milliseconds: 320);
  static const intro = Duration(milliseconds: 900);
}

/// Type scale. Geist for prose; GeistMono for ids, values and figures.
/// Titles top out at 26px, data and labels never drop under 12px.
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
      letterSpacing: -.9,
      height: 1.12);
  static const h2 = TextStyle(
      fontFamily: 'Geist',
      color: KColors.text,
      fontSize: 20,
      fontWeight: FontWeight.w600,
      letterSpacing: -.45,
      height: 1.22);
  static const h3 = TextStyle(
      fontFamily: 'Geist',
      color: KColors.text,
      fontSize: 16,
      fontWeight: FontWeight.w600,
      letterSpacing: -.25,
      height: 1.3);
  static const body = TextStyle(
      fontFamily: 'Geist',
      color: KColors.text,
      fontSize: 15,
      letterSpacing: -.12,
      height: 1.47);
  static const small = TextStyle(
      fontFamily: 'Geist',
      color: KColors.muted,
      fontSize: 13.5,
      letterSpacing: -.05,
      height: 1.45);
  static const label = TextStyle(
      fontFamily: 'Geist',
      color: KColors.muted,
      fontSize: 12,
      fontWeight: FontWeight.w500,
      height: 1.3);
  static const mono = TextStyle(
      fontFamily: 'GeistMono',
      color: KColors.text,
      fontSize: 12.5,
      height: 1.5);
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

/// Momentum scrolling everywhere plus mouse and trackpad drag on desktop.
class KScrollBehavior extends MaterialScrollBehavior {
  const KScrollBehavior();
  @override
  ScrollPhysics getScrollPhysics(BuildContext context) =>
      const BouncingScrollPhysics(parent: AlwaysScrollableScrollPhysics());
  @override
  Set<PointerDeviceKind> get dragDevices =>
      {...super.dragDevices, PointerDeviceKind.mouse};
}

ThemeData kTheme() {
  final base = ThemeData.dark(useMaterial3: true);
  return base.copyWith(
    scaffoldBackgroundColor: KColors.bg,
    canvasColor: KColors.bg,
    splashFactory: NoSplash.splashFactory,
    highlightColor: Colors.transparent,
    hoverColor: Colors.white.withValues(alpha: .04),
    focusColor: KColors.accent.withValues(alpha: .18),
    pageTransitionsTheme: const PageTransitionsTheme(builders: {
      TargetPlatform.android: FadeForwardsPageTransitionsBuilder(),
      TargetPlatform.iOS: CupertinoPageTransitionsBuilder(),
    }),
    colorScheme: const ColorScheme.dark(
        primary: KColors.text,
        onPrimary: KColors.bg,
        secondary: KColors.accent,
        surface: KColors.surface,
        onSurface: KColors.text,
        error: KColors.fail),
    textTheme: base.textTheme
        .apply(
            fontFamily: 'Geist',
            bodyColor: KColors.text,
            displayColor: KColors.text)
        .copyWith(
          bodyLarge: KType.body.copyWith(fontSize: 15),
          bodyMedium: KType.body,
          bodySmall: KType.small,
          headlineLarge: KType.h1,
          headlineMedium: KType.h1.copyWith(fontSize: 22, letterSpacing: -.4),
          headlineSmall: KType.h2,
          titleLarge: KType.h2,
          titleMedium: KType.h3,
          titleSmall: KType.h3.copyWith(fontSize: 14),
          labelLarge: KType.label.copyWith(color: KColors.text, fontSize: 13),
          labelMedium: KType.label,
          labelSmall: KType.label.copyWith(fontSize: 11),
        ),
    dividerColor: KColors.border,
    inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: KColors.surface2,
        isDense: true,
        contentPadding:
            const EdgeInsets.symmetric(horizontal: 16, vertical: 15),
        border: OutlineInputBorder(
            borderRadius: BorderRadius.circular(KRadius.control),
            borderSide: BorderSide.none),
        enabledBorder: OutlineInputBorder(
            borderRadius: BorderRadius.circular(KRadius.control),
            borderSide: BorderSide.none),
        disabledBorder: OutlineInputBorder(
            borderRadius: BorderRadius.circular(KRadius.control),
            borderSide: BorderSide.none),
        focusedBorder: OutlineInputBorder(
            borderRadius: BorderRadius.circular(KRadius.control),
            borderSide: BorderSide(
                color: KColors.text.withValues(alpha: .55), width: 1.2)),
        hintStyle: const TextStyle(color: KColors.faint, fontSize: 15),
        labelStyle: const TextStyle(color: KColors.muted, fontSize: 14)),
    dialogTheme: DialogThemeData(
        backgroundColor: KColors.surface3,
        surfaceTintColor: Colors.transparent,
        shape: KShape.border(26)),
    bottomSheetTheme: BottomSheetThemeData(
        backgroundColor: KColors.surface,
        surfaceTintColor: Colors.transparent,
        modalBackgroundColor: KColors.surface,
        showDragHandle: true,
        dragHandleColor: KColors.borderStrong,
        dragHandleSize: const Size(36, 4),
        shape: RoundedSuperellipseBorder(
            borderRadius:
                const BorderRadius.vertical(top: Radius.circular(28)))),
    chipTheme: ChipThemeData(
        showCheckmark: false,
        backgroundColor: KColors.surface2,
        selectedColor: KColors.text,
        disabledColor: KColors.surface2,
        side: BorderSide.none,
        shape: const StadiumBorder(),
        labelStyle: const TextStyle(
            fontFamily: 'Geist', fontSize: 13.5, color: KColors.muted),
        secondaryLabelStyle: const TextStyle(
            fontFamily: 'Geist', fontSize: 13.5, color: KColors.bg),
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8)),
    dividerTheme: const DividerThemeData(color: KColors.hairline, space: 1),
    popupMenuTheme: PopupMenuThemeData(
        color: KColors.surface3,
        surfaceTintColor: Colors.transparent,
        elevation: 12,
        shadowColor: const Color(0x66000000),
        shape: KShape.border(18)),
    // Bottom bar: no indicator and no outline. The selected item is simply
    // brighter, the way a hardware tab bar reads; the bar rises by tone.
    navigationBarTheme: NavigationBarThemeData(
        height: 66,
        backgroundColor: KColors.surface,
        surfaceTintColor: Colors.transparent,
        elevation: 0,
        indicatorColor: Colors.transparent,
        labelBehavior: NavigationDestinationLabelBehavior.alwaysShow,
        iconTheme: WidgetStateProperty.resolveWith((s) => IconThemeData(
            size: 24,
            color: s.contains(WidgetState.selected)
                ? KColors.text
                : KColors.faint)),
        labelTextStyle: WidgetStateProperty.resolveWith((s) => TextStyle(
            fontFamily: 'Geist',
            fontSize: 11.5,
            letterSpacing: .1,
            fontWeight: s.contains(WidgetState.selected)
                ? FontWeight.w600
                : FontWeight.w500,
            color: s.contains(WidgetState.selected)
                ? KColors.text
                : KColors.faint))),
    navigationRailTheme: NavigationRailThemeData(
        backgroundColor: KColors.bg,
        indicatorColor: KColors.surface3,
        indicatorShape: KShape.border(16),
        elevation: 0,
        minWidth: 80,
        labelType: NavigationRailLabelType.all,
        selectedIconTheme: const IconThemeData(size: 23, color: KColors.text),
        unselectedIconTheme:
            const IconThemeData(size: 23, color: KColors.faint),
        selectedLabelTextStyle: const TextStyle(
            fontFamily: 'Geist',
            fontSize: 11.5,
            fontWeight: FontWeight.w600,
            color: KColors.text),
        unselectedLabelTextStyle: const TextStyle(
            fontFamily: 'Geist', fontSize: 11.5, color: KColors.faint)),
    checkboxTheme: CheckboxThemeData(
        fillColor: WidgetStateProperty.resolveWith((s) =>
            s.contains(WidgetState.selected)
                ? KColors.text
                : Colors.transparent),
        checkColor: const WidgetStatePropertyAll(KColors.bg),
        side: const BorderSide(color: KColors.faint, width: 1.4),
        visualDensity: VisualDensity.standard),
    switchTheme: SwitchThemeData(
        thumbColor: WidgetStateProperty.resolveWith((s) =>
            s.contains(WidgetState.selected) ? KColors.bg : KColors.muted),
        trackColor: WidgetStateProperty.resolveWith((s) =>
            s.contains(WidgetState.selected) ? KColors.text : KColors.surface3),
        trackOutlineColor: const WidgetStatePropertyAll(Colors.transparent)),
    sliderTheme: SliderThemeData(
        trackHeight: 2,
        activeTrackColor: KColors.text,
        inactiveTrackColor: KColors.borderStrong,
        thumbColor: KColors.text,
        overlayColor: Colors.white.withValues(alpha: .08),
        thumbShape: const RoundSliderThumbShape(enabledThumbRadius: 8),
        overlayShape: const RoundSliderOverlayShape(overlayRadius: 22)),
    dataTableTheme: DataTableThemeData(
        headingTextStyle: KType.label,
        dataTextStyle: KType.body.copyWith(fontSize: 13),
        dividerThickness: 1,
        headingRowColor: const WidgetStatePropertyAll(Colors.transparent),
        dataRowColor: WidgetStateProperty.resolveWith((s) =>
            s.contains(WidgetState.hovered)
                ? Colors.white.withValues(alpha: .025)
                : Colors.transparent)),
    scrollbarTheme: ScrollbarThemeData(
        thickness: const WidgetStatePropertyAll(4),
        radius: const Radius.circular(8),
        thumbColor:
            WidgetStatePropertyAll(Colors.white.withValues(alpha: .16))),
    progressIndicatorTheme: const ProgressIndicatorThemeData(
        color: KColors.accent,
        linearTrackColor: KColors.surface3,
        circularTrackColor: Colors.transparent,
        linearMinHeight: 2),
    tooltipTheme: TooltipThemeData(
        decoration:
            ShapeDecoration(color: KColors.surface3, shape: KShape.border(10)),
        textStyle: const TextStyle(
            fontFamily: 'Geist', fontSize: 12.5, color: KColors.text)),
  );
}
