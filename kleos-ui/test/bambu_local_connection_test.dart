import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:kleos_ui/data/models/json.dart';
import 'package:kleos_ui/data/services/production_api.dart';
import 'package:kleos_ui/ui/core/widgets.dart';
import 'package:kleos_ui/ui/features/production/bambu_local_connection.dart';

const codeLabel = 'LAN access code';
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
    JsonMap state = const {'enabled': true},
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
                  state: state,
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
  await enter(tester, codeLabel, 'fixtureCode123');
}

void main() {
  for (final width in [320.0, 390.0, 1000.0]) {
    testWidgets(
        'simple connection fits $width and hides technical certificate fields',
        (tester) async {
      await mount(tester, width: width);
      expect(find.text('Connect your printer'), findsOneWidget);
      expect(tester.widget<TextField>(field(codeLabel)).obscureText, isTrue);
      expect(
          tester
              .widget<KButton>(find.widgetWithText(KButton, 'Connect'))
              .onPressed,
          isNull);
      expect(find.textContaining('certificate SHA-256'), findsNothing);
      expect(find.text('Refresh status'), findsNothing);
      expect(find.byType(SwitchListTile), findsNothing);
      expect(tester.takeException(), isNull);
    });
  }
  testWidgets(
      'connect delegates certificate validation and clears code; refresh and forget work',
      (tester) async {
    JsonMap? sent;
    var refreshed = 0, forgotten = 0;
    JsonMap connected() => {
          'connected': true,
          'paired': true,
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
    await tap(tester, 'Connect');
    expect(sent, {
      'address': '192.168.0.50',
      'serial': 'A1MINIFIXTURE',
      'accessCode': 'fixtureCode123',
      'modelId': 'N1'
    });
    expect(
        tester.widget<TextField>(field(codeLabel)).controller!.text, isEmpty);
    expect(find.text('Printer reports: idle'), findsOneWidget);
    await tap(tester, 'Refresh status');
    expect(refreshed, 1);
    await tap(tester, 'Forget connection');
    expect(forgotten, 1);
    expect(find.text('Refresh status'), findsNothing);
  });
  testWidgets(
      'unsuccessful response remains visible after mounting from saved state',
      (tester) async {
    await mount(tester, state: const {
      'enabled': true,
      'address': '192.168.0.50',
      'serial': 'A1MINIFIXTURE',
      'connected': false,
      'credentialsStored': false,
      'message':
          'The printer rejected the access code. Check the code on its screen.'
    });
    expect(find.textContaining('rejected the access code'), findsOneWidget);
    expect(find.text('Refresh status'), findsNothing);
  });
  testWidgets('stale status still shows paired and offers refresh without code',
      (tester) async {
    await mount(tester, state: const {
      'enabled': true,
      'address': '192.168.0.50',
      'serial': 'A1MINIFIXTURE',
      'connected': false,
      'paired': true,
      'credentialsStored': true,
      'status': 'stale',
      'message': 'Status is stale; choose Refresh status'
    });
    expect(find.text('Printer paired · refresh to get its latest status'),
        findsOneWidget);
    expect(
        tester
            .widget<KButton>(find.widgetWithText(KButton, 'Refresh status'))
            .onPressed,
        isNotNull);
    expect(find.text('Printer reports: unknown'), findsNothing);
  });
  testWidgets('server validation errors are shown and code is cleared',
      (tester) async {
    await mount(tester,
        connect: (_) async => throw const ProductionApiException(
            'Printer identity could not be verified',
            status: 422));
    await fill(tester);
    await tap(tester, 'Connect');
    expect(find.text('Printer identity could not be verified'), findsOneWidget);
    expect(
        tester.widget<TextField>(field(codeLabel)).controller!.text, isEmpty);
  });
  testWidgets('expired pairing explains why the code is needed again',
      (tester) async {
    await mount(tester, state: const {
      'enabled': true,
      'status': 'expired',
      'message': 'Local connection expired; re-enter the access code'
    });
    expect(find.text('Local connection expired; re-enter the access code'),
        findsOneWidget);
    expect(find.text('Refresh status'), findsNothing);
  });

  testWidgets('editing printer identity does not reuse the old paired status',
      (tester) async {
    await mount(tester, state: const {
      'address': '192.168.0.50',
      'serial': 'A1MINIFIXTURE',
      'connected': true,
      'paired': true,
      'credentialsStored': true,
      'observation': {'state': 'idle'},
      'message': 'Connected'
    });
    expect(find.text('Printer paired · connected'), findsOneWidget);
    await enter(tester, codeLabel, '12345678');
    await enter(tester, 'Printer IP address', '192.168.0.51');
    expect(
        tester.widget<TextField>(field(codeLabel)).controller!.text, isEmpty);
    expect(find.text('Printer paired · connected'), findsNothing);
    expect(find.text('Printer reports: idle'), findsNothing);
    expect(find.text('Refresh status'), findsNothing);
  });

  testWidgets('offline printer never appears connected', (tester) async {
    await mount(tester);
    await fill(tester);
    await tap(tester, 'Connect');
    expect(find.text('Printer unreachable'), findsOneWidget);
    expect(find.text('Refresh status'), findsNothing);
    expect(
        tester.widget<TextField>(field(codeLabel)).controller!.text, isEmpty);
  });
}
