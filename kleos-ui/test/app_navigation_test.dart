import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter/semantics.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:kleos_ui/data/models/json.dart';
import 'support.dart';
import 'package:kleos_ui/data/services/api_service.dart';
import 'package:kleos_ui/main.dart';
import 'package:kleos_ui/ui/core/widgets.dart';

void main() {
  for (final size in [const Size(390, 844), const Size(1280, 900)]) {
    testWidgets('outside clicks keep the current route at $size', (tester) async {
      WidgetController.hitTestWarningShouldBeFatal = true;
      addTearDown(() => WidgetController.hitTestWarningShouldBeFatal = false);
      final semantics = tester.ensureSemantics();
      tester.view.physicalSize = size;
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.reset);
      final controller = TestKleos(ApiService(
          baseUrl: 'http://local.test/api',
          client: MockClient((_) async => http.Response(jsonEncode({
                'revision': 0,
                'state': {
                  'projects': <dynamic>[],
                  'connectors': <dynamic>[],
                  'humanTasks': <dynamic>[]
                },
                'demo': {'speed': 1}
              }), 200))));
      addTearDown(controller.dispose);
      await controller.refresh();
      final router = controller.container.read(routerProvider);
      await tester.pumpWidget(controller.scope(const KleosApp()));
      await tester.pumpAndSettle();
      for (final path in [
        '/human-tasks', '/new', '/connections', '/connections?tab=services',
        '/connections?tab=human', '/connections?tab=printers', '/provider',
        '/demand', '/developer', '/agent-access',
      ]) {
        router.go(path);
        await tester.pumpAndSettle();
        // Web exposes semantics nodes as clickable DOM regions. A header
        // action must never merge into a screen-sized node over the background.
        void checkNode(SemanticsNode node) {
          final data = node.getSemanticsData();
          if (data.hasAction(SemanticsAction.tap)) {
            expect(node.rect.height, lessThan(size.height / 2),
                reason: 'Oversized tap target: ${data.label} (${node.rect})');
          }
          node.visitChildren((child) {
            checkNode(child);
            return true;
          });
        }
        checkNode(tester.getSemantics(find.byType(Scaffold).first));
        await tester.tapAt(Offset(size.width - 6, size.height / 2));
        await tester.pumpAndSettle();
        expect(router.routeInformationProvider.value.uri.toString(), path);
        await tester.tap(find.byTooltip('Ask Kleos9 or jump to a page').evaluate().isNotEmpty
            ? find.byTooltip('Ask Kleos9 or jump to a page')
            : find.widgetWithText(KButton, 'Ask Kleos9 or jump to…  ⌘K'));
        await tester.pumpAndSettle();
        await tester.tapAt(const Offset(4, 100));
        await tester.pumpAndSettle();
        expect(find.byType(Dialog), findsNothing);
        expect(router.routeInformationProvider.value.uri.toString(), path);
      }
      for (final destination in {
        'Tasks': '/human-tasks',
        'Connectors': '/connections',
        'Providers': '/provider',
        'Developers': '/developer',
        'Builds': '/',
      }.entries) {
        await tester.ensureVisible(find.text(destination.key).first);
        await tester.pumpAndSettle();
        await tester.tap(find.text(destination.key).first);
        await tester.pumpAndSettle();
        expect(router.routeInformationProvider.value.uri.path, destination.value);
      }
      await tester.pumpWidget(const SizedBox.shrink());
      semantics.dispose();
    });
  }

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
