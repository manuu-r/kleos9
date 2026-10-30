import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:kleos_ui/data/models/json.dart';
import 'package:kleos_ui/data/services/production_api.dart';
import 'package:kleos_ui/ui/core/widgets.dart';
import 'package:kleos_ui/ui/features/project/engineering_preview.dart';

JsonMap _preview({bool actual = false, int revision = 1}) => {
      'revision': revision,
      'phase': actual ? 'generating' : 'interfaces',
      'source': 'deterministic',
      'mechanics': {
        'status': actual ? 'candidate' : 'ready',
        'source': actual ? 'artifact' : 'interface',
        'summary': 'Enclosure with shared battery clearance.',
        'envelope': {'width': 50, 'height': 35, 'depth': 12},
        if (actual) ...{
          'artifactId': 'cad',
          'name': 'enclosure.stl',
          'mesh': {
            'triangles': [
              [
                [0, 0, 0],
                [50, 0, 0],
                [0, 35, 12]
              ],
              [
                [0, 0, 0],
                [0, 35, 12],
                [0, 35, 0]
              ]
            ]
          },
        }
      },
      'electronics': {
        'status': actual ? 'candidate' : 'ready',
        'source': actual ? 'artifact' : 'interface',
        'pcb': {
          'board': {'width': 42, 'height': 28},
          'components': [
            {
              'id': 'U1',
              'label': 'MCU',
              'x': 10,
              'y': 10,
              'width': 9,
              'height': 9
            },
            {
              'id': 'J1',
              'label': 'USB',
              'x': 35,
              'y': 22,
              'width': 6,
              'height': 4
            },
          ],
          'traces': [
            {
              'layer': 'F.Cu',
              'points': [
                {'x': 10, 'y': 10},
                {'x': 35, 'y': 22}
              ]
            }
          ],
        },
      },
      'application': {
        'source': actual ? 'artifact' : 'interface',
        'status': actual ? 'candidate' : 'queued',
        'artifactId': 'app',
        'app': {
          'title': 'Air monitor',
          'widgets': [
            {'kind': 'metric', 'label': 'CO₂', 'value': 'Illustrative reading'}
          ]
        },
        if (actual) 'code': '<html><script>window.evil()</script></html>',
      },
      'files': actual
          ? [
              {
                'id': 'cad',
                'name': 'enclosure.stl',
                'lane': 'mechanics',
                'status': 'candidate',
                'mediaType': 'model/stl'
              },
              {
                'id': 'app',
                'name': 'companion.html',
                'lane': 'application',
                'status': 'candidate',
                'mediaType': 'text/html'
              },
            ]
          : <JsonMap>[],
    };

Future<void> _mount(WidgetTester tester, Widget child,
    {double width = 390, double textScale = 1}) async {
  tester.view.physicalSize = Size(width, 1100);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(MaterialApp(
    theme: kTheme(),
    builder: (context, child) => MediaQuery(
      data: MediaQuery.of(context)
          .copyWith(textScaler: TextScaler.linear(textScale)),
      child: child!,
    ),
    home: Scaffold(
        body: SingleChildScrollView(
            padding: const EdgeInsets.all(16), child: child)),
  ));
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('interfaces provide native CAD, PCB and app sketches on a phone',
      (tester) async {
    await _mount(tester, EngineeringPreviewPanel(data: _preview()),
        width: 320, textScale: 1.3);
    expect(find.text('Shared size envelope'), findsOneWidget);
    expect(find.text('Approximation'), findsOneWidget);
    expect(find.text('50 × 35 × 12 mm'), findsOneWidget);
    expect(find.byKey(const ValueKey('cad-mesh-canvas')), findsOneWidget);
    await tester.drag(
        find.byKey(const ValueKey('cad-mesh-canvas')), const Offset(70, 20));
    await tester.pump();
    await tester.tap(find.byTooltip('Zoom in CAD'));
    await tester.pump();
    await tester.tap(find.byKey(const ValueKey('preview-tab-electronics')));
    await tester.pumpAndSettle();
    expect(find.text('Board layout sketch'), findsOneWidget);
    expect(find.byKey(const ValueKey('pcb-geometry-canvas')), findsOneWidget);
    await tester.tap(find.byKey(const ValueKey('preview-tab-application')));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('native-app-sketch')), findsOneWidget);
    expect(find.text('Air monitor'), findsOneWidget);
    expect(find.text('Preview values are illustrative'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets(
      'actual artifact geometry stays distinct from the native app sketch',
      (tester) async {
    await _mount(tester, EngineeringPreviewPanel(data: _preview(actual: true)),
        width: 1100);
    expect(find.text('Generated CAD mesh'), findsOneWidget);
    expect(find.text('Actual file'), findsOneWidget);
    expect(find.text('candidate'), findsOneWidget);
    await tester.tap(find.byKey(const ValueKey('preview-tab-electronics')));
    await tester.pumpAndSettle();
    expect(find.text('Generated PCB geometry'), findsOneWidget);
    expect(find.text('Actual file'), findsOneWidget);
    await tester.tap(find.byKey(const ValueKey('preview-tab-application')));
    await tester.pumpAndSettle();
    expect(find.text('Approximation'), findsOneWidget);
    expect(find.text('Actual file'), findsNothing);
    await tester.ensureVisible(find.text('Show generated source'));
    await tester.tap(find.text('Show generated source'));
    await tester.pumpAndSettle();
    expect(find.text('Generated source'), findsOneWidget);
    expect(find.text('Actual file'), findsOneWidget);
    final source = tester.widget<SelectableText>(find.descendant(
      of: find.byKey(const ValueKey('engineering-source-text')),
      matching: find.byType(SelectableText),
    ));
    expect(source.data, '<html><script>window.evil()</script></html>');
    // HTML remains literal SelectableText; no browser or embedded frame exists.
    expect(find.byKey(const ValueKey('native-app-sketch')), findsNothing);
    expect(tester.takeException(), isNull);
  });

  testWidgets('malformed geometry never produces a fabricated actual mesh',
      (tester) async {
    final data = _preview(actual: true);
    data['mechanics'] = {
      'source': 'artifact',
      'status': 'unknown',
      'mesh': {
        'triangles': [
          [
            [double.nan, 1, 2],
            [3, 4, 5],
            [6, 7, 8]
          ]
        ]
      },
      'envelope': {'width': -5, 'height': double.infinity, 'depth': 0},
    };
    await _mount(tester, EngineeringPreviewPanel(data: data));
    expect(find.text('Approximation'), findsOneWidget);
    expect(find.text('Actual file'), findsNothing);
    expect(find.text('Shared dimensions will appear here.'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('firmware file opens actual C source, never the companion sketch',
      (tester) async {
    final data = _preview(actual: true);
    data['firmware'] = {
      'title': 'Firmware',
      'source': 'artifact',
      'status': 'candidate',
      'code': '#include <zephyr/kernel.h>\nint main(void) { return 0; }',
      'codeName': 'src/main.c',
      'codeArtifactId': 'firmware',
      'codeStatus': 'candidate',
    };
    (data['files'] as List).add({
      'id': 'firmware',
      'name': 'src/main.c',
      'lane': 'firmware',
      'status': 'candidate',
      'mediaType': 'text/x-c',
    });
    await _mount(tester, EngineeringPreviewPanel(data: data),
        width: 320, textScale: 1.3);
    expect(find.byKey(const ValueKey('preview-tab-firmware')), findsOneWidget);
    await tester.ensureVisible(find.text('Files appearing in this build  3'));
    await tester.tap(find.text('Files appearing in this build  3'));
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.byTooltip('Preview firmware lane'));
    await tester.tap(find.byTooltip('Preview firmware lane'));
    await tester.pumpAndSettle();
    expect(find.text('Firmware source'), findsOneWidget);
    expect(find.text('Actual file'), findsOneWidget);
    expect(find.byKey(const ValueKey('native-app-sketch')), findsNothing);
    final source = tester.widget<SelectableText>(find.descendant(
      of: find.byKey(const ValueKey('engineering-source-text')),
      matching: find.byType(SelectableText),
    ));
    expect(source.data, contains('#include <zephyr/kernel.h>'));
    expect(find.text('Show visual preview'), findsNothing);
    expect(tester.takeException(), isNull);
  });

  testWidgets('generated source keeps its own provenance before CAD is ready',
      (tester) async {
    final data = _preview();
    data['mechanics'] = {
      ...asMap(data['mechanics']),
      'code': 'from build123d import Box\nBox(50, 35, 12)',
      'codeArtifactId': 'source',
      'codeName': 'enclosure.py',
      'codeStatus': 'candidate',
    };
    await _mount(tester, EngineeringPreviewPanel(data: data));
    await tester.ensureVisible(find.text('Show generated source'));
    await tester.tap(find.text('Show generated source'));
    await tester.pumpAndSettle();
    expect(find.text('Actual file'), findsOneWidget);
    expect(find.text('enclosure.py'), findsOneWidget);
    expect(find.text('candidate'), findsOneWidget);
    expect(find.text('Approximation'), findsNothing);
    expect(tester.takeException(), isNull);
  });

  testWidgets('PCB preview exposes an incomplete circuit contract as UNKNOWN',
      (tester) async {
    final data = _preview();
    data['circuitContractCheck'] = {
      'name': 'electronics_contract',
      'status': 'UNKNOWN',
      'details': {'reason': 'A complete pin and net contract is required.'}
    };
    await _mount(tester, EngineeringPreviewPanel(data: data));
    await tester.tap(find.byKey(const ValueKey('preview-tab-electronics')));
    await tester.pumpAndSettle();
    expect(find.text('Circuit definition check'), findsOneWidget);
    expect(find.text('UNKNOWN'), findsOneWidget);
    expect(
        find.text(
            'The circuit needs complete component and pin connections before electronics can pass validation.'),
        findsOneWidget);
    expect(find.text('A complete pin and net contract is required.'),
        findsNothing);
    expect(find.text('PASS'), findsNothing);
    expect(tester.takeException(), isNull);
  });

  testWidgets('the concept remains visible until interface dimensions arrive',
      (tester) async {
    final data = _preview();
    data['mechanics'] = {
      'source': 'interface',
      'status': 'queued',
      'envelope': <String, dynamic>{}
    };
    await _mount(
        tester,
        EngineeringPreviewPanel(data: data, fallbackScene: {
          'schemaVersion': 'concept-scene-v1',
          'title': 'Workshop sensor',
          'parts': [
            {
              'id': 'shell',
              'label': 'Enclosure',
              'role': 'shell',
              'shape': 'rounded_box',
              'size': {'x': 4, 'y': 3, 'z': 1},
              'position': {'x': 0, 'y': 0, 'z': 0},
              'color': '#778899'
            }
          ]
        }));
    expect(find.text('Planning concept'), findsOneWidget);
    expect(find.text('Approximation'), findsOneWidget);
    expect(find.text('Shared dimensions will appear here.'), findsNothing);
    expect(tester.takeException(), isNull);
  });

  testWidgets(
      'live revision refresh retains the last preview and rejects late results',
      (tester) async {
    final second = Completer<JsonMap>();
    final third = Completer<JsonMap>();
    var loads = 0;
    var revision = 1;
    late StateSetter update;
    await _mount(
        tester,
        ProviderScope(
            overrides: [
              engineeringPreviewProvider((projectId: 'p', revision: 1))
                  .overrideWith((ref) async {
                loads++;
                return _preview();
              }),
              engineeringPreviewProvider((projectId: 'p', revision: 2))
                  .overrideWith((ref) {
                loads++;
                return second.future;
              }),
              engineeringPreviewProvider((projectId: 'p', revision: 3))
                  .overrideWith((ref) {
                loads++;
                return third.future;
              }),
            ],
            child: StatefulBuilder(builder: (context, setState) {
              update = setState;
              return EngineeringPreview(projectId: 'p', revision: revision);
            })));
    expect(loads, 1);
    update(() {});
    await tester.pump();
    expect(loads, 1);
    update(() => revision = 2);
    await tester.pump();
    expect(find.text('Shared size envelope'), findsOneWidget);
    expect(find.text('Previous preview'), findsOneWidget);
    update(() => revision = 3);
    await tester.pump();
    third.complete(_preview(actual: true, revision: 3));
    await tester.pumpAndSettle();
    expect(find.text('Generated CAD mesh'), findsOneWidget);
    second.complete(_preview(revision: 2));
    await tester.pumpAndSettle();
    expect(find.text('Generated CAD mesh'), findsOneWidget);
    expect(loads, 3);
    expect(tester.takeException(), isNull);
  });

  testWidgets('preview errors offer a local retry without engineering commands',
      (tester) async {
    var attempts = 0;
    await _mount(
        tester,
        ProviderScope(overrides: [
          engineeringPreviewProvider((projectId: 'p', revision: 1))
              .overrideWith((ref) async {
            if (++attempts == 1) throw Exception('Temporary read failure');
            return _preview();
          }),
        ], child: const EngineeringPreview(projectId: 'p', revision: 1)));
    expect(find.text('Preview update interrupted'), findsOneWidget);
    await tester.tap(find.text('Retry preview'));
    await tester.pumpAndSettle();
    expect(find.text('Shared size envelope'), findsOneWidget);
    expect(attempts, 2);
    expect(tester.takeException(), isNull);
  });

  test('preview API keeps project and organization authorization scoped',
      () async {
    var requests = 0;
    late http.Request sent;
    final api = ProductionApi(
        baseUrl: 'https://api.example/api/v1',
        client: MockClient((request) async {
          requests++;
          sent = request;
          return http.Response(jsonEncode(_preview()), 200,
              headers: {'content-type': 'application/json; charset=utf-8'});
        }));
    api.accessToken = () async => 'session';
    addTearDown(api.close);
    final result = await api.engineeringPreview('a/b', organizationId: 'org');
    expect(sent.method, 'GET');
    expect(sent.url.toString(),
        'https://api.example/api/v1/projects/a%2Fb/engineering-preview');
    expect(sent.headers['Authorization'], 'Bearer session');
    expect(sent.headers['X-Organization-Id'], 'org');
    expect(result['source'], 'deterministic');
    expect(requests, 1);
  });
}
