import 'package:flutter/gestures.dart';
import 'package:flutter/cupertino.dart' show CupertinoPageTransitionsBuilder;
import 'package:flutter/material.dart';

/// Kleos9 design tokens, "data terminal" direction.
///
/// Surfaces are flat and opaque. Cards sit one step *darker* than the ground
/// and are drawn by a 1px border, never by a shadow or a gradient. Colour is
/// reserved for status and data; the copper [accent] only ever appears as a
/// selection line or the single primary action. Every text pair below clears
/// 4.5:1 on the surface it is used on.
abstract final class KColors {
  /// Page ground.
  static const bg = Color(0xff171719);

  /// Cards and panels: darker than the ground.
  static const surface = Color(0xff131217);

  /// Nested wells and hover fills, between surface and raised.
  static const surface2 = Color(0xff1b1a1f);

  /// Raised controls and selected rows.
  static const surface3 = Color(0xff262529);
  static const border = Color(0xff222224);
  static const borderStrong = Color(0xff3a3a3e);
  static const text = Color(0xffffffff);
  static const muted = Color(0xff92949a);
  static const faint = Color(0xff8a8c92);

  /// PCB copper. Selection lines and the one primary action only.
  static const accent = Color(0xffec7a45);
  static const pass = Color(0xff5fc39c);
  static const fail = Color(0xffee6a80);
  static const unknown = Color(0xffb2adb7);
  static const warn = Color(0xffd9a441);

  /// "Needs a person". A cool blue so it never reads as violet.
  static const human = Color(0xff6ea5e8);

  /// Row rules inside panels: 3-7% off the surface.
  static const hairline = Color(0x12ffffff);

  /// Kept for older call sites; there is no lit top edge any more.
  static const highlight = Color(0x00ffffff);

  /// Ink for text that sits on an accent fill.
  static const onAccent = Color(0xff160a04);

  /// Pale status tints with dark-enough ink, for pills inside rows.
  static Color tint(Color c) => c.withValues(alpha: .12);
}

/// One radius scale: tight cards, softer controls, round pills.
abstract final class KRadius {
  static const card = 8.0;
  static const control = 8.0;
  static const pill = 999.0;
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
      fontSize: 26,
      fontWeight: FontWeight.w600,
      letterSpacing: -.6,
      height: 1.15);
  static const h2 = TextStyle(
      fontFamily: 'Geist',
      color: KColors.text,
      fontSize: 19,
      fontWeight: FontWeight.w600,
      letterSpacing: -.3,
      height: 1.25);
  static const h3 = TextStyle(
      fontFamily: 'Geist',
      color: KColors.text,
      fontSize: 15,
      fontWeight: FontWeight.w600,
      letterSpacing: -.1,
      height: 1.3);
  static const body = TextStyle(
      fontFamily: 'Geist', color: KColors.text, fontSize: 14, height: 1.5);
  static const small = TextStyle(
      fontFamily: 'Geist', color: KColors.muted, fontSize: 12.5, height: 1.45);
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

/// Selection mark shared by the bottom bar and the rail: a 2px copper line
/// above the icon, as wide as the icon's slot. Nothing is filled.
class KLineIndicator extends ShapeBorder {
  const KLineIndicator();
  @override
  EdgeInsetsGeometry get dimensions => EdgeInsets.zero;
  @override
  Path getInnerPath(Rect rect, {TextDirection? textDirection}) =>
      getOuterPath(rect, textDirection: textDirection);
  @override
  Path getOuterPath(Rect rect, {TextDirection? textDirection}) => Path()
    ..addRect(Rect.fromLTWH(
        rect.left + rect.width * .18, rect.top, rect.width * .64, 2));
  @override
  void paint(Canvas canvas, Rect rect, {TextDirection? textDirection}) {}
  @override
  ShapeBorder scale(double t) => this;
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
        fillColor: KColors.surface,
        isDense: true,
        contentPadding:
            const EdgeInsets.symmetric(horizontal: 14, vertical: 14),
        border: OutlineInputBorder(
            borderRadius: BorderRadius.circular(KRadius.control),
            borderSide: const BorderSide(color: KColors.borderStrong)),
        enabledBorder: OutlineInputBorder(
            borderRadius: BorderRadius.circular(KRadius.control),
            borderSide: const BorderSide(color: KColors.border)),
        focusedBorder: OutlineInputBorder(
            borderRadius: BorderRadius.circular(KRadius.control),
            borderSide: const BorderSide(color: KColors.accent, width: 1.2)),
        hintStyle: const TextStyle(color: KColors.faint, fontSize: 14),
        labelStyle: const TextStyle(color: KColors.muted, fontSize: 13)),
    dialogTheme: DialogThemeData(
        backgroundColor: KColors.surface,
        surfaceTintColor: Colors.transparent,
        shape: RoundedRectangleBorder(
            side: const BorderSide(color: KColors.borderStrong),
            borderRadius: BorderRadius.circular(12))),
    bottomSheetTheme: const BottomSheetThemeData(
        backgroundColor: KColors.surface,
        surfaceTintColor: Colors.transparent,
        modalBackgroundColor: KColors.surface,
        showDragHandle: true,
        dragHandleColor: KColors.borderStrong,
        dragHandleSize: Size(36, 4),
        shape: RoundedRectangleBorder(
            side: BorderSide(color: KColors.border),
            borderRadius: BorderRadius.vertical(top: Radius.circular(16)))),
    chipTheme: ChipThemeData(
        showCheckmark: false,
        backgroundColor: Colors.transparent,
        selectedColor: KColors.surface3,
        disabledColor: Colors.transparent,
        side: const BorderSide(color: KColors.border),
        shape: const StadiumBorder(),
        labelStyle: const TextStyle(
            fontFamily: 'Geist', fontSize: 12.5, color: KColors.muted),
        secondaryLabelStyle: const TextStyle(
            fontFamily: 'Geist', fontSize: 12.5, color: KColors.text),
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 6)),
    dividerTheme: const DividerThemeData(color: KColors.border, space: 1),
    popupMenuTheme: PopupMenuThemeData(
        color: KColors.surface3,
        surfaceTintColor: Colors.transparent,
        shape: RoundedRectangleBorder(
            side: const BorderSide(color: KColors.borderStrong),
            borderRadius: BorderRadius.circular(10))),
    navigationBarTheme: NavigationBarThemeData(
        height: 64,
        backgroundColor: KColors.bg,
        surfaceTintColor: Colors.transparent,
        elevation: 0,
        indicatorColor: KColors.accent,
        indicatorShape: const KLineIndicator(),
        labelBehavior: NavigationDestinationLabelBehavior.alwaysShow,
        iconTheme: WidgetStateProperty.resolveWith((s) => IconThemeData(
            size: 22,
            color: s.contains(WidgetState.selected)
                ? KColors.text
                : KColors.muted)),
        labelTextStyle: WidgetStateProperty.resolveWith((s) => TextStyle(
            fontFamily: 'Geist',
            fontSize: 11.5,
            fontWeight: FontWeight.w500,
            color: s.contains(WidgetState.selected)
                ? KColors.text
                : KColors.muted))),
    navigationRailTheme: NavigationRailThemeData(
        backgroundColor: KColors.bg,
        indicatorColor: KColors.accent,
        indicatorShape: const KLineIndicator(),
        elevation: 0,
        minWidth: 76,
        labelType: NavigationRailLabelType.all,
        selectedIconTheme: const IconThemeData(size: 22, color: KColors.text),
        unselectedIconTheme:
            const IconThemeData(size: 22, color: KColors.muted),
        selectedLabelTextStyle: const TextStyle(
            fontFamily: 'Geist',
            fontSize: 11.5,
            fontWeight: FontWeight.w500,
            color: KColors.text),
        unselectedLabelTextStyle: const TextStyle(
            fontFamily: 'Geist', fontSize: 11.5, color: KColors.muted)),
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
        decoration: BoxDecoration(
            color: KColors.surface3,
            border: Border.all(color: KColors.borderStrong),
            borderRadius: BorderRadius.circular(6)),
        textStyle: const TextStyle(
            fontFamily: 'Geist', fontSize: 12.5, color: KColors.text)),
  );
}
