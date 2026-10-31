import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:kleos_ui/data/models/json.dart';
import 'package:kleos_ui/data/services/api_service.dart';
import 'package:kleos_ui/state/kleos_store.dart';
import 'package:kleos_ui/state/production_store.dart';
import 'package:kleos_ui/ui/core/widgets.dart';
import 'package:kleos_ui/ui/features/production/engineering_tools.dart';
import 'production_ui_test.dart' show TestAccount;

const policy = <String, Object>{
  'mechanics': {'id': 'build123d', 'version': '1'},
  'electronics': {'id': 'atopile-kicad', 'version': '1'},
};
final catalog = <String, dynamic>{
  'defaultPolicy': policy,
  'tools': <JsonMap>[
    {
      'id': 'build123d',
      'version': '1',
      'name': 'build123d',
      'lane': 'mechanics',
      'qualified': true,
      'configured': true,
      'isDefault': true,
      'limitations': <String>[]
    },
    {
      'id': 'cad-worker',
      'version': '2',
      'name': 'Precision CAD alternative',
      'lane': 'mechanics',
      'qualified': true,
      'configured': true,
      'isDefault': false,
      'limitations': <String>[]
    },
    {
      'id': 'cad-unqualified',
      'version': '1',
      'name': 'Experimental CAD',
      'lane': 'mechanics',
      'qualified': false,
      'configured': true,
      'isDefault': false,
      'limitations': <String>[]
    },
    {
      'id': 'cad-offline',
      'version': '1',
      'name': 'Offline CAD',
      'lane': 'mechanics',
      'qualified': true,
      'configured': false,
      'isDefault': false,
      'limitations': <String>[]
    },
    {
      'id': 'atopile-kicad',
      'version': '1',
      'name': 'atopile + KiCad',
      'lane': 'electronics',
      'qualified': false,
      'configured': true,
      'isDefault': true,
      'limitations': ['Layout qualification is pending.']
    },
    {
      'id': 'pcb-worker',
      'version': '3',
      'name': 'Qualified PCB alternative',
      'lane': 'electronics',
      'qualified': true,
      'configured': true,
      'isDefault': false,
      'limitations': <String>[]
    },
  ],
};

class Service extends ApiService {
  final calls = <JsonMap>[];
  int revision = 1;
  JsonMap currentPolicy = {...policy};
  @override
  Future<JsonMap> load() async => {
        'revision': revision,
        'state': {
          'production': true,
          'projects': <JsonMap>[
            {
              'id': 'p',
              'name':
                  'A real engineering build with an exceptionally long device name',
              'production': true,
              'state': {'toolPolicy': currentPolicy}
            },
            {
              'id': 'simulation',
              'name': 'Simulation fixture',
              'production': false
            },
          ],
        },
      };
  @override
  Future<JsonMap> command(String type, JsonMap payload) async {
    calls.add({'type': type, 'payload': payload});
    currentPolicy = asMap(payload['toolPolicy']);
    revision++;
    return {
      'snapshot': await load(),
      'result': {'projectId': 'p'}
    };
  }
}

Future<({ProviderContainer container, Service service})> mount(
    WidgetTester tester,
    {double width = 390,
    JsonMap? tools}) async {
  tester.view.physicalSize = Size(width, 1000);
  tester.view.devicePixelRatio = 1;
  final service = Service();
  final container = ProviderContainer(overrides: [
    apiServiceProvider.overrideWithValue(service),
    pollingEnabledProvider.overrideWithValue(false),
    accountProvider.overrideWith(TestAccount.new),
    engineeringToolsProvider.overrideWith((_) async => tools ?? catalog),
  ]);
  container.listen(kleosProvider, (_, __) {});
  await container.read(kleosProvider).refresh();
  await tester.pumpWidget(UncontrolledProviderScope(
      container: container,
      child: MaterialApp(
          theme: kTheme(),
          home: Scaffold(
              body: SingleChildScrollView(
            padding: const EdgeInsets.all(16),
            child: Consumer(
                builder: (_, ref, __) =>
                    EngineeringTools(controller: ref.watch(kleosProvider))),
          )))));
  await tester.pumpAndSettle();
  addTearDown(() async {
    await tester.pumpWidget(const SizedBox());
    container.dispose();
    service.close();
    tester.view.reset();
  });
  return (container: container, service: service);
}

Finder laneField(String lane) => find.byWidgetPredicate((widget) =>
    widget is DropdownButtonFormField<String> &&
    widget.key is ValueKey<String> &&
    (widget.key! as ValueKey<String>)
        .value
        .startsWith('engineering-tool-$lane-'));

void main() {
  for (final width in [390.0, 1200.0]) {
    testWidgets(
        'CAD and PCB controls fit $width and save only real build choices',
        (tester) async {
      final fixture = await mount(tester, width: width);
      expect(find.text('CAD and PCB tools'), findsOneWidget);
      expect(find.text('Simulation fixture'), findsNothing);
      expect(find.text('Muse'), findsNothing);
      expect(find.text('Muse on Modal'), findsNothing);
      await tester.ensureVisible(laneField('mechanics'));
      await tester.tap(laneField('mechanics'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Precision CAD alternative').last);
      await tester.pumpAndSettle();
      await tester.ensureVisible(laneField('electronics'));
      await tester.tap(laneField('electronics'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Qualified PCB alternative').last);
      await tester.pumpAndSettle();
      final save = find.widgetWithText(KButton, 'Save tool choices');
      await tester.ensureVisible(save);
      await tester.tap(save);
      await tester.pumpAndSettle();
      expect(fixture.service.calls.single, {
        'type': 'project.setToolPolicy',
        'payload': {
          'projectId': 'p',
          'toolPolicy': {
            'mechanics': {'id': 'cad-worker', 'version': '2'},
            'electronics': {'id': 'pcb-worker', 'version': '3'},
          }
        },
      });
      expect(find.textContaining('running attempts keep'), findsOneWidget);
      expect(tester.takeException(), isNull);
    });
  }

  testWidgets('unqualified and missing alternative workers cannot be selected',
      (tester) async {
    await mount(tester);
    final field = tester.widget<DropdownButton<String>>(find.descendant(
        of: laneField('mechanics'),
        matching: find
            .byWidgetPredicate((widget) => widget is DropdownButton<String>)));
    final items = field.items!;
    expect(items.singleWhere((item) => item.value == 'cad-worker@2').enabled,
        true);
    expect(
        items.singleWhere((item) => item.value == 'cad-unqualified@1').enabled,
        false);
    expect(items.singleWhere((item) => item.value == 'cad-offline@1').enabled,
        false);
    expect(
        items.singleWhere((item) => item.value == 'build123d@1').enabled, true);
    expect(find.text('Qualification pending'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets(
      'default tools remain visible and selectable when setup is missing',
      (tester) async {
    final unavailable = {
      ...catalog,
      'tools': asMaps(catalog['tools'])
          .map((tool) => tool['id'] == 'build123d'
              ? {...tool, 'configured': false, 'qualified': false}
              : tool)
          .toList()
    };
    await mount(tester, tools: unavailable);
    expect(find.text('Setup required'), findsOneWidget);
    final field = tester.widget<DropdownButton<String>>(find.descendant(
        of: laneField('mechanics'),
        matching: find
            .byWidgetPredicate((widget) => widget is DropdownButton<String>)));
    expect(
        field.items!.singleWhere((item) => item.value == 'build123d@1').enabled,
        true);
    expect(tester.takeException(), isNull);
  });
}
