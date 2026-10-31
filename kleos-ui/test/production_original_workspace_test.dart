import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:kleos_ui/data/models/json.dart';
import 'package:kleos_ui/data/models/production.dart';
import 'package:kleos_ui/data/services/api_service.dart';
import 'package:kleos_ui/state/kleos_store.dart';
import 'package:kleos_ui/state/production_store.dart';
import 'package:kleos_ui/ui/core/device_visual.dart';
import 'package:kleos_ui/ui/core/concept_preview.dart';
import 'package:kleos_ui/ui/core/widgets.dart';
import 'package:kleos_ui/ui/features/project/stages.dart';
import 'package:kleos_ui/ui/features/project/engineering_preview.dart';

class _Owner extends AccountController {
  @override
  Future<AccountSession?> build() async => AccountSession.fromJson(
      {'subject': 'test-user', 'tenantId': 'test-org', 'role': 'owner'});
}

class _Service extends ApiService {
  final commands = <JsonMap>[];
  JsonMap state = {};
  int loads = 0;
  @override
  Future<JsonMap> load() async => {'revision': ++loads, 'state': state};
  @override
  Future<JsonMap> command(String type, JsonMap payload) async {
    commands.add({'type': type, 'payload': payload});
    return {
      'snapshot': await load(),
      'result': {'projectId': 'project'}
    };
  }
}

JsonMap _project(
        {int stage = 0,
        JsonMap state = const {},
        List<JsonMap> checks = const [],
        List<JsonMap> artifacts = const [],
        List<JsonMap> runs = const []}) =>
    {
      'id': 'project',
      'name': 'Workshop air sensor',
      'brief': 'Track workshop air',
      'stage': stage,
      'production': true,
      'productionWorkspace': {
        'project': {
          'id': 'project',
          'name': 'Workshop air sensor',
          'brief': 'Track workshop air',
          'stage': stage,
          'revision': 3,
          'engineeringRevision': 2,
          'status': 'blocked',
          'state': state,
          'interfaces': stage > 3
              ? {
                  'battery': {
                    'dimensionsMm': [20, 30, 4]
                  }
                }
              : <String, dynamic>{},
          'budget': {
            'allowanceUsd': 20,
            'spentEstimateUsd': 2,
            'reservedUsd': 0
          },
          'modelPolicy': {
            for (final lane in engineeringLanes.keys)
              lane: {'provider': 'modal', 'model': 'muse-spark-open'}
          },
        },
        'artifacts': artifacts,
        'checks': checks,
        'runs': runs,
        'trainingOptedIn': false,
      },
    };

Future<ProviderContainer> _mount(
    WidgetTester tester, _Service service, JsonMap project, int stage,
    {VoidCallback? onNext}) async {
  tester.view.physicalSize = const Size(390, 844);
  tester.view.devicePixelRatio = 1;
  final container = ProviderContainer(overrides: [
    apiServiceProvider.overrideWithValue(service),
    pollingEnabledProvider.overrideWithValue(false),
    accountProvider.overrideWith(_Owner.new),
    engineeringPreviewProvider((projectId: 'project', revision: 3))
        .overrideWith((ref) async => {
              'revision': 3,
              'mechanics': {
                'source': 'interface',
                'status': 'ready',
                'envelope': {'width': 50, 'height': 35, 'depth': 12}
              },
            }),
    modelCatalogProvider.overrideWith((ref) async => ModelCatalog([
          CatalogModel(const ModelChoice('modal', 'muse-spark-open'), true,
              engineeringLanes.keys.toList()),
          CatalogModel(const ModelChoice('meta', 'test-muse'), true,
              engineeringLanes.keys.toList()),
        ], {
          for (final lane in engineeringLanes.keys)
            lane: const ModelChoice('modal', 'muse-spark-open')
        })),
  ]);
  await container.read(kleosProvider.notifier).refresh();
  await tester.pumpWidget(UncontrolledProviderScope(
      container: container,
      child: MaterialApp(
          theme: kTheme(),
          home: Scaffold(
              body: SingleChildScrollView(
                  padding: const EdgeInsets.all(16),
                  child: Consumer(
                      builder: (context, ref, _) => ProjectStage(
                          controller: ref.watch(kleosProvider),
                          project: project,
                          stage: stage,
                          onNext: onNext ?? () {})))))));
  await tester.pumpAndSettle();
  addTearDown(() async {
    await tester.pumpWidget(const SizedBox());
    container.dispose();
    service.close();
    tester.view.reset();
  });
  return container;
}

void main() {
  for (final stage in [3, 4]) {
    testWidgets(
        'stage $stage uses the frozen approved concept instead of a changed draft',
        (tester) async {
      final frozen = <String, dynamic>{
        'schemaVersion': 'concept-scene-v1',
        'title': 'Approved round device',
        'units': 'relative',
        'parts': [
          {
            'id': 'approved-shell',
            'role': 'shell',
            'label': 'Approved case',
            'shape': 'cylinder',
            'size': {'x': 4, 'y': 4, 'z': 1},
            'color': '#778899'
          }
        ]
      };
      final project = _project(stage: stage, state: {
        'confirmed': true,
        'concept': {
          'version': 9,
          'scene': {'title': 'Unapproved square draft', 'parts': <Object>[]}
        },
        'approvedDesign': {
          'schemaVersion': 'approved-design-v1',
          'version': 1,
          'sceneVersion': 5,
          'sceneHash': 'approved-hash',
          'scene': frozen
        },
      });
      await _mount(tester, _Service(), project, stage);
      expect(
          tester
              .widget<EngineeringPreview>(find.byType(EngineeringPreview))
              .fallbackScene,
          frozen);
      await tester.ensureVisible(find.text('View approved 3D reference'));
      await tester.tap(find.text('View approved 3D reference'));
      await tester.pumpAndSettle();
      expect(tester.widget<ConceptPreview>(find.byType(ConceptPreview)).scene,
          frozen);
      expect(find.text('Unapproved square draft'), findsNothing);
      expect(tester.takeException(), isNull);
    });

    testWidgets(
        'stage $stage shows interface preview inside the original workspace',
        (tester) async {
      await _mount(
          tester,
          _Service(),
          _project(stage: stage, state: {
            'confirmed': true,
            'proposal': {'summary': 'A compact workshop sensor'},
          }),
          stage);
      expect(find.byType(EngineeringPreview), findsOneWidget);
      expect(find.text('Build preview'), findsOneWidget);
      // The studio opens on its overview: CAD, PCB and app side by side.
      expect(
          find.byKey(const ValueKey('preview-tab-overview')), findsOneWidget);
      expect(find.text('Approximation'), findsNWidgets(3));
      await tester
          .ensureVisible(find.byKey(const ValueKey('preview-tab-mechanics')));
      await tester.tap(find.byKey(const ValueKey('preview-tab-mechanics')));
      await tester.pumpAndSettle();
      expect(find.text('Shared size envelope'), findsOneWidget);
      expect(find.text('Approximation'), findsOneWidget);
      expect(tester.takeException(), isNull);
    });
  }

  testWidgets('an unlimited local budget is displayed without a fake allowance',
      (tester) async {
    final project = _project(stage: 5);
    (project['productionWorkspace'] as JsonMap)['project']['budget'] = {
      'unlimited': true,
      'allowanceUsd': null,
      'spentEstimateUsd': 1.25,
      'reservedUsd': .4,
    };
    await _mount(tester, _Service(), project, 5);
    await tester.ensureVisible(find.text('Usage allowance'));
    await tester.tap(find.text('Usage allowance'));
    await tester.pumpAndSettle();
    expect(find.text('Unlimited in development'), findsOneWidget);
    expect(find.text('Used: \$1.25 · Reserved: \$0.4'), findsOneWidget);
    expect(find.text('Project allowance: \$0'), findsNothing);
    expect(tester.takeException(), isNull);
  });

  testWidgets('stream failures retain the workspace and offer a real refresh',
      (tester) async {
    final service = _Service()
      ..state = {
        'liveConnected': false,
        'liveError': 'Events connection was interrupted'
      };
    await _mount(tester, service, _project(), 0);
    expect(find.text('Reconnecting updates'), findsOneWidget);
    expect(find.text('Events connection was interrupted'), findsOneWidget);
    expect(find.text('Shape your device'), findsOneWidget);
    final before = service.loads;
    service.state = {'liveConnected': true};
    await tester.tap(find.widgetWithText(KButton, 'Refresh workspace'));
    await tester.pumpAndSettle();
    expect(service.loads, greaterThan(before));
    expect(find.text('Live updates'), findsOneWidget);
    expect(find.text('Events connection was interrupted'), findsNothing);
  });

  testWidgets(
      'failed workspace read shows retry instead of a permanent skeleton',
      (tester) async {
    final service = _Service()
      ..state = {
        'liveConnected': false,
        'liveError': 'Workspace could not be loaded'
      };
    final project = _project()..remove('productionWorkspace');
    await _mount(tester, service, project, 0);
    expect(find.text('Workspace could not be loaded'), findsOneWidget);
    expect(find.widgetWithText(KButton, 'Refresh workspace'), findsOneWidget);
    expect(find.byType(KPageSkeleton), findsNothing);
  });

  testWidgets(
      'original intent stage starts live capture without fixture questions',
      (tester) async {
    final service = _Service();
    await _mount(tester, service, _project(), 0);
    expect(find.text('Shape your device'), findsOneWidget);
    expect(find.text('Which screen suits you?'), findsNothing);
    expect(find.byType(DeviceVisual), findsNothing);
    await tester.tap(find.widgetWithText(KButton, 'Start planning'));
    await tester.pumpAndSettle();
    expect(service.commands.single, {
      'type': 'project.capture',
      'payload': {'projectId': 'project', 'autoResume': true}
    });
    expect(tester.takeException(), isNull);
  });

  testWidgets('actual clarification is saved and next stage remains navigable',
      (tester) async {
    final service = _Service();
    var next = 0;
    await _mount(
        tester,
        service,
        _project(stage: 2, state: {
          'proposal': {
            'summary': 'A measured workshop sensor design.',
            'questions': ['Which particulate range must be measured?'],
            'limitations': ['Sensor calibration needs physical verification.']
          },
        }),
        0,
        onNext: () => next++);
    expect(find.text('A measured workshop sensor design.'), findsNothing);
    expect(
        find.text('Which particulate range must be measured?'), findsOneWidget);
    expect(find.byType(DeviceVisual), findsNothing);
    await tester.ensureVisible(find.byType(TextField).first);
    await tester.enterText(find.byType(TextField).first, 'PM2.5');
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.widgetWithText(KButton, 'Send answer'));
    await tester.tap(find.widgetWithText(KButton, 'Send answer'));
    await tester.pumpAndSettle();
    expect(service.commands.single, {
      'type': 'project.answer',
      'payload': {
        'projectId': 'project',
        'questionKey': 'Which particulate range must be measured?',
        'values': ['PM2.5'],
        'autoResume': true
      }
    });
    final continuation =
        find.widgetWithText(KButton, 'Continue to Research feasibility');
    await tester.ensureVisible(continuation);
    await tester.tap(continuation);
    expect(next, 1);
    expect(tester.takeException(), isNull);
  });

  testWidgets(
      'verification shows real UNKNOWN evidence, blocker and downloadable artifact',
      (tester) async {
    final service = _Service();
    await _mount(
        tester,
        service,
        _project(stage: 5, state: {
          'confirmed': true,
          'proposal': {'summary': 'Qualified source proposal.'}
        }, checks: [
          {
            'name': 'Electronics qualification',
            'status': 'UNKNOWN',
            'evidenceKind': 'digital',
            'details': {'reason': 'Peripheral layout is not qualified'}
          },
        ], artifacts: [
          {
            'id': 'a',
            'name': 'enclosure.step',
            'lane': 'mechanics',
            'status': 'candidate'
          },
        ], runs: [
          {
            'id': 'r',
            'kind': 'project.verify',
            'status': 'blocked',
            'blocker': 'Electronics needs qualification'
          },
        ]),
        5);
    expect(find.text('Electronics needs qualification'), findsWidgets);
    expect(find.text('UNKNOWN'), findsOneWidget);
    expect(find.textContaining('Kit estimate:'), findsNothing);
    expect(find.byType(DeviceVisual), findsNothing);
    await tester.ensureVisible(find.text('enclosure.step'));
    expect(find.byTooltip('Download artifact'), findsOneWidget);
    expect(find.text('enclosure.step'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });
}
