import 'dart:async';
import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:share_plus/share_plus.dart';
import '../../../data/models/json.dart';
import '../../../data/models/production.dart';
import '../../../data/services/command_journal.dart';
import '../../../data/services/production_auth.dart';
import '../../../state/production_store.dart';
import '../../core/widgets.dart';
import '../admin/admin_page.dart';
import '../home_pages.dart' show stageNames;

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
      return const Scaffold(body: Center(child: CircularProgressIndicator()));
    }
    if (account.error is OrganizationSelectionRequired) {
      final selection = account.error! as OrganizationSelectionRequired;
      return Scaffold(
          body: SafeArea(
              child: _Page(children: [
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
      return _SignInPage(
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
              child: _Page(children: [
        const KSectionHeader(title: 'Choose your organization'),
        _Notice(selection.toString()),
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
      return _SignInPage(
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

class _SignInPage extends ConsumerStatefulWidget {
  const _SignInPage({required this.loading, this.error});
  final bool loading;
  final String? error;
  @override
  ConsumerState<_SignInPage> createState() => _SignInPageState();
}

class _SignInPageState extends ConsumerState<_SignInPage> {
  final token = TextEditingController();
  final email = TextEditingController();
  final password = TextEditingController();
  final inviteCode = TextEditingController();
  final name = TextEditingController();
  final setupToken = TextEditingController();
  final organization = TextEditingController();
  bool creating = false;
  late Future<JsonMap> config = ref.read(productionAuthProvider).config();
  @override
  void dispose() {
    token.dispose();
    email.dispose();
    password.dispose();
    inviteCode.dispose();
    name.dispose();
    setupToken.dispose();
    organization.dispose();
    super.dispose();
  }

  Future<void> signIn({required bool development, bool setup = false}) {
    FocusManager.instance.primaryFocus?.unfocus();
    final account = ref.read(accountProvider.notifier);
    if (development) return account.signIn(developmentToken: token.text);
    if (setup) {
      return account.setUp(
          setupToken: setupToken.text,
          organization: organization.text,
          email: email.text,
          password: password.text,
          name: name.text);
    }
    if (creating) {
      return account.signUp(
          inviteCode: inviteCode.text,
          email: email.text,
          password: password.text,
          name: name.text);
    }
    return account.signIn(email: email.text, password: password.text);
  }

  @override
  Widget build(BuildContext context) => Scaffold(
          body: Stack(children: [
        const Positioned.fill(child: KAmbient()),
        SafeArea(
            child: Center(
          child: SingleChildScrollView(
            padding: const EdgeInsets.all(24),
            child: ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: 480),
                child: KCard(
                    child: kColumn([
                  Row(children: [
                    Container(
                        width: 34,
                        height: 34,
                        alignment: Alignment.center,
                        decoration: BoxDecoration(
                            gradient: const LinearGradient(
                                begin: Alignment.topCenter,
                                end: Alignment.bottomCenter,
                                colors: [Color(0xffffffff), Color(0xffd5d5da)]),
                            borderRadius: BorderRadius.circular(10)),
                        child: const Text('K9',
                            style: TextStyle(
                                color: KColors.bg,
                                fontSize: 12,
                                letterSpacing: -.2,
                                fontWeight: FontWeight.w800))),
                    const SizedBox(width: 12),
                    const KBadge('Kleos9 private beta'),
                  ]),
                  Text('Your engineering workspace.',
                      style: Theme.of(context).textTheme.headlineLarge),
                  const KMuted(
                      'Sign in with your invited account to design, inspect, and revise your builds.'),
                  if (widget.error != null) _Notice(widget.error!, error: true),
                  FutureBuilder<JsonMap>(
                      future: config,
                      builder: (context, snapshot) {
                        if (snapshot.data?['loginRequired'] == false) {
                          return KButton(
                              label: 'Retry workspace',
                              onPressed: () => ref.invalidate(accountProvider));
                        }
                        final dev = snapshot.data?['authMode'] == 'development';
                        final setup = snapshot.data?['setupRequired'] == true;
                        final signup = creating && !setup;
                        return kColumn([
                          if (dev)
                            TextField(
                                key: const ValueKey('production-dev-token'),
                                controller: token,
                                obscureText: true,
                                enableSuggestions: false,
                                autocorrect: false,
                                decoration: const InputDecoration(
                                    labelText: 'Development access token'),
                                onSubmitted: (_) => signIn(development: true)),
                          if (!dev && snapshot.hasData) ...[
                            if (setup)
                              const _Notice(
                                  'No account exists yet. Create the first owner with the setup token from your AWS Secrets Manager.'),
                            if (setup)
                              TextField(
                                  key: const ValueKey('production-setup-token'),
                                  controller: setupToken,
                                  obscureText: true,
                                  enableSuggestions: false,
                                  autocorrect: false,
                                  decoration: const InputDecoration(
                                      labelText: 'Setup token')),
                            if (setup)
                              TextField(
                                  key:
                                      const ValueKey('production-organization'),
                                  controller: organization,
                                  textCapitalization: TextCapitalization.words,
                                  decoration: const InputDecoration(
                                      labelText: 'Organization name')),
                            if (signup)
                              TextField(
                                  key: const ValueKey('production-invite-code'),
                                  controller: inviteCode,
                                  enableSuggestions: false,
                                  autocorrect: false,
                                  decoration: const InputDecoration(
                                      labelText: 'Invite code')),
                            if (creating || setup)
                              TextField(
                                  key: const ValueKey('production-name'),
                                  controller: name,
                                  textCapitalization: TextCapitalization.words,
                                  autofillHints: const [AutofillHints.name],
                                  decoration: const InputDecoration(
                                      labelText: 'Name (optional)')),
                            TextField(
                                key: const ValueKey('production-email'),
                                controller: email,
                                keyboardType: TextInputType.emailAddress,
                                autofillHints: const [AutofillHints.email],
                                autocorrect: false,
                                decoration:
                                    const InputDecoration(labelText: 'Email')),
                            TextField(
                                key: const ValueKey('production-password'),
                                controller: password,
                                obscureText: true,
                                enableSuggestions: false,
                                autocorrect: false,
                                autofillHints: [
                                  creating || setup
                                      ? AutofillHints.newPassword
                                      : AutofillHints.password
                                ],
                                decoration: InputDecoration(
                                    labelText: creating || setup
                                        ? 'Choose a password (12+ characters)'
                                        : 'Password'),
                                onSubmitted: (_) =>
                                    signIn(development: false, setup: setup)),
                          ],
                          if (snapshot.hasError)
                            _Notice(snapshot.error.toString(), error: true),
                          if (snapshot.hasError)
                            KButton(
                                label: 'Retry connection',
                                onPressed: () {
                                  setState(() => config = ref
                                      .read(productionAuthProvider)
                                      .config());
                                  ref.invalidate(accountProvider);
                                }),
                          KButton(
                              key: const ValueKey('production-sign-in'),
                              label: widget.loading
                                  ? 'Connecting…'
                                  : setup
                                      ? 'Create owner account'
                                      : creating
                                          ? 'Create account'
                                          : 'Sign in',
                              primary: true,
                              onPressed: widget.loading || !snapshot.hasData
                                  ? null
                                  : () =>
                                      signIn(development: dev, setup: setup)),
                          if (!dev && snapshot.hasData && !setup)
                            KButton(
                                key: const ValueKey('production-toggle-signup'),
                                label: creating
                                    ? 'I already have an account'
                                    : 'I have an invite code',
                                onPressed: widget.loading
                                    ? null
                                    : () =>
                                        setState(() => creating = !creating)),
                        ]);
                      }),
                  const KMuted(
                      'Engineering runs use real models and tools. Physical fulfillment remains simulated in this release.',
                      size: 11),
                ], gap: 20))),
          ),
        ))
      ]));
}

class ProductionProjectsPage extends ConsumerWidget {
  const ProductionProjectsPage({super.key});
  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final projects = ref.watch(productionProjectsProvider);
    return _Page(children: [
      KSectionHeader(
          title: 'Your builds',
          description: 'From a brief to a traceable engineering package.',
          trailing: KButton(
              key: const ValueKey('production-new-build'),
              label: 'New build',
              primary: true,
              icon: Icons.add,
              onPressed: () => context.go('/new'))),
      const _Notice(
          'AI and engineering outputs are real. Human work, printing, shipping, and physical evidence are visibly simulated.'),
      projects.when(
        loading: () => const _Loading(),
        error: (error, _) => _Retry(error.toString(),
            retry: () => ref.invalidate(productionProjectsProvider)),
        data: (value) => kColumn([
          if (value.pending.isNotEmpty)
            KCard(
                title: 'Requests awaiting confirmation',
                child: kColumn([
                  for (final pending in value.pending)
                    _PendingRequest(command: pending)
                ])),
          if (value.projects.isEmpty)
            KCard(
                child: kColumn([
              const Text('Start with one device.'),
              const KMuted(
                  'Describe its purpose, size, features, and constraints. You choose the AI provider for each engineering lane.'),
              KButton(
                  label: 'Describe your first build',
                  onPressed: () => context.go('/new')),
            ])),
          for (final project in value.projects)
            KCard(
                title: project.name,
                trailing: KBadge(project.status,
                    tone: project.status == 'blocked' ? 'unknown' : null),
                child: kColumn([
                  Text(project.brief,
                      maxLines: 3, overflow: TextOverflow.ellipsis),
                  KMuted(stageNames[project.stage.clamp(0, 9)]),
                  KButton(
                      key: ValueKey('open-project-${project.id}'),
                      label: 'Open workspace',
                      icon: Icons.arrow_forward,
                      onPressed: () => context.go('/projects/${project.id}')),
                ])),
          if (value.nextCursor != null)
            KButton(
                label: 'More projects',
                onPressed: () =>
                    ref.read(productionProjectsProvider.notifier).nextPage()),
          KButton(
              label: 'Refresh',
              icon: Icons.refresh,
              onPressed: () =>
                  ref.read(productionProjectsProvider.notifier).refresh()),
        ], gap: 20),
      ),
    ]);
  }
}

class _PendingRequest extends ConsumerStatefulWidget {
  const _PendingRequest({required this.command});
  final PendingCommand command;
  @override
  ConsumerState<_PendingRequest> createState() => _PendingRequestState();
}

class _PendingRequestState extends ConsumerState<_PendingRequest> {
  bool busy = false;
  String? error;
  @override
  Widget build(BuildContext context) => kColumn([
        Text(widget.command.body['type'].toString()),
        const KMuted(
            'This request may already be accepted. Retry checks it using the original request identifier.',
            size: 11),
        if (error != null) _Notice(error!, error: true),
        KButton(
            label: busy ? 'Checking…' : 'Check request',
            onPressed: busy
                ? null
                : () async {
                    setState(() {
                      busy = true;
                      error = null;
                    });
                    try {
                      final result = await ref
                          .read(productionRepositoryProvider)
                          .retry(widget.command);
                      ref.invalidate(productionProjectsProvider);
                      if (context.mounted) {
                        context.go('/projects/${result.projectId}');
                      }
                    } catch (e) {
                      if (mounted) setState(() => error = e.toString());
                    } finally {
                      if (mounted) setState(() => busy = false);
                    }
                  }),
      ]);
}

class ProductionNewProjectPage extends ConsumerStatefulWidget {
  const ProductionNewProjectPage({super.key});
  @override
  ConsumerState<ProductionNewProjectPage> createState() =>
      _ProductionNewProjectPageState();
}

class _ProductionNewProjectPageState
    extends ConsumerState<ProductionNewProjectPage> {
  final brief = TextEditingController();
  Map<String, ModelChoice>? policy;
  bool busy = false;
  String? error;
  @override
  void dispose() {
    brief.dispose();
    super.dispose();
  }

  Future<void> create(ModelCatalog catalog) async {
    if (busy || brief.text.trim().isEmpty) return;
    FocusManager.instance.primaryFocus?.unfocus();
    setState(() {
      busy = true;
      error = null;
    });
    try {
      final result = await ref
          .read(productionRepositoryProvider)
          .command('project.create', {
        'brief': brief.text.trim(),
        'modelPolicy':
            (policy ?? catalog.defaults).map((k, v) => MapEntry(k, v.toJson())),
      });
      ref.invalidate(productionProjectsProvider);
      if (mounted) context.go('/projects/${result.projectId}');
    } catch (e) {
      if (mounted) setState(() => error = e.toString());
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  @override
  Widget build(BuildContext context) => _Page(children: [
        const KSectionHeader(
            title: 'Describe your device',
            description:
                'Tell us what it should do, who it is for, and which constraints matter.'),
        TextField(
            key: const ValueKey('production-project-brief'),
            controller: brief,
            minLines: 5,
            maxLines: 9,
            maxLength: 10000,
            onChanged: (_) => setState(() {}),
            decoration: const InputDecoration(
                labelText: 'Your brief',
                hintText:
                    'A simple smartwatch with a custom case, a small screen, and a companion app…')),
        if (error != null) _Notice(error!, error: true),
        ref.watch(modelCatalogProvider).when(
              loading: () => const _Loading(),
              error: (e, _) => _Retry(e.toString(),
                  retry: () => ref.invalidate(modelCatalogProvider)),
              data: (catalog) => kColumn([
                ModelPolicyEditor(
                    catalog: catalog,
                    policy: policy ?? catalog.defaults,
                    onChanged: (value) => setState(() => policy = value)),
                KButton(
                    label: busy ? 'Creating…' : 'Create project',
                    primary: true,
                    onPressed: busy || brief.text.trim().isEmpty
                        ? null
                        : () => create(catalog)),
              ], gap: 24),
            ),
      ]);
}

class ModelPolicyEditor extends StatelessWidget {
  const ModelPolicyEditor(
      {super.key,
      required this.catalog,
      required this.policy,
      required this.onChanged,
      this.enabled = true});
  final ModelCatalog catalog;
  final Map<String, ModelChoice> policy;
  final ValueChanged<Map<String, ModelChoice>> onChanged;
  final bool enabled;
  @override
  Widget build(BuildContext context) => KCard(
        title: 'AI providers by lane',
        child: kColumn([
          const KMuted(
              'Choose a model for each part of the work. Changes apply to new attempts; active work keeps its selected model.'),
          Wrap(spacing: 8, runSpacing: 8, children: [
            for (final model in catalog.models.where((model) =>
                model.configured &&
                engineeringLanes.keys.every(model.lanes.contains)))
              KButton(
                  key: ValueKey('provider-preset-${model.choice.model}'),
                  label:
                      'Use ${model.choice.model} for all lanes',
                  compact: true,
                  onPressed: enabled
                      ? () => onChanged({
                            for (final lane in engineeringLanes.keys)
                              lane: model.choice
                          })
                      : null),
          ]),
          for (final lane in engineeringLanes.entries)
            Builder(builder: (context) {
              final choices = catalog.models
                  .where((m) => m.lanes.contains(lane.key))
                  .toList();
              final selected = policy[lane.key];
              final current = choices
                  .where((m) =>
                      m.choice.model == selected?.model &&
                      m.choice.provider == selected?.provider)
                  .firstOrNull;
              return kColumn([
                KeyedSubtree(
                    key: ValueKey('provider-${lane.key}'),
                    child: DropdownButtonFormField<String>(
                      key: ValueKey(lane.key + (selected?.model ?? '')),
                      initialValue: current == null
                          ? null
                          : choices.indexOf(current).toString(),
                      isExpanded: true,
                      decoration: InputDecoration(labelText: lane.value),
                      items: [
                        for (final (index, model) in choices.indexed)
                          DropdownMenuItem(
                              value: index.toString(),
                              child: Text(
                                  '${providerLabel(model.choice.provider)} · ${model.choice.model}',
                                  key: ValueKey(
                                      'provider-option-${lane.key}-${model.choice.model}'),
                                  overflow: TextOverflow.ellipsis))
                      ],
                      onChanged: !enabled
                          ? null
                          : (value) {
                              final choice = choices[int.parse(value!)].choice;
                              onChanged({...policy, lane.key: choice});
                            },
                    )),
                if (current != null && !current.configured)
                  const KMuted(
                      'Not connected yet. An administrator must configure this provider before it can run.',
                      size: 11),
              ], gap: 8);
            }),
          const KMuted(
              'Provider credentials stay on the server. Engineering acceptance always comes from independent checks.',
              size: 11),
        ], gap: 20),
      );
}

class ProductionWorkspacePage extends ConsumerWidget {
  const ProductionWorkspacePage({super.key, required this.projectId});
  final String projectId;
  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final view = ref.watch(productionProjectProvider(projectId));
    return view.when(
      loading: () => const _Page(children: [_Loading()]),
      error: (e, _) => _Page(children: [
        _Retry(e.toString(),
            retry: () => ref.invalidate(productionProjectProvider(projectId)))
      ]),
      data: (value) => _workspace(context, ref, value),
    );
  }

  Widget _workspace(BuildContext context, WidgetRef ref, ProjectView value) {
    final workspace = value.workspace;
    final project = workspace.project;
    final controller = ref.read(productionProjectProvider(projectId).notifier);
    final canWrite = ref.watch(accountProvider).asData?.value?.canWrite == true;
    final proposal = asMap(project.state['proposal']);
    final proposedRequirements = asMap(proposal['requirements']);
    return _Page(children: [
      KSectionHeader(
          title: project.name,
          description: project.brief,
          trailing: Wrap(spacing: 8, runSpacing: 8, children: [
            KBadge(project.status,
                tone: project.status == 'blocked' ? 'unknown' : null),
            KBadge(value.connected ? 'Live updates' : 'Waiting for updates'),
            IconButton(
                tooltip: 'Refresh workspace',
                onPressed: controller.refresh,
                icon: const Icon(Icons.refresh)),
          ])),
      const _Notice(
          'Real design and digital checks · Simulated physical fulfillment'),
      if (value.error != null) _Notice(value.error!, error: true),
      KCard(
          title: stageNames[project.stage.clamp(0, 9)],
          child: kColumn([
            KProgress((project.stage + 1) * 10.0),
            KMuted('Engineering revision ${project.engineeringRevision}'),
            if (workspace.runs.isNotEmpty &&
                workspace.runs.first.blocker != null)
              _Notice(workspace.runs.first.blocker!, error: true),
            if (canWrite)
              Wrap(spacing: 10, runSpacing: 10, children: [
                for (final action in _actions(workspace))
                  KButton(
                      key: ValueKey('command-${action.$1}'),
                      label: action.$2,
                      primary: action.$3,
                      onPressed: value.busy
                          ? null
                          : () => controller.execute(action.$1)),
                if (project.isActive)
                  KButton(
                      label: 'Cancel run',
                      onPressed: value.busy
                          ? null
                          : () => controller.execute('project.cancel')),
                if (!project.isActive &&
                    workspace.runs.isNotEmpty &&
                    const {'blocked', 'failed'}
                        .contains(workspace.runs.first.status))
                  KButton(
                      label: 'Retry last run',
                      onPressed: value.busy
                          ? null
                          : () => controller.execute('project.retry',
                              {'runId': workspace.runs.first.id})),
              ]),
            if (value.busy) const LinearProgressIndicator(),
          ])),
      if (proposal.isNotEmpty)
        KCard(
            title: 'Concept and findings',
            child: kColumn([
              SelectionArea(child: Text(proposal['summary']?.toString() ?? '')),
              if (asList(proposedRequirements['excludedCoverage']).isNotEmpty)
                _Notice('Excluded from digital validation: '
                    '${asList(proposedRequirements['excludedCoverage']).map(_coverageLabel).join(', ')}. '
                    'These capabilities remain unverified.'),
              if (asList(proposedRequirements['requiredPeripheralCoverage'])
                  .isNotEmpty)
                Text('Required peripheral checks: '
                    '${asList(proposedRequirements['requiredPeripheralCoverage']).map(_coverageLabel).join(', ')}.'),
              if (proposedRequirements.isNotEmpty)
                _JsonDetails('Proposed requirements and validation scope',
                    proposedRequirements),
              for (final question in asList(proposal['questions']))
                _AnswerQuestion(
                    projectId: projectId,
                    question: question.toString(),
                    enabled: canWrite && !value.busy),
              if (asList(proposal['limitations']).isNotEmpty)
                _Notice(asList(proposal['limitations']).join('\n')),
            ], gap: 16)),
      if (workspace.checks.isNotEmpty)
        KCard(
            title: 'Independent checks',
            child: kColumn([
              for (final raw in workspace.checks)
                Builder(builder: (context) {
                  final check = asMap(raw);
                  return kColumn([
                    Wrap(spacing: 10, runSpacing: 8, children: [
                      KBadge(check['status'].toString(),
                          tone: check['status'] == 'PASS' ? 'pass' : 'unknown'),
                      Text(check['name'].toString()),
                      KMuted(check['evidenceKind'].toString(), size: 11),
                    ]),
                    _JsonDetails(
                        'Check evidence and limitations', check['details'])
                  ]);
                }),
            ])),
      _ModelSettings(
          projectId: projectId,
          project: project,
          enabled: canWrite && !value.busy),
      if (project.data['acceptedReleaseId'] != null)
        _ReleaseDownload(
            releaseId: project.data['acceptedReleaseId'] as String,
            previous: project.stage < 6),
      _EngineeringSettings(
          projectId: projectId,
          project: project,
          enabled: canWrite && !value.busy),
      if (project.interfaces.isNotEmpty)
        _JsonDetails('Agreed engineering interfaces', project.interfaces),
      if (asMap(project.data['requirements']).isNotEmpty)
        _JsonDetails('Accepted requirements', project.data['requirements']),
      if (project.stage >= 6)
        KCard(
            title: 'Physical fulfillment · simulated',
            child: kColumn([
              const KMuted(
                  'These receipts represent synthetic outcomes. No purchases, printing, shipping, or physical acceptance occur.'),
              for (final entry in project.state.entries
                  .where((e) => asMap(e.value)['evidenceKind'] == 'synthetic'))
                _JsonDetails(entry.key.replaceAll('_', ' '), entry.value),
              if (canWrite)
                DropdownButtonFormField<String>(
                    key: ValueKey(project.state['printMode']),
                    initialValue: project.state['printMode'] as String?,
                    decoration:
                        const InputDecoration(labelText: 'Printing preference'),
                    items: const [
                      DropdownMenuItem(
                          value: 'home', child: Text('My home printer')),
                      DropdownMenuItem(
                          value: 'order',
                          child: Text('Order printed parts (simulated)'))
                    ],
                    onChanged: value.busy
                        ? null
                        : (mode) => controller
                            .execute('project.selectPrint', {'mode': mode})),
              if (project.stage >= 8 && canWrite)
                KButton(
                    label: 'Record next assembly step (simulated)',
                    onPressed: value.busy
                        ? null
                        : () => controller.execute('project.assemblyStep', {
                              'step':
                                  ((project.state['assemblyStep'] as int?) ??
                                          -1) +
                                      1,
                            })),
            ])),
      KCard(
          title: 'Usage allowance',
          child: Wrap(spacing: 30, runSpacing: 12, children: [
            _Metric(
                'Allowance',
                project.budget['unlimited'] == true
                    ? 'Unlimited in development'
                    : _money(project.budget['allowanceUsd'])),
            _Metric(
                'Allowance used', _money(project.budget['spentEstimateUsd'])),
            _Metric('Reserved', _money(project.budget['reservedUsd'])),
          ])),
      KCard(
          title: 'Engineering artifacts',
          child: workspace.artifacts.isEmpty
              ? const KMuted(
                  'Generated sources, builds, exports, and evidence will appear here.')
              : kColumn([
                  for (final artifact in workspace.artifacts)
                    _ArtifactRow(artifact: artifact)
                ])),
      KCard(
          title: 'Runs',
          child: workspace.runs.isEmpty
              ? const KMuted('Start research to create the first run.')
              : kColumn([
                  for (final run in workspace.runs)
                    ListTile(
                        key: ValueKey('open-run-${run.id}'),
                        contentPadding: EdgeInsets.zero,
                        title: Text(run.kind.replaceFirst('project.', '')),
                        subtitle: Text(run.blocker ?? run.status),
                        trailing: const Icon(Icons.chevron_right),
                        onTap: () => context.go('/runs/${run.id}'))
                ])),
      KCard(
          title: 'Activity',
          child: value.events.isEmpty
              ? const KMuted(
                  'New activity will appear here as work progresses.')
              : kColumn([
                  for (final event in value.events.reversed.take(30))
                    kColumn([
                      Text(event.type.replaceAll('.', ' ')),
                      if (event.data['reason'] != null)
                        KMuted(event.data['reason'].toString()),
                      if (event.data['summary'] != null)
                        KMuted(event.data['summary'].toString()),
                      if (event.data['evidenceKind'] == 'synthetic')
                        const KBadge('Simulated'),
                    ], gap: 4)
                ])),
      KCard(
          title: 'Training data choice',
          child: SwitchListTile.adaptive(
            key: const ValueKey('production-training-consent'),
            contentPadding: EdgeInsets.zero,
            title: const Text('Contribute validated examples'),
            subtitle: const Text(
                'Optional. Allow eligible project examples to be considered for future model training. Turning this off excludes them from future datasets.'),
            value: workspace.trainingOptedIn,
            onChanged: canWrite && !value.busy
                ? (value) => controller
                    .execute('project.setTrainingConsent', {'optedIn': value})
                : null,
          )),
    ]);
  }
}

List<(String, String, bool)> _actions(Workspace workspace) {
  final p = workspace.project;
  if (p.isActive) return [];
  final state = p.state;
  if (state['proposal'] == null) {
    return [('project.research', 'Research the concept', true)];
  }
  if (state['confirmed'] != true) {
    return [
      ('project.requestReview', 'Refine concept', false),
      ('project.confirm', 'Confirm concept', true),
    ];
  }
  if (p.interfaces.isEmpty) {
    return [('project.solveInterfaces', 'Establish interfaces', true)];
  }
  if (p.stage < 5) {
    return [('project.generate', 'Generate engineering files', true)];
  }
  if (p.stage == 5) {
    return [
      ('project.verify', 'Run independent checks', true),
      ('project.repair', 'Repair candidates', false),
      if (workspace.checks.isNotEmpty)
        ('project.acceptRelease', 'Accept digital release', false),
    ];
  }
  if (state['quotes'] == null) {
    return [('project.requestQuotes', 'Simulate quotes', true)];
  }
  if (state['prototype_order'] == null) {
    return [('project.placeOrders', 'Simulate prototype order', true)];
  }
  if (state['physical_tasks'] == null) {
    return [('project.createPhysicalTasks', 'Simulate physical tasks', true)];
  }
  if (state['physical_acceptance'] == null) {
    return [
      ('project.acceptPhysical', 'Record simulated physical review', true)
    ];
  }
  if (state['kit'] == null) {
    return [('project.prepareKit', 'Prepare kit package', true)];
  }
  if (state['kit_acceptance'] == null) {
    return [('project.acceptKit', 'Accept simulated kit', true)];
  }
  if (state['shipment'] == null) {
    return [('project.ship', 'Simulate shipment', true)];
  }
  if (state['activation'] == null) {
    return [('project.activate', 'Simulate activation', true)];
  }
  return [];
}

class _AnswerQuestion extends ConsumerStatefulWidget {
  const _AnswerQuestion(
      {required this.projectId, required this.question, required this.enabled});
  final String projectId;
  final String question;
  final bool enabled;
  @override
  ConsumerState<_AnswerQuestion> createState() => _AnswerQuestionState();
}

class _AnswerQuestionState extends ConsumerState<_AnswerQuestion> {
  final answer = TextEditingController();
  @override
  void dispose() {
    answer.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => kColumn([
        Text(widget.question),
        TextField(
            key: ValueKey('answer-${widget.question}'),
            controller: answer,
            enabled: widget.enabled,
            decoration: const InputDecoration(labelText: 'Your answer'),
            onChanged: (_) => setState(() {})),
        KButton(
            key: ValueKey('save-answer-${widget.question}'),
            label: 'Save answer',
            onPressed: !widget.enabled || answer.text.trim().isEmpty
                ? null
                : () => ref
                        .read(productionProjectProvider(widget.projectId)
                            .notifier)
                        .execute('project.answer', {
                      'questionKey': widget.question,
                      'values': [answer.text.trim()]
                    })),
      ]);
}

class _ModelSettings extends ConsumerStatefulWidget {
  const _ModelSettings(
      {required this.projectId, required this.project, required this.enabled});
  final String projectId;
  final ProjectRecord project;
  final bool enabled;
  @override
  ConsumerState<_ModelSettings> createState() => _ModelSettingsState();
}

class _ModelSettingsState extends ConsumerState<_ModelSettings> {
  Map<String, ModelChoice>? draft;
  @override
  Widget build(BuildContext context) => ref.watch(modelCatalogProvider).when(
        loading: () => const _Loading(),
        error: (e, _) => _Retry(e.toString(),
            retry: () => ref.invalidate(modelCatalogProvider)),
        data: (catalog) => ExpansionTile(
          tilePadding: EdgeInsets.zero,
          title: const Text('AI provider settings'),
          subtitle: const Text('Muse and Gemini models by engineering lane'),
          children: [
            ModelPolicyEditor(
                catalog: catalog,
                policy: draft ?? widget.project.modelPolicy,
                enabled: widget.enabled,
                onChanged: (value) => setState(() => draft = value)),
            if (draft != null)
              Padding(
                  padding: const EdgeInsets.symmetric(vertical: 16),
                  child: KButton(
                      label: 'Save provider choices',
                      primary: true,
                      onPressed: !widget.enabled
                          ? null
                          : () async {
                              final result = await ref
                                  .read(productionProjectProvider(
                                          widget.projectId)
                                      .notifier)
                                  .execute('project.setModelPolicy', {
                                'modelPolicy': draft!
                                    .map((k, v) => MapEntry(k, v.toJson()))
                              });
                              if (result != null && mounted) {
                                setState(() => draft = null);
                              }
                            })),
          ],
        ),
      );
}

class _EngineeringSettings extends ConsumerStatefulWidget {
  const _EngineeringSettings(
      {required this.projectId, required this.project, required this.enabled});
  final String projectId;
  final ProjectRecord project;
  final bool enabled;
  @override
  ConsumerState<_EngineeringSettings> createState() =>
      _EngineeringSettingsState();
}

class _EngineeringSettingsState extends ConsumerState<_EngineeringSettings> {
  final capacity = TextEditingController();
  final width = TextEditingController();
  final length = TextEditingController();
  final height = TextEditingController();
  @override
  void dispose() {
    for (final field in [capacity, width, length, height]) {
      field.dispose();
    }
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final fields = {
      capacity: 'Capacity (mAh)',
      width: 'Width (mm)',
      length: 'Length (mm)',
      height: 'Height (mm)'
    };
    final valid = fields.keys.every((f) {
      final value = double.tryParse(f.text);
      return value != null && value.isFinite && value > 0;
    });
    return ExpansionTile(
        tilePadding: EdgeInsets.zero,
        title: const Text('Revise battery requirements'),
        children: [
          kColumn([
            const KMuted(
                'Changing the battery starts a new engineering revision and invalidates dependent designs and checks. The previous accepted baseline remains available.'),
            for (final field in fields.entries)
              TextField(
                  key: ValueKey('battery-${field.value}'),
                  controller: field.key,
                  enabled: widget.enabled,
                  keyboardType:
                      const TextInputType.numberWithOptions(decimal: true),
                  decoration: InputDecoration(labelText: field.value),
                  onChanged: (_) => setState(() {})),
            KButton(
                label: 'Apply battery revision',
                onPressed: !widget.enabled || !valid
                    ? null
                    : () => ref
                            .read(productionProjectProvider(widget.projectId)
                                .notifier)
                            .execute('project.swapBattery', {
                          'capacityMah': double.parse(capacity.text),
                          'dimensionsMm': {
                            'x': double.parse(width.text),
                            'y': double.parse(length.text),
                            'z': double.parse(height.text)
                          },
                        })),
          ])
        ]);
  }
}

class _ArtifactRow extends ConsumerStatefulWidget {
  const _ArtifactRow({required this.artifact});
  final ArtifactRecord artifact;
  @override
  ConsumerState<_ArtifactRow> createState() => _ArtifactRowState();
}

class _ArtifactRowState extends ConsumerState<_ArtifactRow> {
  bool busy = false;
  String? error;
  @override
  Widget build(BuildContext context) => kColumn([
        ListTile(
            contentPadding: EdgeInsets.zero,
            title: Text(widget.artifact.name),
            subtitle:
                Text('${widget.artifact.lane} · ${widget.artifact.status}'),
            trailing: IconButton(
                key: ValueKey('download-artifact-${widget.artifact.id}'),
                tooltip: 'Download or share artifact',
                icon: busy
                    ? const SizedBox(
                        width: 18,
                        height: 18,
                        child: CircularProgressIndicator(strokeWidth: 2))
                    : const Icon(Icons.download_outlined),
                onPressed: busy
                    ? null
                    : () async {
                        setState(() {
                          busy = true;
                          error = null;
                        });
                        try {
                          final bytes = await ref
                              .read(productionApiProvider)
                              .artifactBytes(widget.artifact.id);
                          if (!context.mounted) return;
                          final box = context.findRenderObject() as RenderBox;
                          await SharePlus.instance.share(ShareParams(
                            files: [
                              XFile.fromData(bytes,
                                  mimeType: widget.artifact.mediaType)
                            ],
                            fileNameOverrides: [
                              widget.artifact.name.split('/').last
                            ],
                            sharePositionOrigin:
                                box.localToGlobal(Offset.zero) & box.size,
                          ));
                        } catch (e) {
                          if (mounted) setState(() => error = e.toString());
                        } finally {
                          if (mounted) setState(() => busy = false);
                        }
                      })),
        if (error != null) _Notice(error!, error: true),
      ]);
}

class ProductionRunPage extends ConsumerStatefulWidget {
  const ProductionRunPage({super.key, required this.runId});
  final String runId;
  @override
  ConsumerState<ProductionRunPage> createState() => _ProductionRunPageState();
}

class _ProductionRunPageState extends ConsumerState<ProductionRunPage> {
  Timer? timer;
  @override
  void initState() {
    super.initState();
    timer = Timer.periodic(const Duration(seconds: 3), (_) {
      final run = ref.read(runInspectorProvider(widget.runId)).asData?.value;
      if (run != null &&
          const {'running', 'queued', 'waiting'}.contains(run.status)) {
        ref.invalidate(runInspectorProvider(widget.runId));
      }
    });
  }

  @override
  void dispose() {
    timer?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => _Page(children: [
        KSectionHeader(
            title: 'Run inspector',
            description: 'Trace the work from inputs to evidence.',
            trailing: IconButton(
                tooltip: 'Refresh run',
                onPressed: () =>
                    ref.invalidate(runInspectorProvider(widget.runId)),
                icon: const Icon(Icons.refresh))),
        ref.watch(runInspectorProvider(widget.runId)).when(
              loading: () => const _Loading(),
              error: (e, _) => _Retry(e.toString(),
                  retry: () =>
                      ref.invalidate(runInspectorProvider(widget.runId))),
              data: (run) => kColumn([
                Wrap(spacing: 10, runSpacing: 8, children: [
                  KBadge(run.status),
                  Text(run.kind.replaceFirst('project.', '')),
                  KButton(
                      label: 'Project',
                      compact: true,
                      onPressed: () =>
                          context.go('/projects/${run.data['projectId']}')),
                ]),
                if (run.blocker != null) _Notice(run.blocker!, error: true),
                SelectableText('Trace: ${run.traceId}',
                    style: const TextStyle(
                        fontFamily: 'GeistMono',
                        fontSize: 11,
                        color: KColors.muted)),
                for (final task in run.tasks)
                  KCard(
                      title: engineeringLanes[task['lane']] ??
                          task['lane'].toString(),
                      trailing: KBadge(task['status'].toString()),
                      child: kColumn([
                        if (asList(task['dependencies']).isNotEmpty)
                          _JsonDetails('Dependencies', task['dependencies']),
                        for (final attempt in asMaps(task['attempts']))
                          kColumn([
                            Text(
                                'Attempt ${attempt['number']} · ${attempt['status']}'),
                            KMuted(
                                '${providerLabel(attempt['provider'].toString())} · ${attempt['model']}'),
                            if (attempt['error'] != null)
                              _Notice(attempt['error'].toString(), error: true),
                            if (asMap(attempt['output'])['summary'] != null)
                              SelectionArea(
                                  child: Text(
                                      asMap(attempt['output'])['summary']
                                          .toString())),
                            _JsonDetails('Context and evidence references',
                                attempt['contextManifest']),
                            _JsonDetails(
                                'Output and changes', attempt['output']),
                            for (final call in asMaps(attempt['calls']))
                              _CallRecord(call: call),
                          ], gap: 12),
                        for (final job in asMaps(task['nativeJobs']))
                          _NativeJobRecord(
                              job: job,
                              projectId: run.data['projectId'] as String),
                      ], gap: 24)),
              ], gap: 24),
            ),
      ]);
}

class _ReleaseDownload extends ConsumerStatefulWidget {
  const _ReleaseDownload({required this.releaseId, required this.previous});
  final String releaseId;
  final bool previous;
  @override
  ConsumerState<_ReleaseDownload> createState() => _ReleaseDownloadState();
}

class _ReleaseDownloadState extends ConsumerState<_ReleaseDownload> {
  bool busy = false;
  String? error;
  @override
  Widget build(BuildContext context) => kColumn([
        KButton(
            label: busy
                ? 'Preparing package…'
                : widget.previous
                    ? 'Download previous accepted release'
                    : 'Download accepted release',
            icon: Icons.folder_zip_outlined,
            onPressed: busy
                ? null
                : () async {
                    setState(() {
                      busy = true;
                      error = null;
                    });
                    try {
                      final bytes = await ref
                          .read(productionApiProvider)
                          .releaseBytes(widget.releaseId);
                      if (!context.mounted) return;
                      final box = context.findRenderObject() as RenderBox;
                      await SharePlus.instance.share(ShareParams(
                          files: [
                            XFile.fromData(bytes, mimeType: 'application/zip')
                          ],
                          fileNameOverrides: [
                            'kleos-release-${widget.releaseId}.zip'
                          ],
                          sharePositionOrigin:
                              box.localToGlobal(Offset.zero) & box.size));
                    } catch (e) {
                      if (mounted) setState(() => error = e.toString());
                    } finally {
                      if (mounted) setState(() => busy = false);
                    }
                  }),
        if (error != null) _Notice(error!, error: true),
      ]);
}

class _NativeJobRecord extends ConsumerStatefulWidget {
  const _NativeJobRecord({required this.job, required this.projectId});
  final JsonMap job;
  final String projectId;
  @override
  ConsumerState<_NativeJobRecord> createState() => _NativeJobRecordState();
}

class _NativeJobRecordState extends ConsumerState<_NativeJobRecord> {
  Object? logs;
  bool busy = false;
  bool rebuilding = false;
  String? rebuildError;

  Future<void> rebuildSources() async {
    if (rebuilding) return;
    setState(() {
      rebuilding = true;
      rebuildError = null;
    });
    // Capture the organization-scoped repository before asynchronous work.
    final repository = ref.read(productionRepositoryProvider);
    try {
      final workspace = await repository.workspace(widget.projectId);
      if (!mounted) return;
      final accepted = await repository.command(
          'project.retryNative',
          {
            'projectId': widget.projectId,
            'jobId': widget.job['id'],
          },
          revision: workspace.project.revision);
      if (!mounted) return;
      ref.invalidate(productionProjectProvider(widget.projectId));
      ref.invalidate(productionProjectsProvider);
      context.go(accepted.runId == null
          ? '/projects/${widget.projectId}'
          : '/runs/${accepted.runId}');
    } catch (error) {
      if (mounted) {
        setState(() => rebuildError = error.toString());
        // Uncertain requests remain in the existing durable command journal.
        ref.invalidate(productionProjectsProvider);
      }
    } finally {
      if (mounted) setState(() => rebuilding = false);
    }
  }

  @override
  Widget build(BuildContext context) => kColumn([
        Wrap(spacing: 10, runSpacing: 8, children: [
          Text('Engineering job · ${widget.job['purpose']}'),
          KBadge(widget.job['status'].toString()),
        ]),
        KMuted('Tool version: ${widget.job['toolVersion']}'),
        if (asList(widget.job['limitations']).isNotEmpty)
          _Notice(asList(widget.job['limitations']).join('\n')),
        _JsonDetails('Job inputs', widget.job['inputManifest']),
        if (widget.job['hasLogs'] == true &&
            const {'owner', 'admin'}
                .contains(ref.watch(accountProvider).asData?.value?.role))
          KButton(
              key: ValueKey('native-logs-${widget.job['id']}'),
              label: busy ? 'Loading logs…' : 'View redacted engineering logs',
              onPressed: busy
                  ? null
                  : () async {
                      setState(() => busy = true);
                      try {
                        final result = await ref
                            .read(productionApiProvider)
                            .get('/native-jobs/${widget.job['id']}/logs');
                        if (mounted) setState(() => logs = result);
                      } catch (e) {
                        if (mounted) setState(() => logs = e.toString());
                      } finally {
                        if (mounted) setState(() => busy = false);
                      }
                    }),
        if (logs != null) _JsonDetails('Engineering logs', logs),
        if (widget.job['purpose'] == 'build' &&
            const {'succeeded', 'failed', 'unsupported'}
                .contains(widget.job['status']) &&
            ref.watch(accountProvider).asData?.value?.canWrite == true) ...[
          const KMuted(
              'Rebuilds the saved sources using compute allowance only. No new AI generation.',
              size: 11),
          KButton(
              key: ValueKey('rebuild-native-${widget.job['id']}'),
              label: rebuilding ? 'Starting rebuild…' : 'Rebuild saved sources',
              onPressed: rebuilding ? null : rebuildSources),
        ],
        if (rebuildError != null) _Notice(rebuildError!, error: true),
      ], gap: 12);
}

class _CallRecord extends ConsumerStatefulWidget {
  const _CallRecord({required this.call});
  final JsonMap call;
  @override
  ConsumerState<_CallRecord> createState() => _CallRecordState();
}

class _CallRecordState extends ConsumerState<_CallRecord> {
  String? content;
  bool busy = false;
  @override
  Widget build(BuildContext context) => kColumn([
        Wrap(spacing: 12, runSpacing: 8, children: [
          Text(widget.call['operation'].toString()),
          KBadge(widget.call['status'].toString()),
          Text(
              'Allowance debit: ${_allowanceDebit(asMap(widget.call['usage'])['allowanceDebitUsd'])}'),
          Text(
              'Provider estimate: ${_providerEstimate(asMap(widget.call['usage'])['estimatedUsd'])}'),
        ]),
        _JsonDetails('Usage', widget.call['usage']),
        if (widget.call['hasTranscript'] == true &&
            const {'owner', 'admin'}
                .contains(ref.watch(accountProvider).asData?.value?.role))
          KButton(
              key: ValueKey('transcript-${widget.call['id']}'),
              label: busy ? 'Loading…' : 'View redacted AI record',
              onPressed: busy
                  ? null
                  : () async {
                      setState(() => busy = true);
                      try {
                        final value = await ref.read(productionApiProvider).get(
                            '/invocations/${widget.call['id']}/transcript');
                        if (mounted) {
                          setState(() => content =
                              const JsonEncoder.withIndent('  ')
                                  .convert(value));
                        }
                      } catch (e) {
                        if (mounted) setState(() => content = e.toString());
                      } finally {
                        if (mounted) setState(() => busy = false);
                      }
                    }),
        if (content != null) _JsonDetails('AI record', content),
      ]);
}

class _JsonDetails extends StatelessWidget {
  const _JsonDetails(this.title, this.value);
  final String title;
  final Object? value;
  @override
  Widget build(BuildContext context) => ExpansionTile(
        tilePadding: EdgeInsets.zero,
        title: Text(title, style: const TextStyle(fontSize: 13)),
        children: [
          Align(
              alignment: Alignment.centerLeft,
              child: SelectionArea(
                  child: Text(
                      value is String
                          ? value as String
                          : const JsonEncoder.withIndent('  ').convert(value),
                      style: const TextStyle(
                          fontFamily: 'GeistMono', fontSize: 11, height: 1.5))))
        ],
      );
}

class _Page extends StatelessWidget {
  const _Page({required this.children});
  final List<Widget> children;
  @override
  Widget build(BuildContext context) => ListView(
        key: const ValueKey('production-page-scroll'),
        keyboardDismissBehavior: ScrollViewKeyboardDismissBehavior.onDrag,
        padding:
            EdgeInsets.all(MediaQuery.sizeOf(context).width < 600 ? 16 : 32),
        children: [
          for (final child in children)
            Padding(padding: const EdgeInsets.only(bottom: 24), child: child)
        ],
      );
}

class _Notice extends StatelessWidget {
  const _Notice(this.message, {this.error = false});
  final String message;
  final bool error;
  @override
  Widget build(BuildContext context) => Container(
        width: double.infinity,
        padding: const EdgeInsets.all(14),
        decoration: BoxDecoration(
            color:
                (error ? KColors.fail : KColors.accent).withValues(alpha: .08),
            borderRadius: BorderRadius.circular(9)),
        child: Text(message,
            style: TextStyle(
                fontSize: 12,
                height: 1.5,
                color: error ? KColors.fail : KColors.muted)),
      );
}

class _Loading extends StatelessWidget {
  const _Loading();
  @override
  Widget build(BuildContext context) => const Padding(
      padding: EdgeInsets.all(32),
      child: Center(child: CircularProgressIndicator()));
}

class _Retry extends StatelessWidget {
  const _Retry(this.message, {required this.retry});
  final String message;
  final VoidCallback retry;
  @override
  Widget build(BuildContext context) => kColumn([
        _Notice(message, error: true),
        KButton(label: 'Retry', onPressed: retry),
      ]);
}

class _Metric extends StatelessWidget {
  const _Metric(this.label, this.value);
  final String label;
  final String value;
  @override
  Widget build(BuildContext context) => kColumn([
        KMuted(label, size: 11),
        Text(value, style: Theme.of(context).textTheme.titleLarge),
      ], gap: 6);
}

String _money(Object? value) =>
    '\$${((value as num?) ?? 0).toStringAsFixed(2)}';

String _providerEstimate(Object? value) {
  if (value is! num || !value.isFinite || value < 0) return 'Unavailable';
  if (value > 0 && value < .0001) return '<\$0.0001';
  return '\$${value.toStringAsFixed(value < .01 ? 4 : 2)}';
}

String _allowanceDebit(Object? value) =>
    value is num && value.isFinite && value >= 0
        ? _money(value)
        : 'Unavailable';

String _coverageLabel(Object? value) => switch (value) {
      'ble' => 'Bluetooth (BLE)',
      'sensor' => 'sensors',
      'display' => 'display interaction',
      'radio' => 'radio operation',
      'thermal' => 'thermal behavior',
      'physicalOperation' => 'physical operation',
      _ => value.toString(),
    };
