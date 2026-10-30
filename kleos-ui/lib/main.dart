import 'package:flutter/material.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter/semantics.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'state/kleos_store.dart';
import 'ui/core/app_shell.dart';
import 'ui/core/theme.dart';
import 'ui/features/connections_page.dart';
import 'ui/features/developer_page.dart';
import 'ui/features/home_pages.dart';
import 'ui/features/portal_pages.dart';
import 'ui/features/project/project_workspace_page.dart';
import 'ui/features/production/production_app.dart';

const usesProductionBackend =
    String.fromEnvironment('KLEOS_BACKEND', defaultValue: 'mock') ==
        'production';

SemanticsHandle? semanticsHandle;
void main() {
  WidgetsFlutterBinding.ensureInitialized();
  LicenseRegistry.addLicense(() async* {
    yield LicenseEntryWithLineBreaks(['Anatomy'],
        await rootBundle.loadString('assets/licenses/anatomy-LICENSE.txt'));
  });
  if (kIsWeb) semanticsHandle = SemanticsBinding.instance.ensureSemantics();
  runApp(const ProviderScope(child: KleosApp()));
}

/// Wraps a route body so it reads the store itself. Each route rebuilds only
/// when the store publishes, never on the shell's own rebuilds.
Widget _routed(Widget Function(KleosController c) page) => AppPage(
    child: Consumer(builder: (_, ref, __) => page(ref.watch(kleosProvider))));

/// Short fade with a small rise: fast enough to feel instant, enough motion to
/// show where the content came from. Collapses to a cut with reduced motion.
CustomTransitionPage<void> _page(GoRouterState state, Widget child) =>
    CustomTransitionPage<void>(
        key: state.pageKey,
        child: child,
        transitionDuration: const Duration(milliseconds: 220),
        reverseTransitionDuration: const Duration(milliseconds: 120),
        transitionsBuilder: (context, animation, secondary, child) {
          if (MediaQuery.disableAnimationsOf(context)) return child;
          final curved = CurvedAnimation(parent: animation, curve: KMotion.out);
          return FadeTransition(
              opacity: curved,
              child: SlideTransition(
                  position:
                      Tween(begin: const Offset(0, .012), end: Offset.zero)
                          .animate(curved),
                  child: child));
        });

final routerProvider = Provider<GoRouter>((ref) {
  final router = GoRouter(
      routes: [
        ShellRoute(
            builder: (context, state, child) => usesProductionBackend
                ? ProductionAccessGate(
                    child: _ConnectedRouteScope(
                        uri: state.uri,
                        child: AppShell(uri: state.uri, child: child)))
                : AppShell(uri: state.uri, child: child),
            routes: [
              GoRoute(
                  path: '/',
                  pageBuilder: (context, state) => _page(
                      state, _routed((c) => ProjectsPage(controller: c)))),
              GoRoute(
                  path: '/new',
                  pageBuilder: (context, state) => _page(
                      state, _routed((c) => NewBuildPage(controller: c)))),
              GoRoute(
                  path: '/projects/:id',
                  pageBuilder: (context, state) => _page(
                      state,
                      _routed((c) => ProjectWorkspacePage(
                          controller: c,
                          projectId: state.pathParameters['id']!,
                          stage: int.tryParse(
                              state.uri.queryParameters['s'] ?? ''))))),
              if (usesProductionBackend)
                GoRoute(
                    path: '/runs/:id',
                    builder: (_, state) =>
                        ProductionRunPage(runId: state.pathParameters['id']!)),
              GoRoute(
                  path: '/human-tasks',
                  pageBuilder: (context, state) => _page(
                      state, _routed((c) => HumanTasksPage(controller: c)))),
              GoRoute(
                  path: '/connections',
                  pageBuilder: (context, state) => _page(
                      state,
                      _routed((c) => ConnectionsPage(
                          controller: c,
                          tab: state.uri.queryParameters['tab'] ?? 'tools',
                          connectId: state.uri.queryParameters['connect'],
                          back: state.uri.queryParameters['back'])))),
              GoRoute(
                  path: '/provider',
                  pageBuilder: (context, state) => _page(
                      state,
                      _routed((c) => ProviderPage(
                          controller: c,
                          tab: state.uri.queryParameters['tab'],
                          projectId: state.uri.queryParameters['projectId'])))),
              GoRoute(
                  path: '/demand',
                  pageBuilder: (context, state) =>
                      _page(state, _routed((c) => DemandPage(controller: c)))),
              GoRoute(
                  path: '/developer',
                  pageBuilder: (context, state) => _page(
                      state,
                      _routed((c) => DeveloperPage(
                          controller: c,
                          kind: state.uri.queryParameters['kind'],
                          capability: state.uri.queryParameters['cap'])))),
              GoRoute(
                  path: '/agent-access',
                  pageBuilder: (context, state) => _page(
                      state, _routed((c) => AgentAccessPage(controller: c)))),
            ])
      ],
      errorBuilder: (context, state) => Scaffold(
          body: Center(
              child: TextButton(
                  onPressed: () => context.go('/'),
                  child: const Text('Page not found. Return to builds')))));
  ref.onDispose(router.dispose);
  return router;
});

class KleosApp extends ConsumerWidget {
  const KleosApp({super.key});
  @override
  Widget build(BuildContext context, WidgetRef ref) => MaterialApp.router(
      title: 'Kleos9',
      debugShowCheckedModeBanner: false,
      theme: kTheme(),
      scrollBehavior: const KScrollBehavior(),
      builder: (context, child) => AnnotatedRegion<SystemUiOverlayStyle>(
            value: SystemUiOverlayStyle.light.copyWith(
              statusBarColor: Colors.transparent,
              systemNavigationBarColor: KColors.bg,
              systemNavigationBarIconBrightness: Brightness.light,
            ),
            child: child!,
          ),
      routerConfig: ref.watch(routerProvider));
}

/// The original screens keep one active workspace subscription while summaries
/// remain lightweight. Authentication scopes the underlying repository.
class _ConnectedRouteScope extends ConsumerWidget {
  const _ConnectedRouteScope({required this.uri, required this.child});
  final Uri uri;
  final Widget child;
  @override
  Widget build(BuildContext context, WidgetRef ref) {
    ref.watch(apiServiceProvider);
    final parts = uri.pathSegments;
    final id = parts.length > 1 && parts.first == 'projects' ? parts[1] : null;
    Future.microtask(() {
      if (context.mounted) {
        ref.read(kleosProvider.notifier).setActiveProject(id);
      }
    });
    return child;
  }
}
