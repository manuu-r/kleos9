import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:kleos_ui/data/models/json.dart';
import 'package:kleos_ui/data/services/api_service.dart';
import 'package:kleos_ui/ui/core/theme.dart';
import 'package:kleos_ui/ui/features/home_pages.dart';
import 'support.dart';

void main() {
  for (final width in [320.0, 1024.0]) {
    testWidgets('explicit demo creation works at ${width}px', (tester) async {
      tester.view.physicalSize = Size(width, 1000);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      final commands = <JsonMap>[];
      final state = {
        'production': true,
        'simulationAvailable': true,
        'projects': <JsonMap>[],
        'connectors': <JsonMap>[]
      };
      final controller = TestKleos(ApiService(
        baseUrl: 'http://local.test/api',
        client: MockClient((request) async {
          if (request.method == 'POST') {
            commands.add(asMap(jsonDecode(request.body)));
            return http.Response(
                jsonEncode({
                  'snapshot': {'revision': 1, 'state': state},
                  'result': {'projectId': 'demo'}
                }),
                200);
          }
          return http.Response(
              jsonEncode({'revision': 0, 'state': state}), 200);
        }),
      ));
      await controller.refresh();
      final router = GoRouter(routes: [
        GoRoute(
            path: '/',
            builder: (_, __) => Scaffold(
                  body: SingleChildScrollView(
                      child: Padding(
                    padding: const EdgeInsets.all(20),
                    child: NewBuildPage(controller: controller.view),
                  )),
                )),
        GoRoute(
            path: '/projects/:id',
            builder: (_, __) => const Scaffold(
                  body: Text('Demo workspace opened'),
                )),
      ]);
      await tester.pumpWidget(controller.scope(MaterialApp.router(
        theme: kTheme(),
        routerConfig: router,
      )));
      await tester.pumpAndSettle();
      expect(tester.widget<SwitchListTile>(find.byType(SwitchListTile)).value,
          false);
      await tester.ensureVisible(find.text('Simulated demo'));
      await tester.tap(find.text('Simulated demo'));
      await tester.pumpAndSettle();
      await tester.enterText(find.byType(TextField), 'A reference monitor');
      await tester.pumpAndSettle();
      await tester.ensureVisible(find.text('Start demo build'));
      await tester.tap(find.text('Start demo build'));
      await tester.pumpAndSettle();
      await tester.runAsync(() async {
        await Future<void>.delayed(const Duration(milliseconds: 30));
      });
      await tester.pumpAndSettle();
      expect(controller.error, isNull);
      expect(commands.single, {
        'type': 'project.create',
        'payload': {'brief': 'A reference monitor', 'simulation': true}
      });
      expect(find.text('Demo workspace opened'), findsOneWidget);
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox.shrink());
      router.dispose();
      controller.dispose();
    });
  }
}
