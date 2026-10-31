import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:kleos_ui/data/models/json.dart';
import 'package:kleos_ui/ui/core/widgets.dart';
import 'package:kleos_ui/ui/features/project/pcb_preview.dart';

JsonMap _pcb() => {
      'board': {'width': 60, 'height': 40},
      'components': [
        {'id': 'U1', 'label': 'MCU', 'x': 24, 'y': 18, 'width': 9, 'height': 9},
        {
          'id': 'J1',
          'label': 'USB-C',
          'x': 52,
          'y': 20,
          'width': 8,
          'height': 7
        },
        {
          'id': 'R1',
          'label': '10k',
          'x': 14,
          'y': 28,
          'width': 1.8,
          'height': 1
        },
      ],
      'traces': [
        {
          'layer': 'F.Cu',
          'points': [
            {'x': 24, 'y': 18},
            {'x': 38, 'y': 18},
            {'x': 52, 'y': 20}
          ]
        },
        {
          'layer': 'B.Cu',
          'points': [
            {'x': 14, 'y': 28},
            {'x': 24, 'y': 18}
          ]
        },
      ],
    };

Future<void> _mount(WidgetTester tester, JsonMap pcb,
    {double width = 360, double height = 420}) async {
  tester.view.physicalSize = Size(width, 800);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(MaterialApp(
      theme: kTheme(),
      home: Scaffold(
          body: Padding(
              padding: const EdgeInsets.all(16),
              child: PcbViewer(
                  pcb: pcb,
                  height: height,
                  fallback: const Text('Waiting for the board'))))));
  await tester.pump(const Duration(milliseconds: 1200));
  await tester.pumpAndSettle();
}

IconButton _button(WidgetTester tester, IconData icon) =>
    tester.widget<IconButton>(find.ancestor(
        of: find.byIcon(icon), matching: find.byType(IconButton)));

void main() {
  testWidgets('shows the board, its readout and a chip per component',
      (tester) async {
    await _mount(tester, _pcb());
    expect(find.byKey(const ValueKey('pcb-geometry-canvas')), findsOneWidget);
    expect(find.text('60 × 40 mm · 3 parts · 2 traces'), findsOneWidget);
    for (final id in ['U1', 'J1', 'R1']) {
      expect(find.byKey(ValueKey('pcb-part-$id')), findsOneWidget);
    }
    expect(tester.takeException(), isNull);
  });

  testWidgets('every control is at least 44px', (tester) async {
    await _mount(tester, _pcb());
    for (final tooltip in [
      'Expand PCB viewer',
      'PCB layers',
      'Zoom in PCB',
      'Zoom out PCB',
      'Fit PCB to view'
    ]) {
      final size = tester.getSize(find.byTooltip(tooltip));
      expect(size.width, greaterThanOrEqualTo(44), reason: tooltip);
      expect(size.height, greaterThanOrEqualTo(44), reason: tooltip);
    }
    expect(tester.getSize(find.byKey(const ValueKey('pcb-part-U1'))).height,
        greaterThanOrEqualTo(40));
  });

  testWidgets(
      'choosing a component selects it, flies to it and lights its traces',
      (tester) async {
    await _mount(tester, _pcb());
    // The whole board is in view, so there is nothing to zoom out of yet.
    expect(_button(tester, Icons.remove).onPressed, isNull);
    await tester.tap(find.byKey(const ValueKey('pcb-part-U1')));
    await tester.pumpAndSettle();
    // U1 touches both traces.
    expect(find.text('U1 · MCU · 9 × 9 mm · 2 traces'), findsOneWidget);
    expect(_button(tester, Icons.remove).onPressed, isNotNull,
        reason: 'the camera zoomed in on the part');
    // Fit returns to the whole board.
    await tester.tap(find.byTooltip('Fit PCB to view'));
    await tester.pumpAndSettle();
    expect(_button(tester, Icons.remove).onPressed, isNull);
    // Fit keeps the selection; choosing the same chip again clears it.
    expect(find.text('U1 · MCU · 9 × 9 mm · 2 traces'), findsOneWidget);
    await tester.tap(find.byKey(const ValueKey('pcb-part-U1')));
    await tester.pumpAndSettle();
    expect(find.text('60 × 40 mm · 3 parts · 2 traces'), findsOneWidget);
  });

  testWidgets('tapping a component on the board selects it', (tester) async {
    await _mount(tester, _pcb());
    final canvas = find.byKey(const ValueKey('pcb-geometry-canvas'));
    final box = tester.getRect(canvas);
    // Same fit as the viewer: a short canvas keeps 56px side margins.
    final fit = ((box.width - 112) / 60) < ((box.height - 64) / 40)
        ? (box.width - 112) / 60
        : (box.height - 64) / 40;
    final u1 = box.center + Offset((24 - 30) * fit, (18 - 20) * fit);
    await tester.tapAt(u1);
    await tester.pump();
    expect(find.text('U1 · MCU · 9 × 9 mm · 2 traces'), findsOneWidget);
    // Tapping empty board clears it (after the double-tap window).
    await tester.pump(const Duration(milliseconds: 400));
    await tester.tapAt(box.center + Offset(-120 * 0 + 0, 70));
    await tester.pump();
    expect(find.text('60 × 40 mm · 3 parts · 2 traces'), findsOneWidget);
  });

  testWidgets('two fingers pinch to zoom in', (tester) async {
    await _mount(tester, _pcb());
    expect(_button(tester, Icons.remove).onPressed, isNull);
    final centre =
        tester.getCenter(find.byKey(const ValueKey('pcb-geometry-canvas')));
    final a = await tester.startGesture(centre + const Offset(-20, 0));
    final b = await tester.startGesture(centre + const Offset(20, 0));
    await a.moveTo(centre + const Offset(-70, 0));
    await b.moveTo(centre + const Offset(70, 0));
    await a.up();
    await b.up();
    await tester.pumpAndSettle();
    expect(_button(tester, Icons.remove).onPressed, isNotNull);
    expect(tester.takeException(), isNull);
  });

  testWidgets('the layers menu toggles a layer', (tester) async {
    await _mount(tester, _pcb());
    await tester.tap(find.byTooltip('PCB layers'));
    await tester.pumpAndSettle();
    CheckedPopupMenuItem<String> item(String label) =>
        tester.widget<CheckedPopupMenuItem<String>>(find.ancestor(
            of: find.text(label),
            matching: find.byType(CheckedPopupMenuItem<String>)));
    expect(item('Labels').checked, isTrue);
    await tester.tap(find.text('Labels'));
    await tester.pumpAndSettle();
    await tester.tap(find.byTooltip('PCB layers'));
    await tester.pumpAndSettle();
    expect(item('Labels').checked, isFalse);
    expect(item('Top copper').checked, isTrue);
    expect(tester.takeException(), isNull);
  });

  testWidgets('expand opens the board full screen', (tester) async {
    await _mount(tester, _pcb());
    await tester.tap(find.byTooltip('Expand PCB viewer'));
    await tester.pumpAndSettle();
    expect(find.text('PCB · 60 × 40 mm'), findsOneWidget);
    expect(find.byTooltip('Expand PCB viewer'), findsNothing);
    expect(
        tester
            .getSize(find.byKey(const ValueKey('pcb-geometry-canvas')))
            .height,
        greaterThan(400));
    await tester.tap(find.byType(CloseButton));
    await tester.pumpAndSettle();
    expect(find.byTooltip('Expand PCB viewer'), findsOneWidget);
  });

  testWidgets('missing board dimensions fall back instead of drawing nothing',
      (tester) async {
    await _mount(
        tester, {'board': <String, dynamic>{}, 'components': <Object>[]});
    expect(find.text('Waiting for the board'), findsOneWidget);
    expect(find.byKey(const ValueKey('pcb-geometry-canvas')), findsNothing);
  });

  testWidgets('malformed components and traces are skipped, not fatal',
      (tester) async {
    await _mount(tester, {
      'board': {'width': 30, 'height': 20},
      'components': [
        {'id': 'bad', 'x': 'nope', 'y': 1, 'width': 2, 'height': 2},
        {'id': 'ok', 'x': 10, 'y': 10, 'width': 4, 'height': 3},
      ],
      'traces': [
        {
          'layer': 'F.Cu',
          'points': [
            {'x': 1, 'y': 1}
          ]
        },
        {
          'layer': 'F.Cu',
          'points': [
            {'x': 'a', 'y': 1},
            {'x': 2, 'y': 2}
          ]
        },
      ],
    });
    expect(find.text('30 × 20 mm · 1 part · 0 traces'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });
}
