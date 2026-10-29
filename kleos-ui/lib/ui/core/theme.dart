import 'package:flutter/gestures.dart';
import 'package:flutter/cupertino.dart' show CupertinoPageTransitionsBuilder;
import 'package:flutter/material.dart';

abstract final class KColors {
  static const bg = Color(0xff0b0b0d);
  static const surface = Color(0xff131316);
  static const surface2 = Color(0xff19191d);
  static const border = Color(0xff29292e);
  static const text = Color(0xffededf0);
  static const muted = Color(0xffa3a3ad);
  static const faint = Color(0xff80808b);
  static const accent = Color(0xffec7a45);
  static const pass = Color(0xff5fc68a);
  static const fail = Color(0xfff08585);
  static const unknown = Color(0xffb2adb7);
  static const human = Color(0xffb4a1e3);
}

/// Motion tokens. One curve family and three durations keep every transition
/// in the app feeling like the same hand drew it.
abstract final class KMotion {
  static const out = Cubic(.16, 1, .3, 1);
  static const fast = Duration(milliseconds: 120);
  static const base = Duration(milliseconds: 220);
  static const slow = Duration(milliseconds: 420);
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
          bodyMedium: const TextStyle(
              fontFamily: 'Geist',
              fontSize: 13,
              height: 1.5,
              color: KColors.text),
          bodySmall: const TextStyle(
              fontFamily: 'Geist',
              fontSize: 11,
              height: 1.5,
              color: KColors.muted),
          headlineLarge: const TextStyle(
              fontFamily: 'Geist',
              color: KColors.text,
              fontSize: 32,
              fontWeight: FontWeight.w600,
              letterSpacing: -1.2,
              height: 1.08),
          headlineSmall: const TextStyle(
              fontFamily: 'Geist',
              color: KColors.text,
              fontSize: 20,
              fontWeight: FontWeight.w600,
              letterSpacing: -.4),
          titleMedium: const TextStyle(
              fontFamily: 'Geist',
              color: KColors.text,
              fontSize: 14,
              fontWeight: FontWeight.w600),
        ),
    dividerColor: KColors.border,
    inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: KColors.bg,
        isDense: true,
        contentPadding: const EdgeInsets.all(12),
        border: OutlineInputBorder(
            borderRadius: BorderRadius.circular(9),
            borderSide: const BorderSide(color: KColors.border)),
        enabledBorder: OutlineInputBorder(
            borderRadius: BorderRadius.circular(9),
            borderSide: const BorderSide(color: KColors.border)),
        focusedBorder: OutlineInputBorder(
            borderRadius: BorderRadius.circular(9),
            borderSide: const BorderSide(color: KColors.accent)),
        labelStyle: const TextStyle(color: KColors.muted, fontSize: 12)),
    dialogTheme: const DialogThemeData(backgroundColor: KColors.surface),
    checkboxTheme: CheckboxThemeData(
        fillColor: WidgetStateProperty.resolveWith((s) =>
            s.contains(WidgetState.selected)
                ? KColors.accent
                : Colors.transparent),
        checkColor: const WidgetStatePropertyAll(KColors.bg),
        side: const BorderSide(color: KColors.faint),
        visualDensity: VisualDensity.compact),
    tooltipTheme: const TooltipThemeData(
        textStyle:
            TextStyle(fontFamily: 'Geist', fontSize: 12, color: KColors.text)),
  );
}
