import 'dart:convert';
import 'dart:io';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:kleos_ui/data/models/json.dart';
import 'support.dart';
import 'navigation_audit.dart';
import 'package:kleos_ui/data/services/api_service.dart';
import 'package:kleos_ui/ui/core/app_shell.dart';
import 'package:kleos_ui/ui/core/theme.dart';
import 'package:kleos_ui/ui/features/connections_page.dart';
import 'package:kleos_ui/ui/features/developer_page.dart';
import 'package:kleos_ui/ui/features/home_pages.dart';
import 'package:kleos_ui/ui/features/portal_pages.dart';

void main() {
  final fixtures = asMap(jsonDecode(
      File('../mock-backend/app/data/fixtures.json').readAsStringSync()));
  final state = <String, dynamic>{
    ...asMap(fixtures['seed']),
    'connectionRuns': <String, dynamic>{},
    'printerProfiles': fixtures['printers'],
    'developerMeta': <String, dynamic>{
      'templates': fixtures['templates'],
      'fixHints': fixtures['fixHints']
    },
    'agentExchange': <dynamic>[],
    'agentExchangeTotal': asList(fixtures['exchange']).length,
    'providerSummary': fixtures['providerSummary']
  };
  for (final kind in ['tool', 'driver']) {
    testWidgets('developer renders a $kind manifest', (tester) async {
      tester.view.physicalSize = const Size(1280, 900);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      final controller = TestKleos(ApiService(
        baseUrl: 'http://local.test/api',
        client: MockClient((_) async => http.Response(jsonEncode({
          'revision': 0,
          'state': {
            ...state,
            'dev': {
              'kind': kind,
              'manifest': jsonEncode({
                'provider_slug': 'example',
                kind: {'operations': {'run': 'supported'}}
              })
            }
          },
          'demo': {'speed': 1}
        }), 200, headers: {'content-type': 'application/json'})),
      ));
      await controller.refresh();
      final router = GoRouter(routes: [GoRoute(path: "/", builder: (context, route) => AppShell(uri: route.uri, child: AppPage(child: DeveloperPage(controller: controller.view))))]);
      await tester.pumpWidget(controller.scope(MaterialApp.router(
        theme: kTheme(), routerConfig: router,
      )));
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
      expect(find.textContaining('example.run'), findsOneWidget);
      await tester.pumpWidget(const SizedBox.shrink());
      router.dispose();
      controller.dispose();
    });
  }
  for (final width in [320.0, 390.0, 768.0, 1024.0, 1280.0]) {
    for (final path in [
      '/',
      '/new',
      '/connections',
      '/connections?tab=services',
      '/connections?tab=human',
      '/connections?tab=printers',
      '/human-tasks',
      '/provider',
      '/demand',
      '/developer',
      '/agent-access'
    ]) {
      testWidgets(
          '$path renders its full native screen at ${width.toInt()}px without overflow',
          (tester) async {
        final semantics = tester.ensureSemantics();
        tester.view.physicalSize = Size(width, 900);
        tester.view.devicePixelRatio = 1;
        addTearDown(tester.view.resetPhysicalSize);
        addTearDown(tester.view.resetDevicePixelRatio);
        final controller = TestKleos(ApiService(
            baseUrl: 'http://local.test/api',
            client: MockClient((_) async => http.Response(
                    jsonEncode({
                      'revision': 0,
                      'state': state,
                      'demo': {'speed': 1}
                    }),
                    200,
                    headers: {
                      'content-type': 'application/json; charset=utf-8'
                    }))));
        await controller.refresh();
        final uri = Uri.parse(path);
        Widget page() => switch (uri.path) {
              '/' => ProjectsPage(controller: controller.view),
              '/new' => NewBuildPage(controller: controller.view),
              '/connections' => ConnectionsPage(controller: controller.view,
                  tab: uri.queryParameters['tab'] ?? 'tools'),
              '/human-tasks' => HumanTasksPage(controller: controller.view),
              '/provider' => ProviderPage(controller: controller.view),
              '/demand' => DemandPage(controller: controller.view),
              '/developer' => DeveloperPage(controller: controller.view),
              _ => AgentAccessPage(controller: controller.view),
            };
        final router = GoRouter(initialLocation: path, routes: [
          GoRoute(
              path: uri.path,
              builder: (context, route) =>
                  AppShell(uri: route.uri, child: AppPage(child: page())))
        ]);
        await tester.pumpWidget(controller.scope(MaterialApp.router(
          theme: kTheme(),
          routerConfig: router,
          builder: (context, child) => MediaQuery(
            data: MediaQuery.of(context).copyWith(
                textScaler:
                    TextScaler.linear(width == 320 || width == 768 ? 1.3 : 1)),
            child: child!,
          ),
        )));
        await tester.pumpAndSettle();
        expect(tester.takeException(), isNull);
        expectBoundedClickTargets(tester, screen: '$path at $width');
        await tester.tapAt(Offset(width - 2, 500));
        await tester.pumpAndSettle();
        expect(router.routeInformationProvider.value.uri.toString(), path);
        await tester.pumpWidget(const SizedBox.shrink());
        semantics.dispose();
        router.dispose();
        controller.dispose();
      });
    }
  }
}
