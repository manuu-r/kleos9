import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:url_launcher/url_launcher.dart';

import '../../../data/models/json.dart';
import '../../../state/kleos_store.dart';
import '../../../state/production_store.dart';
import '../../core/widgets.dart';
import '../project/bambu_print.dart';
import '../project/common.dart';

final bambuConnectionProvider =
    FutureProvider.autoDispose<JsonMap>((ref) async {
  final repository = ref.watch(productionRepositoryProvider);
  final responses = await Future.wait([
    repository.api.bambuCatalog(organizationId: repository.organizationId),
    repository.api
        .get('/connectors', organizationId: repository.organizationId),
  ]);
  return {'catalog': responses[0], 'connectors': responses[1]};
}, retry: (_, __) => null);

bool hasBambuStatus(JsonMap data) =>
    asMaps(asMap(data['connectors'])['connectors']).any((c) {
      final telemetry = asMap(c['telemetry']);
      return telemetry['kind'] == 'bambu' &&
          telemetry['connected'] == true &&
          telemetry['stale'] != true;
    });

class BambuConnectionCard extends ConsumerWidget {
  const BambuConnectionCard({super.key, required this.onSetup});
  final VoidCallback onSetup;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final status = ref.watch(bambuConnectionProvider);
    return KCard(
        key: const ValueKey('real-bambu-adapter'),
        child: pColumn([
          Wrap(spacing: 10, runSpacing: 8, children: [
            pText('Bambu Lab', strong: true),
            const KBadge('Real adapter', tone: 'accent')
          ]),
          pText(
              'Prepare an accepted part for Bambu Connect. Review the plate and start the print there.',
              small: true,
              muted: true),
          status.when(
              loading: () => pText('Checking setup…', small: true),
              error: (_, __) => pText(
                  'Bambu setup is unavailable. Retry when the backend is reachable.',
                  small: true),
              data: (data) {
                final profiles = asMaps(asMap(data['catalog'])['profiles'])
                    .where((p) => p['qualified'] == true)
                    .length;
                return pText(
                    '${profiles == 0 ? 'Slicing setup needed' : '$profiles approved slicing profiles'} · '
                    '${hasBambuStatus(data) ? 'Local status bridge connected' : 'Local status bridge not connected'}',
                    small: true);
              }),
          KButton(
              label: 'Set up Bambu Lab',
              icon: Icons.print_outlined,
              onPressed: onSetup),
        ]));
  }
}

class BambuConnectionDialog extends ConsumerWidget {
  const BambuConnectionDialog({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final projects = asMaps(ref.watch(kleosProvider).state['projects'])
        .where((p) => p['production'] == true && p['simulation'] != true)
        .toList();
    return Dialog(
        insetPadding: const EdgeInsets.all(20),
        child: ConstrainedBox(
          constraints: BoxConstraints(
              maxWidth: 700, maxHeight: MediaQuery.sizeOf(context).height - 40),
          child: SingleChildScrollView(
              padding: const EdgeInsets.all(24),
              child: pColumn([
                Row(children: [
                  Expanded(child: pText('Set up Bambu Lab', strong: true)),
                  IconButton(
                      tooltip: 'Close',
                      onPressed: () => Navigator.of(context).pop(false),
                      icon: const Icon(Icons.close))
                ]),
                const KBadge('Real adapter', tone: 'accent'),
                ref.watch(bambuConnectionProvider).when(
                      loading: () => pText('Loading printer setup…'),
                      error: (_, __) => pColumn([
                        pText('Could not load Bambu setup.'),
                        KButton(
                            label: 'Retry setup',
                            onPressed: () =>
                                ref.invalidate(bambuConnectionProvider))
                      ]),
                      data: (data) => BambuConnectionControls(
                          data: data,
                          projects: projects,
                          onGuide: () async {
                            if (!await launchUrl(
                                Uri.parse(
                                    'https://github.com/manuu-r/kleos9/blob/feature/ngspice-simulation/backend/docs/bambu-printers.md'),
                                mode: LaunchMode.externalApplication)) {
                              throw StateError(
                                  'Setup guide could not be opened');
                            }
                          },
                          preparation: (project, model) => BambuPrintPanel(
                              projectId: '${project['id']}',
                              revision: number(project['revision']).toInt(),
                              initialModelId: model,
                              showModelSelector: false)),
                    ),
              ])),
        ));
  }
}

class BambuConnectionControls extends StatefulWidget {
  const BambuConnectionControls(
      {super.key,
      required this.data,
      required this.projects,
      required this.onGuide,
      required this.preparation});
  final JsonMap data;
  final List<JsonMap> projects;
  final Future<void> Function() onGuide;
  final Widget Function(JsonMap project, String? modelId) preparation;

  @override
  State<BambuConnectionControls> createState() =>
      _BambuConnectionControlsState();
}

class _BambuConnectionControlsState extends State<BambuConnectionControls> {
  String? selectedModel, selectedProject, error;

  @override
  Widget build(BuildContext context) {
    final catalog = asMap(widget.data['catalog']);
    final models = asMaps(catalog['models']);
    final detected = detectedBambuModel(widget.data);
    final model =
        models.any((m) => m['id'] == selectedModel) ? selectedModel : detected;
    final profiles = asMaps(catalog['profiles'])
        .where((p) => p['qualified'] == true && p['printerModelId'] == model)
        .toList();
    final projects = widget.projects
        .where((p) =>
            p['production'] == true &&
            p['simulation'] != true &&
            p['acceptedReleaseId'] != null)
        .toList();
    final project =
        projects.where((p) => p['id'] == selectedProject).firstOrNull;
    return pColumn([
      pText(
          'Select your model and a build with an accepted part. Printer access codes stay on your computer.',
          small: true,
          muted: true),
      if (detected != null && selectedModel == null)
        pText('Model reported by your local printer bridge.', small: true),
      DropdownButtonFormField<String>(
          key: ValueKey('bambu-setup-model-$model'),
          initialValue: model,
          isExpanded: true,
          decoration: const InputDecoration(labelText: 'Printer model'),
          items: [
            for (final item in models)
              DropdownMenuItem(
                  value: '${item['id']}',
                  child: Text('${item['name']}',
                      maxLines: 1, overflow: TextOverflow.ellipsis))
          ],
          onChanged: (value) => setState(() => selectedModel = value)),
      pText(
          hasBambuStatus(widget.data)
              ? 'Local status bridge: connected (read-only).'
              : 'Local status bridge: not connected.',
          small: true),
      if (model != null)
        pText(
            profiles.isEmpty
                ? 'Slicing setup needed: no approved nozzle/material profile is installed for this model.'
                : '${profiles.length} approved nozzle/material profiles installed for this model.',
            small: true),
      pText(
          'Finish local setup with the guide: install the slicing adapter and a reviewed profile. For optional live status, enroll the LAN bridge on your computer.',
          small: true,
          muted: true),
      KButton(
          label: 'Open Bambu setup guide',
          icon: Icons.open_in_new,
          onPressed: () async {
            try {
              await widget.onGuide();
            } catch (_) {
              if (mounted) {
                setState(() => error = 'Could not open the setup guide.');
              }
            }
          }),
      if (error != null) pText(error, color: KColors.fail),
      if (projects.isEmpty)
        pText(
            'Accept a digital release in a build to prepare its parts for printing.',
            small: true),
      if (projects.isNotEmpty)
        DropdownButtonFormField<String>(
            key: ValueKey('bambu-setup-project-$selectedProject'),
            initialValue: project == null ? null : selectedProject,
            isExpanded: true,
            decoration: const InputDecoration(labelText: 'Build to prepare'),
            items: [
              for (final item in projects)
                DropdownMenuItem(
                    value: '${item['id']}',
                    child: Text('${item['name']}',
                        maxLines: 1, overflow: TextOverflow.ellipsis))
            ],
            onChanged: (value) => setState(() => selectedProject = value)),
      if (project != null) widget.preparation(project, model),
      pText(
          'Printing starts only after your review in Bambu Connect. A prepared plate does not establish physical acceptance.',
          small: true,
          muted: true),
    ]);
  }
}
