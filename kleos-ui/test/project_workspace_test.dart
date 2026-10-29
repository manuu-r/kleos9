import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:kleos_ui/data/models/json.dart';
import 'support.dart';
import 'package:kleos_ui/data/services/api_service.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:kleos_ui/state/kleos_store.dart';
import 'package:kleos_ui/ui/core/human_task_card.dart';
import 'package:kleos_ui/ui/core/widgets.dart';
import 'package:kleos_ui/ui/features/project/project_workspace_page.dart';

JsonMap _project() => {
      'id': 'p1',
      'name': 'Air Quality Monitor',
      'brief': 'A desk air-quality monitor.',
      'stage': 0,
      'done': List<bool>.filled(10, false),
      'answers': <String, dynamic>{},
      'requirements': <dynamic>[],
      'assumptions': <dynamic>[],
      'unknowns': <dynamic>[],
      'feasibility': 'idle',
      'research': <String, dynamic>{},
    };

class _ApiFixture {
  _ApiFixture() {
    final client = MockClient((request) async {
      if (request.method == 'POST') {
        final command = asMap(jsonDecode(request.body));
        requests.add(command);
        final payload = asMap(command['payload']);
        if (command['type'] == 'project.answer') {
          (project['answers'] as JsonMap)['${payload['questionKey']}'] =
              payload['values'];
        }
        if (command['type'] == 'task.requestRevision') {
          tasks.first['state'] = 'REVISION_REQUESTED';
        }
        revision++;
        return http.Response(
            jsonEncode({'snapshot': snapshot(), 'result': <String, dynamic>{}}),
            200);
      }
      return http.Response(jsonEncode(snapshot()), 200);
    });
    controller =
        TestKleos(ApiService(client: client, baseUrl: 'http://mock.test/api'));
  }
  final JsonMap project = _project();
  final List<JsonMap> tasks = [];
  final List<JsonMap> requests = [];
  final List<JsonMap> connectors = [];
  int revision = 1;
  late final TestKleos controller;
  JsonMap snapshot() => {
        'revision': revision,
        'state': {
          'projects': [project],
          'humanTasks': tasks,
          'connectors': connectors
        },
        'demo': {'speed': 1}
      };

  GoRouter router({String location = '/projects/p1'}) =>
      GoRouter(initialLocation: location, routes: [
        GoRoute(
            path: '/projects/:id',
            builder: (context, state) => Scaffold(
                body: SingleChildScrollView(
                    child: Padding(
                        padding: const EdgeInsets.all(24),
                        child: Consumer(
                            builder: (_, ref, __) => ProjectWorkspacePage(
                                controller: ref.watch(kleosProvider),
                                projectId: state.pathParameters['id']!,
                                stage: int.tryParse(
                                    state.uri.queryParameters['s'] ?? ''))))))),
      ]);
}

void _complete(_ApiFixture fixture) {
  fixture.project.addAll(asMap(jsonDecode(r'''{
    "stage":9,"done":[true,true,true,true,true,true,true,true,true,true],
    "answers":{"features":["CO2 level"],"display":["Small OLED"],"size":["Desk"],"battery":["3 days"],"finish":["Matte black"],"assembly":["First-timer"],"budget":["$80-150"]},
    "requirements":[{"label":"Features","value":"CO2 level"},{"label":"Finish","value":"Matte black"}],
    "assumptions":["Indoor use"],"unknowns":["Button feel"],"feasibility":"done",
    "research":{"Parts & availability":100,"Materials & printing":100,"Electronics & power budget":100,"Firmware & app support":100},
    "features":[{"name":"CO2 level","status":"supported","note":"Calibrated sensor."}],"confirmed":true,
    "interfaces":[{"name":"Power rail","value":"3.3 V","owner":"Electronics","state":"PASS"}],"interfacesAgreed":true,
    "generation":"done","generationLanes":["Mechanical","Electronics","Firmware","App"],"genProgress":{"Mechanical":100,"Electronics":100,"Firmware":100,"App":100},
    "tools":{"Mechanical":"mechanical","Electronics":"electronics","Firmware":"firmware"},"genLog":["Candidate files persisted"],
    "generatedSource":{"Mechanical":"# Generated case","Electronics":"// Generated board","Firmware":"/* Firmware */","App":"// Companion app"},
    "artifacts":[{"id":"case","name":"Case top and bottom","domain":"Mechanical","file":"case.step","deps":["battery"],"version":2,"hash":"abc123","status":"accepted"}],
    "checks":[{"id":"fit","domain":"Mechanical","name":"USB-C notch clearance","state":"PASS","detail":"0.22 mm"},{"id":"co2","domain":"Firmware","name":"CO2 sensor","state":"UNKNOWN","detail":"Physical evidence needed"}],
    "verifyRun":"done","batteryMah":2500,"manifestHash":"abc123def4567890",
    "casePrint":"home","homePrint":{"state":"done","progress":100,"layer":142,"layers":142,"nozzle":40},
    "quotes":[{"id":"q1","item":"Shells print (PETG)","connectorId":"provider","price":18,"leadDays":2,"quality":95,"onTime":96,"selected":true,"reason":"Best total score"},{"id":"q2","item":"PCB assembly","connectorId":"provider","price":60,"leadDays":3,"quality":95,"onTime":96,"selected":true,"reason":"Qualified provider"}],
    "mandate":{"limit":250,"reserved":0,"spent":60},"orders":[{"connectorId":"provider","amount":60,"externalRef":"EXT-123","state":"RECEIVED"}],"procurement":"done",
    "physicalTaskIds":["task-1"],"kitTaskIds":["task-2","task-3"],"kitAccepted":true,
    "shipment":{"step":4,"tracking":"PL123456"},"guideStep":6,"assemblyDone":true,"activation":"done",
    "selfTest":[{"name":"Display","state":"PASS"},{"name":"CO2 sensor","state":"PASS"}],"activity":[]
  }''')));
  fixture.connectors.addAll(asMaps(jsonDecode(r'''[
    {"id":"printer","name":"Home printer","kind":"printer","linked":true,"enabled":true,"printer":{"model":"Prusa MK3S+","tolerance":"X +0.06 mm"}},
    {"id":"provider","name":"Northside Print Lab"},
    {"id":"mechanical","name":"Kleos9 Mechanical","kind":"tool","domain":"Mechanical","linked":true,"enabled":true,"builtIn":true},
    {"id":"electronics","name":"Kleos9 Electronics","kind":"tool","domain":"Electronics","linked":true,"enabled":true,"builtIn":true},
    {"id":"firmware","name":"Kleos9 Firmware","kind":"tool","domain":"Firmware","linked":true,"enabled":true,"builtIn":true}
  ]''')));
  for (var i = 1; i <= 3; i++) {
    fixture.tasks.add(<String, dynamic>{
      'id': 'task-$i',
      'title': 'Accepted human task $i',
      'projectName': 'Monitor',
      'state': 'ACCEPTED',
      'trigger': 'Independent physical evidence',
      'fee': 30,
      'platform': 'Hosted portal',
      'applicants': <dynamic>[],
      'validation': <dynamic>[],
      'requirements': <dynamic>[],
      'evidenceSchema': <dynamic>[],
      'history': <dynamic>[]
    });
  }
}

void main() {
  testWidgets(
      'intent submits selected values with the question key and shows the next question',
      (tester) async {
    final fixture = _ApiFixture();
    addTearDown(fixture.controller.dispose);
    await fixture.controller.refresh();
    final router = fixture.router();
    addTearDown(router.dispose);
    await tester.pumpWidget(fixture.controller
        .scope(MaterialApp.router(theme: kTheme(), routerConfig: router)));
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.text('CO2 level'));
    await tester.tap(find.text('CO2 level'));
    await tester.pump();
    await tester.ensureVisible(find.widgetWithText(KButton, 'Continue'));
    await tester.ensureVisible(find.widgetWithText(KButton, 'Continue'));
    await tester.tap(find.widgetWithText(KButton, 'Continue'));
    await tester.pumpAndSettle();
    expect(fixture.requests.single['type'], 'project.answer');
    expect(fixture.requests.single['payload'], {
      'projectId': 'p1',
      'questionKey': 'features',
      'values': ['CO2 level']
    });
    expect(find.text('Which screen suits you?'), findsOneWidget);
  });

  testWidgets(
      'completed work keeps the viewed stage pinned until explicit navigation',
      (tester) async {
    final fixture = _ApiFixture();
    addTearDown(fixture.controller.dispose);
    await fixture.controller.refresh();
    final router = fixture.router();
    addTearDown(router.dispose);
    await tester.pumpWidget(fixture.controller
        .scope(MaterialApp.router(theme: kTheme(), routerConfig: router)));
    await tester.pumpAndSettle();
    expect(router.routeInformationProvider.value.uri.queryParameters['s'], '0');
    fixture.project['stage'] = 1;
    fixture.project['done'] = [true, ...List<bool>.filled(9, false)];
    fixture.project['answers'] = {
      for (final key in [
        'features',
        'display',
        'size',
        'battery',
        'finish',
        'assembly',
        'budget'
      ])
        key: ['Answered']
    };
    fixture.revision++;
    await fixture.controller.refresh();
    await tester.pumpAndSettle();
    expect(
        find.text(
            'A few focused questions so the design matches what you actually want.'),
        findsOneWidget);
    expect(find.text('Run feasibility research'), findsNothing);
    router.go('/projects/p1?s=1');
    await tester.pumpAndSettle();
    expect(find.text('Run feasibility research'), findsOneWidget);
  });

  testWidgets('stage query clamps to available stages and can return to intent',
      (tester) async {
    final fixture = _ApiFixture();
    addTearDown(fixture.controller.dispose);
    fixture.project['stage'] = 1;
    await fixture.controller.refresh();
    final router = fixture.router(location: '/projects/p1?s=99');
    addTearDown(router.dispose);
    await tester.pumpWidget(fixture.controller
        .scope(MaterialApp.router(theme: kTheme(), routerConfig: router)));
    await tester.pumpAndSettle();
    expect(find.text('Run feasibility research'), findsOneWidget);
    fixture.project['stage'] = 2;
    fixture.revision++;
    await fixture.controller.refresh();
    await tester.pumpAndSettle();
    expect(find.text('Run feasibility research'), findsOneWidget);
    router.go('/projects/p1?s=-3');
    await tester.pumpAndSettle();
    expect(
        find.text(
            'A few focused questions so the design matches what you actually want.'),
        findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets(
      'human evidence cannot be accepted before validation and revisions use the API',
      (tester) async {
    final fixture = _ApiFixture();
    addTearDown(fixture.controller.dispose);
    fixture.tasks.add({
      'id': 'task-1',
      'title': 'Fit check',
      'projectName': 'Monitor',
      'state': 'REVIEW',
      'trigger': 'Button feel is unknown',
      'fee': 30,
      'platform': 'Hosted portal',
      'applicants': <dynamic>[],
      'validation': [
        {'label': 'Required reading', 'ok': false}
      ],
      'requirements': <dynamic>[],
      'evidenceSchema': <dynamic>[],
      'history': <dynamic>[],
      'revisionCount': 0,
    });
    await fixture.controller.refresh();
    await tester.pumpWidget(fixture.controller.scope(MaterialApp(
        theme: kTheme(),
        home: Scaffold(
            body: SingleChildScrollView(
                child: Consumer(
          builder: (context, ref, _) => HumanTaskCard(
              controller: ref.watch(kleosProvider),
              task: asMaps(ref.watch(kleosProvider).state['humanTasks']).first),
        ))))));
    await tester.pumpAndSettle();
    final accept = tester.widget<KButton>(
        find.widgetWithText(KButton, r'Accept and release $30'));
    expect(accept.onPressed, isNull);
    await tester.tap(find.widgetWithText(KButton, 'Ask for a revision'));
    await tester.pumpAndSettle();
    expect(fixture.requests.single, {
      'type': 'task.requestRevision',
      'payload': {'taskId': 'task-1'}
    });
    expect(find.text('Revision requested'), findsOneWidget);
  });
  testWidgets(
      'all ten completed stages render on phone and desktop without layout errors',
      (tester) async {
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetDevicePixelRatio);
    addTearDown(tester.view.resetPhysicalSize);
    for (final width in <double>[320, 390, 768, 1024, 1280]) {
      tester.view.physicalSize = Size(width, 1000);
      for (var stage = 0; stage < 10; stage++) {
        final fixture = _ApiFixture();
        _complete(fixture);
        await fixture.controller.refresh();
        final router = fixture.router(location: '/projects/p1?s=$stage');
        try {
          await tester.pumpWidget(fixture.controller.scope(
              MaterialApp.router(theme: kTheme(), routerConfig: router)));
          await tester.pumpAndSettle();
          expect(tester.takeException(), isNull,
              reason: 'stage $stage at width $width');
        } finally {
          await tester.pumpWidget(const SizedBox.shrink());
          router.dispose();
          fixture.controller.dispose();
        }
      }
    }
  });
}
