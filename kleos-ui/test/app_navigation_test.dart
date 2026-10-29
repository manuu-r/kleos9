import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:kleos_ui/data/models/json.dart';
import 'support.dart';
import 'package:kleos_ui/data/services/api_service.dart';
import 'package:kleos_ui/main.dart';
import 'package:kleos_ui/ui/core/widgets.dart';

void main() {
  testWidgets('actual app navigates from home to a build through the API',
      (tester) async {
    final semantics = tester.ensureSemantics();
    tester.view.physicalSize = const Size(1280, 900);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    JsonMap state = {
      'projects': <dynamic>[],
      'connectors': <dynamic>[],
      'humanTasks': <dynamic>[]
    };
    final sent = <JsonMap>[];
    final service = ApiService(
        baseUrl: 'http://local.test/api',
        client: MockClient((request) async {
          if (request.method == 'GET') {
            return http.Response(
                jsonEncode({
                  'revision': 0,
                  'state': state,
                  'demo': {'speed': 1}
                }),
                200,
                headers: {'content-type': 'application/json'});
          }
          final command = asMap(jsonDecode(request.body));
          sent.add(command);
          state = {
            ...state,
            'projects': <dynamic>[
              <String, dynamic>{
                'id': 'project-flutter',
                'name': 'A local device',
                'brief': asMap(command['payload'])['brief'],
                'stage': 0,
                'done': List<bool>.filled(10, false),
                'answers': <String, dynamic>{},
                'activity': <dynamic>[],
                'requirements': <dynamic>[]
              }
            ]
          };
          return http.Response(
              jsonEncode({
                'snapshot': {
                  'revision': 1,
                  'state': state,
                  'demo': {'speed': 1}
                },
                'result': {'projectId': 'project-flutter'}
              }),
              200,
              headers: {'content-type': 'application/json'});
        }));
    final controller = TestKleos(service);
    await controller.refresh();
    await tester.pumpWidget(controller.scope(const KleosApp()));
    await tester.pumpAndSettle();
    expect(find.bySemanticsLabel('Builds'), findsOneWidget);
    expect(find.bySemanticsLabel('Connectors'), findsOneWidget);
    await tester.tap(find.widgetWithText(KButton, 'New build').last);
    await tester.pumpAndSettle();
    expect(find.text('Describe the device you want to build.'), findsOneWidget);
    expect(find.bySemanticsLabel('Builds'), findsOneWidget);
    expect(find.bySemanticsLabel('Connectors'), findsOneWidget);
    expect(tester.takeException(), isNull);
    await tester.enterText(find.byType(TextField), 'A local device');
    await tester.pump();
    await tester.tap(find.widgetWithText(KButton, 'Start build'));
    await tester.pumpAndSettle();
    expect(sent.single['type'], 'project.create');
    expect(asMap(sent.single['payload'])['brief'], 'A local device');
    expect(find.text('CO2 level'), findsOneWidget);
    expect(find.bySemanticsLabel('Builds'), findsOneWidget);
    expect(find.bySemanticsLabel('Connectors'), findsOneWidget);
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox.shrink());
    controller.dispose();
    semantics.dispose();
  });
}
