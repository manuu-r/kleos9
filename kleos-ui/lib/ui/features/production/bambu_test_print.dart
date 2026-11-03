import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:share_plus/share_plus.dart';

import '../../../data/models/json.dart';
import '../../../data/services/production_api.dart';
import '../../../state/production_store.dart';
import '../../core/widgets.dart';
import '../project/common.dart';

class BambuTestPrintPanel extends ConsumerWidget {
  const BambuTestPrintPanel(
      {super.key, required this.modelId, required this.profiles});
  final String? modelId;
  final List<JsonMap> profiles;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final repository = ref.watch(productionRepositoryProvider);
    final organization = repository.organizationId;
    return BambuTestPrintControls(
      key: ValueKey('test-print-$organization-${identityHashCode(repository)}'),
      modelId: modelId,
      profiles: profiles,
      onPrepare: (body) => repository.api
          .prepareBambuTestPrint(body, organizationId: organization),
      onRead: (id) =>
          repository.api.bambuTestPrint(id, organizationId: organization),
      onDownload: (job, source) async {
        final bytes = await repository.api.bambuTestPrintBytes('${job['id']}',
            source: source,
            checksum: '${job[source ? 'sourceSha256' : 'packageSha256']}',
            organizationId: organization);
        if (!context.mounted ||
            !identical(ref.read(productionRepositoryProvider), repository) ||
            (context.widget as BambuTestPrintPanel).modelId !=
                job['printerModelId']) {
          return;
        }
        final box = context.findRenderObject() as RenderBox;
        await SharePlus.instance.share(ShareParams(files: [
          XFile.fromData(bytes,
              mimeType: source ? 'model/stl' : 'application/zip')
        ], fileNameOverrides: [
          source ? 'kleos-test-tile.stl' : 'kleos-test-tile.gcode.3mf'
        ], sharePositionOrigin: box.localToGlobal(Offset.zero) & box.size));
      },
    );
  }
}

class BambuTestPrintControls extends StatefulWidget {
  const BambuTestPrintControls(
      {super.key,
      required this.modelId,
      required this.profiles,
      required this.onPrepare,
      required this.onRead,
      required this.onDownload});
  final String? modelId;
  final List<JsonMap> profiles;
  final Future<JsonMap> Function(JsonMap) onPrepare;
  final Future<JsonMap> Function(String) onRead;
  final Future<void> Function(JsonMap, bool) onDownload;

  @override
  State<BambuTestPrintControls> createState() => _BambuTestPrintControlsState();
}

class _BambuTestPrintControlsState extends State<BambuTestPrintControls> {
  String? nozzle, filament, bed, profileId, error;
  JsonMap? job;
  bool expanded = false, busy = false;
  Timer? poll;
  int generation = 0, polls = 0;

  @override
  void didUpdateWidget(covariant BambuTestPrintControls oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.modelId != widget.modelId ||
        jsonEncode(oldWidget.profiles) != jsonEncode(widget.profiles)) {
      nozzle = filament = bed = profileId = null;
      invalidate();
    }
  }

  @override
  void dispose() {
    poll?.cancel();
    generation++;
    super.dispose();
  }

  void invalidate() {
    poll?.cancel();
    generation++;
    job = null;
    error = null;
    busy = false;
    polls = 0;
  }

  String filamentKey(JsonMap p) =>
      jsonEncode([p['material'], p['filamentPreset']]);
  String bedKey(JsonMap p) => '${p['bedType'] ?? 'Profile-defined'}';
  String? choose(String? selected, Iterable<String> values) {
    final options = values.toSet();
    return options.contains(selected)
        ? selected
        : options.length == 1
            ? options.single
            : null;
  }

  void schedule(int fence) {
    poll?.cancel();
    if (job?['status'] != 'slicing' || !mounted || generation != fence) return;
    if (polls >= 180) {
      setState(() => error =
          'Still waiting for slicing. Choose Check progress to get the latest result.');
      return;
    }
    poll = Timer(const Duration(seconds: 2), () => read(fence));
  }

  Future<void> read(int fence) async {
    if (busy || !mounted || generation != fence || job == null) return;
    final id = '${job!['id']}';
    setState(() => busy = true);
    try {
      polls++;
      final result = await widget.onRead(id);
      if (!mounted || generation != fence) return;
      setState(() {
        job = result;
        error = null;
      });
    } catch (e) {
      if (mounted && generation == fence) {
        setState(() => error = e is ProductionApiException
            ? e.message
            : 'Could not check slicing. Choose Check progress to retry.');
      }
    } finally {
      if (mounted && generation == fence) {
        setState(() => busy = false);
        if (error == null) schedule(fence);
      }
    }
  }

  Future<void> prepare(String id) async {
    invalidate();
    final fence = generation;
    setState(() => busy = true);
    try {
      final result = await widget
          .onPrepare({'profileId': id, 'printerModelId': widget.modelId});
      if (!mounted || generation != fence) return;
      setState(() => job = result);
    } catch (e) {
      if (mounted && generation == fence) {
        setState(() => error = e is ProductionApiException
            ? e.message
            : 'Could not prepare the test part. Check the local slicing setup.');
      }
    } finally {
      if (mounted && generation == fence) {
        setState(() => busy = false);
        schedule(fence);
      }
    }
  }

  Future<void> download(bool source) async {
    final fence = generation;
    final selected = job;
    if (selected == null) return;
    setState(() {
      busy = true;
      error = null;
    });
    try {
      await widget.onDownload(selected, source);
    } catch (e) {
      if (mounted && generation == fence) {
        setState(() => error = e is ProductionApiException
            ? e.message
            : 'Could not download the test file. Try again.');
      }
    } finally {
      if (mounted && generation == fence) {
        setState(() => busy = false);
        schedule(fence);
      }
    }
  }

  Widget dropdown(String label, String? value, Map<String, String> choices,
          ValueChanged<String?> changed) =>
      DropdownButtonFormField<String>(
        key: ValueKey('$label-$value-${choices.keys.join(',')}'),
        initialValue: value,
        isExpanded: true,
        decoration: InputDecoration(labelText: label),
        items: [
          for (final entry in choices.entries)
            DropdownMenuItem(
                value: entry.key,
                child: Text(entry.value,
                    maxLines: 1, overflow: TextOverflow.ellipsis))
        ],
        onChanged: busy || job?['status'] == 'slicing' || choices.isEmpty
            ? null
            : changed,
      );

  @override
  Widget build(BuildContext context) {
    final available = widget.profiles
        .where((p) =>
            p['testPreparation'] == true &&
            p['printerModelId'] == widget.modelId)
        .toList();
    final selectedNozzle =
        choose(nozzle, available.map((p) => '${p['nozzleMm']}'));
    final nozzleProfiles =
        available.where((p) => '${p['nozzleMm']}' == selectedNozzle).toList();
    final selectedFilament = choose(filament, nozzleProfiles.map(filamentKey));
    final filamentProfiles = nozzleProfiles
        .where((p) => filamentKey(p) == selectedFilament)
        .toList();
    final selectedBed = choose(bed, filamentProfiles.map(bedKey));
    final bedProfiles =
        filamentProfiles.where((p) => bedKey(p) == selectedBed).toList();
    final selectedProfile =
        choose(profileId, bedProfiles.map((p) => '${p['id']}'));
    final slicing = job?['status'] == 'slicing';
    final ready = job?['status'] == 'ready';
    return pColumn([
      KButton(
          label: 'Small test print',
          icon: Icons.view_in_ar_outlined,
          onPressed: () => setState(() => expanded = !expanded)),
      if (expanded) ...[
        pText('10 × 10 × 2 mm test tile', strong: true),
        pText(
            'A tiny solid CAD part for checking your slicing setup. No device build is needed.',
            small: true),
        if (widget.modelId == null)
          pText('Select your printer model above.', small: true)
        else if (available.isEmpty)
          pText(
              'No installed Orca slicing profiles for this model. Install a matching profile to prepare a test.',
              small: true)
        else ...[
          dropdown(
              'Test nozzle',
              selectedNozzle,
              {
                for (final p in available)
                  '${p['nozzleMm']}': '${p['nozzleMm']} mm'
              },
              (v) => setState(() {
                    invalidate();
                    nozzle = v;
                    filament = bed = profileId = null;
                  })),
          dropdown(
              'Test filament',
              selectedFilament,
              {
                for (final p in nozzleProfiles)
                  filamentKey(p): '${p['filamentPreset'] ?? p['material']}'
              },
              (v) => setState(() {
                    invalidate();
                    filament = v;
                    bed = profileId = null;
                  })),
          dropdown(
              'Test build plate',
              selectedBed,
              {for (final p in filamentProfiles) bedKey(p): bedKey(p)},
              (v) => setState(() {
                    invalidate();
                    bed = v;
                    profileId = null;
                  })),
          if (bedProfiles.length > 1)
            dropdown(
                'Test slicing profile',
                selectedProfile,
                {for (final p in bedProfiles) '${p['id']}': '${p['id']}'},
                (v) => setState(() {
                      invalidate();
                      profileId = v;
                    })),
          pText(
              'Review the sliced plate and print it in Bambu Connect. Preparing this test does not approve the profile or start the printer.',
              small: true,
              muted: true),
          KButton(
              label: slicing ? 'Slicing test part…' : 'Slice test part',
              loading: busy || slicing,
              onPressed: busy || slicing || selectedProfile == null
                  ? null
                  : () => prepare(selectedProfile)),
        ],
        if (error != null) pText(error, color: KColors.fail, small: true),
        if (job != null) ...[
          pText(job!['message'] ?? 'Preparing test part', small: true),
          if (ready) ...[
            if (job!['estimatedSeconds'] is num)
              pText(
                  'Estimated print time: ${(number(job!['estimatedSeconds']) / 60).toStringAsFixed(1)} min',
                  small: true),
            if (job!['filamentGrams'] is num)
              pText(
                  'Estimated filament: ${number(job!['filamentGrams']).toStringAsFixed(2)} g',
                  small: true),
            KButton(
                label: 'Download sliced test plate',
                icon: Icons.download_outlined,
                onPressed: busy ? null : () => download(false)),
          ],
          if (slicing)
            KButton(
                label: 'Check progress',
                onPressed: busy
                    ? null
                    : () {
                        polls = 0;
                        read(generation);
                      }),
          KButton(
              label: 'Download test STL',
              onPressed: busy ? null : () => download(true)),
        ],
      ],
    ]);
  }
}
