import 'dart:convert';
import 'dart:async';

import 'package:crypto/crypto.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:share_plus/share_plus.dart';

import '../../../data/models/json.dart';
import '../../../data/repositories/production_repository.dart';
import '../../../state/production_store.dart';
import '../../core/widgets.dart';
import 'common.dart';

typedef BambuPrintRequest = ({String projectId, int revision});

String _filamentKey(JsonMap profile) =>
    jsonEncode([profile['material'], profile['filamentPreset']]);
String _filamentLabel(JsonMap profile) => profile['filamentPreset'] is String &&
        (profile['filamentPreset'] as String).isNotEmpty
    ? '${profile['filamentPreset']} (${profile['material']})'
    : '${profile['material']} · Profile-defined';
String _bedKey(JsonMap profile) =>
    profile['bedType'] is String && (profile['bedType'] as String).isNotEmpty
        ? '${profile['bedType']}'
        : 'Profile-defined';
String _profileSummary(JsonMap profile) =>
    '${profile['nozzleMm']} mm nozzle · ${_filamentLabel(profile)} · Build plate: ${_bedKey(profile)}';
String? _singleOrChosen(String? chosen, Iterable<String> values) {
  final options = values.toSet();
  return options.contains(chosen) ? chosen : options.length == 1 ? options.single : null;
}

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
    repository.workspaceJson(request.projectId),
    repository.connectors(),
    api.printPreparations(request.projectId, organizationId: organization),
  ]);
  JsonMap local = {}, dispatches = {};
  if (responses[0]['localPrintingEnabled'] == true) {
    try {
      local = await api.bambuLocalState(organizationId: organization);
      if (local['enabled'] == true && local['directPrinting'] == true) {
        dispatches = await api.bambuDispatches(request.projectId,
            organizationId: organization);
      }
    } catch (_) {
      /* Optional local admin facility; hosted preparation still works. */
    }
  }
  return {
    // Keep actions bound to the same authenticated scope that loaded this snapshot.
    '_repository': repository,
    'local': local,
    'dispatches': dispatches,
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
          data: (data) {
            final repository = data['_repository'] as ProductionRepository?;
            return BambuPrintControls(
              key: ValueKey(
                  '$projectId-$revision-$initialModelId-${repository?.organizationId}-${identityHashCode(repository)}'),
              data: data,
              initialModelId: initialModelId,
              showModelSelector: showModelSelector,
              onRefresh: () async {
                ref.invalidate(provider);
              },
              onCreateDispatch: repository == null
                  ? null
                  : (body, key) => repository.api.createBambuDispatch(
                      projectId, body,
                      key: key, organizationId: repository.organizationId),
              onDispatchAction: repository == null
                  ? null
                  : (id, action, body) => repository.api.bambuDispatchAction(
                      projectId, id, action,
                      body: body, organizationId: repository.organizationId),
              onPrepare: (body, key) async {
                if (repository == null) {
                  throw StateError('Sign in to prepare a print');
                }
                final result = await repository.api.prepareBambuPrint(
                    projectId, body,
                    key: key, organizationId: repository.organizationId);
                if (context.mounted) ref.invalidate(provider);
                return result;
              },
              onDownload: (id) async {
                if (repository == null) {
                  throw StateError('Sign in to download a plate');
                }
                final bytes = await repository.api.artifactBytes(id);
                if (!context.mounted) return;
                final box = context.findRenderObject() as RenderBox;
                await SharePlus.instance.share(ShareParams(
                    files: [XFile.fromData(bytes, mimeType: 'application/zip')],
                    fileNameOverrides: ['bambu-plate.gcode.3mf'],
                    sharePositionOrigin:
                        box.localToGlobal(Offset.zero) & box.size));
              },
            );
          },
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
      this.showModelSelector = true,
      this.onRefresh,
      this.onCreateDispatch,
      this.onDispatchAction});
  final Future<void> Function()? onRefresh;
  final Future<JsonMap> Function(JsonMap, String)? onCreateDispatch;
  final Future<JsonMap> Function(String, String, JsonMap)? onDispatchAction;
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
  String? chosenProfile, chosenPart, chosenNozzle, chosenFilament, chosenBed, error, notice;
  String? selectionSignature;
  bool busy = false;
  Timer? poll;
  int polls = 0;
  final attempted = <String>{};
  final localDispatches = <String, JsonMap>{};

  @override
  void initState() {
    super.initState();
    poll = Timer.periodic(const Duration(seconds: 5), (_) {
      if (!mounted ||
          busy ||
          polls >= 120 ||
          WidgetsBinding.instance.lifecycleState != AppLifecycleState.resumed) {
        return;
      }
      final profiles = asMaps(asMap(widget.data['catalog'])['profiles']);
      final pending = asMaps(asMap(widget.data['preparations'])['preparations'])
          .any((job) =>
              !['ready_for_operator', 'cancelled', 'failed']
                  .contains(job['status']) &&
              profiles.any((p) =>
                  p['id'] == job['profileId'] &&
                  p['automaticPreparation'] == true));
      final activeDispatch =
          asMaps(asMap(widget.data['dispatches'])['dispatches']).any((d) => [
                'authorized',
                'uploading',
                'uploaded',
                'start_requested',
                'printing',
                'uncertain',
                'cancelled_uncertain'
              ].contains(d['status']));
      if (pending || activeDispatch) {
        polls++;
        widget.onRefresh?.call();
      }
    });
  }

  @override
  void didUpdateWidget(covariant BambuPrintControls oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.initialModelId != widget.initialModelId) {
      chosenModel = widget.initialModelId;
      resetProfileChoices();
    }
    if (!identical(oldWidget.data['dispatches'], widget.data['dispatches'])) {
      for (final dispatch
          in asMaps(asMap(widget.data['dispatches'])['dispatches'])) {
        localDispatches.remove('${dispatch['id']}');
      }
    }
  }

  @override
  void dispose() {
    poll?.cancel();
    super.dispose();
  }

  void resetProfileChoices() {
    chosenNozzle = chosenFilament = chosenBed = chosenProfile = null;
  }

  Future<void> start(JsonMap job, JsonMap project, JsonMap local) async {
    var confirmed = false;
    final approved = await showDialog<bool>(
        context: context,
        builder: (context) => StatefulBuilder(
            builder: (context, update) => AlertDialog(
                    title: const Text('Review and start print'),
                    content: SingleChildScrollView(
                        child: Column(
                            mainAxisSize: MainAxisSize.min,
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                          Text(
                              'Part: ${asMaps(asMap(widget.data['workspace'])['artifacts']).where((a) => a['id'] == job['sourceArtifactId']).firstOrNull?['name'] ?? job['sourceArtifactId']}'),
                          if (job['sourceSha256'] != null)
                            SelectableText(
                                'Source SHA-256: ${job['sourceSha256']}'),
                          Text(
                              'Profile: ${job['profileId']} · ${_profileSummary(asMap(job['profile']))}'),
                          Text(
                              'Printer: ${local['serial']} · Firmware ${local['firmwareVersion']}'),
                          const Text('Exact sliced plate SHA-256:'),
                          SelectableText('${job['preparedSha256']}'),
                          CheckboxListTile(
                              contentPadding: EdgeInsets.zero,
                              value: confirmed,
                              title: const Text(
                                  'I checked the plate, clear bed and external spool. Start this print now.'),
                              onChanged: (value) =>
                                  update(() => confirmed = value == true)),
                        ])),
                    actions: [
                      TextButton(
                          onPressed: () => Navigator.pop(context, false),
                          child: const Text('Back')),
                      FilledButton(
                          onPressed: confirmed
                              ? () => Navigator.pop(context, true)
                              : null,
                          child: const Text('Upload and start'))
                    ])));
    if (!mounted || approved != true) return;
    final id = '${job['id']}';
    if (attempted.contains(id)) return;
    setState(() {
      busy = true;
      attempted.add(id);
      error = null;
    });
    final create = widget.onCreateDispatch!;
    final action = widget.onDispatchAction!;
    final key =
        'print-${sha256.convert(utf8.encode('${project['id']}:$id:${job['preparedSha256']}'))}'
            .substring(0, 64);
    try {
      final dispatch = await create({
        'preparationId': id,
        'preparedSha256': job['preparedSha256'],
        'expectedEngineeringRevision': project['engineeringRevision'],
        'authorizePrint': true,
        'externalSpoolConfirmed': true
      }, key);
      if (!mounted) return;
      localDispatches['${dispatch['id']}'] = dispatch;
      // A recovered authorization may already have been sent. Never send it again.
      if (dispatch['status'] == 'authorized') {
        final result = await action('${dispatch['id']}', 'send', const {});
        if (!mounted) return;
        localDispatches['${result['id']}'] = result;
      }
    } catch (_) {
      if (mounted) {
        for (final entry in localDispatches.entries.toList()) {
          if (entry.value['preparationId'] == id) {
            localDispatches[entry.key] = {
              ...entry.value,
              'status': 'uncertain'
            };
          }
        }
        setState(() => error =
            'Print outcome is uncertain. Refresh status and check the printer before taking another action. The start command will not be resent.');
      }
    } finally {
      if (mounted) {
        setState(() => busy = false);
        await widget.onRefresh?.call();
      }
    }
  }

  Future<void> cancelAuthorization(JsonMap dispatch) async {
    setState(() => busy = true);
    try {
      final result = await widget.onDispatchAction!(
          '${dispatch['id']}', 'cancel', const {});
      if (mounted) setState(() => localDispatches['${result['id']}'] = result);
    } catch (_) {
      if (mounted) {
        setState(() => error =
            'Could not cancel the authorization. Check status. This action does not physically stop the printer.');
      }
    } finally {
      if (mounted) {
        setState(() => busy = false);
        await widget.onRefresh?.call();
      }
    }
  }

  Future<void> reconcile(JsonMap dispatch) async {
    String outcome = 'not_started';
    var idle = false;
    final confirmed = await showDialog<bool>(
        context: context,
        builder: (context) => StatefulBuilder(
            builder: (context, update) => AlertDialog(
                    title: const Text('Confirm printer outcome'),
                    content: SingleChildScrollView(
                        child:
                            Column(mainAxisSize: MainAxisSize.min, children: [
                      const Text(
                          'Check the printer itself. This records the outcome; it does not stop or move the printer.'),
                      DropdownButton<String>(
                          value: outcome,
                          isExpanded: true,
                          items: const [
                            DropdownMenuItem(
                                value: 'not_started',
                                child: Text('Print did not start')),
                            DropdownMenuItem(
                                value: 'finished',
                                child: Text('Print finished')),
                            DropdownMenuItem(
                                value: 'stopped',
                                child: Text('Print was stopped'))
                          ],
                          onChanged: (value) => update(() => outcome = value!)),
                      CheckboxListTile(
                          value: idle,
                          title:
                              const Text('I observed that the printer is idle'),
                          onChanged: (value) =>
                              update(() => idle = value == true))
                    ])),
                    actions: [
                      TextButton(
                          onPressed: () => Navigator.pop(context, false),
                          child: const Text('Back')),
                      FilledButton(
                          onPressed:
                              idle ? () => Navigator.pop(context, true) : null,
                          child: const Text('Record outcome'))
                    ])));
    if (!mounted || confirmed != true) return;
    setState(() => busy = true);
    try {
      final result = await widget.onDispatchAction!('${dispatch['id']}',
          'reconcile', {'confirmPrinterIdle': true, 'outcome': outcome});
      if (mounted) setState(() => localDispatches['${result['id']}'] = result);
    } catch (_) {
      if (mounted) {
        setState(() => error =
            'Could not reconcile. Refresh the paired printer status and check it is idle.');
      }
    } finally {
      if (mounted) {
        setState(() => busy = false);
        await widget.onRefresh?.call();
      }
    }
  }

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
            RegExp(r'\.(stl|obj)$', caseSensitive: false)
                .hasMatch('${a['name']}'))
        .toList();
    final detected = detectedBambuModel(widget.data);
    final modelId = chosenModel == null
        ? detected
        : models.any((m) => m['id'] == chosenModel) ? chosenModel : null;
    final model = models.where((m) => m['id'] == modelId).firstOrNull;
    final profiles = asMaps(catalog['profiles'])
        .where((p) => p['printerModelId'] == modelId)
        .toList();
    final signature = jsonEncode([modelId, profiles, model?['nozzlesMm']]);
    if (selectionSignature != signature) {
      resetProfileChoices();
      selectionSignature = signature;
    }
    final installedNozzles = profiles.map((p) => '${p['nozzleMm']}').toSet();
    final nozzleOptions = {
      ...((model?['nozzlesMm'] as List?) ?? []).map((n) => '$n'),
      ...installedNozzles,
    }.toList()..sort((a, b) => (double.tryParse(a) ?? 0).compareTo(double.tryParse(b) ?? 0));
    final nozzle = _singleOrChosen(chosenNozzle, installedNozzles);
    final nozzleProfiles = profiles.where((p) => '${p['nozzleMm']}' == nozzle).toList();
    final filaments = {for (final p in nozzleProfiles) _filamentKey(p): _filamentLabel(p)};
    final filament = _singleOrChosen(chosenFilament, filaments.keys);
    final filamentProfiles = nozzleProfiles.where((p) => _filamentKey(p) == filament).toList();
    final beds = filamentProfiles.map(_bedKey).toSet();
    final bed = _singleOrChosen(chosenBed, beds);
    final matchingProfiles = filamentProfiles.where((p) => _bedKey(p) == bed).toList();
    final profileId = matchingProfiles.any((p) => p['id'] == chosenProfile) ? chosenProfile : null;
    final partId = parts.any((p) => p['id'] == chosenPart)
        ? chosenPart
        : parts.length == 1
            ? '${parts.single['id']}'
            : null;
    final releaseId = project['acceptedReleaseId'];
    final local = asMap(widget.data['local']);
    final dispatches = <String, JsonMap>{
      for (final d in asMaps(asMap(widget.data['dispatches'])['dispatches']))
        '${d['id']}': d,
      ...localDispatches,
    }.values.toList();
    final automaticProfiles = asMaps(catalog['profiles'])
        .where((p) => p['automaticPreparation'] == true)
        .map((p) => p['id'])
        .toSet();
    final selectedPart = parts.where((p) => p['id'] == partId).firstOrNull;
    final obj = '${selectedPart?['name']}'.toLowerCase().endsWith('.obj');
    final selectedProfile =
        profiles.where((p) => p['id'] == profileId).firstOrNull;
    final canPrepare = !busy &&
        releaseId != null &&
        partId != null &&
        profileId != null &&
        selectedProfile?['qualified'] == true &&
        (!obj || selectedProfile?['slicer'] == 'orca');
    return KCard(
        child: pColumn([
      Wrap(spacing: 10, runSpacing: 8, children: [
        pText('Print with Bambu Lab', strong: true),
        const KBadge('Real preparation', tone: 'accent'),
      ]),
      pText(
          catalog['localPrintingEnabled'] == true
              ? 'Prepare an accepted part, then review its exact sliced plate before starting a supported paired printer (experimental). Download remains available for Bambu Connect.'
              : 'Slice an accepted part, then review the plate and start printing in Bambu Connect.',
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
                      resetProfileChoices();
                    })),
      if (modelId != null && !profiles.any((p) => p['qualified'] == true))
        pText(
            'This model needs an approved nozzle/material/build plate slicing profile before preparation is available.',
            small: true,
            muted: true),
      if (modelId != null && nozzleOptions.isNotEmpty)
        DropdownButtonFormField<String>(
            key: ValueKey('bambu-nozzle-$modelId-$nozzle'),
            initialValue: nozzle,
            isExpanded: true,
            decoration: const InputDecoration(labelText: 'Nozzle size'),
            items: [for (final value in nozzleOptions)
              DropdownMenuItem(value: value, enabled: installedNozzles.contains(value),
                child: Text('$value mm${installedNozzles.contains(value) ? '' : ' · No installed profile'}',
                    maxLines: 1, overflow: TextOverflow.ellipsis))],
            onChanged: busy ? null : (value) => setState(() {
              chosenNozzle = value;
              chosenFilament = chosenBed = chosenProfile = null;
            })),
      if (modelId != null)
        DropdownButtonFormField<String>(
            key: ValueKey('bambu-filament-$modelId-$nozzle-$filament'),
            initialValue: filament,
            isExpanded: true,
            decoration: const InputDecoration(labelText: 'Filament'),
            items: [for (final entry in filaments.entries)
              DropdownMenuItem(value: entry.key, child: Text(entry.value,
                  maxLines: 1, overflow: TextOverflow.ellipsis))],
            onChanged: busy || filaments.isEmpty ? null : (value) => setState(() {
              chosenFilament = value;
              chosenBed = chosenProfile = null;
            })),
      if (modelId != null)
        DropdownButtonFormField<String>(
            key: ValueKey('bambu-bed-$modelId-$nozzle-$filament-$bed'),
            initialValue: bed,
            isExpanded: true,
            decoration: const InputDecoration(labelText: 'Build plate'),
            items: [for (final value in beds)
              DropdownMenuItem(value: value, child: Text(value,
                  maxLines: 1, overflow: TextOverflow.ellipsis))],
            onChanged: busy || beds.isEmpty ? null : (value) => setState(() {
              chosenBed = value;
              chosenProfile = null;
            })),
      if (modelId != null && matchingProfiles.isEmpty)
        pText('Choose a nozzle, filament and build plate with an installed profile.',
            small: true, muted: true),
      if (matchingProfiles.isNotEmpty)
        DropdownButtonFormField<String>(
            key: ValueKey('bambu-profile-$modelId-$profileId'),
            initialValue: profileId,
            isExpanded: true,
            decoration: const InputDecoration(labelText: 'Slicing profile'),
            items: [
              for (final profile in matchingProfiles)
                DropdownMenuItem(
                    value: '${profile['id']}',
                    child: Text(
                        '${profile['id']}${profile['qualified'] == true ? '' : ' · Needs qualification'}',
                        maxLines: 1, overflow: TextOverflow.ellipsis))
            ],
            onChanged: busy ? null : (value) => setState(() => chosenProfile = value)),
      if (selectedProfile != null)
        pText(_profileSummary(selectedProfile), small: true),
      if (selectedProfile != null && selectedProfile['qualified'] != true)
        pText('This combination needs qualification before it can prepare a print.', small: true),
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
      if (obj && selectedProfile?['slicer'] != 'orca')
        pText(
            'Triangle-only OBJ in millimetres requires an approved Orca profile.',
            small: true),
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
                      setState(() => notice = automaticProfiles
                              .contains(profileId)
                          ? 'Automatic Orca preparation queued. Conversion, repair and slicing progress will update here.'
                          : 'Preparation saved. Your local slicing adapter can now prepare the plate.');
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
                    : automaticProfiles.contains(job['profileId'])
                        ? 'Automatic preparation: ${job['status']}'
                        : 'Waiting for local slicing'),
            pText(
                _profileSummary(asMap(job['profile'])),
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
          if (widget.onCreateDispatch != null &&
              widget.onDispatchAction != null &&
              catalog['localPrintingEnabled'] == true &&
              local['directPrinting'] == true &&
              local['connected'] == true &&
              (catalog['directPrintModelIds'] is List
                  ? (catalog['directPrintModelIds'] as List)
                      .contains(local['modelId'])
                  : const ['N1', 'N2S', 'C12', 'BL-P001'].contains(local['modelId'])) &&
              asMap(job['profile'])['printerModelId'] == local['modelId'] &&
              asMap(job['profile'])['qualified'] != false &&
              job['engineeringRevision'] == project['engineeringRevision'] &&
              job['automaticNativePrepared'] == true &&
              job['status'] == 'ready_for_operator' &&
              !attempted.contains('${job['id']}') &&
              !dispatches.any((d) => d['preparationId'] == job['id']))
            KButton(
                label: 'Upload and start print',
                onPressed: busy ? null : () => start(job, project, local)),
          pDisclosure(
              title: 'Local adapter details',
              child: pColumn([
                pText('Preparation ID: ${job['id']}', small: true, mono: true),
                pText(
                    automaticProfiles.contains(job['profileId'])
                        ? 'Automatic isolated preparation verifies the source, repairs the mesh and slices the plate with the pinned Orca profile.'
                        : 'Run the local Bambu adapter for this preparation. It verifies the accepted source, slices offline and opens Bambu Connect only when requested.',
                    small: true),
              ])),
        ], gap: 8),
      for (final dispatch in dispatches)
        pColumn([
          pText('Print: ${'${dispatch['status']}'.replaceAll('_', ' ')}',
              strong: true),
          if (dispatch['status'] == 'start_requested')
            pText(
                'Start request acknowledged. Printing has not yet been confirmed.',
                small: true),
          if (dispatch['message'] != null)
            pText('${dispatch['message']}', small: true),
          if (widget.onDispatchAction != null &&
              dispatch['status'] == 'authorized')
            KButton(
                label: 'Cancel print authorization',
                onPressed: busy ? null : () => cancelAuthorization(dispatch)),
          if (widget.onDispatchAction != null &&
              [
                'cancelled_uncertain',
                'uncertain',
                'start_requested',
                'printing',
                'uploaded'
              ].contains(dispatch['status']))
            KButton(
                label: 'Confirm printer outcome',
                onPressed: busy ? null : () => reconcile(dispatch)),
        ]),
      if (widget.onRefresh != null)
        KButton(
            label: 'Refresh print status',
            onPressed: busy ? null : widget.onRefresh),
      pText(
          'Slicing is digital preparation, not physical acceptance. Confirm orientation, supports, bed, filament and AMS mapping before starting the printer.',
          small: true,
          muted: true),
    ]));
  }
}
