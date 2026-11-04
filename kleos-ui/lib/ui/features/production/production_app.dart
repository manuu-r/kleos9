import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import '../../../data/services/production_auth.dart';
import '../../../state/production_store.dart';
import '../../core/widgets.dart';
import '../admin/admin_page.dart';
import 'production_widgets.dart';
import 'production_sign_in_page.dart';
import 'production_projects_pages.dart';
import 'production_workspace_page.dart';
import 'production_run_page.dart';
export 'model_policy_editor.dart';
export 'production_projects_pages.dart';
export 'production_run_page.dart';
export 'production_widgets.dart';
export 'production_workspace_page.dart';

final productionRouterProvider = Provider<GoRouter>((ref) {
  final router = GoRouter(
      routes: [
        ShellRoute(
          builder: (context, state, child) => _AuthenticatedShell(child: child),
          routes: [
            GoRoute(
                path: '/', builder: (_, __) => const ProductionProjectsPage()),
            GoRoute(
                path: '/new',
                builder: (_, __) => const ProductionNewProjectPage()),
            GoRoute(
                path: '/projects/:id',
                builder: (_, state) => ProductionWorkspacePage(
                    projectId: state.pathParameters['id']!)),
            GoRoute(
                path: '/runs/:id',
                builder: (_, state) =>
                    ProductionRunPage(runId: state.pathParameters['id']!)),
            GoRoute(
                path: '/admin',
                builder: (_, __) => const SingleChildScrollView(
                    padding: EdgeInsets.all(24), child: AdminPage())),
          ],
        ),
      ],
      errorBuilder: (context, _) => Scaffold(
          body: Center(
              child: KButton(
                  label: 'Return to projects',
                  onPressed: () => context.go('/')))));
  ref.onDispose(router.dispose);
  return router;
});

class ProductionApp extends ConsumerWidget {
  const ProductionApp({super.key});
  @override
  Widget build(BuildContext context, WidgetRef ref) => MaterialApp.router(
        title: 'Kleos9',
        debugShowCheckedModeBanner: false,
        theme: kTheme(),
        scrollBehavior: const KScrollBehavior(),
        routerConfig: ref.watch(productionRouterProvider),
      );
}

/// Identity gate shared by the original application shell and diagnostic UI.
class ProductionAccessGate extends ConsumerWidget {
  const ProductionAccessGate({super.key, required this.child});
  final Widget child;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final account = ref.watch(accountProvider);
    if (account.isLoading) {
      return const Scaffold(body: SafeArea(child: KPageSkeleton()));
    }
    if (account.error is OrganizationSelectionRequired) {
      final selection = account.error! as OrganizationSelectionRequired;
      return Scaffold(
          body: SafeArea(
              child: ProductionPage(children: [
        const KSectionHeader(title: 'Choose your organization'),
        for (final organization in selection.organizations)
          KButton(
              label: organization.name,
              onPressed: () => ref
                  .read(accountProvider.notifier)
                  .selectOrganization(organization.id)),
      ])));
    }
    if (account.asData?.value == null) {
      return ProductionSignInPage(
          loading: account.isLoading,
          error: account.hasError ? account.error.toString() : null);
    }
    return child;
  }
}

class _AuthenticatedShell extends ConsumerWidget {
  const _AuthenticatedShell({required this.child});
  final Widget child;
  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final account = ref.watch(accountProvider);
    final session = account.asData?.value;
    if (account.error is OrganizationSelectionRequired) {
      final selection = account.error! as OrganizationSelectionRequired;
      return Scaffold(
          body: SafeArea(
              child: ProductionPage(children: [
        const KSectionHeader(title: 'Choose your organization'),
        ProductionNotice(selection.toString()),
        for (final organization in selection.organizations)
          KButton(
              label: organization.name,
              onPressed: () {
                context.go('/');
                ref
                    .read(accountProvider.notifier)
                    .selectOrganization(organization.id);
              }),
      ])));
    }
    if (session == null) {
      return ProductionSignInPage(
          loading: account.isLoading,
          error: account.hasError ? account.error.toString() : null);
    }
    return ListenableBuilder(
        listenable: FocusManager.instance,
        builder: (context, _) => Scaffold(
              // Mobile browsers can retain the last keyboard inset after navigation.
              // Only reserve that space while an actual text field is being edited.
              resizeToAvoidBottomInset: FocusManager
                      .instance.primaryFocus?.context
                      ?.findAncestorWidgetOfExactType<EditableText>() !=
                  null,
              appBar: AppBar(
                title: InkWell(
                    onTap: () => context.go('/'), child: const Text('Kleos9')),
                actions: [
                  if (session.organizations.length > 1)
                    PopupMenuButton<String>(
                        tooltip: 'Switch organization',
                        icon: const Icon(Icons.business_outlined),
                        onSelected: (id) {
                          if (id == session.tenantId) return;
                          context.go('/');
                          ref
                              .read(accountProvider.notifier)
                              .selectOrganization(id);
                        },
                        itemBuilder: (_) => [
                              for (final org in session.organizations)
                                CheckedPopupMenuItem(
                                    value: org.id,
                                    checked: org.id == session.tenantId,
                                    child: Text(org.name))
                            ]),
                  const Padding(
                      padding: EdgeInsets.only(right: 12),
                      child: Center(child: KBadge('Private beta'))),
                  if (const {'owner', 'admin'}.contains(session.role))
                    IconButton(
                        tooltip: 'Admin',
                        onPressed: () => context.go('/admin'),
                        icon: const Icon(Icons.admin_panel_settings_outlined)),
                  IconButton(
                      tooltip: 'Projects',
                      onPressed: () => context.go('/'),
                      icon: const Icon(Icons.dashboard_outlined)),
                  IconButton(
                      tooltip: 'Sign out',
                      onPressed: () async {
                        await ref.read(accountProvider.notifier).signOut();
                      },
                      icon: const Icon(Icons.logout)),
                ],
              ),
              body: SafeArea(
                  child: Align(
                      alignment: Alignment.topCenter,
                      child: ConstrainedBox(
                          constraints: const BoxConstraints(maxWidth: 1240),
                          child: child))),
            ));
  }
}
