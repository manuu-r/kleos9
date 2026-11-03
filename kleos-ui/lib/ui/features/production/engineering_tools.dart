import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../data/models/json.dart';
import '../../../state/kleos_store.dart';
import '../../../state/production_store.dart';
import '../../core/widgets.dart';

/// Operator-installed tools are distinct from the prototype connector catalog.
/// Keeping this provider scoped to the authenticated repository also scopes
/// access to the current organization.
final engineeringToolsProvider = FutureProvider<JsonMap>((ref) {
  final repository = ref.watch(productionRepositoryProvider);
  return repository.engineeringTools();
});

class EngineeringTools extends ConsumerStatefulWidget {
  const EngineeringTools({super.key, required this.controller});
  final KleosController controller;

  @override
  ConsumerState<EngineeringTools> createState() => _EngineeringToolsState();
}

class _EngineeringToolsState extends ConsumerState<EngineeringTools> {
  static const lanes = {'mechanics': 'CAD tool', 'electronics': 'PCB tool'};
  String? selected;
  String? draftProject;
  JsonMap? draft;
  bool saving = false;
  bool failed = false;
  String? message;

  String choiceKey(JsonMap choice) => '${choice['id']}@${choice['version']}';

  bool usable(JsonMap tool) =>
      tool['isDefault'] == true ||
      (tool['qualified'] == true && tool['configured'] == true);

  JsonMap basePolicy(JsonMap project, JsonMap catalog) {
    final saved = asMap(asMap(project['state'])['toolPolicy']);
    final defaults = asMap(catalog['defaultPolicy']);
    return {
      for (final lane in lanes.keys)
        lane: asMap(saved[lane]).isNotEmpty
            ? asMap(saved[lane])
            : asMap(defaults[lane])
    };
  }

  bool validPolicy(JsonMap policy, List<JsonMap> tools) =>
      lanes.keys.every((lane) {
        final choice = asMap(policy[lane]);
        return tools.any((tool) =>
            tool['lane'] == lane &&
            choiceKey(tool) == choiceKey(choice) &&
            usable(tool));
      });

  String status(JsonMap tool) {
    if (tool.isEmpty) return 'Unavailable';
    if (tool['configured'] != true) return 'Setup required';
    if (tool['qualified'] != true) return 'Qualification pending';
    return 'Ready';
  }

  Future<void> save(String id, JsonMap policy) async {
    if (saving) return;
    setState(() {
      saving = true;
      message = null;
      failed = false;
    });
    try {
      final result = await widget.controller.execute('project.setToolPolicy', {
        'projectId': id,
        'toolPolicy': {
          for (final lane in lanes.keys)
            lane: {
              'id': asMap(policy[lane])['id'],
              'version': asMap(policy[lane])['version'],
            }
        },
      });
      if (!mounted) return;
      setState(() {
        failed = !result.ok;
        if (result.ok) {
          draft = null;
          draftProject = null;
          message =
              'Saved. New attempts use these tools; running attempts keep their current tools.';
        } else {
          message = ref.read(kleosProvider).error ??
              'The tool choices could not be saved. Please try again.';
        }
      });
    } catch (_) {
      if (mounted) {
        setState(() {
          failed = true;
          message =
              'Connection interrupted. Your choices are kept; try saving again.';
        });
      }
    } finally {
      if (mounted) setState(() => saving = false);
    }
  }

  Widget choices(JsonMap catalog) {
    final tools = asMaps(catalog['tools']);
    final projects = asMaps(widget.controller.state['projects'])
        .where((project) => project['production'] == true)
        .toList();
    final id = selected ?? projects.firstOrNull?['id'] as String?;
    final project =
        projects.where((project) => project['id'] == id).firstOrNull;
    final policy = draftProject == id && draft != null
        ? draft!
        : basePolicy(project ?? {}, catalog);
    final canWrite =
        ref.watch(accountProvider).asData?.value?.canWrite ?? false;
    return kColumn([
      const KMuted(
          'Choose installed tools for a build. Changes apply to its next engineering attempt.'),
      if (projects.isEmpty)
        const KMuted('Create a build to choose its CAD and PCB tools.')
      else
        DropdownButtonFormField<String>(
          key: ValueKey('engineering-tools-build-$id'),
          initialValue: project == null ? null : id,
          isExpanded: true,
          decoration: const InputDecoration(labelText: 'Build'),
          items: [
            for (final p in projects)
              DropdownMenuItem(
                  value: p['id'] as String,
                  child: Text('${p['name']}', overflow: TextOverflow.ellipsis))
          ],
          onChanged: saving
              ? null
              : (value) => setState(() {
                    selected = value;
                    draft = null;
                    draftProject = null;
                    message = null;
                  }),
        ),
      for (final lane in lanes.entries)
        Builder(builder: (context) {
          final rows = tools.where((tool) => tool['lane'] == lane.key).toList();
          final current = asMap(policy[lane.key]);
          final currentKey = choiceKey(current);
          final chosen =
              rows.where((tool) => choiceKey(tool) == currentKey).firstOrNull;
          final hasChoice = chosen != null;
          return kColumn([
            DropdownButtonFormField<String>(
              key: ValueKey('engineering-tool-${lane.key}-$id-$currentKey'),
              initialValue: current.isEmpty ? null : currentKey,
              isExpanded: true,
              decoration: InputDecoration(labelText: lane.value),
              items: [
                for (final tool in rows)
                  DropdownMenuItem(
                    value: choiceKey(tool),
                    enabled: usable(tool),
                    child: Text(
                        '${tool['name']}${tool['isDefault'] == true ? ' · default' : ''}${usable(tool) ? '' : ' · unavailable'}',
                        overflow: TextOverflow.ellipsis),
                  ),
                if (current.isNotEmpty && !hasChoice)
                  DropdownMenuItem(
                      value: currentKey,
                      enabled: false,
                      child: const Text('Selected tool is unavailable',
                          overflow: TextOverflow.ellipsis)),
              ],
              onChanged: saving || !canWrite || project == null || rows.isEmpty
                  ? null
                  : (value) {
                      final tool = rows
                          .where((tool) => choiceKey(tool) == value)
                          .firstOrNull;
                      if (tool == null || !usable(tool)) return;
                      setState(() {
                        selected = id;
                        draftProject = id;
                        draft = {
                          ...policy,
                          lane.key: {
                            'id': tool['id'],
                            'version': tool['version']
                          }
                        };
                        message = null;
                      });
                    },
            ),
            if (chosen != null)
              Wrap(
                  spacing: 8,
                  runSpacing: 6,
                  crossAxisAlignment: WrapCrossAlignment.center,
                  children: [
                    KBadge(status(chosen),
                        tone: chosen['qualified'] == true &&
                                chosen['configured'] == true
                            ? 'pass'
                            : 'unknown'),
                    for (final limitation
                        in asList(chosen['limitations']).take(2))
                      KMuted('$limitation'),
                  ]),
          ], gap: 8);
        }),
      if (message != null)
        Text(message!,
            style: TextStyle(
                color: failed ? KColors.fail : KColors.muted, fontSize: 12)),
      if (projects.isNotEmpty)
        KButton(
            label: saving ? 'Saving…' : 'Save tool choices',
            primary: true,
            onPressed: saving ||
                    !canWrite ||
                    project == null ||
                    draftProject != id ||
                    draft == null ||
                    !validPolicy(policy, tools)
                ? null
                : () => save(id!, policy)),
      const KMuted(
          'Installed and qualified alternatives appear here. An administrator can add custom workers.'),
    ], gap: 16);
  }

  @override
  Widget build(BuildContext context) => KCard(
        title: 'CAD and PCB tools',
        trailing: IconButton(
            tooltip: 'Refresh engineering tools',
            icon: const Icon(Icons.refresh, size: 18),
            onPressed:
                saving ? null : () => ref.invalidate(engineeringToolsProvider)),
        child: ref.watch(engineeringToolsProvider).when(
              loading: () => const KLineLoader(),
              error: (_, __) => kColumn([
                const KMuted('Engineering tools could not be loaded.'),
                KButton(
                    label: 'Retry tools',
                    compact: true,
                    onPressed: () => ref.invalidate(engineeringToolsProvider)),
              ]),
              data: choices,
            ),
      );
}
