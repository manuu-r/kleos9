import 'dart:async';
import 'package:flutter/widgets.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:kleos_ui/data/models/json.dart';
import 'package:kleos_ui/data/models/production.dart';
import 'package:kleos_ui/data/services/production_api.dart';
import 'package:kleos_ui/data/services/production_legacy_api_service.dart';
import 'package:kleos_ui/state/production_store.dart';
import 'production_legacy_adapter_test.dart' as fixture;

class LiveRepository extends fixture.Repository {
  Completer<Workspace>? held;
  int? failureStatus;
  @override
  Future<Workspace> workspace(String id) {
    if (held != null) {
      reads.add(id);
      return held!.future;
    }
    if (failureStatus != null) {
      reads.add(id);
      throw ProductionApiException('Access ended', status: failureStatus!);
    }
    return super.workspace(id);
  }
}

void main() {
  testWidgets('legacy silent connection polls snapshot and cursor',
      (tester) async {
    final repo = LiveRepository();
    final adapter = ProductionLegacyApiService(repo);
    final updates = <JsonMap>[];
    final sub = adapter.updates.listen(updates.add);
    await adapter.setActiveProject('p');
    repo.revision = 9;
    await tester.pump(const Duration(seconds: 15));
    await tester.pump();
    expect(repo.reads, ['p', 'p']);
    expect(repo.cursors, [4, 9]);
    expect(asMap(updates.last['state'])['liveConnected'], true);
    adapter.close();
    await tester.runAsync(sub.cancel);
    repo.close();
    await tester.pump(const Duration(seconds: 30));
    expect(repo.reads, ['p', 'p']);
  });
  testWidgets('legacy gap resyncs and duplicate activity stays unique',
      (tester) async {
    final repo = LiveRepository();
    final adapter = ProductionLegacyApiService(repo);
    final updates = <JsonMap>[];
    final sub = adapter.updates.listen(updates.add);
    await adapter.setActiveProject('p');
    repo.revision = 8;
    final event = ProjectEvent.fromJson({
      'sequence': 8,
      'type': 'agent.progress',
      'data': <String, dynamic>{},
      'createdAt': 'now'
    });
    repo.controllers.single.add(event);
    repo.controllers.single.add(event);
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 120));
    await tester.pump();
    expect(repo.cursors, [4, 8]);
    expect(
        asList(asMaps(asMap(updates.last['state'])['projects'])
            .single['activity']),
        hasLength(1));
    adapter.close();
    await tester.runAsync(sub.cancel);
    repo.close();
  });
  testWidgets('legacy deleted snapshot stops all retry loops', (tester) async {
    final repo = LiveRepository();
    final adapter = ProductionLegacyApiService(repo);
    await adapter.setActiveProject('p');
    repo.failureStatus = 404;
    await tester.pump(const Duration(seconds: 15));
    await tester.pump();
    final count = repo.reads.length;
    await tester.pump(const Duration(minutes: 1));
    expect(repo.reads.length, count);
    adapter.close();
    repo.close();
  });
  testWidgets('legacy switched project rejects old in-flight response',
      (tester) async {
    final repo = LiveRepository();
    final adapter = ProductionLegacyApiService(repo);
    final updates = <JsonMap>[];
    final sub = adapter.updates.listen(updates.add);
    await adapter.setActiveProject('p');
    final old = await repo.workspace('p');
    final held = Completer<Workspace>();
    repo.held = held;
    await tester.pump(const Duration(seconds: 15));
    repo.held = null;
    await adapter.setActiveProject('q');
    await tester.pump();
    final count = updates.length;
    held.complete(old);
    await tester.pump();
    expect(updates.length, count);
    adapter.close();
    await tester.runAsync(sub.cancel);
    repo.close();
  });
  testWidgets('controller polls while foreground and resumes after background',
      (tester) async {
    final repo = LiveRepository();
    final container = ProviderContainer(
        overrides: [productionRepositoryProvider.overrideWithValue(repo)]);
    final provider = productionProjectProvider('p');
    final sub = container.listen(provider, (_, __) {});
    await container.read(provider.future);
    await tester.pump();
    repo.revision = 8;
    await tester.pump(const Duration(seconds: 15));
    await tester.pump();
    expect(container.read(provider).requireValue.workspace.eventCursor, 8);
    expect(repo.cursors.last, 8);
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.inactive);
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.hidden);
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.paused);
    final count = repo.reads.length;
    await tester.pump(const Duration(seconds: 30));
    expect(repo.reads.length, count);
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.hidden);
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.inactive);
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.resumed);
    await tester.pump();
    expect(repo.reads.length, greaterThan(count));
    sub.close();
    container.dispose();
    repo.close();
  });
  testWidgets('controller account switch fences delayed old refresh',
      (tester) async {
    final first = LiveRepository();
    final second = LiveRepository()..revision = 20;
    final container = ProviderContainer(
        overrides: [productionRepositoryProvider.overrideWithValue(first)]);
    final provider = productionProjectProvider('p');
    final sub = container.listen(provider, (_, __) {});
    final initial = await container.read(provider.future);
    await tester.pump();
    first.held = Completer<Workspace>();
    final refresh = container.read(provider.notifier).refresh();
    container.updateOverrides(
        [productionRepositoryProvider.overrideWithValue(second)]);
    await container.read(provider.future);
    await tester.pump();
    first.held!.complete(initial.workspace);
    await refresh;
    await tester.pump();
    expect(container.read(provider).requireValue.workspace.eventCursor, 20);
    sub.close();
    container.dispose();
    first.close();
    second.close();
  });
  testWidgets('legacy expired cursor recovers from a fresh snapshot',
      (tester) async {
    final repo = LiveRepository();
    final adapter = ProductionLegacyApiService(repo);
    await adapter.setActiveProject('p');
    repo.revision = 12;
    repo.controllers.single
        .addError(const ProductionApiException('Cursor expired', status: 410));
    await tester.pump();
    await tester.pump(const Duration(seconds: 2));
    await tester.pump();
    expect(repo.cursors, [4, 12]);
    adapter.close();
    repo.close();
  });

  testWidgets(
      'controller forbidden snapshot stops polling and disposal leaves no reads',
      (tester) async {
    final repo = LiveRepository();
    final container = ProviderContainer(
        overrides: [productionRepositoryProvider.overrideWithValue(repo)]);
    final provider = productionProjectProvider('p');
    final sub = container.listen(provider, (_, __) {});
    await container.read(provider.future);
    await tester.pump();
    repo.failureStatus = 403;
    await tester.pump(const Duration(seconds: 15));
    await tester.pump();
    expect(container.read(provider).requireValue.connected, false);
    final count = repo.reads.length;
    await tester.pump(const Duration(minutes: 1));
    expect(repo.reads.length, count);
    sub.close();
    container.dispose();
    repo.close();
    await tester.pump(const Duration(minutes: 1));
    expect(repo.reads.length, count);
  });
}
