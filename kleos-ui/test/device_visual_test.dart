import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:kleos_ui/ui/core/device_visual.dart';

void main() {
  testWidgets('original vector illustration assembles without backend state',
      (tester) async {
    await tester.pumpWidget(const MaterialApp(
      home: Scaffold(
        body: Center(
          child: SizedBox(width: 380, height: 500, child: DeviceVisual()),
        ),
      ),
    ));
    await tester.runAsync(() async {
      await Future<void>.delayed(const Duration(milliseconds: 50));
    });
    await tester.pumpAndSettle();
    expect(
        find.text('Tap to assemble · 6 assemblies, 65 parts'), findsOneWidget);
    await tester.tap(find.byType(DeviceVisual));
    await tester.pumpAndSettle();
    expect(find.text('Tap to take apart'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('guide illustration renders on a narrow screen', (tester) async {
    await tester.binding.setSurfaceSize(const Size(320, 640));
    addTearDown(() => tester.binding.setSurfaceSize(null));
    await tester.pumpWidget(const MaterialApp(
      home: Scaffold(
        body: Padding(
          padding: EdgeInsets.all(16),
          child: DeviceVisual(interactive: false, guideStep: 3),
        ),
      ),
    ));
    await tester.pumpAndSettle();
    expect(find.byType(CustomPaint), findsWidgets);
    expect(tester.takeException(), isNull);
  });
}
