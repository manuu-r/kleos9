import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:kleos_ui/ui/features/home_pages.dart';
import 'package:kleos_ui/ui/features/project/project_workspace_page.dart';
import 'package:kleos_ui/data/models/json.dart';
import 'package:kleos_ui/data/models/production.dart';
import 'package:kleos_ui/data/services/api_service.dart';
import 'package:kleos_ui/state/kleos_store.dart';
import 'package:kleos_ui/state/production_store.dart';
import 'package:kleos_ui/ui/core/build_actions.dart';
import 'package:kleos_ui/ui/core/widgets.dart';

class Account extends AccountController {
  Account(this.role);
  final String role;
  @override
  Future<AccountSession?> build() async => AccountSession.fromJson(
      {'subject': 'user', 'tenantId': 'org', 'role': role});
}

class Service extends ApiService {
  final commands = <JsonMap>[];
  JsonMap initialState = {};
  bool fail = false;
  Completer<void>? pending;
  @override
  Future<JsonMap> load() async => {'revision': 1, 'state': initialState};
  @override
  Future<JsonMap> command(String type, JsonMap payload) async {
    commands.add({'type': type, 'payload': payload});
    if (pending != null) await pending!.future;
    if (fail) {
      throw const ApiException(
          'Only an organization administrator can delete this build.');
    }
    return {
      'snapshot': await load(),
      'result': {'deleted': true}
    };
  }
}

const project = <String, dynamic>{
  'id': 'build-1',
  'name': 'Desk air sensor',
  'production': true
};

Future<ProviderContainer> mount(WidgetTester tester, Service service,
    {String role = 'owner',
    VoidCallback? onDeleted,
    bool simulated = false}) async {
  final store = ProviderContainer(overrides: [
    apiServiceProvider.overrideWithValue(service),
    pollingEnabledProvider.overrideWithValue(false),
    accountProvider.overrideWith(() => Account(role)),
  ]);
  store.listen(kleosProvider, (_, __) {});
  await store.read(kleosProvider).refresh();
  await tester.pumpWidget(UncontrolledProviderScope(
      container: store,
      child: MaterialApp(
          theme: kTheme(),
          home: Scaffold(
              body: Consumer(
                  builder: (_, ref, __) => BuildActionsMenu(
                      project: {...project, if (simulated) 'production': false},
                      controller: ref.watch(kleosProvider),
                      onDeleted: onDeleted))))));
  await tester.pumpAndSettle();
  addTearDown(() async {
    await tester.pumpWidget(const SizedBox());
    store.dispose();
    service.close();
  });
  return store;
}

Future<void> openDelete(WidgetTester tester) async {
  await tester.tap(find.byTooltip('Build actions'));
  await tester.pumpAndSettle();
  await tester.tap(find.text('Delete build'));
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('delete requires explicit confirmation and cancel keeps build',
      (tester) async {
    final service = Service();
    await mount(tester, service);
    await openDelete(tester);
    expect(find.text('Delete this build?'), findsOneWidget);
    expect(find.text('Desk air sensor'), findsOneWidget);
    expect(find.textContaining('permanently clears'), findsOneWidget);
    expect(service.commands, isEmpty);
    await tester.tap(find.text('Keep build'));
    await tester.pumpAndSettle();
    expect(find.text('Delete this build?'), findsNothing);
    expect(service.commands, isEmpty);
  });
  testWidgets('confirmed deletion sends scoped command and reports success',
      (tester) async {
    var deleted = 0;
    final service = Service();
    await mount(tester, service, onDeleted: () => deleted++);
    await openDelete(tester);
    await tester.tap(find.text('Delete and clear build'));
    await tester.pumpAndSettle();
    expect(service.commands.single, {
      'type': 'project.delete',
      'payload': {'projectId': 'build-1'}
    });
    expect(deleted, 1);
    expect(find.text('Delete this build?'), findsNothing);
  });
  testWidgets('failed deletion keeps dialog open with actual backend error',
      (tester) async {
    final service = Service()..fail = true;
    await mount(tester, service);
    await openDelete(tester);
    await tester.tap(find.text('Delete and clear build'));
    await tester.pumpAndSettle();
    expect(
        find.text('Only an organization administrator can delete this build.'),
        findsOneWidget);
    expect(find.text('Delete this build?'), findsOneWidget);
    service.fail = false;
    await tester.tap(find.text('Delete and clear build'));
    await tester.pumpAndSettle();
    expect(service.commands, hasLength(2));
    expect(find.text('Delete this build?'), findsNothing);
  });
  testWidgets('pending delete prevents repeated submission', (tester) async {
    final service = Service()..pending = Completer<void>();
    await mount(tester, service);
    await openDelete(tester);
    await tester.tap(find.text('Delete and clear build'));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 50));
    expect(find.text('Deleting…'), findsOneWidget);
    expect(
        tester
            .widget<FilledButton>(
                find.widgetWithText(FilledButton, 'Deleting…'))
            .onPressed,
        isNull);
    expect(
        tester
            .widget<TextButton>(find.widgetWithText(TextButton, 'Keep build'))
            .onPressed,
        isNull);
    expect(service.commands, hasLength(1));
    service.pending!.complete();
    await tester.pumpAndSettle();
  });
  for (final role in ['member', 'viewer']) {
    testWidgets('$role has no delete action for a real project',
        (tester) async {
      await mount(tester, Service(), role: role);
      expect(find.byTooltip('Build actions'), findsNothing);
    });
  }
  testWidgets('simulated build uses the same explicit deletion flow',
      (tester) async {
    final service = Service();
    await mount(tester, service, role: 'viewer', simulated: true);
    await openDelete(tester);
    expect(find.textContaining('simulated build'), findsOneWidget);
    await tester.tap(find.text('Delete and clear build'));
    await tester.pumpAndSettle();
    expect(service.commands.single['type'], 'project.delete');
  });
  testWidgets('dashboard overflow opens deletion without opening the build',
      (tester) async {
    final service = Service()
      ..initialState = {
        'projects': [
          {
            ...project,
            'production': false,
            'brief': 'Measure room air',
            'stage': 0,
            'done': <bool>[]
          }
        ],
        'connectors': <JsonMap>[],
      };
    final store = ProviderContainer(overrides: [
      apiServiceProvider.overrideWithValue(service),
      pollingEnabledProvider.overrideWithValue(false),
    ]);
    store.listen(kleosProvider, (_, __) {});
    await store.read(kleosProvider).refresh();
    final router = GoRouter(routes: [
      GoRoute(
          path: '/',
          builder: (_, __) => Scaffold(
              body: SingleChildScrollView(
                  child: Consumer(
                      builder: (_, ref, __) => ProjectsPage(
                          controller: ref.watch(kleosProvider)))))),
      GoRoute(
          path: '/projects/:id',
          builder: (_, __) => const Scaffold(body: Text('Opened build'))),
    ]);
    await tester.pumpWidget(UncontrolledProviderScope(
        container: store,
        child: MaterialApp.router(theme: kTheme(), routerConfig: router)));
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.byTooltip('Build actions'));
    await openDelete(tester);
    expect(router.routeInformationProvider.value.uri.path, '/');
    expect(find.text('Opened build'), findsNothing);
    await tester.tap(find.text('Keep build'));
    await tester.pumpAndSettle();
    expect(router.routeInformationProvider.value.uri.path, '/');
    expect(service.commands, isEmpty);
    await tester.pumpWidget(const SizedBox());
    router.dispose();
    store.dispose();
    service.close();
  });
  testWidgets(
      'workspace returns home when deletion removes its menu before receipt',
      (tester) async {
    final service = Service()
      ..pending = Completer<void>()
      ..initialState = {
        'projects': [
          {
            ...project,
            'production': false,
            'brief': 'Measure room air',
            'stage': 0,
            'done': List<bool>.filled(10, false)
          }
        ],
      };
    final store = ProviderContainer(overrides: [
      apiServiceProvider.overrideWithValue(service),
      pollingEnabledProvider.overrideWithValue(false),
    ]);
    store.listen(kleosProvider, (_, __) {});
    await store.read(kleosProvider).refresh();
    final router = GoRouter(initialLocation: '/projects/build-1?s=0', routes: [
      GoRoute(
          path: '/',
          builder: (_, __) => const Scaffold(body: Text('Build dashboard'))),
      GoRoute(
          path: '/projects/:id',
          builder: (_, __) => Scaffold(
              body: SingleChildScrollView(
                  child: Consumer(
                      builder: (_, ref, __) => ProjectWorkspacePage(
                          controller: ref.watch(kleosProvider),
                          projectId: 'build-1',
                          stage: 0))))),
    ]);
    await tester.pumpWidget(UncontrolledProviderScope(
        container: store,
        child: MaterialApp.router(theme: kTheme(), routerConfig: router)));
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.byTooltip('Build actions'));
    await openDelete(tester);
    await tester.tap(find.text('Delete and clear build'));
    await tester.pump();
    // A progress event hides the deleted workspace while its command receipt
    // is still in flight. Its former action menu has now been disposed.
    service.initialState = {'projects': <JsonMap>[]};
    await store.read(kleosProvider).refresh();
    await tester.pump();
    expect(find.byTooltip('Build actions'), findsNothing);
    expect(find.text('Project not found.'), findsOneWidget);
    service.pending!.complete();
    await tester.pumpAndSettle();
    expect(router.routeInformationProvider.value.uri.path, '/');
    expect(find.text('Build dashboard'), findsOneWidget);
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox());
    router.dispose();
    store.dispose();
    service.close();
  });
}
