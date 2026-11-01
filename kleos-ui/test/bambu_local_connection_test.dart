import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:kleos_ui/data/models/json.dart';
import 'package:kleos_ui/ui/core/widgets.dart';
import 'package:kleos_ui/ui/features/production/bambu_local_connection.dart';

const codeLabel = 'LAN access code (kept on this computer)';
const pinLabel = 'Independently verified certificate SHA-256';

Finder field(String label) => find.byWidgetPredicate(
    (w) => w is TextField && w.decoration?.labelText == label);
Future<void> enter(WidgetTester tester, String label, String text) async {
  await tester.ensureVisible(field(label));
  await tester.enterText(field(label), text);
  await tester.pump();
}

Future<void> tap(WidgetTester tester, String label) async {
  await tester.ensureVisible(find.text(label));
  await tester.tap(find.text(label));
  await tester.pumpAndSettle();
}

Future<void> mount(WidgetTester tester,
    {double width = 390,
    Future<JsonMap> Function(JsonMap)? inspect,
    Future<JsonMap> Function(JsonMap)? connect,
    Future<JsonMap> Function()? refresh,
    Future<JsonMap> Function()? forget}) async {
  tester.view.physicalSize = Size(width, 1600);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(MaterialApp(
      theme: kTheme(),
      home: Scaffold(
          body: SingleChildScrollView(
              padding: const EdgeInsets.all(20),
              child: BambuLocalConnectionForm(
                  modelId: 'N1',
                  state: const {'enabled': true},
                  onCertificate: inspect ??
                      (_) async =>
                          {'certificateSha256': 'a' * 64, 'verified': false},
                  onConnect: connect ??
                      (_) async => {
                            'connected': false,
                            'message': 'Printer unreachable'
                          },
                  onRefresh: refresh ?? () async => {},
                  onForget: forget ?? () async => {})))));
  await tester.pumpAndSettle();
}

Future<void> fill(WidgetTester tester) async {
  await enter(tester, 'Printer IP address', '192.168.0.50');
  await enter(tester, 'Printer serial number', 'A1MINIFIXTURE');
  await enter(tester, pinLabel, 'a' * 64);
  tester
      .widget<CheckboxListTile>(find.byType(CheckboxListTile))
      .onChanged!(true);
  await tester.pump();
  await enter(tester, codeLabel, 'fixtureCode123');
}

void main() {
  for (final width in [320.0, 390.0, 1000.0]) {
    testWidgets(
        'local read-only form fits $width pixels without assuming a printer is connected',
        (tester) async {
      await mount(tester, width: width);
      expect(find.text('Connect locally · read-only'), findsOneWidget);
      expect(tester.widget<TextField>(field(codeLabel)).obscureText, isTrue);
      expect(
          tester
              .widget<KButton>(
                  find.widgetWithText(KButton, 'Test read-only connection'))
              .onPressed,
          isNull);
      expect(find.text('Refresh status'), findsNothing);
      expect(tester.takeException(), isNull);
    });
  }

  testWidgets(
      'inspection sends no access code and never trusts a fingerprint automatically',
      (tester) async {
    JsonMap? sent;
    await mount(tester, inspect: (body) async {
      sent = body;
      return {'certificateSha256': 'a' * 64, 'verified': false};
    });
    await enter(tester, 'Printer IP address', '192.168.0.50');
    await enter(tester, codeLabel, 'fixtureCode123');
    await tap(tester, 'Inspect certificate (no access code sent)');
    expect(sent, {'address': '192.168.0.50'});
    expect(tester.widget<TextField>(field(pinLabel)).controller!.text, isEmpty);
    expect(
        tester.widget<TextField>(field(codeLabel)).controller!.text, isEmpty);
    expect(tester.widget<CheckboxListTile>(find.byType(CheckboxListTile)).value,
        isFalse);
  });

  testWidgets(
      'explicit A1 mini connection clears the code and supports refresh and forget',
      (tester) async {
    JsonMap? sent;
    var refreshed = 0, forgotten = 0;
    JsonMap connected() => {
          'connected': true,
          'credentialsStored': true,
          'address': '192.168.0.50',
          'message': 'Read-only status received',
          'observation': {'state': 'idle'}
        };
    await mount(tester, connect: (body) async {
      sent = body;
      return connected();
    }, refresh: () async {
      refreshed++;
      return connected();
    }, forget: () async {
      forgotten++;
      return {
        'enabled': true,
        'connected': false,
        'message': 'Connection forgotten'
      };
    });
    await fill(tester);
    await tap(tester, 'Test read-only connection');
    expect(sent?['modelId'], 'N1');
    expect(sent?['certificateVerified'], isTrue);
    expect(sent?.containsKey('command'), isFalse);
    expect(
        tester.widget<TextField>(field(codeLabel)).controller!.text, isEmpty);
    expect(find.text('Printer reports: idle'), findsOneWidget);
    await tap(tester, 'Refresh status');
    expect(refreshed, 1);
    await tap(tester, 'Forget connection');
    expect(forgotten, 1);
    expect(find.text('Refresh status'), findsNothing);
  });

  testWidgets('changing the endpoint requires certificate verification again',
      (tester) async {
    await mount(tester);
    await fill(tester);
    await enter(tester, 'Printer IP address', '192.168.0.51');
    expect(tester.widget<CheckboxListTile>(find.byType(CheckboxListTile)).value,
        isFalse);
    expect(
        tester
            .widget<KButton>(
                find.widgetWithText(KButton, 'Test read-only connection'))
            .onPressed,
        isNull);
  });

  testWidgets(
      'offline printer stays disconnected and the access code is cleared',
      (tester) async {
    await mount(tester);
    await fill(tester);
    await tap(tester, 'Test read-only connection');
    expect(find.text('Printer unreachable'), findsOneWidget);
    expect(find.text('Refresh status'), findsNothing);
    expect(
        tester.widget<TextField>(field(codeLabel)).controller!.text, isEmpty);
  });
}
