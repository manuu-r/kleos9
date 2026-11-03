import 'dart:async';
import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:kleos_ui/data/models/json.dart';
import 'package:kleos_ui/data/services/production_api.dart';

const pairingBody = {
  'address': '192.168.0.50',
  'serial': 'PRINTER123',
  'accessCode': 'fixtureCode123',
  'modelId': 'BL-P001',
};
const interrupted =
    'Printer check interrupted. Refresh status to check whether it connected, or try again.';

Future<JsonMap> operation(ProductionApi api, String action) =>
    action == 'connect'
        ? api.connectLocalBambu(pairingBody, organizationId: 'org-a')
        : api.refreshLocalBambu(organizationId: 'org-a');

void main() {
  for (final action in ['connect', 'refresh']) {
    testWidgets('$action waits for a slower printer check beyond 20 seconds',
        (tester) async {
      var calls = 0;
      http.Request? observed;
      final client = MockClient((request) async {
        calls++;
        observed = request;
        await Future<void>.delayed(const Duration(seconds: 35));
        return http.Response(
            jsonEncode({'enabled': true, 'connected': true}), 200);
      });
      addTearDown(client.close);
      final api =
          ProductionApi(client: client, baseUrl: 'http://localhost/api/v1');
      JsonMap? result;
      Object? error;
      final pending = operation(api, action).then((value) {
        result = value;
      }, onError: (Object value) {
        error = value;
      });
      await tester.pump();
      expect(calls, 1);
      expect(observed!.url.path, '/api/v1/printers/bambu/local/$action');
      expect(observed!.headers['x-organization-id'], 'org-a');
      await tester.pump(const Duration(seconds: 21));
      expect(result, isNull);
      expect(error, isNull);
      await tester.pump(const Duration(seconds: 14));
      await pending;
      expect(result?['connected'], isTrue);
      expect(error, isNull);
      expect(calls, 1);
    });

    testWidgets(
        '$action times out after 60 seconds without retry or saved claim',
        (tester) async {
      var calls = 0;
      final response = Completer<http.Response>();
      final client = MockClient((_) {
        calls++;
        return response.future;
      });
      addTearDown(client.close);
      final api = ProductionApi(client: client);
      Object? error;
      final pending =
          operation(api, action).then<void>((_) {}, onError: (Object value) {
        error = value;
      });
      await tester.pump();
      await tester.pump(const Duration(seconds: 59));
      expect(error, isNull);
      await tester.pump(const Duration(seconds: 1));
      await pending;
      expect(error, isA<ProductionApiException>());
      expect((error as ProductionApiException).message, interrupted);
      expect(calls, 1);
      response.complete(http.Response('{"enabled":true}', 200));
      await tester.pump();
    });

    test(
        '$action network errors do not leak details or claim credentials saved',
        () async {
      var calls = 0;
      final client = MockClient((_) async {
        calls++;
        throw http.ClientException('fixtureCode123 must not appear');
      });
      addTearDown(client.close);
      await expectLater(
          operation(ProductionApi(client: client), action),
          throwsA(isA<ProductionApiException>()
              .having((e) => e.message, 'message', interrupted)));
      expect(calls, 1);
    });
  }

  testWidgets('ordinary API request retains its 20 second timeout',
      (tester) async {
    final response = Completer<http.Response>();
    final client = MockClient((_) => response.future);
    addTearDown(client.close);
    Object? error;
    final pending = ProductionApi(client: client)
        .get('/projects')
        .then<void>((_) {}, onError: (Object value) {
      error = value;
    });
    await tester.pump();
    await tester.pump(const Duration(seconds: 19));
    expect(error, isNull);
    await tester.pump(const Duration(seconds: 1));
    await pending;
    expect(error, isA<ProductionApiException>());
    response.complete(http.Response('{}', 200));
    await tester.pump();
  });
}
