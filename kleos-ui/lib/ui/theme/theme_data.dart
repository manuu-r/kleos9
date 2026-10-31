import 'package:flutter/cupertino.dart' show CupertinoPageTransitionsBuilder;
import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';
import 'shape.dart';
import 'tokens.dart';

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

OutlineInputBorder _field(Color color, [double width = 1]) =>
    OutlineInputBorder(
        borderRadius: BorderRadius.circular(KRadius.control),
        borderSide: BorderSide(color: color, width: width));

/// Flutter's [ThemeData] for the app. Everything here follows from
/// tokens.dart and shape.dart; widget-level visuals are in components.dart.
ThemeData kTheme() {
  final base = ThemeData.dark(useMaterial3: true);
  return base.copyWith(
    scaffoldBackgroundColor: KColors.bg,
    canvasColor: KColors.bg,
    splashFactory: NoSplash.splashFactory,
    highlightColor: Colors.transparent,
    hoverColor: Colors.white.withValues(alpha: .04),
    focusColor: KColors.actionInk.withValues(alpha: .2),
    pageTransitionsTheme: const PageTransitionsTheme(builders: {
      TargetPlatform.android: FadeForwardsPageTransitionsBuilder(),
      TargetPlatform.iOS: CupertinoPageTransitionsBuilder(),
    }),
    colorScheme: const ColorScheme.dark(
        primary: KColors.actionInk,
        onPrimary: KColors.bg,
        secondary: KColors.accent,
        surface: KColors.surface,
        onSurface: KColors.text,
        error: KColors.fail),
    textSelectionTheme: TextSelectionThemeData(
        cursorColor: KColors.actionInk,
        selectionColor: KColors.actionInk.withValues(alpha: .32),
        selectionHandleColor: KColors.actionInk),
    textTheme: base.textTheme
        .apply(
            fontFamily: 'Geist',
            bodyColor: KColors.text,
            displayColor: KColors.text)
        .copyWith(
          bodyLarge: KType.body.copyWith(fontSize: 16),
          bodyMedium: KType.body,
          bodySmall: KType.small,
          headlineLarge: KType.h1,
          headlineMedium: KType.h1.copyWith(fontSize: 22, letterSpacing: -.4),
          headlineSmall: KType.h2,
          titleLarge: KType.h2,
          titleMedium: KType.h3,
          titleSmall: KType.h3.copyWith(fontSize: 14),
          labelLarge: KType.label.copyWith(color: KColors.text, fontSize: 13.5),
          labelMedium: KType.label,
          labelSmall: KType.label.copyWith(fontSize: 11.5),
        ),
    dividerColor: KColors.border,
    dividerTheme: const DividerThemeData(color: KColors.hairline, space: 1),
    inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: KColors.surface,
        isDense: true,
        contentPadding:
            const EdgeInsets.symmetric(horizontal: 14, vertical: 14),
        border: _field(KColors.borderStrong),
        enabledBorder: _field(KColors.border),
        disabledBorder: _field(KColors.border),
        focusedBorder: _field(KColors.actionInk, 1.5),
        errorBorder: _field(KColors.fail),
        focusedErrorBorder: _field(KColors.fail, 1.5),
        hintStyle: const TextStyle(color: KColors.faint, fontSize: 15),
        labelStyle: const TextStyle(color: KColors.muted, fontSize: 14)),
    dialogTheme: DialogThemeData(
        backgroundColor: KColors.surface,
        surfaceTintColor: Colors.transparent,
        elevation: 0,
        shape: KShape.panel(
            cut: 14, side: const BorderSide(color: KColors.borderStrong))),
    bottomSheetTheme: const BottomSheetThemeData(
        backgroundColor: KColors.surface,
        surfaceTintColor: Colors.transparent,
        modalBackgroundColor: KColors.surface,
        elevation: 0,
        showDragHandle: true,
        dragHandleColor: KColors.borderStrong,
        dragHandleSize: Size(36, 3),
        shape: Border(top: BorderSide(color: KColors.borderStrong))),
    chipTheme: ChipThemeData(
        showCheckmark: false,
        backgroundColor: Colors.transparent,
        selectedColor: KColors.surface3,
        disabledColor: Colors.transparent,
        side: const BorderSide(color: KColors.borderStrong),
        shape: KShape.control(),
        labelStyle: const TextStyle(
            fontFamily: 'Geist', fontSize: 13.5, color: KColors.muted),
        secondaryLabelStyle: const TextStyle(
            fontFamily: 'Geist', fontSize: 13.5, color: KColors.text),
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8)),
    popupMenuTheme: PopupMenuThemeData(
        color: KColors.surface2,
        surfaceTintColor: Colors.transparent,
        elevation: 0,
        shape: KShape.control(
            radius: 4, side: const BorderSide(color: KColors.borderStrong))),
    // Bottom bar: flat, sharp. The selected destination sits on a raised
    // rectangular tile, the way tool tiles do in a pro app.
    navigationBarTheme: NavigationBarThemeData(
        height: 66,
        backgroundColor: KColors.surface,
        surfaceTintColor: Colors.transparent,
        elevation: 0,
        indicatorColor: KColors.surface3,
        indicatorShape: KShape.control(),
        labelBehavior: NavigationDestinationLabelBehavior.alwaysShow,
        iconTheme: WidgetStateProperty.resolveWith((s) => IconThemeData(
            size: 23,
            color: s.contains(WidgetState.selected)
                ? KColors.text
                : KColors.faint)),
        labelTextStyle: WidgetStateProperty.resolveWith((s) => TextStyle(
            fontFamily: 'Geist',
            fontSize: 11.5,
            fontWeight: s.contains(WidgetState.selected)
                ? FontWeight.w600
                : FontWeight.w500,
            color: s.contains(WidgetState.selected)
                ? KColors.text
                : KColors.faint))),
    navigationRailTheme: NavigationRailThemeData(
        backgroundColor: KColors.surface,
        indicatorColor: KColors.surface3,
        indicatorShape: KShape.control(),
        elevation: 0,
        minWidth: 80,
        labelType: NavigationRailLabelType.all,
        selectedIconTheme: const IconThemeData(size: 22, color: KColors.text),
        unselectedIconTheme:
            const IconThemeData(size: 22, color: KColors.faint),
        selectedLabelTextStyle: const TextStyle(
            fontFamily: 'Geist',
            fontSize: 11.5,
            fontWeight: FontWeight.w600,
            color: KColors.text),
        unselectedLabelTextStyle: const TextStyle(
            fontFamily: 'Geist', fontSize: 11.5, color: KColors.faint)),
    checkboxTheme: CheckboxThemeData(
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(2)),
        fillColor: WidgetStateProperty.resolveWith((s) =>
            s.contains(WidgetState.selected)
                ? KColors.action
                : Colors.transparent),
        checkColor: const WidgetStatePropertyAll(KColors.onAction),
        side: const BorderSide(color: KColors.faint, width: 1.4),
        visualDensity: VisualDensity.standard),
    switchTheme: SwitchThemeData(
        thumbColor: WidgetStateProperty.resolveWith((s) =>
            s.contains(WidgetState.selected)
                ? KColors.onAction
                : KColors.muted),
        trackColor: WidgetStateProperty.resolveWith((s) =>
            s.contains(WidgetState.selected)
                ? KColors.action
                : KColors.surface3),
        trackOutlineColor: WidgetStateProperty.resolveWith((s) =>
            s.contains(WidgetState.selected)
                ? Colors.transparent
                : KColors.borderStrong)),
    sliderTheme: SliderThemeData(
        trackHeight: 2,
        activeTrackColor: KColors.actionInk,
        inactiveTrackColor: KColors.borderStrong,
        thumbColor: KColors.text,
        overlayColor: KColors.actionInk.withValues(alpha: .16),
        thumbShape: const RoundSliderThumbShape(enabledThumbRadius: 8),
        overlayShape: const RoundSliderOverlayShape(overlayRadius: 22)),
    dataTableTheme: DataTableThemeData(
        headingTextStyle: KType.hud,
        dataTextStyle: KType.body.copyWith(fontSize: 13),
        dividerThickness: 1,
        headingRowColor: const WidgetStatePropertyAll(Colors.transparent),
        dataRowColor: WidgetStateProperty.resolveWith((s) =>
            s.contains(WidgetState.hovered)
                ? Colors.white.withValues(alpha: .03)
                : Colors.transparent)),
    scrollbarTheme: ScrollbarThemeData(
        thickness: const WidgetStatePropertyAll(4),
        radius: Radius.zero,
        thumbColor:
            WidgetStatePropertyAll(Colors.white.withValues(alpha: .18))),
    progressIndicatorTheme: const ProgressIndicatorThemeData(
        color: KColors.actionInk,
        linearTrackColor: KColors.surface3,
        circularTrackColor: Colors.transparent,
        linearMinHeight: 2),
    tooltipTheme: TooltipThemeData(
        decoration: ShapeDecoration(
            color: KColors.surface3,
            shape: KShape.control(
                radius: 3,
                side: const BorderSide(color: KColors.borderStrong))),
        textStyle: const TextStyle(
            fontFamily: 'Geist', fontSize: 12.5, color: KColors.text)),
  );
}
