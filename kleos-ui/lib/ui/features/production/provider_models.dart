import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../data/models/json.dart';
import '../../../data/models/production.dart';
import '../../../state/kleos_store.dart';
import '../../../state/model_defaults.dart';
import '../../../state/production_store.dart';
import '../../core/widgets.dart';
import 'model_policy_editor.dart' show ModelPolicyEditor;

/// Model choices live with providers; the original services portal stays intact.
class ProviderModels extends ConsumerStatefulWidget {
  const ProviderModels(
      {super.key, required this.controller, this.initialProjectId});
  final KleosController controller;
  final String? initialProjectId;
  @override
  ConsumerState<ProviderModels> createState() => _ProviderModelsState();
}

class _ProviderModelsState extends ConsumerState<ProviderModels> {
  static const defaults = '__new_build_defaults__';
  late String selected = widget.initialProjectId ?? defaults;
  Map<String, ModelChoice>? draft;
  bool busy = false;
  String? message;

  Future<void> save() async {
    if (draft == null || busy) return;
    setState(() {
      busy = true;
      message = null;
    });
    try {
      if (selected == defaults) {
        await ref.read(newBuildModelPolicyProvider.notifier).save(draft!);
      } else {
        final result =
            await widget.controller.execute('project.setModelPolicy', {
          'projectId': selected,
          'modelPolicy':
              draft!.map((lane, choice) => MapEntry(lane, choice.toJson())),
        });
        if (!result.ok) {
          throw StateError(
              widget.controller.error ?? 'Could not save provider choices.');
        }
      }
      if (mounted) {
        setState(() {
          draft = null;
          message = selected == defaults
              ? 'Saved for new builds. Existing builds are unchanged.'
              : 'Saved. New attempts use these providers; running attempts stay pinned.';
        });
      }
    } catch (error) {
      if (mounted) setState(() => message = error.toString());
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final catalog = ref.watch(modelCatalogProvider);
    final initial = ref.watch(newBuildModelPolicyProvider);
    final projects = asMaps(widget.controller.state['projects'])
        .where((project) => project['production'] == true)
        .toList();
    final current =
        projects.where((project) => project['id'] == selected).firstOrNull;
    final canWrite =
        ref.watch(accountProvider).asData?.value?.canWrite ?? false;
    return kColumn([
      const KSectionHeader(
          title: 'AI model providers',
          description:
              'Choose a model for new builds or for an existing engineering project.'),
      catalog.when(
        loading: () => const KLineLoader(),
        error: (error, _) => KCard(
            child: kColumn([
          KMuted(error.toString()),
          KButton(
              label: 'Retry providers',
              onPressed: () => ref.invalidate(modelCatalogProvider)),
        ])),
        data: (models) => KResponsive(columns: 3, children: [
          for (final provider in ['meta', 'modal'])
            KCard(
                title: providerLabel(provider),
                child: kColumn([
                  KBadge(models.models.any(
                          (m) => m.choice.provider == provider && m.configured)
                      ? 'Connected'
                      : 'Not configured'),
                  for (final model in models.models
                      .where((m) => m.choice.provider == provider))
                    KMuted(model.choice.model),
                ])),
        ]),
      ),
      DropdownButtonFormField<String>(
        key: ValueKey(selected),
        initialValue: selected == defaults || current != null ? selected : null,
        isExpanded: true,
        decoration: const InputDecoration(labelText: 'Apply model choices to'),
        items: [
          const DropdownMenuItem(
              value: defaults, child: Text('New build defaults')),
          for (final project in projects)
            DropdownMenuItem(
                value: project['id'] as String,
                child: Text('${project['name']}',
                    overflow: TextOverflow.ellipsis)),
        ],
        onChanged: busy
            ? null
            : (value) => setState(() {
                  selected = value!;
                  draft = null;
                  message = null;
                }),
      ),
      if (selected != defaults && current == null)
        const KMuted(
            'This project is no longer available. Choose another build.'),
      if (catalog.hasValue && (selected == defaults || current != null))
        if (selected == defaults && !initial.hasValue)
          initial.when(
              data: (_) => const SizedBox.shrink(),
              loading: () => const KLineLoader(),
              error: (error, _) => KCard(
                      child: kColumn([
                    KMuted(error.toString()),
                    KButton(
                        label: 'Retry saved choices',
                        onPressed: () =>
                            ref.invalidate(newBuildModelPolicyProvider)),
                  ])))
        else
          ModelPolicyEditor(
              catalog: catalog.requireValue,
              policy: draft ??
                  (selected == defaults
                      ? initial.requireValue
                      : asMap(current!['modelPolicy']).map((lane, value) =>
                          MapEntry(lane, ModelChoice.fromJson(asMap(value))))),
              enabled: !busy && canWrite,
              onChanged: (value) => setState(() {
                    draft = value;
                    message = null;
                  })),
      if (message != null) KMuted(message!),
      KButton(
          label: busy ? 'Saving…' : 'Save model choices',
          primary: true,
          onPressed: !canWrite || busy || draft == null ? null : save),
    ], gap: 20);
  }
}
