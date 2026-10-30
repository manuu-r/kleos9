import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:kleos_ui/data/models/json.dart';
import 'package:kleos_ui/data/models/production.dart';
import 'package:kleos_ui/data/services/api_service.dart';
import 'package:kleos_ui/state/kleos_store.dart';
import 'package:kleos_ui/state/model_defaults.dart';
import 'package:kleos_ui/state/production_store.dart';
import 'package:kleos_ui/ui/core/theme.dart';
import 'package:kleos_ui/ui/features/portal_pages.dart';
import 'production_ui_test.dart' show TestAccount, testCatalog;

class Defaults extends NewBuildModelPolicy {
  static Map<String, ModelChoice>? saved;
  @override
  Future<Map<String, ModelChoice>> build() async => testCatalog.defaults;
  @override
  Future<void> save(Map<String, ModelChoice> policy) async {
    saved = policy;
    state = AsyncData(policy);
  }
}

void main() {
  testWidgets(
      'models live under Providers without replacing the service portal',
      (tester) async {
    tester.view.physicalSize = const Size(1280, 1600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    Defaults.saved = null;
    final commands = <JsonMap>[];
    final snapshot = <String, Object>{
      'revision': 1,
      'state': {
        'production': true,
        'providerSummary': {
          'name': 'Original Fabricator',
          'stats': <Object>[],
          'sources': <Object>[]
        },
        'providerJobs': <Object>[],
        'providerLevel': 'Listed',
        'projects': [
          {
            'id': 'p',
            'name': 'Real build',
            'production': true,
            'modelPolicy': testCatalog.defaults
                .map((lane, choice) => MapEntry(lane, choice.toJson())),
          }
        ],
      },
    };
    final api = ApiService(client: MockClient((request) async {
      if (request.method == 'POST') {
        commands.add(asMap(jsonDecode(request.body)));
        return http.Response(
            jsonEncode({
              'snapshot': snapshot,
              'result': {'projectId': 'p'}
            }),
            200);
      }
      return http.Response(jsonEncode(snapshot), 200);
    }));
    final container = ProviderContainer(overrides: [
      apiServiceProvider.overrideWithValue(api),
      pollingEnabledProvider.overrideWithValue(false),
      accountProvider.overrideWith(TestAccount.new),
      modelCatalogProvider.overrideWith((_) async => testCatalog),
      newBuildModelPolicyProvider.overrideWith(Defaults.new),
    ]);
    addTearDown(() {
      container.dispose();
      api.close();
    });
    container.listen(kleosProvider, (_, __) {});
    await container.read(kleosProvider).refresh();
    await tester.pumpWidget(UncontrolledProviderScope(
        container: container,
        child: MaterialApp(
            theme: kTheme(),
            home: Scaffold(
                body: SingleChildScrollView(
                    child: Consumer(
                        builder: (_, ref, __) => ProviderPage(
                            controller: ref.watch(kleosProvider))))))));
    await tester.pumpAndSettle();
    expect(find.text('Original Fabricator'), findsOneWidget);
    expect(find.text('Qualification level'), findsOneWidget);
    await tester.tap(find.text('AI models'));
    await tester.pumpAndSettle();
    expect(find.text('AI model providers'), findsOneWidget);
    expect(find.text('Muse'), findsWidgets);
    expect(find.text('Muse on Modal'), findsWidgets);
    await tester.tap(find.text('Use muse-spark-open for all lanes'));
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.text('Save model choices'));
    await tester.tap(find.text('Save model choices'));
    await tester.pumpAndSettle();
    expect(
        Defaults.saved!.values.every((choice) => choice.provider == 'modal'),
        true);
    expect(commands, isEmpty);
    await tester.ensureVisible(find.text('Services'));
    await tester.tap(find.text('Services'));
    await tester.pumpAndSettle();
    expect(find.text('Original Fabricator'), findsOneWidget);
    expect(find.text('Routed jobs'), findsOneWidget);
    await tester.pumpWidget(UncontrolledProviderScope(
        container: container,
        child: MaterialApp(
            theme: kTheme(),
            home: Scaffold(
                body: SingleChildScrollView(
                    child: Consumer(
                        builder: (_, ref, __) => ProviderPage(
                            controller: ref.watch(kleosProvider),
                            tab: 'models',
                            projectId: 'p')))))));
    await tester.pumpAndSettle();
    expect(find.text('AI model providers'), findsOneWidget);
    expect(find.text('Real build'), findsOneWidget);
    await tester.ensureVisible(find.text('Use muse-spark-open for all lanes'));
    await tester.tap(find.text('Use muse-spark-open for all lanes'));
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.text('Save model choices'));
    await tester.tap(find.text('Save model choices'));
    await tester.pumpAndSettle();
    expect(commands.single['type'], 'project.setModelPolicy');
    expect(asMap(commands.single['payload'])['projectId'], 'p');
    expect(
        asMap(asMap(commands.single['payload'])['modelPolicy'])
            .values
            .every((value) => asMap(value)['provider'] == 'modal'),
        true);
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox());
  });
}
