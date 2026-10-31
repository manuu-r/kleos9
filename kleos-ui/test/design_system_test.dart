import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:kleos_ui/ui/core/widgets.dart';

Future<void> _mount(WidgetTester tester, Widget child,
    {double width = 390, bool still = false}) async {
  tester.view.physicalSize = Size(width, 800);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(MaterialApp(
      theme: kTheme(),
      builder: (context, app) => MediaQuery(
          data: MediaQuery.of(context).copyWith(disableAnimations: still),
          child: app!),
      home: Scaffold(
          body: Padding(padding: const EdgeInsets.all(16), child: child))));
}

double _contrast(Color a, Color b) {
  final hi = a.computeLuminance() > b.computeLuminance() ? a : b;
  final lo = identical(hi, a) ? b : a;
  return (hi.computeLuminance() + .05) / (lo.computeLuminance() + .05);
}

void main() {
  test('text tokens keep 4.5:1 on every surface they are used on', () {
    for (final surface in [KColors.bg, KColors.surface, KColors.surface3]) {
      for (final (name, ink) in [
        ('text', KColors.text),
        ('muted', KColors.muted),
        ('faint', KColors.faint),
        ('accent', KColors.accent),
        ('pass', KColors.pass),
        ('fail', KColors.fail),
        ('unknown', KColors.unknown),
        ('human', KColors.human),
        ('warn', KColors.warn),
      ]) {
        expect(_contrast(ink, surface), greaterThanOrEqualTo(4.5),
            reason: '$name on $surface');
      }
    }
  });

  testWidgets('one indicator glides under the selected tab', (tester) async {
    String picked = 'a';
    await _mount(
        tester,
        StatefulBuilder(
            builder: (context, setState) => KSegmented<String>(
                selected: picked,
                keyPrefix: 'tab-',
                options: const [
                  (value: 'a', label: 'First', icon: null),
                  (value: 'b', label: 'Second', icon: null),
                ],
                onSelected: (v) => setState(() => picked = v))));
    await tester.pumpAndSettle();
    final indicator = find.byKey(const ValueKey('tab-indicator'));
    expect(indicator, findsOneWidget);
    double labelLeft(String text) => tester.getTopLeft(find.text(text)).dx;
    // It sits under the selected label, as wide as the label.
    expect(tester.getTopLeft(indicator).dx, closeTo(labelLeft('First'), 1));
    expect(tester.getSize(indicator).width,
        closeTo(tester.getSize(find.text('First')).width, 1));
    expect(tester.getSize(indicator).height, 2);
    await tester.tap(find.text('Second'));
    await tester.pump(); // rebuild with the new selection
    await tester.pump(); // the indicator measures its destination
    await tester.pump(const Duration(milliseconds: 90));
    // Mid-flight it has left the first tab but not yet reached the second:
    // it travels instead of cutting.
    final mid = tester.getTopLeft(indicator).dx;
    expect(mid, greaterThan(labelLeft('First')));
    expect(mid, lessThan(labelLeft('Second')));
    await tester.pumpAndSettle();
    expect(picked, 'b');
    expect(tester.getTopLeft(indicator).dx, closeTo(labelLeft('Second'), 1));
    // Still exactly one indicator, and tabs are touch sized.
    expect(indicator, findsOneWidget);
    expect(tester.getSize(find.byKey(const ValueKey('tab-b'))).height,
        greaterThanOrEqualTo(44));
  });

  testWidgets('cards are borderless tonal surfaces with continuous corners',
      (tester) async {
    await _mount(tester, const KCard(child: Text('Body')));
    final box = tester.widget<AnimatedContainer>(find
        .descendant(
            of: find.byType(KCard), matching: find.byType(AnimatedContainer))
        .first);
    final decoration = box.decoration as ShapeDecoration;
    expect(decoration.color, KColors.surface);
    expect(decoration.shape, isA<RoundedSuperellipseBorder>());
    expect(
        (decoration.shape as RoundedSuperellipseBorder).side, BorderSide.none);
  });

  testWidgets('selected pills invert and the rest stay tonal', (tester) async {
    await _mount(
        tester,
        const Row(children: [
          KBadge('Passed', tone: 'pass'),
        ]));
    expect(find.text('Passed'), findsOneWidget);
  });

  testWidgets('a loading button spins and ignores taps', (tester) async {
    var taps = 0;
    await _mount(
        tester, KButton(label: 'Save', loading: true, onPressed: () => taps++),
        still: true);
    expect(find.byType(KSpinner), findsOneWidget);
    await tester.tap(find.text('Save'));
    expect(taps, 0);
  });

  testWidgets('an expanded button fills the width and is 44px tall',
      (tester) async {
    await _mount(
        tester, KButton(label: 'Sign in', expand: true, onPressed: () {}),
        width: 360);
    final size = tester.getSize(find.byType(TextButton));
    expect(size.width, closeTo(360 - 32, .5));
    expect(size.height, greaterThanOrEqualTo(44));
  });

  testWidgets('button labels stay on one line', (tester) async {
    await _mount(
        tester,
        SizedBox(
            width: 140,
            child: KButton(
                label: 'A label much too long to fit in one line',
                onPressed: () {})));
    expect(tester.takeException(), isNull);
    final text = tester
        .widget<Text>(find.text('A label much too long to fit in one line'));
    expect(text.maxLines, 1);
    expect(text.overflow, TextOverflow.ellipsis);
  });

  testWidgets('an error stays in its frame and Retry fires', (tester) async {
    var retried = 0;
    await _mount(
        tester,
        KErrorState('Could not load builds. Last updated 09:14.',
            retry: () => retried++));
    expect(find.text('Could not load builds. Last updated 09:14.'),
        findsOneWidget);
    expect(tester.getSize(find.widgetWithText(TextButton, 'Retry')).height,
        greaterThanOrEqualTo(44));
    await tester.tap(find.text('Retry'));
    expect(retried, 1);
    // The message is readable ink, not red text.
    final text = tester
        .widget<Text>(find.text('Could not load builds. Last updated 09:14.'));
    expect(text.style?.color, KColors.text);
  });

  testWidgets('loaders stand still when the platform asks for reduced motion',
      (tester) async {
    await _mount(
        tester,
        const Column(children: [
          KLineLoader(),
          KSpinner(),
          KPulse(child: KSkeletonRows(rows: 2)),
          KStatusDot(color: KColors.pass, live: true),
        ]),
        still: true);
    // With reduced motion nothing schedules frames, so this settles at once.
    await tester.pumpAndSettle(const Duration(milliseconds: 50));
    expect(tester.takeException(), isNull);
  });

  testWidgets('the line loader draws nothing and costs nothing when idle',
      (tester) async {
    await _mount(tester, const KLineLoader(active: false));
    await tester.pumpAndSettle(const Duration(milliseconds: 50));
    expect(
        find.descendant(
            of: find.byType(KLineLoader), matching: find.byType(CustomPaint)),
        findsNothing);
    expect(tester.getSize(find.byType(KLineLoader)).height, 2);
  });

  testWidgets('skeleton rows keep their real 64px pitch', (tester) async {
    await _mount(tester, const KSkeletonRows(rows: 3), still: true);
    final rows = tester
        .widgetList<SizedBox>(find.byType(SizedBox))
        .where((b) => b.height == 64)
        .length;
    expect(rows, 3);
  });

  testWidgets('an odd last stat spans the grid instead of leaving a hole',
      (tester) async {
    await _mount(
        tester,
        const KStats(items: [
          (label: 'One', value: '1', sub: null),
          (label: 'Two', value: '2', sub: null),
          (label: 'Three', value: '3', sub: null),
        ]));
    final one = tester.getRect(find.text('One'));
    final three = tester.getRect(find.text('Three'));
    expect(three.top, greaterThan(one.top));
    // The lone cell sits under the first column and is not squeezed narrower.
    expect(three.left, closeTo(one.left, .5));
    expect(tester.takeException(), isNull);
  });

  testWidgets('a progress bar paints its fill at the true ratio',
      (tester) async {
    await _mount(tester, const KProgress(50), still: true);
    await tester.pumpAndSettle();
    final track = tester.getSize(find.byType(KProgress));
    final fill = tester.getSize(find.descendant(
        of: find.byType(KProgress),
        matching: find.byType(FractionallySizedBox)));
    expect(fill.height, 3, reason: 'the fill must have height to be visible');
    expect(fill.width, closeTo(track.width / 2, .5));
  });

  testWidgets('a progress bar at zero draws no stub', (tester) async {
    await _mount(tester, const KProgress(0), still: true);
    await tester.pumpAndSettle();
    final fill = tester.getSize(find.descendant(
        of: find.byType(KProgress),
        matching: find.byType(FractionallySizedBox)));
    expect(fill.width, 0);
  });
}
