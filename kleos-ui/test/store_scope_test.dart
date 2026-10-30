import 'dart:async';
import 'dart:convert';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:kleos_ui/data/services/api_service.dart';
import 'package:kleos_ui/state/kleos_store.dart';

Map<String, Object> snapshot(String organization) => {
      'revision': 1,
      'state': {'organization': organization},
    };

void main() {
  test('late previous-account reads cannot overwrite a new account', () async {
    final pending = Completer<http.Response>();
    final old = ApiService(client: MockClient((_) => pending.future));
    final next = ApiService(
        client: MockClient(
            (_) async => http.Response(jsonEncode(snapshot('new')), 200)));
    final container = ProviderContainer(overrides: [
      apiServiceProvider.overrideWithValue(old),
      pollingEnabledProvider.overrideWithValue(false),
    ]);
    addTearDown(() {
      container.dispose();
      old.close();
      next.close();
    });
    container.listen(kleosProvider, (_, __) {});
    final request = container.read(kleosProvider).refresh();
    container.updateOverrides([
      apiServiceProvider.overrideWithValue(next),
      pollingEnabledProvider.overrideWithValue(false)
    ]);
    await container.read(kleosProvider).refresh();
    pending.complete(http.Response(jsonEncode(snapshot('old')), 200));
    await request;
    expect(container.read(kleosProvider).state['organization'], 'new');
  });

  test('late previous-account command receipt cannot change new account',
      () async {
    final pending = Completer<http.Response>();
    final old = ApiService(client: MockClient((_) => pending.future));
    final next = ApiService(
        client: MockClient(
            (_) async => http.Response(jsonEncode(snapshot('new')), 200)));
    final container = ProviderContainer(overrides: [
      apiServiceProvider.overrideWithValue(old),
      pollingEnabledProvider.overrideWithValue(false),
    ]);
    addTearDown(() {
      container.dispose();
      old.close();
      next.close();
    });
    container.listen(kleosProvider, (_, __) {});
    final request = container.read(kleosProvider).execute('project.create');
    await Future<void>.delayed(Duration.zero);
    container.updateOverrides([
      apiServiceProvider.overrideWithValue(next),
      pollingEnabledProvider.overrideWithValue(false)
    ]);
    await container.read(kleosProvider).refresh();
    pending.complete(http.Response(
        jsonEncode({
          'snapshot': snapshot('old'),
          'result': {'projectId': 'old-project'}
        }),
        200));
    expect((await request).ok, false);
    expect(container.read(kleosProvider).state['organization'], 'new');
    expect(container.read(kleosProvider).pendingTypes, isEmpty);
  });
}
