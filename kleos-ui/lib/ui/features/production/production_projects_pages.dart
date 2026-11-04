import '../../../data/services/reference_images.dart';
import '../../core/reference_image_attachments.dart';
import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import '../../../data/models/production.dart';
import '../../../data/services/command_journal.dart';
import '../../../state/production_store.dart';
import '../../core/widgets.dart';
import '../home_pages.dart' show stageNames;
import 'production_widgets.dart';
import 'model_policy_editor.dart';

class ProductionProjectsPage extends ConsumerWidget {
  const ProductionProjectsPage({super.key});
  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final projects = ref.watch(productionProjectsProvider);
    return ProductionPage(children: [
      KSectionHeader(
          title: 'Your builds',
          description: 'From a brief to a traceable engineering package.',
          trailing: KButton(
              key: const ValueKey('production-new-build'),
              label: 'New build',
              primary: true,
              icon: Icons.add,
              onPressed: () => context.go('/new'))),
      const ProductionNotice(
          'AI and engineering outputs are real. Human work, printing, shipping, and physical evidence are visibly simulated.'),
      projects.when(
        loading: () => const ProductionLoading(),
        error: (error, _) => ProductionRetry(error.toString(),
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
            'This request may already be accepted. Retry checks it using the original request identifier.'),
        if (error != null) ProductionNotice(error!, error: true),
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
  List<ReferenceImageDraft> images = [];
  String? createdProjectId;
  bool captureNeeded = false;
  Map<String, ModelChoice>? policy;
  bool busy = false;
  String? error;
  @override
  void dispose() {
    brief.dispose();
    super.dispose();
  }

  Future<void> create(ModelCatalog catalog) async {
    if (busy || (brief.text.trim().isEmpty && images.isEmpty)) return;
    FocusManager.instance.primaryFocus?.unfocus();
    setState(() {
      busy = true;
      error = null;
    });
    try {
      final repository = ref.read(productionRepositoryProvider);
      if (createdProjectId == null) {
        final result = await repository.command('project.create', {
          'brief': brief.text.trim().isEmpty
              ? 'Create a device concept from the attached reference images.'
              : brief.text.trim(),
          'modelPolicy': (policy ?? catalog.defaults)
              .map((k, v) => MapEntry(k, v.toJson())),
          if (images.isNotEmpty) 'autoStart': false,
        });
        createdProjectId = result.projectId;
        captureNeeded = images.isNotEmpty;
      }
      if (captureNeeded) {
        final ids = <String>[];
        for (final image in images) {
          ids.add(await image.upload(repository, createdProjectId!));
        }
        final workspace = await repository.workspace(createdProjectId!);
        await repository.command(
            'project.capture',
            {
              'projectId': createdProjectId!,
              'autoResume': true,
              'referenceImageIds': ids
            },
            revision: workspace.project.revision);
      }
      ref.invalidate(productionProjectsProvider);
      if (mounted) context.go('/projects/$createdProjectId');
    } catch (e) {
      if (mounted) setState(() => error = e.toString());
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  @override
  Widget build(BuildContext context) => ProductionPage(children: [
        const KSectionHeader(
            title: 'Describe your device',
            description:
                'Tell us what it should do, who it is for, and which constraints matter.'),
        ReferenceImageAttachments(
            images: images,
            enabled: !busy,
            onChanged: (value) => setState(() => images = value),
            input: TextField(
                key: const ValueKey('production-project-brief'),
                enabled: !busy && createdProjectId == null,
                controller: brief,
                minLines: 5,
                maxLines: 9,
                maxLength: 10000,
                onChanged: (_) => setState(() {}),
                decoration: const InputDecoration(
                    labelText: 'Your brief',
                    hintText:
                        'A simple smartwatch with a custom case, a small screen, and a companion app…'))),
        if (error != null) ProductionNotice(error!, error: true),
        ref.watch(modelCatalogProvider).when(
              loading: () => const ProductionLoading(),
              error: (e, _) => ProductionRetry(e.toString(),
                  retry: () => ref.invalidate(modelCatalogProvider)),
              data: (catalog) => kColumn([
                ModelPolicyEditor(
                    catalog: catalog,
                    policy: policy ?? catalog.defaults,
                    onChanged: (value) => setState(() => policy = value)),
                KButton(
                    label: busy ? 'Creating…' : 'Create project',
                    primary: true,
                    onPressed:
                        busy || (brief.text.trim().isEmpty && images.isEmpty)
                            ? null
                            : () => create(catalog)),
              ], gap: 24),
            ),
      ]);
}
