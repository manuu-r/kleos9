import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:kleos_ui/data/models/json.dart';
import 'package:kleos_ui/data/services/api_service.dart';
import 'package:kleos_ui/ui/core/widgets.dart';
import 'package:kleos_ui/ui/features/connections_page.dart';

import 'support.dart';

Future<void> openDialog(
    WidgetTester tester, TestKleos controller, JsonMap connector) async {
  await tester.pumpWidget(controller.scope(MaterialApp(
    theme: kTheme(),
    home: Scaffold(
        body: Builder(
            builder: (context) => TextButton(
                  onPressed: () => showDialog<bool>(
                      context: context,
                      builder: (_) => ConnectorDialog(
                          controller: controller.view, connector: connector)),
                  child: const Text('Open connection'),
                ))),
  )));
  await tester.tap(find.text('Open connection'));
  await tester.pumpAndSettle();
}

void main() {
  for (final printer in [true, false]) {
    testWidgets(
        '${printer ? 'printer' : 'service'} dialog opens without an inherited-scope lifecycle error',
        (tester) async {
      final connector = <String, dynamic>{
        'id': 'fixture',
        'kind': printer ? 'printer' : 'service',
        'name': 'Fixture connection',
        'integration': 'REST',
        'capabilities': ['printer.status']
      };
      final controller = TestKleos(ApiService(
          client: MockClient((_) async => http.Response(
              jsonEncode({
                'revision': 0,
                'state': {
                  'printerProfiles': {
                    'fixture': {'host': '192.168.1.25'}
                  }
                },
                'demo': {'speed': 1},
              }),
              200))));
      addTearDown(controller.dispose);
      await controller.refresh();
      await openDialog(tester, controller, connector);
      expect(tester.takeException(), isNull);
      expect(find.text('Connect Fixture connection'), findsOneWidget);
      if (printer) {
        expect(
            tester.widget<TextField>(find.byType(TextField)).controller!.text,
            '192.168.1.25');
      } else {
        expect(tester.widget<TextField>(find.byType(TextField)).obscureText,
            isTrue);
      }
      await tester.tap(find.byTooltip('Close'));
      await tester.pumpAndSettle();
      expect(find.byType(ConnectorDialog), findsNothing);
      await tester.pumpWidget(const SizedBox.shrink());
    });
  }

  testWidgets('printer discovery displays the latest provider profile',
      (tester) async {
    var revision = 0;
    final controller = TestKleos(ApiService(client: MockClient((request) async {
      if (request.method == 'POST') {
        expect(asMap(jsonDecode(request.body))['type'], 'connector.discover');
        revision++;
      }
      final snapshot = {
        'revision': revision,
        'state': {
          'printerProfiles': {
            'fixture': {
              'host': '192.168.1.25',
              'model': revision == 0 ? 'Saved model' : 'Newly discovered model',
              'buildVolume': '256 mm'
            }
          },
          'connectionRuns': {
            'fixture': {'phase': 'discover', 'progress': revision == 0 ? 0 : 3}
          },
        },
        'demo': {'speed': 1}
      };
      return http.Response(
          jsonEncode(
              request.method == 'GET' ? snapshot : {'snapshot': snapshot}),
          200);
    })));
    addTearDown(controller.dispose);
    await controller.refresh();
    await openDialog(tester, controller, {
      'id': 'fixture',
      'kind': 'printer',
      'name': 'Fixture printer',
      'capabilities': ['printer.status']
    });
    expect(tester.takeException(), isNull);
    await tester.tap(find.text('Connect (simulated)'));
    await tester.pumpAndSettle();
    expect(find.text('Newly discovered model'), findsOneWidget);
    expect(find.text('Saved model'), findsNothing);
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox.shrink());
  });
}
