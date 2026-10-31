import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:kleos_ui/data/models/production.dart';
import 'package:kleos_ui/data/repositories/production_repository.dart';
import 'package:kleos_ui/data/services/command_journal.dart';
import 'package:kleos_ui/data/services/production_api.dart';
import 'package:kleos_ui/state/production_store.dart';
import 'package:kleos_ui/ui/core/widgets.dart';
import 'package:kleos_ui/ui/features/production/production_app.dart';

class _Owner extends AccountController {
  @override
  Future<AccountSession?> build() async => AccountSession.fromJson(
      const {'subject': 'user', 'tenantId': 'org', 'role': 'owner'});
}

class _Viewer extends AccountController {
  @override
  Future<AccountSession?> build() async => AccountSession.fromJson(
      const {'subject': 'user', 'tenantId': 'org', 'role': 'viewer'});
}

class _Repository extends ProductionRepository {
  _Repository(super.api, super.journal) : super(organizationId: 'org');
  int revision = 17;
  int reads = 0;
  @override
  Future<Workspace> workspace(String id) async {
    reads++;
    return Workspace.fromJson({
      'project': {
        'id': id,
        'name': 'Watch',
        'brief': 'Saved source rebuild',
        'revision': revision,
      },
      'eventCursor': 1,
    });
  }
}

RunRecord _run(List<Map<String, dynamic>> jobs) => RunRecord({
      'id': 'original',
      'projectId': 'project',
      'kind': 'project.generate',
      'status': 'succeeded',
      'tasks': [
        {
          'lane': 'mechanics',
          'status': 'succeeded',
          'attempts': const <dynamic>[],
          'nativeJobs': jobs,
        }
      ]
    });

Map<String, dynamic> _job(String status, {String purpose = 'build'}) => {
      'id': '$purpose-$status',
      'purpose': purpose,
      'status': status,
      'toolVersion': 'qualified-tool-v2',
      'inputManifest': <String, dynamic>{},
      'hasLogs': false,
    };

void main() {
  testWidgets('native rebuild retries a durable receipt and opens its new run',
      (tester) async {
    String? disk;
    final journal = DurableCommandJournal('rebuild-test',
        read: (_) async => disk, write: (_, value) async => disk = value);
    final requests = <http.Request>[];
    final api = ProductionApi(client: MockClient((request) async {
      expect(request.url.path, '/api/v1/commands');
      expect(request.headers['X-Organization-Id'], 'org');
      expect(await journal.pending(), hasLength(1));
      requests.add(request);
      if (requests.length == 1) {
        return http.Response('{"message":"Temporary compute outage"}', 503);
      }
      return http.Response(
          jsonEncode({
            'commandId': 'command',
            'projectId': 'project',
            'runId': 'rebuilt',
            'revision': 18,
          }),
          202);
    }));
    final repository = _Repository(api, journal);
    final router = GoRouter(initialLocation: '/runs/original', routes: [
      GoRoute(
          path: '/runs/original',
          builder: (_, __) =>
              const Scaffold(body: ProductionRunPage(runId: 'original'))),
      GoRoute(
          path: '/runs/rebuilt',
          builder: (_, __) => const Scaffold(body: Text('New rebuild run'))),
    ]);
    await tester.pumpWidget(ProviderScope(overrides: [
      accountProvider.overrideWith(_Owner.new),
      productionRepositoryProvider.overrideWithValue(repository),
      runInspectorProvider('original')
          .overrideWith((ref) async => _run([_job('succeeded')])),
    ], child: MaterialApp.router(theme: kTheme(), routerConfig: router)));
    await tester.pumpAndSettle();
    final rebuild =
        find.byKey(const ValueKey('rebuild-native-build-succeeded'));
    await tester.ensureVisible(rebuild);
    expect(find.textContaining('No new AI generation.'), findsOneWidget);
    await tester.tap(rebuild);
    await tester.pumpAndSettle();
    expect(find.textContaining('Temporary compute outage'), findsOneWidget);
    expect(await journal.pending(), hasLength(1));
    repository.revision = 19;
    await tester.ensureVisible(rebuild);
    await tester.tap(rebuild);
    await tester.pumpAndSettle();
    expect(repository.reads, 2);
    expect(requests, hasLength(2));
    expect(requests.last.headers['Idempotency-Key'],
        requests.first.headers['Idempotency-Key']);
    expect(jsonDecode(requests.last.body), {
      'type': 'project.retryNative',
      'payload': {'projectId': 'project', 'jobId': 'build-succeeded'},
      'expectedRevision': 17,
    });
    expect(await journal.pending(), isEmpty);
    expect(find.text('New rebuild run'), findsOneWidget);
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox());
    router.dispose();
    api.close();
  });

  testWidgets('only completed build jobs offer rebuilds to project writers',
      (tester) async {
    final run = _run([
      for (final status in [
        'succeeded',
        'failed',
        'unsupported',
        'running',
        'cancelled'
      ])
        _job(status),
      _job('succeeded', purpose: 'validate'),
    ]);
    Widget app(bool writer) => ProviderScope(
            overrides: [
              accountProvider.overrideWith(writer ? _Owner.new : _Viewer.new),
              runInspectorProvider('original').overrideWith((ref) async => run),
            ],
            child: MaterialApp(
                theme: kTheme(),
                home: const Scaffold(
                    body: ProductionRunPage(runId: 'original'))));
    await tester.pumpWidget(app(true));
    await tester.pumpAndSettle();
    expect(find.text('Rebuild saved sources'), findsNWidgets(3));
    expect(find.byKey(const ValueKey('rebuild-native-build-running')),
        findsNothing);
    expect(find.byKey(const ValueKey('rebuild-native-validate-succeeded')),
        findsNothing);
    await tester.pumpWidget(const SizedBox());
    await tester.pumpWidget(app(false));
    await tester.pumpAndSettle();
    expect(find.text('Rebuild saved sources'), findsNothing);
    await tester.pumpWidget(const SizedBox());
  });
}
