import 'package:flutter/gestures.dart';
import 'package:flutter/cupertino.dart' show CupertinoPageTransitionsBuilder;
import 'package:flutter/material.dart';

abstract final class KColors {
  static const bg = Color(0xff09090b);
  static const surface = Color(0xff111114);
  static const surface2 = Color(0xff18181c);
  static const surface3 = Color(0xff202026);
  static const border = Color(0xff25252b);
  static const borderStrong = Color(0xff34343c);
  static const text = Color(0xfff3f3f5);
  static const muted = Color(0xffa1a1ab);
  static const faint = Color(0xff7d7d89);
  static const accent = Color(0xffec7a45);
  static const pass = Color(0xff5fc68a);
  static const fail = Color(0xfff08585);
  static const unknown = Color(0xffb2adb7);
  static const human = Color(0xffb4a1e3);

  /// Crisp 1px line that reads on every surface; used for dividers inside
  /// panels where a solid border colour would look heavy.
  static const hairline = Color(0x14ffffff);

  /// Top-edge highlight that makes a surface look lit from above.
  static const highlight = Color(0x1fffffff);

  /// Ink colour for text that sits on an accent fill.
  static const onAccent = Color(0xff160a04);
}

/// One radius scale for the whole app: containers, controls, pills.
abstract final class KRadius {
  static const card = 18.0;
  static const control = 10.0;
  static const pill = 999.0;
}

abstract final class KShadow {
  /// Shadows are tinted toward the background hue, never pure black.
  static const lift = [
    BoxShadow(color: Color(0x66050507), blurRadius: 28, offset: Offset(0, 14)),
  ];

  /// Soft ambient shadow for hero surfaces such as viewports and phones.
  static const stage = [
    BoxShadow(color: Color(0x8c050507), blurRadius: 60, offset: Offset(0, 28)),
    BoxShadow(color: Color(0x1fec7a45), blurRadius: 80, offset: Offset(0, 0)),
  ];
}

/// Motion tokens. One curve family and three durations keep every transition
/// in the app feeling like the same hand drew it.
abstract final class KMotion {
  static const out = Cubic(.16, 1, .3, 1);
  static const fast = Duration(milliseconds: 120);
  static const base = Duration(milliseconds: 220);
  static const slow = Duration(milliseconds: 420);
  static const intro = Duration(milliseconds: 1400);
}

/// Type scale. Geist for prose, GeistMono for anything that is a number, a
/// path or a value. Sizes step by roughly 1.2 so hierarchy reads at a glance.
abstract final class KType {
  static const display = TextStyle(
      fontFamily: 'Geist',
      color: KColors.text,
      fontSize: 56,
      fontWeight: FontWeight.w600,
      letterSpacing: -2.4,
      height: 1.02);
  static const h1 = TextStyle(
      fontFamily: 'Geist',
      color: KColors.text,
      fontSize: 34,
      fontWeight: FontWeight.w600,
      letterSpacing: -1.3,
      height: 1.1);
  static const h2 = TextStyle(
      fontFamily: 'Geist',
      color: KColors.text,
      fontSize: 22,
      fontWeight: FontWeight.w600,
      letterSpacing: -.6,
      height: 1.2);
  static const h3 = TextStyle(
      fontFamily: 'Geist',
      color: KColors.text,
      fontSize: 15,
      fontWeight: FontWeight.w600,
      letterSpacing: -.2,
      height: 1.3);
  static const body = TextStyle(
      fontFamily: 'Geist', color: KColors.text, fontSize: 13.5, height: 1.55);
  static const small = TextStyle(
      fontFamily: 'Geist', color: KColors.muted, fontSize: 12, height: 1.5);
  static const label = TextStyle(
      fontFamily: 'Geist',
      color: KColors.faint,
      fontSize: 11,
      fontWeight: FontWeight.w500,
      letterSpacing: .1,
      height: 1.3);
  static const mono = TextStyle(
      fontFamily: 'GeistMono', color: KColors.text, fontSize: 12, height: 1.5);
  static const monoSmall = TextStyle(
      fontFamily: 'GeistMono', color: KColors.muted, fontSize: 11, height: 1.5);
  static const numeral = TextStyle(
      fontFamily: 'GeistMono',
      color: KColors.text,
      fontSize: 28,
      fontWeight: FontWeight.w500,
      letterSpacing: -1.2,
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
        surface: KColors.surface,
        onSurface: KColors.text,
        error: KColors.fail),
    textTheme: base.textTheme
        .apply(
            fontFamily: 'Geist',
            bodyColor: KColors.text,
            displayColor: KColors.text)
        .copyWith(
          bodyMedium: KType.body.copyWith(fontSize: 13),
          bodySmall: KType.small.copyWith(fontSize: 11),
          headlineLarge: KType.h1,
          headlineMedium: KType.h1.copyWith(fontSize: 30, letterSpacing: -1.1),
          headlineSmall: KType.h2,
          titleLarge: KType.h2.copyWith(fontSize: 19),
          titleMedium: KType.h3,
        ),
    dividerColor: KColors.border,
    inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: KColors.bg,
        isDense: true,
        contentPadding: const EdgeInsets.all(13),
        border: OutlineInputBorder(
            borderRadius: BorderRadius.circular(KRadius.control),
            borderSide: const BorderSide(color: KColors.border)),
        enabledBorder: OutlineInputBorder(
            borderRadius: BorderRadius.circular(KRadius.control),
            borderSide: const BorderSide(color: KColors.border)),
        focusedBorder: OutlineInputBorder(
            borderRadius: BorderRadius.circular(KRadius.control),
            borderSide: const BorderSide(color: KColors.accent, width: 1.2)),
        hintStyle: const TextStyle(color: KColors.faint, fontSize: 13),
        labelStyle: const TextStyle(color: KColors.muted, fontSize: 12)),
    dialogTheme: DialogThemeData(
        backgroundColor: KColors.surface,
        surfaceTintColor: Colors.transparent,
        shape: RoundedRectangleBorder(
            side: const BorderSide(color: KColors.borderStrong),
            borderRadius: BorderRadius.circular(KRadius.card))),
    bottomSheetTheme: const BottomSheetThemeData(
        backgroundColor: KColors.surface,
        surfaceTintColor: Colors.transparent,
        modalBackgroundColor: KColors.surface,
        shape: RoundedRectangleBorder(
            side: BorderSide(color: KColors.borderStrong),
            borderRadius:
                BorderRadius.vertical(top: Radius.circular(KRadius.card)))),
    chipTheme: ChipThemeData(
        showCheckmark: false,
        backgroundColor: Colors.transparent,
        selectedColor: KColors.surface3,
        disabledColor: Colors.transparent,
        side: const BorderSide(color: KColors.border),
        shape: const StadiumBorder(),
        labelStyle: const TextStyle(
            fontFamily: 'Geist', fontSize: 12, color: KColors.muted),
        secondaryLabelStyle: const TextStyle(
            fontFamily: 'Geist', fontSize: 12, color: KColors.text),
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 5)),
    dividerTheme: const DividerThemeData(color: KColors.border, space: 1),
    popupMenuTheme: PopupMenuThemeData(
        color: KColors.surface2,
        surfaceTintColor: Colors.transparent,
        shape: RoundedRectangleBorder(
            side: const BorderSide(color: KColors.borderStrong),
            borderRadius: BorderRadius.circular(14))),
    checkboxTheme: CheckboxThemeData(
        fillColor: WidgetStateProperty.resolveWith((s) =>
            s.contains(WidgetState.selected)
                ? KColors.accent
                : Colors.transparent),
        checkColor: const WidgetStatePropertyAll(KColors.bg),
        side: const BorderSide(color: KColors.faint),
        visualDensity: VisualDensity.compact),
    switchTheme: SwitchThemeData(
        thumbColor: WidgetStateProperty.resolveWith((s) =>
            s.contains(WidgetState.selected)
                ? KColors.onAccent
                : KColors.muted),
        trackColor: WidgetStateProperty.resolveWith((s) =>
            s.contains(WidgetState.selected)
                ? KColors.accent
                : KColors.surface3),
        trackOutlineColor: const WidgetStatePropertyAll(Colors.transparent)),
    dataTableTheme: DataTableThemeData(
        headingTextStyle: KType.label,
        dataTextStyle: KType.body.copyWith(fontSize: 12.5),
        dividerThickness: .5,
        headingRowColor: const WidgetStatePropertyAll(Colors.transparent),
        dataRowColor: WidgetStateProperty.resolveWith((s) =>
            s.contains(WidgetState.hovered)
                ? Colors.white.withValues(alpha: .025)
                : Colors.transparent)),
    scrollbarTheme: ScrollbarThemeData(
        thickness: const WidgetStatePropertyAll(5),
        radius: const Radius.circular(8),
        thumbColor:
            WidgetStatePropertyAll(Colors.white.withValues(alpha: .16))),
    progressIndicatorTheme: const ProgressIndicatorThemeData(
        color: KColors.accent, linearTrackColor: KColors.surface3),
    tooltipTheme: TooltipThemeData(
        decoration: BoxDecoration(
            color: KColors.surface3,
            border: Border.all(color: KColors.borderStrong),
            borderRadius: BorderRadius.circular(8)),
        textStyle: const TextStyle(
            fontFamily: 'Geist', fontSize: 12, color: KColors.text)),
  );
}
