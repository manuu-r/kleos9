import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:kleos_watch_companion/ble_controller.dart';
import 'package:kleos_watch_companion/main.dart';

import 'ble_controller_test.dart' show FakeBle, MemorySettings;

void main() {
  testWidgets(
    'preview stays disconnected, renders no sample values and disables scan',
    (tester) async {
      final transport = FakeBle(supported: false);
      final controller = BleController(transport, MemorySettings());
      await tester.pumpWidget(WatchCompanion(controller: controller));
      await tester.pumpAndSettle();
      expect(find.textContaining('Web preview'), findsOneWidget);
      expect(find.text('Battery: —'), findsOneWidget);
      expect(find.text('Steps: —'), findsOneWidget);
      expect(find.text('Heart rate: 72 bpm'), findsNothing);
      final scan = tester.widget<FilledButton>(
        find.byKey(const Key('scan-button')),
      );
      expect(scan.onPressed, isNull);
      expect(transport.prepares + transport.scanCalls, 0);
      await tester.enterText(
        find.byKey(const Key('service-uuid')),
        'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
      );
      await tester.ensureVisible(find.text('Save configuration'));
      await tester.tap(find.text('Save configuration'));
      await tester.pumpAndSettle();
      expect(
        controller.config.serviceUuid,
        'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
      );
      await tester.pumpWidget(const SizedBox.shrink());
      controller.dispose();
      await transport.close();
    },
  );
}
