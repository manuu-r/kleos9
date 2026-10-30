import 'dart:async';
import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:kleos_ui/data/models/json.dart';
import 'package:kleos_ui/data/models/production.dart';
import 'package:kleos_ui/data/repositories/production_repository.dart';
import 'package:kleos_ui/data/services/command_journal.dart';
import 'package:kleos_ui/data/services/production_api.dart';
import 'package:kleos_ui/state/production_store.dart';
import 'package:kleos_ui/ui/core/widgets.dart';
import 'package:kleos_ui/ui/features/production/production_app.dart';

const choices = [
  ModelChoice('meta', 'muse-spark-1.3'),
  ModelChoice('google', 'gemini-3.8-flash')
];
final testCatalog = ModelCatalog([
  for (final choice in choices)
    CatalogModel(
        choice, choice.provider != 'meta', engineeringLanes.keys.toList()),
], {
  for (final lane in engineeringLanes.keys) lane: choices.first
});

Workspace snapshot(
        {int revision = 1,
        int cursor = 1,
        String status = 'draft',
        int stage = 0,
        JsonMap state = const {},
        List<JsonMap> runs = const []}) =>
    Workspace.fromJson({
      'project': {
        'id': 'p',
        'name': 'Watch',
        'brief': 'A custom watch',
        'revision': revision,
        'engineeringRevision': 1,
        'status': status,
        'stage': stage,
        'state': state,
        'modelPolicy':
            testCatalog.defaults.map((k, v) => MapEntry(k, v.toJson())),
        'budget': {'allowanceUsd': 20},
        'interfaces':
            stage >= 4 ? {'battery': 'qualified'} : <String, dynamic>{}
      },
      'eventCursor': cursor,
      'runs': runs,
    });

class TestAccount extends AccountController {
  @override
  Future<AccountSession?> build() async => AccountSession.fromJson(
      {'subject': 'user', 'tenantId': 'org', 'role': 'owner'});
}

class TestRepository extends ProductionRepository {
  TestRepository(this.current)
      : super(
            ProductionApi(),
            DurableCommandJournal('test',
                read: (_) async => null, write: (_, __) async {}));
  Workspace current;
  final streams = <StreamController<ProjectEvent>>[];
  final cursors = <int>[];
  final commands = <JsonMap>[];
  @override
  Future<Workspace> workspace(String id) async => current;
  @override
  Future<ModelCatalog> catalog() async => testCatalog;
  @override
  Stream<ProjectEvent> events(String id, int cursor,
      {void Function()? onConnected}) {
    cursors.add(cursor);
    final stream = StreamController<ProjectEvent>();
    onConnected?.call();
    streams.add(stream);
    return stream.stream;
  }

  @override
  Future<AcceptedCommand> command(String type, JsonMap payload,
      {int? revision}) async {
    commands.add({'type': type, 'payload': payload, 'revision': revision});
    return AcceptedCommand.fromJson(
        {'commandId': 'c', 'projectId': 'p', 'revision': revision ?? 1});
  }

  void dispose() {
    for (final stream in streams) {
      unawaited(stream.close());
    }
    api.close();
  }
}

Widget scope(TestRepository repository, Widget page) =>
    ProviderScope(overrides: [
      productionRepositoryProvider.overrideWithValue(repository),
      accountProvider.overrideWith(TestAccount.new),
    ], child: MaterialApp(theme: kTheme(), home: Scaffold(body: page)));

void main() {
  testWidgets(
      'phone workspace clears stale keyboard space but resizes for editing',
      (tester) async {
    tester.view.physicalSize = const Size(390, 844);
    tester.view.devicePixelRatio = 1;
    tester.view.viewInsets = FakeViewPadding(bottom: 300);
    addTearDown(tester.view.reset);
    final repository = TestRepository(snapshot());
    final container = ProviderContainer(overrides: [
      productionRepositoryProvider.overrideWithValue(repository),
      accountProvider.overrideWith(TestAccount.new),
    ]);
    final router = container.read(productionRouterProvider);
    router.go('/projects/p');
    await tester.pumpWidget(UncontrolledProviderScope(
        container: container, child: const ProductionApp()));
    await tester.pumpAndSettle();
    final page = find.byKey(const ValueKey('production-page-scroll'));
    expect(tester.getRect(page).bottom, 844);

    router.go('/new');
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('production-project-brief')));
    await tester.pumpAndSettle();
    expect(tester.getRect(page).bottom, 544);
    FocusManager.instance.primaryFocus?.unfocus();
    await tester.pumpAndSettle();
    expect(tester.getRect(page).bottom, 844);

    tester.view.viewInsets = FakeViewPadding();
    tester.view.physicalSize = const Size(844, 390);
    await tester.pumpAndSettle();
    expect(tester.getRect(page).bottom, 390);
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox());
    container.dispose();
    repository.dispose();
  });

  testWidgets('phone swipes cross long selectable engineering summaries',
      (tester) async {
    tester.view.physicalSize = const Size(390, 844);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    final repository = TestRepository(snapshot(stage: 4, state: {
      'proposal': {
        'summary': List.filled(8,
                'The proposed enclosure preserves the accepted dimensions and interface. ')
            .join(),
      }
    }));
    await tester.pumpWidget(
        scope(repository, const ProductionWorkspacePage(projectId: 'p')));
    await tester.pumpAndSettle();
    final page = find.byKey(const ValueKey('production-page-scroll'));
    final settings = find.text('AI provider settings');
    for (var i = 0; i < 8 && settings.hitTestable().evaluate().isEmpty; i++) {
      await tester.drag(page, const Offset(0, -250));
      await tester.pumpAndSettle();
    }
    expect(settings.hitTestable(), findsOneWidget);
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox());
    repository.dispose();
  });

  testWidgets('provider estimates stay separate from conservative debits',
      (tester) async {
    final run = RunRecord({
      'id': 'r',
      'kind': 'project.research',
      'status': 'succeeded',
      'projectId': 'p',
      'tasks': [
        {
          'lane': 'research',
          'status': 'succeeded',
          'nativeJobs': <dynamic>[],
          'attempts': [
            {
              'number': 1,
              'status': 'succeeded',
              'provider': 'google',
              'model': 'gemini-3.8-flash',
              'calls': [
                {
                  'operation': 'generate',
                  'status': 'succeeded',
                  'estimatedUsd': 1.0,
                  'usage': {'estimatedUsd': .0031, 'allowanceDebitUsd': 1.0}
                },
                {
                  'operation': 'generate',
                  'status': 'succeeded',
                  'estimatedUsd': 1.0,
                  'usage': {'allowanceDebitUsd': 1.0}
                },
              ]
            }
          ]
        }
      ]
    });
    await tester.pumpWidget(ProviderScope(
        overrides: [
          accountProvider.overrideWith(TestAccount.new),
          runInspectorProvider('r').overrideWith((ref) async => run),
        ],
        child: MaterialApp(
            theme: kTheme(),
            home: const Scaffold(body: ProductionRunPage(runId: 'r')))));
    await tester.pumpAndSettle();
    expect(find.text('Allowance debit: \$1.00'), findsNWidgets(2));
    expect(find.text('Provider estimate: \$0.0031'), findsOneWidget);
    expect(find.text('Provider estimate: Unavailable'), findsOneWidget);
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('run inspector exposes native limitations and authorized logs',
      (tester) async {
    var fetched = false;
    final api = ProductionApi(client: MockClient((request) async {
      expect(request.url.path, '/api/v1/native-jobs/job/logs');
      fetched = true;
      return http.Response(
          jsonEncode({'logs': 'Compiler exit 1; redacted'}), 200);
    }));
    final run = RunRecord({
      'id': 'r',
      'kind': 'project.generate',
      'status': 'blocked',
      'projectId': 'p',
      'tasks': [
        {
          'lane': 'firmware',
          'status': 'blocked',
          'attempts': <dynamic>[],
          'nativeJobs': [
            {
              'id': 'job',
              'purpose': 'build',
              'status': 'unsupported',
              'hasLogs': true,
              'toolVersion': 'zephyr-4',
              'inputManifest': <String, dynamic>{},
              'limitations': ['Peripheral simulation is unsupported']
            }
          ]
        }
      ]
    });
    await tester.pumpWidget(ProviderScope(
        overrides: [
          productionApiProvider.overrideWithValue(api),
          accountProvider.overrideWith(TestAccount.new),
          runInspectorProvider('r').overrideWith((ref) async => run),
        ],
        child: MaterialApp(
            theme: kTheme(),
            home: const Scaffold(body: ProductionRunPage(runId: 'r')))));
    await tester.pumpAndSettle();
    expect(find.text('Peripheral simulation is unsupported'), findsOneWidget);
    final logs = find.widgetWithText(KButton, 'View redacted engineering logs');
    await tester.scrollUntilVisible(logs, 150,
        scrollable: find.byType(Scrollable).first);
    await tester.pumpAndSettle();
    await tester.tap(logs);
    await tester.pumpAndSettle();
    expect(fetched, true);
    expect(find.text('Engineering logs'), findsOneWidget);
    await tester.pumpWidget(const SizedBox());
    api.close();
  });

  testWidgets('all seven lanes switch providers independently on a phone',
      (tester) async {
    tester.view.physicalSize = const Size(390, 844);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    var selected = {...testCatalog.defaults};
    await tester.pumpWidget(MaterialApp(
        theme: kTheme(),
        home: Scaffold(
            body: SingleChildScrollView(
                child: StatefulBuilder(
                    builder: (context, update) => ModelPolicyEditor(
                        catalog: testCatalog,
                        policy: selected,
                        onChanged: (value) =>
                            update(() => selected = value)))))));
    await tester.pumpAndSettle();
    expect(find.byType(DropdownButtonFormField<String>), findsNWidgets(7));
    for (final entry in [
      (0, 'Gemini · gemini-3.8-flash', 'google'),
      (1, 'Gemini · gemini-3.8-flash', 'google')
    ]) {
      final picker = find.byType(DropdownButtonFormField<String>).at(entry.$1);
      await tester.ensureVisible(picker);
      await tester.tap(picker);
      await tester.pumpAndSettle();
      await tester.tap(find.text(entry.$2).last);
      await tester.pumpAndSettle();
      expect(selected[engineeringLanes.keys.elementAt(entry.$1)]!.provider,
          entry.$3);
    }
    expect(selected['mechanics']!.provider, 'meta');
    expect(find.textContaining('Not connected yet.'), findsWidgets);
    expect(tester.takeException(), isNull);
  });

  testWidgets('workspace submits lifecycle commands with the current revision',
      (tester) async {
    final repository = TestRepository(snapshot(revision: 9));
    await tester.pumpWidget(
        scope(repository, const ProductionWorkspacePage(projectId: 'p')));
    await tester.pumpAndSettle();
    await tester.tap(find.widgetWithText(KButton, 'Research the concept'));
    await tester.pumpAndSettle();
    expect(repository.commands.single, {
      'type': 'project.research',
      'payload': {'projectId': 'p'},
      'revision': 9
    });
    expect(
        find.text(
            'Real design and digital checks · Simulated physical fulfillment'),
        findsOneWidget);
    await tester.pumpWidget(const SizedBox());
    repository.dispose();
  });

  testWidgets(
      'blocked model reason remains visible and retry targets the actual run',
      (tester) async {
    tester.view.physicalSize = const Size(320, 568);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    final repository = TestRepository(snapshot(status: 'blocked', runs: [
      {
        'id': 'r',
        'kind': 'project.research',
        'status': 'blocked',
        'blocker': 'Muse is not configured',
      }
    ]));
    await tester.pumpWidget(
        scope(repository, const ProductionWorkspacePage(projectId: 'p')));
    await tester.pumpAndSettle();
    expect(find.text('Muse is not configured'), findsWidgets);
    final retry = find.widgetWithText(KButton, 'Retry last run');
    await tester.scrollUntilVisible(retry, 200);
    await tester.pumpAndSettle();
    await tester.tap(retry);
    await tester.pumpAndSettle();
    expect(repository.commands.single['payload'],
        {'projectId': 'p', 'runId': 'r'});
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox());
    repository.dispose();
  });

  testWidgets('battery revisions submit finite dimensions in the API axes',
      (tester) async {
    final repository = TestRepository(snapshot(revision: 9));
    await tester.pumpWidget(
        scope(repository, const ProductionWorkspacePage(projectId: 'p')));
    await tester.pumpAndSettle();
    final settings = find.text('Revise battery requirements');
    await tester.scrollUntilVisible(settings, 250);
    await tester.tap(settings);
    await tester.pumpAndSettle();
    Finder field(String label) => find.byWidgetPredicate((widget) =>
        widget is TextField && widget.decoration?.labelText == label);
    for (final entry in {
      'Capacity (mAh)': '200',
      'Width (mm)': '20',
      'Length (mm)': '30',
      'Height (mm)': '4',
    }.entries) {
      await tester.ensureVisible(field(entry.key));
      await tester.enterText(field(entry.key), entry.value);
    }
    await tester.pumpAndSettle();
    final apply = find.widgetWithText(KButton, 'Apply battery revision');
    await tester.enterText(field('Width (mm)'), 'Infinity');
    await tester.pumpAndSettle();
    expect(tester.widget<KButton>(apply).onPressed, isNull);
    await tester.enterText(field('Width (mm)'), '20');
    await tester.ensureVisible(apply);
    await tester.pumpAndSettle();
    await tester.tap(apply);
    await tester.pumpAndSettle();
    expect(repository.commands.single, {
      'type': 'project.swapBattery',
      'payload': {
        'projectId': 'p',
        'capacityMah': 200.0,
        'dimensionsMm': {'x': 20.0, 'y': 30.0, 'z': 4.0}
      },
      'revision': 9
    });
    await tester.pumpWidget(const SizedBox());
    repository.dispose();
  });

  testWidgets('proposed scope is reviewable before concept confirmation',
      (tester) async {
    final repository = TestRepository(snapshot(stage: 2, state: {
      'proposal': {
        'summary': 'A watch with a bounded digital validation scope',
        'requirements': {
          'validationProfile': 'watch-telemetry-v1',
          'requiredPeripheralCoverage': ['sensor'],
          'excludedCoverage': [
            'ble',
            'display',
            'radio',
            'thermal',
            'physicalOperation'
          ],
        },
      }
    }));
    await tester.pumpWidget(
        scope(repository, const ProductionWorkspacePage(projectId: 'p')));
    await tester.pumpAndSettle();
    expect(find.widgetWithText(KButton, 'Confirm concept'), findsOneWidget);
    expect(
        find.textContaining(
            'Excluded from digital validation: Bluetooth (BLE)'),
        findsOneWidget);
    expect(find.text('Required peripheral checks: sensors.'), findsOneWidget);
    final scopeDetails =
        find.text('Proposed requirements and validation scope');
    await tester.scrollUntilVisible(scopeDetails, 200,
        scrollable: find.byType(Scrollable).first);
    await tester.pumpAndSettle();
    await tester.tap(scopeDetails);
    await tester.pumpAndSettle();
    expect(find.textContaining('watch-telemetry-v1'), findsOneWidget);
    await tester.pumpWidget(const SizedBox());
    repository.dispose();
  });

  testWidgets('long clarification questions remain readable on a phone',
      (tester) async {
    tester.view.physicalSize = const Size(320, 568);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    const question = 'Should the 20 mm spring bar lugs feature blind holes '
        '(1.6 mm diameter) or quick-release inner access slots for tool-less strap replacement?';
    final repository = TestRepository(snapshot(revision: 9, stage: 2, state: {
      'proposal': {
        'summary': 'Choose a strap attachment',
        'questions': [question]
      }
    }));
    await tester.pumpWidget(
        scope(repository, const ProductionWorkspacePage(projectId: 'p')));
    await tester.pumpAndSettle();
    await tester.scrollUntilVisible(find.text(question), 200,
        scrollable: find.byType(Scrollable).first);
    await tester.pumpAndSettle();
    expect(tester.getSize(find.text(question)).height, greaterThan(30));
    final answer = find.byKey(const ValueKey('answer-$question'));
    await tester.ensureVisible(answer);
    await tester.enterText(answer, 'Use blind holes.');
    await tester.pumpAndSettle();
    final save = find.byKey(const ValueKey('save-answer-$question'));
    await tester.ensureVisible(save);
    await tester.pumpAndSettle();
    await tester.tap(save);
    await tester.pumpAndSettle();
    expect(repository.commands.single, {
      'type': 'project.answer',
      'payload': {
        'projectId': 'p',
        'questionKey': question,
        'values': ['Use blind holes.']
      },
      'revision': 9
    });
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox());
    repository.dispose();
  });

  testWidgets('SSE deduplicates replay and snapshots after an event gap',
      (tester) async {
    final repository = TestRepository(snapshot(cursor: 10));
    await tester.pumpWidget(
        scope(repository, const ProductionWorkspacePage(projectId: 'p')));
    await tester.pumpAndSettle();
    expect(repository.cursors, [10]);
    ProjectEvent event(int sequence) => ProjectEvent.fromJson({
          'sequence': sequence,
          'type': 'task.completed',
          'data': {'summary': 'Complete'}
        });
    repository.streams.last.add(event(11));
    repository.streams.last.add(event(11));
    await tester.pump();
    final container = ProviderScope.containerOf(
        tester.element(find.byType(ProductionWorkspacePage)));
    expect(container.read(productionProjectProvider('p')).requireValue.events,
        hasLength(1));
    repository.current = snapshot(revision: 15, cursor: 15);
    repository.streams.last.add(event(14));
    await tester.pumpAndSettle();
    expect(repository.cursors, [10, 15]);
    expect(
        container
            .read(productionProjectProvider('p'))
            .requireValue
            .workspace
            .project
            .revision,
        15);
    await tester.pumpWidget(const SizedBox());
    repository.dispose();
  });

  testWidgets('provider choices can change while a run is active',
      (tester) async {
    final repository = TestRepository(snapshot(revision: 9, status: 'running'));
    await tester.pumpWidget(
        scope(repository, const ProductionWorkspacePage(projectId: 'p')));
    await tester.pumpAndSettle();
    final settings = find.text('AI provider settings');
    await tester.scrollUntilVisible(settings, 250);
    await tester.tap(settings);
    await tester.pumpAndSettle();
    final picker = find.byType(DropdownButtonFormField<String>).first;
    await tester.scrollUntilVisible(picker, 200);
    await tester.tap(picker);
    await tester.pumpAndSettle();
    await tester.tap(find.text('Gemini · gemini-3.8-flash').last);
    await tester.pumpAndSettle();
    final save = find.widgetWithText(KButton, 'Save provider choices');
    await tester.scrollUntilVisible(save, 300);
    await tester.pumpAndSettle();
    await tester.tap(save);
    await tester.pumpAndSettle();
    expect(repository.commands.single['type'], 'project.setModelPolicy');
    expect(repository.commands.single['revision'], 9);
    final policy =
        asMap(asMap(repository.commands.single['payload'])['modelPolicy']);
    expect(asMap(policy['coordination'])['provider'], 'google');
    expect(asMap(policy['firmware'])['provider'], 'meta');
    await tester.pumpWidget(const SizedBox());
    repository.dispose();
  });

  testWidgets('configured provider preset is explicit and saves every lane',
      (tester) async {
    final repository = TestRepository(snapshot(revision: 9, status: 'running'));
    await tester.pumpWidget(
        scope(repository, const ProductionWorkspacePage(projectId: 'p')));
    await tester.pumpAndSettle();
    final settings = find.text('AI provider settings');
    await tester.scrollUntilVisible(settings, 250);
    await tester.tap(settings);
    await tester.pumpAndSettle();
    expect(repository.commands, isEmpty);
    expect(find.byKey(const ValueKey('provider-preset-meta')), findsNothing);
    final preset = find.byKey(const ValueKey('provider-preset-google'));
    await tester.ensureVisible(preset);
    await tester.tap(preset);
    await tester.pumpAndSettle();
    expect(repository.commands, isEmpty);
    final save = find.widgetWithText(KButton, 'Save provider choices');
    await tester.scrollUntilVisible(save, 300);
    await tester.pumpAndSettle();
    await tester.tap(save);
    await tester.pumpAndSettle();
    final saved = repository.commands.single;
    expect(saved['type'], 'project.setModelPolicy');
    expect(saved['revision'], 9);
    final policy = asMap(asMap(saved['payload'])['modelPolicy']);
    expect(policy.keys.toSet(), engineeringLanes.keys.toSet());
    expect(policy.values.every((value) => asMap(value)['provider'] == 'google'),
        isTrue);
    await tester.pumpWidget(const SizedBox());
    repository.dispose();
  });
}
