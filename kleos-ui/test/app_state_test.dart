import 'dart:async';
import 'dart:convert';
import 'package:flutter_test/flutter_test.dart';
import 'package:kleos_ui/state/kleos_store.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:kleos_ui/data/models/json.dart';
import 'support.dart';
import 'package:kleos_ui/data/services/api_service.dart';

JsonMap snapshot(int revision) => {
      'revision': revision,
      'state': {
        'projects': [
          {'id': 'p1', 'answers': <String, dynamic>{}}
        ]
      },
      'demo': {'speed': 1}
    };
void main() {
  test(
      'controller deduplicates pending commands and preserves immutable snapshots',
      () async {
    final response = Completer<http.Response>();
    var calls = 0;
    final controller = TestKleos(ApiService(
        baseUrl: 'http://local.test/api',
        client: MockClient((request) async {
          if (request.method == 'GET') {
            return http.Response(jsonEncode(snapshot(1)), 200);
          }
          calls++;
          return response.future;
        })));
    await controller.refresh();
    final one =
        controller.execute('project.create', {'brief': 'A local device'});
    final two =
        controller.execute('project.create', {'brief': 'A local device'});
    expect(identical(one, two), isTrue);
    await Future<void>.delayed(Duration.zero);
    expect(calls, 1);
    expect(controller.isPending('project.create'), isTrue);
    response.complete(http.Response(
        jsonEncode({
          'snapshot': snapshot(2),
          'result': {'projectId': 'p2'}
        }),
        200));
    expect((await one).result['projectId'], 'p2');
    expect(controller.isPending('project.create'), isFalse);
    expect(() => asList(controller.state['projects']).add(<String, dynamic>{}),
        throwsUnsupportedError);
    controller.dispose();
  });
  test('late load response cannot replace a newer command snapshot', () async {
    final load = Completer<http.Response>();
    final repository = TestKleos(ApiService(
        baseUrl: 'http://local.test/api',
        client: MockClient((request) async {
          if (request.method == 'GET') return load.future;
          return http.Response(jsonEncode({'snapshot': snapshot(3)}), 200);
        })));
    final loading = repository.refresh();
    await repository.execute('agent.nextMessage', {});
    load.complete(http.Response(jsonEncode(snapshot(1)), 200));
    await loading;
    expect(repository.revision, 3);
    repository.dispose();
  });
  test(
      'uncertain command retries retain idempotency keys and errors retain data',
      () async {
    final keys = <String>[];
    final controller = TestKleos(ApiService(
        baseUrl: 'http://local.test/api',
        client: MockClient((request) async {
          if (request.method == 'GET') {
            return http.Response(jsonEncode(snapshot(1)), 200);
          }
          keys.add(request.headers['Idempotency-Key'] ??
              request.headers['idempotency-key'] ??
              '');
          if (keys.length == 1) throw http.ClientException('Offline');
          return http.Response(jsonEncode({'snapshot': snapshot(2)}), 200);
        })));
    await controller.refresh();
    expect(
        (await controller.execute('project.placeOrders', {'projectId': 'p1'}))
            .ok,
        isFalse);
    expect(controller.state['projects'], isNotEmpty);
    expect(controller.error, isNotNull);
    expect(
        (await controller.execute('project.placeOrders', {'projectId': 'p1'}))
            .ok,
        isTrue);
    expect(keys[0], isNotEmpty);
    expect(keys[0], keys[1]);
    expect(controller.error, isNull);
    controller.dispose();
  });

  test('identical polls publish nothing; changes keep unchanged slices',
      () async {
    var tick = 0;
    final store = TestKleos(ApiService(
        baseUrl: 'http://local.test/api',
        client: MockClient((request) async {
          tick++;
          final snap = snapshot(tick);
          if (tick >= 3) {
            (snap['state'] as Map)['other'] = tick;
          }
          return http.Response(jsonEncode(snap), 200);
        })));
    var publishes = 0;
    store.container.listen(kleosProvider, (_, __) => publishes++);
    await store.refresh();
    final first = store.state['projects'];
    await store.refresh();
    expect(publishes, 1);
    expect(identical(store.state['projects'], first), isTrue);
    await store.refresh();
    expect(publishes, 2);
    expect(identical(store.state['projects'], first), isTrue);
    store.dispose();
  });
}
