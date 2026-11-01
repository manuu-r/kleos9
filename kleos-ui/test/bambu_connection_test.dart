import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:kleos_ui/data/models/json.dart';
import 'package:kleos_ui/data/services/api_service.dart';
import 'package:kleos_ui/state/kleos_store.dart';
import 'package:kleos_ui/ui/core/widgets.dart';
import 'package:kleos_ui/ui/features/connections_page.dart';
import 'package:kleos_ui/ui/features/production/bambu_connection.dart';

JsonMap connectionData({bool connected = false, bool stale = false}) => {
      'catalog': {
        'models': asMap(jsonDecode(File('../backend/kleos/bambu-models.json')
            .readAsStringSync()))['models'],
        'profiles': <JsonMap>[]
      },
      'connectors': {
        'connectors': [
          if (connected)
            {
              'telemetry': {
                'kind': 'bambu',
                'connected': true,
                'stale': stale,
                'profile': {'modelId': 'C12'}
              }
            }
        ]
      },
    };

Future<void> page(WidgetTester tester,
    {bool production = true,
    bool deepLink = false,
    Completer<JsonMap>? delayed}) async {
  final snapshot = <String, dynamic>{
    'revision': 1,
    'state': {
      'production': production,
      'connectors': [
        {
          'id': 'bambu',
          'name': 'Bambu Lab (LAN mode)',
          'kind': 'printer',
          'linked': false,
          'simulation': true,
          'capabilities': ['fab.print.fdm.home']
        },
        {
          'id': 'prusa',
          'name': 'Prusa Connect',
          'kind': 'printer',
          'simulation': true,
          'linked': false
        },
      ]
    },
    'demo': {'speed': 1}
  };
  final service = ApiService(client: MockClient((request) async {
    expectSync(request.method, 'GET',
        reason:
            'Real Bambu setup must not invoke simulated discovery or calibration');
    return http.Response(
        jsonEncode(delayed == null ? snapshot : await delayed.future), 200);
  }));
  final container = ProviderContainer(overrides: [
    apiServiceProvider.overrideWithValue(service),
    pollingEnabledProvider.overrideWithValue(false),
    bambuConnectionProvider.overrideWith((_) async => connectionData())
  ]);
  container.listen(kleosProvider, (_, __) {});
  final refresh = container.read(kleosProvider).refresh();
  if (delayed == null) await refresh;
  final router = GoRouter(
      initialLocation:
          '/connections?tab=printers${deepLink ? '&connect=bambu' : ''}',
      routes: [
        GoRoute(
            path: '/connections',
            builder: (_, state) => Consumer(
                builder: (_, ref, __) => Scaffold(
                    body: SingleChildScrollView(
                        child: ConnectionsPage(
                            controller: ref.watch(kleosProvider),
                            tab: 'printers',
                            connectId: state.uri.queryParameters['connect'])))))
      ]);
  await tester.pumpWidget(UncontrolledProviderScope(
      container: container,
      child: MaterialApp.router(theme: kTheme(), routerConfig: router)));
  await tester.pumpAndSettle();
  if (delayed != null) {
    delayed.complete(snapshot);
    await refresh;
    await tester.pumpAndSettle();
    expect(container.read(kleosProvider).error, isNull);
    expect(container.read(kleosProvider).state['production'], production);
    expect(router.routeInformationProvider.value.uri.queryParameters['connect'],
        deepLink ? 'bambu' : null);
  }
  addTearDown(() async {
    await tester.pumpWidget(const SizedBox.shrink());
    router.dispose();
    container.dispose();
    service.close();
  });
}

void main() {
  testWidgets(
      'production Bambu card opens the real setup instead of the demo wizard',
      (tester) async {
    await page(tester);
    expect(find.text('Bambu Lab (LAN mode)'), findsNothing);
    expect(find.text('Real adapter'), findsOneWidget);
    expect(find.textContaining('Slicing setup needed'), findsOneWidget);
    await tester.tap(find.text('Set up Bambu Lab'));
    await tester.pumpAndSettle();
    expect(find.byType(BambuConnectionDialog), findsOneWidget);
    expect(find.byType(ConnectorDialog), findsNothing);
    expect(find.text('Connect (simulated)'), findsNothing);
    expect(find.text('Local status bridge: not connected.'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets(
      'existing Bambu connect links open real setup after initial data loads',
      (tester) async {
    await page(tester, deepLink: true, delayed: Completer<JsonMap>());
    expect(find.byType(BambuConnectionDialog), findsOneWidget);
    expect(find.byType(ConnectorDialog), findsNothing);
    expect(tester.takeException(), isNull);
  });

  testWidgets('mock mode retains its explicitly simulated Bambu wizard',
      (tester) async {
    await page(tester, production: false, deepLink: true);
    expect(find.byType(ConnectorDialog), findsOneWidget);
    expect(find.text('Connect (simulated)'), findsOneWidget);
    expect(find.byType(BambuConnectionDialog), findsNothing);
  });

  for (final width in [320.0, 390.0, 1000.0]) {
    testWidgets(
        'real setup selects all models and passes the choice into preparation at $width',
        (tester) async {
      tester.view.physicalSize = Size(width, 1400);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.reset);
      await tester.pumpWidget(MaterialApp(
          theme: kTheme(),
          home: Scaffold(
              body: SingleChildScrollView(
                  padding: const EdgeInsets.all(24),
                  child: BambuConnectionControls(
                      data: connectionData(),
                      projects: [
                        {
                          'id': 'accepted',
                          'name': 'Accepted build',
                          'production': true,
                          'acceptedReleaseId': 'release'
                        },
                        {
                          'id': 'draft',
                          'name': 'Draft build',
                          'production': true
                        },
                        {
                          'id': 'demo',
                          'name': 'Demo build',
                          'simulation': true,
                          'acceptedReleaseId': 'fixture'
                        },
                      ],
                      onGuide: () async {},
                      preparation: (project, model) =>
                          Text('Prepare ${project['id']} with $model'))))));
      await tester.pumpAndSettle();
      final dropdown = tester.widget<DropdownButton<String>>(find.descendant(
          of: find.byKey(const ValueKey('bambu-setup-model-null')),
          matching: find.byType(DropdownButton<String>)));
      expect(dropdown.items!.length, 14);
      tester
          .widget<DropdownButtonFormField<String>>(
              find.byKey(const ValueKey('bambu-setup-model-null')))
          .onChanged!('C12');
      await tester.pump();
      expect(find.textContaining('no approved nozzle/material profile'),
          findsOneWidget);
      final builds = tester.widget<DropdownButton<String>>(find.descendant(
          of: find.byKey(const ValueKey('bambu-setup-project-null')),
          matching: find.byType(DropdownButton<String>)));
      expect(builds.items!.length, 1);
      tester
          .widget<DropdownButtonFormField<String>>(
              find.byKey(const ValueKey('bambu-setup-project-null')))
          .onChanged!('accepted');
      await tester.pump();
      expect(find.text('Prepare accepted with C12'), findsOneWidget);
      expect(tester.takeException(), isNull);
    });
  }

  testWidgets(
      'only a fresh real bridge can report connected status and auto-select a model',
      (tester) async {
    for (final stale in [true, false]) {
      await tester.pumpWidget(MaterialApp(
          theme: kTheme(),
          home: Scaffold(
              body: SingleChildScrollView(
                  child: BambuConnectionControls(
                      key: ValueKey(stale),
                      data: connectionData(connected: true, stale: stale),
                      projects: const [],
                      onGuide: () async {},
                      preparation: (_, __) => const SizedBox.shrink())))));
      await tester.pumpAndSettle();
      expect(
          find.byKey(ValueKey('bambu-setup-model-${stale ? 'null' : 'C12'}')),
          findsOneWidget);
      expect(
          find.text(stale
              ? 'Local status bridge: not connected.'
              : 'Local status bridge: connected (read-only).'),
          findsOneWidget);
    }
  });
}
