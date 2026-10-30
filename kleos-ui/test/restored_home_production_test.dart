import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:kleos_ui/data/services/api_service.dart';
import 'package:kleos_ui/state/kleos_store.dart';
import 'package:kleos_ui/state/production_store.dart';
import 'package:kleos_ui/ui/core/app_shell.dart';
import 'package:kleos_ui/ui/core/theme.dart';
import 'package:kleos_ui/ui/features/home_pages.dart';
import 'package:kleos_ui/ui/features/connections_page.dart';
import 'production_ui_test.dart' show TestAccount, TestRepository, snapshot;

void main() {
  const production = String.fromEnvironment('KLEOS_BACKEND') == 'production';
  for (final width in [320.0, 390.0, 1280.0]) {
    testWidgets('original navigation and screens retain their layout at $width',
        (tester) async {
      tester.view.physicalSize = Size(width, 900);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.reset);
      final repository = TestRepository(snapshot());
      final service = ApiService(
          client: MockClient((_) async => http.Response(
              jsonEncode({
                'revision': 1,
                'state': {'production': true, 'projects': <Object?>[], 'connectors': <Object?>[]},
                'demo': {'speed': 1}
              }),
              200)));
      final container = ProviderContainer(overrides: [
        apiServiceProvider.overrideWithValue(service),
        pollingEnabledProvider.overrideWithValue(false),
        accountProvider.overrideWith(TestAccount.new),
        productionRepositoryProvider.overrideWithValue(repository),
      ]);
      container.listen(kleosProvider, (_, __) {});
      await container.read(kleosProvider).refresh();
      final router = GoRouter(initialLocation: '/new', routes: [
        GoRoute(
            path: '/new',
            builder: (context, route) => AppShell(
                uri: route.uri,
                child: AppPage(
                    child: NewBuildPage(
                        controller: container.read(kleosProvider))))),
        GoRoute(
            path: '/connections',
            builder: (context, route) => AppShell(
                uri: route.uri,
                child: AppPage(
                    child: ConnectionsPage(
                        controller: container.read(kleosProvider))))),
      ]);
      await tester.pumpWidget(UncontrolledProviderScope(
          container: container,
          child: MaterialApp.router(theme: kTheme(), routerConfig: router)));
      await tester.pumpAndSettle();
      expect(find.text('AI providers by lane'), findsNothing);
      expect(
          find.text(
              'Scripted around an air-quality monitor in this prototype.'),
          findsNothing);
      expect(tester.takeException(), isNull);
      router.go('/connections');
      await tester.pumpAndSettle();
      expect(find.text('AI model providers'), findsNothing);
      expect(find.text('Engineering tools'), findsWidgets);
      expect(find.text('Services'), findsWidgets);
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox());
      router.dispose();
      container.dispose();
      repository.dispose();
    }, skip: !production);
  }
}
