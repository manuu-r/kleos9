import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:kleos_ui/ui/features/production/bambu_local_connection.dart';
import 'package:kleos_ui/ui/core/widgets.dart';

Finder field(String label) => find.byWidgetPredicate(
    (w) => w is TextField && w.decoration?.labelText == label);
Future<void> enter(WidgetTester tester, String label, String value) async {
  await tester.ensureVisible(field(label));
  await tester.enterText(field(label), value);
  await tester.pump();
}

Future<void> tap(WidgetTester tester, String label) async {
  await tester.ensureVisible(find.text(label));
  await tester.tap(find.text(label));
  await tester.pumpAndSettle();
}

void main() {
  for (final width in [320.0, 390.0, 1000.0]) {
    testWidgets('discovery requires only access code to connect at $width',
        (tester) async {
      tester.view.physicalSize = Size(width, 1600);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.reset);
      final result = Completer<Map<String, dynamic>>();
      String? selected;
      Map<String, dynamic>? sent;
      await tester.pumpWidget(MaterialApp(
          theme: kTheme(),
          home: Scaffold(
              body: SingleChildScrollView(
                  child: BambuLocalConnectionForm(
                      modelId: null,
                      state: const {},
                      onDiscover: () => result.future,
                      onModelSelected: (model) => selected = model,
                      onConnect: (body) async {
                        sent = body;
                        return {'message': 'Connected'};
                      },
                      onRefresh: () async => {},
                      onForget: () async => {})))));
      expect(find.byType(TextField), findsOneWidget);
      await tester.tap(find.text('Find printers'));
      await tester.pump();
      expect(
          tester
              .widget<KButton>(find.widgetWithText(KButton, 'Find printers'))
              .loading,
          isTrue);
      result.complete({
        'devices': [
          {
            'address': '192.168.1.25',
            'serial': 'SERIAL123',
            'modelId': 'BL-P001',
            'name': 'My X1 Carbon',
            'firmwareVersion': '01.12.00.00'
          }
        ]
      });
      await tester.pumpAndSettle();
      await tap(tester, 'My X1 Carbon');
      expect(selected, 'BL-P001');
      expect(sent, isNull);
      expect(find.byType(TextField), findsOneWidget);
      await enter(tester, 'LAN access code', '12345678');
      await tap(tester, 'Connect');
      expect(sent, {
        'address': '192.168.1.25',
        'serial': 'SERIAL123',
        'modelId': 'BL-P001',
        'accessCode': '12345678',
        'firmwareVersion': '01.12.00.00'
      });
      expect(
          tester.widget<TextField>(field('LAN access code')).controller!.text,
          isEmpty);
      expect(tester.takeException(), isNull);
    });
  }
  testWidgets('discovery failure opens manual input and preserves typed code',
      (tester) async {
    await tester.pumpWidget(MaterialApp(
        home: Scaffold(
            body: SingleChildScrollView(
                child: BambuLocalConnectionForm(
                    modelId: 'N1',
                    state: const {},
                    onDiscover: () async => throw StateError('offline'),
                    onConnect: (_) async => {},
                    onRefresh: () async => {},
                    onForget: () async => {})))));
    await enter(tester, 'LAN access code', '12345678');
    await tap(tester, 'Find printers');
    expect(find.textContaining('Retry or enter the IP address manually'),
        findsOneWidget);
    expect(
        tester.widget<TextField>(field('Printer IP address')).enabled, isTrue);
    expect(tester.widget<TextField>(field('LAN access code')).controller!.text,
        '12345678');
  });
  testWidgets(
      'direct pairing is opt-in and requires firmware, not manual certificates',
      (tester) async {
    Map<String, dynamic>? sent;
    await tester.pumpWidget(MaterialApp(
        home: Scaffold(
            body: SingleChildScrollView(
                child: BambuLocalConnectionForm(
                    modelId: 'N1',
                    state: const {},
                    onConnect: (body) async {
                      sent = body;
                      return {'message': 'Paired'};
                    },
                    onRefresh: () async => {},
                    onForget: () async => {})))));
    await enter(tester, 'Printer IP address', '192.168.1.25');
    await enter(tester, 'Printer serial number', 'SERIAL123');
    await enter(tester, 'LAN access code', 'fixtureCode123');
    await tap(tester, 'Advanced · direct LAN printing');
    tester.widget<SwitchListTile>(find.byType(SwitchListTile)).onChanged!(true);
    await tester.pump();
    expect(
        tester
            .widget<KButton>(find.widgetWithText(KButton, 'Connect'))
            .onPressed,
        isNull);
    await enter(
        tester, 'Printer firmware version (from printer settings)', '01.02.03');
    await tap(tester, 'Connect');
    expect(sent?['firmwareVersion'], '01.02.03');
    expect(sent?['lanDeveloperMode'], isTrue);
    expect(sent?.containsKey('certificateSha256'), isFalse);
    expect(sent?.containsKey('ftpsCertificateSha256'), isFalse);
  });
  for (final model in ['N1', 'N2S', 'C12', 'BL-P001', 'C11']) {
    testWidgets('advanced direct pairing availability: $model', (tester) async {
      await tester.pumpWidget(MaterialApp(
          home: Scaffold(
              body: SingleChildScrollView(
                  child: BambuLocalConnectionForm(
                      modelId: model,
                      state: const {},
                      onConnect: (_) async => {},
                      onRefresh: () async => {},
                      onForget: () async => {})))));
      await tap(tester, 'Advanced · direct LAN printing');
      expect(
          tester
                  .widget<SwitchListTile>(find.byType(SwitchListTile))
                  .onChanged !=
              null,
          model != 'C11');
      expect(tester.takeException(), isNull);
    });
  }
}
