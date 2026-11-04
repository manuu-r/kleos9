import 'package:flutter/material.dart';
import '../../../data/models/production.dart';
import '../../core/widgets.dart';

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
                const {'meta', 'modal'}.contains(model.choice.provider) &&
                model.configured &&
                engineeringLanes.keys.every(model.lanes.contains)))
              KButton(
                  key: ValueKey(
                      'provider-preset-${model.choice.provider}-${model.choice.model}'),
                  label: 'Use ${model.choice.model} for all lanes',
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
                  .where((m) =>
                      const {'meta', 'modal'}.contains(m.choice.provider) &&
                      m.lanes.contains(lane.key))
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
                      key: ValueKey(
                          '${lane.key}-${selected?.provider}-${selected?.model}'),
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
                                      'provider-option-${lane.key}-${model.choice.provider}-${model.choice.model}'),
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
                      'Not connected yet. An administrator must configure this provider before it can run.'),
              ], gap: 8);
            }),
          const KMuted(
              'Provider credentials stay on the server. Engineering acceptance always comes from independent checks.'),
        ], gap: 20),
      );
}
