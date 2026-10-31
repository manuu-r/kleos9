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

typedef _Work = ({String label, String detail, String route});

/// Work that is running right now, across every project. The same list feeds
/// the running menu and the line loader under the header.
List<_Work> _runningWork(WidgetRef ref) {
  final running = <_Work>[];
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
      running
          .add((label: 'Verifying', detail: name, route: '/projects/$id?s=5'));
    }
    if (['quoting', 'ordering'].contains(p['procurement'])) {
      running.add(
          (label: 'Procuring parts', detail: name, route: '/projects/$id?s=6'));
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
  return running;
}

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

  /// Labelled bottom bar. The copper line over the selected icon comes from
  /// the navigation theme; nothing is filled.
  Widget _bottomNav(BuildContext context) => DecoratedBox(
      decoration: const BoxDecoration(
          border: Border(top: BorderSide(color: KColors.border))),
      child: NavigationBar(
        selectedIndex: index,
        onDestinationSelected: (i) {
          if (i != index) KHaptics.select();
          context.go(_nav[i].path);
        },
        destinations: [
          for (final item in _nav)
            NavigationDestination(
              icon: Icon(item.icon),
              label: item.label,
              tooltip: item.label,
            ),
        ],
      ));

  Widget _rail(BuildContext context) => NavigationRail(
        selectedIndex: index,
        onDestinationSelected: (i) {
          if (i != index) KHaptics.select();
          context.go(_nav[i].path);
        },
        destinations: [
          for (final item in _nav)
            NavigationRailDestination(
              icon: Icon(item.icon),
              label: Text(item.label),
              padding: const EdgeInsets.symmetric(vertical: 4),
            ),
        ],
      );

  @override
  Widget build(BuildContext context, WidgetRef ref) => ListenableBuilder(
      listenable: FocusManager.instance,
      builder: (context, _) => _build(context, ref));

  Widget _build(BuildContext context, WidgetRef ref) {
    final controller = ref.watch(kleosProvider);
    final running = _runningWork(ref);
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
    final gutter = KSpace.gutter(context);
    final header = Container(
        height: phone ? 56 : 64,
        decoration: const BoxDecoration(
            color: KColors.bg,
            border: Border(bottom: BorderSide(color: KColors.border))),
        child: Center(
            child: ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: 1200),
                child: Padding(
                    padding:
                        EdgeInsets.symmetric(horizontal: phone ? 8 : gutter),
                    child: Row(children: [
                      if (detail)
                        IconButton(
                          tooltip: 'Back to builds',
                          onPressed: () => context.go('/'),
                          constraints:
                              const BoxConstraints(minWidth: 44, minHeight: 44),
                          icon: const Icon(Icons.arrow_back, size: 20),
                        )
                      else
                        InkWell(
                            onTap: () => context.go('/'),
                            child: Padding(
                                padding: EdgeInsets.only(
                                    left: phone ? 8 : 0, right: 8),
                                child: Row(children: [
                                  const _BrandMark(),
                                  const SizedBox(width: 10),
                                  if (width >= 400)
                                    const Text('Kleos9',
                                        style: TextStyle(
                                            fontSize: 15,
                                            fontWeight: FontWeight.w600))
                                ]))),
                      if (mobile)
                        const Spacer()
                      else
                        Expanded(
                            child: Align(
                                alignment: Alignment.center,
                                child: SizedBox(
                                    height: 64,
                                    child: KTabStrip(
                                        rule: false,
                                        fill: false,
                                        height: 64,
                                        gap: 14,
                                        selected: index,
                                        tabs: [
                                          for (var i = 0; i < _nav.length; i++)
                                            KTab(
                                                label: _nav[i].label,
                                                icon: _nav[i].icon,
                                                onTap: () =>
                                                    context.go(_nav[i].path))
                                        ])))),
                      _RunningChip(running: running, compact: phone),
                      const SizedBox(width: 4),
                      if (width > 1120)
                        SizedBox(
                            width: 244,
                            child: KButton(
                                label: 'Ask Kleos9 or jump to…  ⌘K',
                                compact: true,
                                icon: Icons.search,
                                onPressed: () => showCommandMenu(context)))
                      else
                        IconButton(
                            tooltip: 'Ask Kleos9 or jump to a page',
                            onPressed: () => showCommandMenu(context),
                            constraints: const BoxConstraints(
                                minWidth: 44, minHeight: 44),
                            icon: const Icon(Icons.search, size: 21)),
                      if (showAccount) const _AccountMenu(),
                      const SizedBox(width: 4),
                      if (width < 340)
                        IconButton.filled(
                            tooltip: 'New build',
                            onPressed: () => context.go('/new'),
                            style: IconButton.styleFrom(
                                backgroundColor: KColors.text,
                                foregroundColor: KColors.bg,
                                minimumSize: const Size(40, 40)),
                            icon: const Icon(Icons.add, size: 20))
                      else
                        KButton(
                            label: mobile ? 'New' : 'New build',
                            primary: true,
                            compact: true,
                            icon: Icons.add,
                            onPressed: () => context.go('/new')),
                      if (phone) const SizedBox(width: 4),
                    ])))));
    final content = controller.state.isEmpty
        ? (controller.loading
            ? const KPageSkeleton()
            : Align(
                alignment: Alignment.topCenter,
                child: Padding(
                    padding: EdgeInsets.fromLTRB(gutter, 24, gutter, 0),
                    child: ConstrainedBox(
                        constraints: const BoxConstraints(maxWidth: 560),
                        child: KNotice(
                            controller.error ??
                                'Waiting for the local mock backend',
                            error: true,
                            action: TextButton(
                                onPressed: controller.refresh,
                                style: TextButton.styleFrom(
                                    minimumSize: const Size(44, 44),
                                    foregroundColor: KColors.text,
                                    textStyle: const TextStyle(
                                        fontFamily: 'Geist',
                                        fontSize: 13,
                                        fontWeight: FontWeight.w600)),
                                child: const Text('Try again')))))))
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
            child: Scaffold(
              backgroundColor: KColors.bg,
              resizeToAvoidBottomInset: editing,
              bottomNavigationBar:
                  phone && !keyboardOpen ? _bottomNav(context) : null,
              body: SafeArea(
                  bottom: !phone || keyboardOpen,
                  child: Column(children: [
                    header,
                    KLineLoader(active: running.isNotEmpty),
                    if (controller.error != null && controller.state.isNotEmpty)
                      Padding(
                          padding: EdgeInsets.fromLTRB(gutter, 12, gutter, 0),
                          child: KNotice(controller.error!,
                              error: true,
                              action: TextButton(
                                  onPressed: controller.dismissError,
                                  style: TextButton.styleFrom(
                                      minimumSize: const Size(44, 44),
                                      foregroundColor: KColors.text),
                                  child: const Text('Dismiss')))),
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
            )));
  }
}

class AppPage extends StatefulWidget {
  const AppPage({super.key, required this.child});
  final Widget child;
  @override
  State<AppPage> createState() => _AppPageState();
}

class _AppPageState extends State<AppPage> {
  // Eased mouse-wheel scrolling on web and desktop; native momentum on touch.
  final _scroll = KSmoothController();
  @override
  void dispose() {
    _scroll.dispose();
    super.dispose();
  }

  Widget get child => widget.child;
  @override
  Widget build(BuildContext context) {
    final gutter = KSpace.gutter(context);
    final phone = KSpace.phone(context);
    return SingleChildScrollView(
      controller: _scroll,
      keyboardDismissBehavior: ScrollViewKeyboardDismissBehavior.onDrag,
      key: ValueKey(GoRouterState.of(context).uri.toString()),
      child: Center(
          child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 1200),
        child: Padding(
          padding: EdgeInsets.fromLTRB(
              gutter, phone ? 20 : 32, gutter, phone ? 24 : 48),
          child: SelectionArea(
              child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                child,
                SizedBox(height: phone ? 40 : 64),
                const Center(
                    child: KMuted(
                        String.fromEnvironment('KLEOS_BACKEND',
                                    defaultValue: 'mock') ==
                                'production'
                            ? 'Prototype. Choose real engineering or a simulated demo for new builds. Physical fulfillment remains simulated.'
                            : 'Prototype. Agent runs, quotes, orders, human jobs, prints and test results are simulated by the connected API.',
                        size: 12)),
              ])),
        ),
      )),
    );
  }
}

/// The product mark: a flat monogram with one copper trace dot. No gradient.
class _BrandMark extends StatelessWidget {
  const _BrandMark();
  @override
  Widget build(BuildContext context) =>
      Stack(clipBehavior: Clip.none, children: [
        Container(
            width: 30,
            height: 30,
            alignment: Alignment.center,
            decoration: ShapeDecoration(
                color: KColors.surface3, shape: KShape.panel(cut: 7)),
            child: const Text('K9',
                style: TextStyle(
                    fontFamily: 'GeistMono',
                    color: KColors.text,
                    fontSize: 12,
                    letterSpacing: -.4,
                    fontWeight: FontWeight.w700))),
        Positioned(
            right: -2,
            top: -2,
            child: Container(
                width: 6,
                height: 6,
                decoration: const BoxDecoration(
                    color: KColors.accent, shape: BoxShape.circle)))
      ]);
}

class _RunningChip extends StatelessWidget {
  const _RunningChip({required this.running, this.compact = false});
  final List<_Work> running;
  final bool compact;
  @override
  Widget build(BuildContext context) {
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
                      KMuted(running[i].detail, size: 12)
                    ], gap: 3))
            ],
        child: ConstrainedBox(
            constraints: const BoxConstraints(minWidth: 44, minHeight: 44),
            child: Center(
                child: compact
                    ? Badge(
                        label: Text('${running.length}'),
                        backgroundColor: KColors.surface3,
                        textColor: KColors.text,
                        child: const KSpinner(size: 18, color: KColors.text))
                    : KBadge('${running.length} running',
                        tone: 'accent', dot: true))));
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
        insetPadding: EdgeInsets.all(KSpace.phone(context) ? 12 : 20),
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
                              prefixIcon: Icon(Icons.search, size: 20),
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
                              child: KMuted('Ask Kleos9', size: 12)),
                          ListTile(
                              minVerticalPadding: 10,
                              leading: const Icon(Icons.auto_awesome,
                                  size: 18, color: KColors.text),
                              title: Text('Start a build: “$q”',
                                  style: const TextStyle(fontSize: 14)),
                              onTap: startBuild)
                        ],
                        for (var i = 0; i < matches.length; i++) ...[
                          if (i == 0 ||
                              matches[i - 1].group != matches[i].group)
                            Padding(
                                padding: const EdgeInsets.all(10),
                                child: KMuted(matches[i].group, size: 12)),
                          ListTile(
                              minVerticalPadding: 10,
                              title: Text(matches[i].label,
                                  style: const TextStyle(fontSize: 14)),
                              leading:
                                  const Icon(Icons.arrow_forward, size: 16),
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
                      style:
                          const TextStyle(fontSize: 13, color: KColors.muted))),
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
