import 'dart:convert';
import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'support.dart';
import 'package:kleos_ui/data/services/api_service.dart';
import 'package:kleos_ui/main.dart';
import 'package:kleos_ui/ui/core/widgets.dart';

TestKleos controller() => TestKleos(ApiService(
    baseUrl: 'http://local.test/api',
    client: MockClient((_) async => http.Response(
        jsonEncode({
          'revision': 0,
          'state': {
            'projects': [
              {
                'id': 'running',
                'name': 'Monitor',
                'brief': 'A room monitor',
                'stage': 1,
                'done': [true],
                'feasibility': 'running'
              }
            ],
            'connectors': <dynamic>[],
            'humanTasks': <dynamic>[]
          },
          'demo': {'speed': 1}
        }),
        200))));

void main() {
  for (final size in [
    const Size(320, 568),
    const Size(390, 844),
    const Size(844, 390),
    const Size(768, 1024),
    const Size(1024, 768),
    const Size(1280, 900)
  ]) {
    testWidgets(
        'home adapts navigation at $size with safe areas and larger text',
        (tester) async {
      tester.view.physicalSize = size;
      tester.view.devicePixelRatio = 1;
      tester.view.viewPadding = const FakeViewPadding(top: 24, bottom: 20);
      addTearDown(tester.view.reset);
      final c = controller();
      await c.refresh();
      await tester.pumpWidget(MediaQuery(
        data: MediaQueryData(
            size: size, textScaler: const TextScaler.linear(1.3)),
        child: c.scope(const KleosApp()),
      ));
      await tester.pumpAndSettle();
      expect(find.text('Build hardware from a sentence.'), findsOneWidget);
      if (size.width >= 1560) {
        expect(find.byType(NavigationRail), findsNothing);
        expect(find.byType(NavigationBar), findsNothing);
      } else if (size.width >= 700 && size.height >= 600) {
        expect(find.byType(NavigationRail), findsOneWidget);
      } else {
        expect(find.byType(NavigationBar), findsOneWidget);
      }
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox.shrink());
      c.dispose();
    });
  }

  testWidgets('phone navigation, keyboard and back keep the form usable',
      (tester) async {
    tester.view.physicalSize = const Size(390, 844);
    tester.view.devicePixelRatio = 1;
    tester.view.viewPadding = const FakeViewPadding(top: 44, bottom: 34);
    addTearDown(tester.view.reset);
    final c = controller();
    await c.refresh();
    await tester.pumpWidget(c.scope(const KleosApp()));
    await tester.pumpAndSettle();
    await tester.tap(find.widgetWithText(KButton, 'New'));
    await tester.pumpAndSettle();
    expect(find.byTooltip('Back to builds'), findsOneWidget);
    final input = find.byType(TextField);
    await tester.ensureVisible(input);
    await tester.tap(input);
    tester.view.viewInsets = const FakeViewPadding(bottom: 320);
    await tester.pumpAndSettle();
    expect(find.byType(NavigationBar), findsNothing);
    await tester.enterText(input, 'A portable air quality monitor');
    final submit = find.widgetWithText(KButton, 'Start build');
    await tester.ensureVisible(submit);
    await tester.pumpAndSettle();
    expect(submit.hitTestable(), findsOneWidget);
    expect(tester.takeException(), isNull);
    tester.view.resetViewInsets();
    FocusManager.instance.primaryFocus?.unfocus();
    await tester.pumpAndSettle();
    await tester.tap(find.byTooltip('Back to builds'));
    await tester.pumpAndSettle();
    expect(find.text('Build hardware from a sentence.'), findsOneWidget);
    await tester.tap(find.text('Tasks').last);
    await tester.pumpAndSettle();
    expect(find.text('Human tasks'), findsOneWidget);
    await tester.binding.handlePopRoute();
    await tester.pumpAndSettle();
    expect(find.text('Build hardware from a sentence.'), findsOneWidget);
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox.shrink());
    c.dispose();
  });

  test('Android emulator uses the host alias and iOS uses loopback', () {
    addTearDown(() => debugDefaultTargetPlatformOverride = null);
    debugDefaultTargetPlatformOverride = TargetPlatform.android;
    expect(ApiService.defaultApiBaseUrl(), 'http://10.0.2.2:8000/api');
    debugDefaultTargetPlatformOverride = TargetPlatform.iOS;
    expect(ApiService.defaultApiBaseUrl(), 'http://127.0.0.1:8000/api');
  });
}
