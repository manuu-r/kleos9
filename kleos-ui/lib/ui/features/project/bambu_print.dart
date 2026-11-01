import 'dart:convert';

import 'package:crypto/crypto.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:share_plus/share_plus.dart';

import '../../../data/models/json.dart';
import '../../../state/production_store.dart';
import '../../core/widgets.dart';
import 'common.dart';

typedef BambuPrintRequest = ({String projectId, int revision});

String? detectedBambuModel(JsonMap data) {
  final models = asMaps(asMap(data['catalog'])['models']);
  final reported = <String>{};
  for (final connector in asMaps(asMap(data['connectors'])['connectors'])) {
    final telemetry = asMap(connector['telemetry']);
    final model = asMap(telemetry['profile'])['modelId'];
    if (telemetry['kind'] == 'bambu' &&
        telemetry['connected'] == true &&
        telemetry['stale'] != true &&
        models.any((m) => m['id'] == model)) {
      reported.add('$model');
    }
  }
  return reported.length == 1 ? reported.single : null;
}

final bambuPrintProvider = FutureProvider.autoDispose
    .family<JsonMap, BambuPrintRequest>((ref, request) async {
  final repository = ref.watch(productionRepositoryProvider);
  final api = repository.api;
  final organization = repository.organizationId;
  final responses = await Future.wait([
    api.bambuCatalog(organizationId: organization),
    api.get('/projects/${Uri.encodeComponent(request.projectId)}/workspace',
        organizationId: organization),
    api.get('/connectors', organizationId: organization),
    api.printPreparations(request.projectId, organizationId: organization),
  ]);
  return {
    'catalog': responses[0],
    'workspace': responses[1],
    'connectors': responses[2],
    'preparations': responses[3]
  };
}, retry: (_, __) => null);

class BambuPrintPanel extends ConsumerWidget {
  const BambuPrintPanel(
      {super.key,
      required this.projectId,
      required this.revision,
      this.initialModelId,
      this.showModelSelector = true});
  final String projectId;
  final int revision;
  final String? initialModelId;
  final bool showModelSelector;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final provider =
        bambuPrintProvider((projectId: projectId, revision: revision));
    return ref.watch(provider).when(
          loading: () => const SizedBox.shrink(),
          error: (_, __) => pText(
              'Printer setup unavailable. Your design files are still available.',
              small: true,
              muted: true),
          data: (data) => BambuPrintControls(
            key: ValueKey('$projectId-$initialModelId'),
            data: data,
            initialModelId: initialModelId,
            showModelSelector: showModelSelector,
            onPrepare: (body, key) async {
              final repository = ref.read(productionRepositoryProvider);
              final result = await repository.api.prepareBambuPrint(
                  projectId, body,
                  key: key, organizationId: repository.organizationId);
              ref.invalidate(provider);
              return result;
            },
            onDownload: (id) async {
              final bytes = await ref
                  .read(productionRepositoryProvider)
                  .api
                  .artifactBytes(id);
              if (!context.mounted) return;
              final box = context.findRenderObject() as RenderBox;
              await SharePlus.instance.share(ShareParams(
                  files: [XFile.fromData(bytes, mimeType: 'application/zip')],
                  fileNameOverrides: ['bambu-plate.gcode.3mf'],
                  sharePositionOrigin:
                      box.localToGlobal(Offset.zero) & box.size));
            },
          ),
        );
  }
}

class BambuPrintControls extends StatefulWidget {
  const BambuPrintControls(
      {super.key,
      required this.data,
      required this.onPrepare,
      required this.onDownload,
      this.initialModelId,
      this.showModelSelector = true});
  final JsonMap data;
  final Future<JsonMap> Function(JsonMap body, String key) onPrepare;
  final Future<void> Function(String artifactId) onDownload;
  final String? initialModelId;
  final bool showModelSelector;

  @override
  State<BambuPrintControls> createState() => _BambuPrintControlsState();
}

class _BambuPrintControlsState extends State<BambuPrintControls> {
  late String? chosenModel = widget.initialModelId;
  String? chosenProfile, chosenPart, error, notice;
  bool busy = false;

  @override
  Widget build(BuildContext context) {
    final catalog = asMap(widget.data['catalog']);
    final models = asMaps(catalog['models']);
    final workspace = asMap(widget.data['workspace']);
    final project = asMap(workspace['project']);
    final parts = asMaps(workspace['artifacts'])
        .where((a) =>
            a['lane'] == 'mechanics' &&
            a['status'] == 'accepted' &&
            a['revision'] == project['engineeringRevision'] &&
            '${a['name']}'.toLowerCase().endsWith('.stl'))
        .toList();
    final detected = detectedBambuModel(widget.data);
    final modelId =
        models.any((m) => m['id'] == chosenModel) ? chosenModel : detected;
    final profiles = asMaps(catalog['profiles'])
        .where((p) => p['printerModelId'] == modelId && p['qualified'] == true)
        .toList();
    final profileId =
        profiles.any((p) => p['id'] == chosenProfile) ? chosenProfile : null;
    final partId = parts.any((p) => p['id'] == chosenPart)
        ? chosenPart
        : parts.length == 1
            ? '${parts.single['id']}'
            : null;
    final releaseId = project['acceptedReleaseId'];
    final canPrepare =
        !busy && releaseId != null && partId != null && profileId != null;
    return KCard(
        child: pColumn([
      Wrap(spacing: 10, runSpacing: 8, children: [
        pText('Print with Bambu Lab', strong: true),
        const KBadge('Real preparation', tone: 'accent'),
      ]),
      pText(
          'Slice an accepted part, then review the plate and start printing in Bambu Connect.',
          small: true,
          muted: true),
      if (detected != null && chosenModel == null)
        pText(
            'Detected from your printer bridge: ${models.firstWhere((m) => m['id'] == detected)['name']}',
            small: true),
      if (widget.showModelSelector)
        DropdownButtonFormField<String>(
            key: ValueKey('bambu-model-$modelId'),
            initialValue: modelId,
            isExpanded: true,
            decoration: const InputDecoration(labelText: 'Printer model'),
            items: [
              for (final model in models)
                DropdownMenuItem(
                    value: '${model['id']}',
                    child: Text('${model['name']}',
                        maxLines: 1, overflow: TextOverflow.ellipsis))
            ],
            onChanged: busy
                ? null
                : (value) => setState(() {
                      chosenModel = value;
                      chosenProfile = null;
                    })),
      if (modelId != null && profiles.isEmpty)
        pText(
            'This model needs an approved nozzle/material slicing profile before preparation is available.',
            small: true,
            muted: true),
      if (profiles.isNotEmpty)
        DropdownButtonFormField<String>(
            key: ValueKey('bambu-profile-$modelId-$profileId'),
            initialValue: profileId,
            isExpanded: true,
            decoration: const InputDecoration(labelText: 'Nozzle and material'),
            items: [
              for (final profile in profiles)
                DropdownMenuItem(
                    value: '${profile['id']}',
                    child: Text(
                        '${profile['nozzleMm']} mm · ${profile['material']}',
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis))
            ],
            onChanged:
                busy ? null : (value) => setState(() => chosenProfile = value)),
      if (parts.isNotEmpty)
        DropdownButtonFormField<String>(
            key: ValueKey('bambu-part-$partId'),
            initialValue: partId,
            isExpanded: true,
            decoration: const InputDecoration(labelText: 'Accepted part'),
            items: [
              for (final part in parts)
                DropdownMenuItem(
                    value: '${part['id']}',
                    child: Text('${part['name']}',
                        maxLines: 1, overflow: TextOverflow.ellipsis))
            ],
            onChanged:
                busy ? null : (value) => setState(() => chosenPart = value)),
      if (releaseId == null || parts.isEmpty)
        pText(
            'Accept the current digital release before preparing a physical print.',
            small: true,
            muted: true),
      if (error != null) pText(error, color: KColors.fail),
      if (notice != null) pText(notice, small: true),
      KButton(
          label: 'Prepare print',
          loading: busy,
          icon: Icons.print_outlined,
          onPressed: canPrepare
              ? () async {
                  final body = <String, dynamic>{
                    'releaseId': releaseId,
                    'artifactId': partId,
                    'profileId': profileId,
                    'expectedEngineeringRevision':
                        project['engineeringRevision']
                  };
                  final key =
                      'bambu-${sha256.convert(utf8.encode(jsonEncode(body)))}';
                  setState(() {
                    busy = true;
                    error = null;
                    notice = null;
                  });
                  try {
                    await widget.onPrepare(body, key.substring(0, 64));
                    if (mounted) {
                      setState(() => notice =
                          'Preparation saved. Your local slicing adapter can now prepare the plate.');
                    }
                  } catch (_) {
                    if (mounted) {
                      setState(() => error =
                          'Could not prepare this print. Check the release and printer profile, then retry.');
                    }
                  } finally {
                    if (mounted) setState(() => busy = false);
                  }
                }
              : null),
      for (final job
          in asMaps(asMap(widget.data['preparations'])['preparations']).take(8))
        pColumn([
          Wrap(spacing: 8, runSpacing: 8, children: [
            KBadge(job['status'] == 'ready_for_operator'
                ? 'Ready for review'
                : job['status'] == 'cancelled'
                    ? 'Cancelled'
                    : 'Waiting for local slicing'),
            pText(
                '${asMap(job['profile'])['material']} · ${asMap(job['profile'])['nozzleMm']} mm',
                small: true),
          ]),
          if (job['status'] == 'ready_for_operator' &&
              job['preparedArtifactId'] != null)
            KButton(
                label: 'Download sliced plate',
                icon: Icons.download_outlined,
                onPressed: () async {
                  // Use the same authorized download/save behavior as engineering files.
                  try {
                    await widget.onDownload('${job['preparedArtifactId']}');
                  } catch (_) {
                    if (mounted) {
                      setState(() => error = 'Sliced plate download failed.');
                    }
                  }
                }),
          pDisclosure(
              title: 'Local adapter details',
              child: pColumn([
                pText('Preparation ID: ${job['id']}', small: true, mono: true),
                pText(
                    'Run the local Bambu adapter for this preparation. It verifies the accepted source, slices offline and opens Bambu Connect only when requested.',
                    small: true),
              ])),
        ], gap: 8),
      pText(
          'Slicing is digital preparation, not physical acceptance. Confirm orientation, supports, bed, filament and AMS mapping before starting the printer.',
          small: true,
          muted: true),
    ]));
  }
}
