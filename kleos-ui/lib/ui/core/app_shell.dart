import 'dart:ui' show ImageFilter;
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import '../../data/models/json.dart';
import '../../state/kleos_store.dart';
import '../../state/production_store.dart';
import 'widgets.dart';

const _nav = [
  (label: 'Builds', path: '/', icon: Icons.inventory_2_outlined),
  (label: 'Tasks', path: '/human-tasks', icon: Icons.pan_tool_outlined),
  (label: 'Connectors', path: '/connections', icon: Icons.power_outlined),
  (label: 'Providers', path: '/provider', icon: Icons.storefront_outlined),
  (label: 'Developers', path: '/developer', icon: Icons.code)
];

class AppShell extends ConsumerWidget {
  const AppShell(
      {super.key,
      required this.uri,
      required this.child,
      this.showAccount = false});
  final Uri uri;
  final Widget child;

  /// Production only: shows the signed-in account menu with Admin and Sign out.
  final bool showAccount;
  int get index => uri.path == '/new' || uri.path.startsWith('/projects')
      ? 0
      : uri.path == '/demand'
          ? 3
          : uri.path == '/agent-access'
              ? 4
              : _nav.indexWhere((n) => n.path == uri.path).clamp(0, 4);
  Widget _bottomNav(BuildContext context) => NavigationBar(
        selectedIndex: index,
        onDestinationSelected: (i) => context.go(_nav[i].path),
        height: 72,
        backgroundColor: KColors.surface,
        indicatorColor: KColors.accent.withValues(alpha: .14),
        labelTextStyle: WidgetStateProperty.resolveWith((states) => TextStyle(
              fontSize: 10,
              color: states.contains(WidgetState.selected)
                  ? KColors.text
                  : KColors.muted,
            )),
        destinations: [
          for (final item in _nav)
            NavigationDestination(
              icon: Icon(item.icon, size: 21),
              selectedIcon: Icon(item.icon, size: 21, color: KColors.accent),
              label: item.label,
            ),
        ],
      );

  Widget _rail(BuildContext context) => NavigationRail(
        selectedIndex: index,
        onDestinationSelected: (i) => context.go(_nav[i].path),
        backgroundColor: KColors.surface,
        indicatorColor: KColors.accent.withValues(alpha: .14),
        labelType: NavigationRailLabelType.all,
        minWidth: 92,
        selectedLabelTextStyle:
            const TextStyle(fontSize: 11, color: KColors.text),
        unselectedLabelTextStyle:
            const TextStyle(fontSize: 11, color: KColors.muted),
        destinations: [
          for (final item in _nav)
            NavigationRailDestination(
              icon: Icon(item.icon, size: 21),
              selectedIcon: Icon(item.icon, size: 21, color: KColors.accent),
              label: Text(item.label),
            ),
        ],
      );

  @override
  Widget build(BuildContext context, WidgetRef ref) => ListenableBuilder(
      listenable: FocusManager.instance,
      builder: (context, _) => _build(context, ref));

  Widget _build(BuildContext context, WidgetRef ref) {
    final controller = ref.watch(kleosProvider);
    final width = MediaQuery.sizeOf(context).width;
    final textScale = MediaQuery.textScalerOf(context).scale(1);
    final mobile = width < 1200 * textScale.clamp(1, 1.5);
    final rail =
        mobile && width >= 700 && MediaQuery.sizeOf(context).height >= 600;
    final phone = mobile && !rail;
    final detail = !(_nav.any((item) => item.path == uri.path));
    final editing = FocusManager.instance.primaryFocus?.context
            ?.findAncestorWidgetOfExactType<EditableText>() !=
        null;
    final keyboardOpen = editing && MediaQuery.viewInsetsOf(context).bottom > 0;
    final headerBar = Container(
        height: phone ? 60 : 68,
        padding: EdgeInsets.symmetric(horizontal: phone ? 12 : 24),
        decoration: BoxDecoration(
            color: KColors.bg.withValues(alpha: .72),
            border: const Border(bottom: BorderSide(color: KColors.hairline))),
        child: Row(children: [
          if (detail)
            IconButton(
              tooltip: 'Back to builds',
              onPressed: () => context.go('/'),
              icon: const Icon(Icons.arrow_back, size: 20),
            )
          else
            InkWell(
                onTap: () => context.go('/'),
                child: Row(children: [
                  Stack(clipBehavior: Clip.none, children: [
                    Container(
                        width: 28,
                        height: 28,
                        alignment: Alignment.center,
                        decoration: BoxDecoration(
                            gradient: const LinearGradient(
                                begin: Alignment.topCenter,
                                end: Alignment.bottomCenter,
                                colors: [Color(0xffffffff), Color(0xffd5d5da)]),
                            borderRadius: BorderRadius.circular(8)),
                        child: const Text('K9',
                            style: TextStyle(
                                color: KColors.bg,
                                fontSize: 10,
                                letterSpacing: -.2,
                                fontWeight: FontWeight.w800))),
                    Positioned(
                        right: -3,
                        top: -3,
                        child: Container(
                            width: 7,
                            height: 7,
                            decoration: BoxDecoration(
                                color: KColors.accent,
                                borderRadius: BorderRadius.circular(2))))
                  ]),
                  const SizedBox(width: 9),
                  if (width >= 360)
                    const Text('Kleos9',
                        style: TextStyle(
                            fontSize: 13, fontWeight: FontWeight.w600))
                ])),
          const Spacer(),
          if (!mobile)
            Container(
                padding: const EdgeInsets.all(3),
                decoration: BoxDecoration(
                    color: KColors.surface.withValues(alpha: .7),
                    border: Border.all(color: KColors.border),
                    borderRadius: BorderRadius.circular(KRadius.control + 4)),
                child: Row(mainAxisSize: MainAxisSize.min, children: [
                  for (var i = 0; i < _nav.length; i++)
                    _NavItem(
                        label: _nav[i].label,
                        icon: _nav[i].icon,
                        selected: i == index,
                        onTap: () => context.go(_nav[i].path))
                ])),
          const Spacer(),
          _RunningChip(compact: phone),
          const SizedBox(width: 8),
          if (width > 1120)
            SizedBox(
                width: 196,
                child: KButton(
                    label: 'Ask Kleos9 or jump to…  ⌘K',
                    compact: true,
                    icon: Icons.auto_awesome_outlined,
                    onPressed: () => showCommandMenu(context)))
          else
            IconButton(
                tooltip: 'Ask Kleos9 or jump to a page',
                onPressed: () => showCommandMenu(context),
                icon: const Icon(Icons.search, size: 20)),
          if (showAccount) const _AccountMenu(),
          const SizedBox(width: 4),
          KButton(
              label: mobile ? 'New' : 'New build',
              primary: true,
              compact: true,
              icon: Icons.add,
              onPressed: () => context.go('/new')),
        ]));
    final header = ClipRect(
        child: BackdropFilter(
            filter: ImageFilter.blur(sigmaX: 18, sigmaY: 18),
            child: headerBar));
    final content = controller.state.isEmpty
        ? Center(
            child: controller.loading
                ? const KPageSkeleton()
                : kColumn([
                    Text(controller.error ??
                        'Waiting for the local mock backend'),
                    KButton(label: 'Try again', onPressed: controller.refresh)
                  ], gap: 16))
        : child;
    return CallbackShortcuts(
        bindings: {
          const SingleActivator(LogicalKeyboardKey.keyK, meta: true): () =>
              showCommandMenu(context),
          const SingleActivator(LogicalKeyboardKey.keyK, control: true): () =>
              showCommandMenu(context)
        },
        child: PopScope(
            canPop: uri.path == '/',
            onPopInvokedWithResult: (didPop, _) {
              if (!didPop) context.go('/');
            },
            child: Stack(children: [
              const Positioned.fill(child: KAmbient()),
              Scaffold(
                backgroundColor: Colors.transparent,
                resizeToAvoidBottomInset: editing,
                bottomNavigationBar:
                    phone && !keyboardOpen ? _bottomNav(context) : null,
                body: SafeArea(
                    bottom: !phone || keyboardOpen,
                    child: Column(children: [
                      header,
                      if (controller.error != null &&
                          controller.state.isNotEmpty)
                        Container(
                            width: double.infinity,
                            padding: const EdgeInsets.symmetric(
                                horizontal: 24, vertical: 12),
                            color: KColors.fail.withValues(alpha: .08),
                            child: Row(children: [
                              Expanded(
                                  child: Text(controller.error!,
                                      style: const TextStyle(
                                          color: KColors.fail, fontSize: 12))),
                              KButton(
                                  label: 'Dismiss',
                                  compact: true,
                                  onPressed: controller.dismissError)
                            ])),
                      Expanded(
                          child: Row(children: [
                        if (rail) _rail(context),
                        Expanded(
                            child: Semantics(
                                container: true,
                                explicitChildNodes: true,
                                child: content)),
                      ]))
                    ])),
              ),
            ])));
  }
}

class AppPage extends StatelessWidget {
  const AppPage({super.key, required this.child});
  final Widget child;
  @override
  Widget build(BuildContext context) {
    final mobile = MediaQuery.sizeOf(context).width < 1000;
    return SingleChildScrollView(
      keyboardDismissBehavior: ScrollViewKeyboardDismissBehavior.onDrag,
      key: ValueKey(GoRouterState.of(context).uri.toString()),
      child: Center(
          child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 1200),
        child: Padding(
          padding: EdgeInsets.fromLTRB(mobile ? 16 : 32, mobile ? 24 : 40,
              mobile ? 16 : 32, mobile ? 32 : 56),
          child: SelectionArea(
              child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                child,
                const SizedBox(height: 72),
                const Center(
                    child: KMuted(
                        String.fromEnvironment('KLEOS_BACKEND',
                                    defaultValue: 'mock') ==
                                'production'
                            ? 'Prototype. Simulation examples remain available. New builds use real engineering runs; physical fulfillment remains simulated.'
                            : 'Prototype. Agent runs, quotes, orders, human jobs, prints and test results are simulated by the connected API.',
                        size: 10)),
              ])),
        ),
      )),
    );
  }
}

class _NavItem extends StatelessWidget {
  const _NavItem(
      {required this.label,
      required this.icon,
      required this.selected,
      required this.onTap});
  final String label;
  final IconData icon;
  final bool selected;
  final VoidCallback onTap;
  @override
  Widget build(BuildContext context) => KTap(
      flat: true,
      radius: KRadius.control,
      onTap: onTap,
      builder: (context, hovered) => AnimatedContainer(
          duration: KMotion.base,
          curve: KMotion.out,
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 9),
          decoration: BoxDecoration(
              color: selected
                  ? KColors.surface3
                  : hovered
                      ? KColors.surface2
                      : Colors.transparent,
              borderRadius: BorderRadius.circular(KRadius.control),
              border: Border.all(
                  color: selected ? KColors.borderStrong : Colors.transparent)),
          child: Row(children: [
            Icon(icon,
                size: 15, color: selected ? KColors.accent : KColors.faint),
            const SizedBox(width: 8),
            Text(label,
                style: TextStyle(
                    fontSize: 12,
                    fontWeight: selected ? FontWeight.w500 : FontWeight.w400,
                    color: selected ? KColors.text : KColors.muted))
          ])));
}

class _RunningChip extends ConsumerWidget {
  const _RunningChip({this.compact = false});
  final bool compact;
  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final running = <({String label, String detail, String route})>[];
    for (final p in ref.watch(projectsProvider)) {
      final id = p['id'];
      final name = '${p['name']}';
      if (p['feasibility'] == 'running') {
        running.add((
          label: 'Researching feasibility',
          detail: name,
          route: '/projects/$id?s=1'
        ));
      }
      if (asMaps(p['interfaces']).any((i) => i['state'] == 'RUNNING')) {
        running.add((
          label: 'Solving interfaces',
          detail: name,
          route: '/projects/$id?s=3'
        ));
      }
      if (p['generation'] == 'running') {
        running.add((
          label: 'Generating designs',
          detail: name,
          route: '/projects/$id?s=4'
        ));
      }
      if (p['verifyRun'] == 'running' ||
          asMaps(p['artifacts']).any((a) => a['status'] == 'generating')) {
        running.add(
            (label: 'Verifying', detail: name, route: '/projects/$id?s=5'));
      }
      if (['quoting', 'ordering'].contains(p['procurement'])) {
        running.add((
          label: 'Procuring parts',
          detail: name,
          route: '/projects/$id?s=6'
        ));
      }
      if (asMap(p['homePrint']).isNotEmpty &&
          !['done', 'idle'].contains(asMap(p['homePrint'])['state'])) {
        running.add((
          label: 'Printing at home',
          detail: name,
          route: '/projects/$id?s=6'
        ));
      }
      if (p['activation'] == 'running') {
        running.add((
          label: 'Running self-test',
          detail: name,
          route: '/projects/$id?s=9'
        ));
      }
    }
    if (running.isEmpty) return const SizedBox.shrink();
    return PopupMenuButton<int>(
        tooltip: 'Running work',
        onSelected: (i) => context.go(running[i].route),
        itemBuilder: (context) => [
              for (var i = 0; i < running.length; i++)
                PopupMenuItem(
                    value: i,
                    child: kColumn([
                      Text(running[i].label),
                      KMuted(running[i].detail, size: 10)
                    ], gap: 3))
            ],
        child: ConstrainedBox(
            constraints: const BoxConstraints(minWidth: 48, minHeight: 48),
            child: Center(
                child: compact
                    ? Badge(
                        label: Text('${running.length}'),
                        child: const Icon(Icons.sync,
                            size: 20, color: KColors.accent))
                    : KBadge('${running.length} running', tone: 'accent'))));
  }
}

Future<void> showCommandMenu(BuildContext context) async => showDialog<void>(
    context: context,
    builder: (dialogContext) => _CommandMenu(onNavigate: (route) {
          Navigator.of(dialogContext).pop();
          context.go(route);
        }));

class _CommandMenu extends ConsumerStatefulWidget {
  const _CommandMenu({required this.onNavigate});
  final ValueChanged<String> onNavigate;
  @override
  ConsumerState<_CommandMenu> createState() => _CommandMenuState();
}

class _CommandMenuState extends ConsumerState<_CommandMenu> {
  final query = TextEditingController();
  @override
  void dispose() {
    query.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final controller = ref.watch(kleosProvider);
    final q = query.text.trim();
    final items = <({String group, String label, VoidCallback run})>[
      (
        group: 'Actions',
        label: 'New build',
        run: () => widget.onNavigate('/new')
      ),
      (
        group: 'Actions',
        label: 'Browse connectors',
        run: () => widget.onNavigate('/connections')
      ),
      (
        group: 'Actions',
        label: 'Run simulations at ${controller.speed == 1 ? '4×' : '1×'}',
        run: () {
          controller.setSpeed(controller.speed == 1 ? 4 : 1);
          Navigator.of(context).pop();
        }
      ),
      (
        group: 'Actions',
        label: 'Reset demo',
        run: () {
          controller.resetDemo();
          widget.onNavigate('/');
        }
      ),
      for (final p in asMaps(controller.state['projects']))
        (
          group: 'Projects',
          label: '${p['name']}',
          run: () => widget.onNavigate('/projects/${p['id']}')
        ),
      for (final entry in {
        'Projects': '/',
        'Human tasks': '/human-tasks',
        'Connections': '/connections',
        'Engineering tools': '/connections?tab=tools',
        'Provider portal': '/provider',
        'Unmet demand': '/demand',
        'Connector kit': '/developer',
        'Platform MCP server': '/agent-access'
      }.entries)
        (
          group: 'Go to',
          label: entry.key,
          run: () => widget.onNavigate(entry.value)
        ),
    ];
    final matches = items
        .where((i) => i.label.toLowerCase().contains(q.toLowerCase()))
        .toList();
    Future<void> startBuild() async {
      FocusManager.instance.primaryFocus?.unfocus();
      final result = await controller.execute('project.create', {'brief': q});
      if (mounted && result.ok) {
        widget.onNavigate('/projects/${result.result['projectId']}');
      }
    }

    return Dialog(
        alignment: const Alignment(0, -.55),
        insetPadding: const EdgeInsets.all(20),
        child: SizedBox(
            width: 620,
            child: ConstrainedBox(
                constraints: BoxConstraints(
                    maxHeight: MediaQuery.sizeOf(context).height * .75),
                child: Column(mainAxisSize: MainAxisSize.min, children: [
                  Padding(
                      padding: const EdgeInsets.all(14),
                      child: TextField(
                          controller: query,
                          autofocus: true,
                          onChanged: (_) => setState(() {}),
                          onSubmitted: (_) {
                            if (q.length > 3) {
                              startBuild();
                            } else if (matches.isNotEmpty) {
                              matches.first.run();
                            }
                          },
                          decoration: const InputDecoration(
                              hintText:
                                  'Describe a device to build, or jump to a page',
                              prefixIcon:
                                  Icon(Icons.auto_awesome_outlined, size: 18),
                              border: InputBorder.none,
                              enabledBorder: InputBorder.none,
                              focusedBorder: InputBorder.none))),
                  const Divider(height: 1),
                  Flexible(
                      child: ListView(
                          shrinkWrap: true,
                          padding: const EdgeInsets.all(10),
                          children: [
                        if (q.length > 3) ...[
                          const Padding(
                              padding: EdgeInsets.all(10),
                              child: KMuted('Ask Kleos9', size: 10)),
                          ListTile(
                              dense: true,
                              leading: const Icon(Icons.auto_awesome,
                                  size: 16, color: KColors.accent),
                              title: Text('Start a build: “$q”',
                                  style: const TextStyle(fontSize: 12)),
                              onTap: startBuild)
                        ],
                        for (var i = 0; i < matches.length; i++) ...[
                          if (i == 0 ||
                              matches[i - 1].group != matches[i].group)
                            Padding(
                                padding: const EdgeInsets.all(10),
                                child: KMuted(matches[i].group, size: 10)),
                          ListTile(
                              dense: true,
                              title: Text(matches[i].label,
                                  style: const TextStyle(fontSize: 12)),
                              leading:
                                  const Icon(Icons.arrow_forward, size: 14),
                              onTap: matches[i].run)
                        ],
                        if (matches.isEmpty && q.length <= 3)
                          const Padding(
                              padding: EdgeInsets.all(16),
                              child: KMuted('No matches')),
                      ])),
                ]))));
  }
}

class _AccountMenu extends ConsumerWidget {
  const _AccountMenu();
  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final account = ref.watch(accountProvider).asData?.value;
    if (account == null) return const SizedBox.shrink();
    final admin = const {'owner', 'admin'}.contains(account.role);
    return PopupMenuButton<String>(
        key: const ValueKey('account-menu'),
        tooltip: 'Account',
        icon: const Icon(Icons.account_circle_outlined, size: 22),
        onSelected: (value) async {
          if (value == 'admin') {
            context.go('/admin');
          } else if (value == 'out') {
            await ref.read(accountProvider.notifier).signOut();
          }
        },
        itemBuilder: (_) => [
              PopupMenuItem(
                  enabled: false,
                  child: Text('Signed in as ${account.role}',
                      style: const TextStyle(fontSize: 12))),
              if (admin)
                const PopupMenuItem(
                    value: 'admin',
                    child: ListTile(
                        dense: true,
                        leading: Icon(Icons.admin_panel_settings_outlined),
                        title: Text('Admin'))),
              const PopupMenuItem(
                  value: 'out',
                  child: ListTile(
                      dense: true,
                      leading: Icon(Icons.logout),
                      title: Text('Sign out'))),
            ]);
  }
}
