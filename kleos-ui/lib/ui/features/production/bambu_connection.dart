import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:url_launcher/url_launcher.dart';

import '../../../data/models/json.dart';
import '../../../state/kleos_store.dart';
import '../../../state/production_store.dart';
import '../../core/widgets.dart';
import '../project/bambu_print.dart';
import '../project/common.dart';
import 'bambu_local_connection.dart';
import 'bambu_test_print.dart';

final bambuConnectionProvider =
    FutureProvider.autoDispose<JsonMap>((ref) async {
  final repository = ref.watch(productionRepositoryProvider);
  final responses = await Future.wait([
    repository.api.bambuCatalog(organizationId: repository.organizationId),
    repository.connectors(),
    repository.api.bambuLocalState(organizationId: repository.organizationId),
  ]);
  final connectors = asMaps(asMap(responses[1])['connectors']);
  final local = asMap(responses[2]);
  return {
    'catalog': responses[0],
    'local': local,
    'connectors': {
      'connectors': [
        ...connectors,
        if (local['connected'] == true) {'telemetry': local['observation']},
      ]
    }
  };
}, retry: (_, __) => null);

bool hasBambuStatus(JsonMap data) =>
    asMaps(asMap(data['connectors'])['connectors']).any((c) {
      final telemetry = asMap(c['telemetry']);
      return telemetry['kind'] == 'bambu' &&
          telemetry['connected'] == true &&
          telemetry['stale'] != true;
    });

bool hasBambuPairing(JsonMap data) {
  final local = asMap(data['local']);
  return local['paired'] == true || local['credentialsStored'] == true;
}

String bambuConnectionStatus(JsonMap data) => hasBambuStatus(data)
    ? 'Printer connected'
    : hasBambuPairing(data)
        ? 'Printer paired · status needs refresh'
        : 'Printer not connected';

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
                final testReady = asMap(data['local'])['enabled'] == true &&
                    asMaps(asMap(data['catalog'])['profiles'])
                        .any((p) => p['testPreparation'] == true);
                return pText(
                    '${profiles == 0 ? (testReady ? 'Test slicing ready' : 'Slicing setup needed') : '$profiles approved slicing profiles'} · '
                    '${bambuConnectionStatus(data)}',
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
                          testPrint: asMap(data['local'])['enabled'] == true
                              ? (model) => BambuTestPrintPanel(
                                  modelId: model,
                                  profiles: asMaps(
                                      asMap(data['catalog'])['profiles']))
                              : null,
                          discoveredConnection: (model, onModelSelected) =>
                              asMap(data['local'])['enabled'] == true
                                  ? BambuLocalConnectionForm(
                                      modelId: model,
                                      onModelSelected: onModelSelected,
                                      onDiscover: () {
                                        final repository = ref
                                            .read(productionRepositoryProvider);
                                        return repository.api
                                            .discoverLocalBambu(
                                                organizationId:
                                                    repository.organizationId);
                                      },
                                      state: asMap(data['local']),
                                      onCertificate: (body) {
                                        final repository = ref
                                            .read(productionRepositoryProvider);
                                        return repository.api.bambuCertificate(
                                            body,
                                            organizationId:
                                                repository.organizationId);
                                      },
                                      onConnect: (body) async {
                                        final repository = ref
                                            .read(productionRepositoryProvider);
                                        final result = await repository.api
                                            .connectLocalBambu(body,
                                                organizationId:
                                                    repository.organizationId);
                                        ref.invalidate(bambuConnectionProvider);
                                        return result;
                                      },
                                      onRefresh: () async {
                                        final repository = ref
                                            .read(productionRepositoryProvider);
                                        final result = await repository.api
                                            .refreshLocalBambu(
                                                organizationId:
                                                    repository.organizationId);
                                        ref.invalidate(bambuConnectionProvider);
                                        return result;
                                      },
                                      onForget: () async {
                                        final repository = ref
                                            .read(productionRepositoryProvider);
                                        final result = await repository.api
                                            .forgetLocalBambu(
                                                organizationId:
                                                    repository.organizationId);
                                        ref.invalidate(bambuConnectionProvider);
                                        return result;
                                      })
                                  : const SizedBox.shrink(),
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
      required this.preparation,
      this.connection,
      this.testPrint,
      this.discoveredConnection});
  final JsonMap data;
  final List<JsonMap> projects;
  final Future<void> Function() onGuide;
  final Widget Function(JsonMap project, String? modelId) preparation;
  final Widget Function(String? modelId)? connection;
  final Widget Function(String? modelId)? testPrint;
  final Widget Function(String? modelId, ValueChanged<String> onModelSelected)?
      discoveredConnection;

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
    final localModel = asMap(widget.data['local'])['modelId'];
    final model = models.any((m) => m['id'] == selectedModel)
        ? selectedModel
        : detected ??
            (models.any((m) => m['id'] == localModel)
                ? localModel as String
                : null);
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
          'Find your printer below or choose its model for manual setup. Printer access codes stay on your computer.',
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
      pText(bambuConnectionStatus(widget.data), small: true),
      if (widget.connection != null) widget.connection!(model),
      if (widget.discoveredConnection != null)
        KeyedSubtree(
            key: const ValueKey('bambu-local-connection'),
            child: widget.discoveredConnection!(model, (value) {
              if (models.any((item) => item['id'] == value)) {
                setState(() => selectedModel = value);
              }
            })),
      if (model != null)
        pText(
            profiles.isEmpty
                ? 'Slicing setup needed: no approved nozzle/material profile is installed for this model.'
                : '${profiles.length} approved nozzle/material profiles installed for this model.',
            small: true),
      if (widget.testPrint != null)
        KeyedSubtree(
            key: const ValueKey('bambu-small-test-print'),
            child: widget.testPrint!(model)),
      pText(
          'Printing needs an accepted part and a reviewed slicing profile. You can connect and check status before setting up slicing.',
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
          catalog['localPrintingEnabled'] == true
              ? 'Paired A1 mini, A1, P1S and X1 Carbon printers have an experimental, separately reviewed direct LAN print path; exact profiles and firmware require qualification. Other setups retain Bambu Connect. A prepared plate does not establish physical acceptance.'
              : 'Printing starts only after your review in Bambu Connect. A prepared plate does not establish physical acceptance.',
          small: true,
          muted: true),
    ]);
  }
}
