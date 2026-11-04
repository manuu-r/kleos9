import 'dart:convert';

import 'package:crypto/crypto.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:url_launcher/url_launcher.dart';

import '../../../data/models/json.dart';
import '../../../state/production_store.dart';
import '../../core/widgets.dart';
import '../project/common.dart';

final rentAHumanStatusProvider = FutureProvider.autoDispose<JsonMap>((ref) {
  final repository = ref.watch(productionRepositoryProvider);
  return repository.api
      .rentAHumanStatus(organizationId: repository.organizationId);
}, retry: (_, __) => null);

Future<void> openRentAHuman() async {
  if (!await launchUrl(Uri.parse('https://rentahuman.ai'),
      mode: LaunchMode.externalApplication)) {
    throw StateError('RentAHuman could not be opened');
  }
}

class RentAHumanDirectory extends ConsumerWidget {
  const RentAHumanDirectory({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) =>
      ref.watch(rentAHumanStatusProvider).when(
            loading: () => const SizedBox.shrink(),
            error: (_, __) => const KMuted(
                'RentAHuman setup is unavailable. Simulated tasks remain available.'),
            data: (status) => RentAHumanDirectoryControls(
                key: ValueKey(
                    ref.watch(productionRepositoryProvider).organizationId),
                status: status,
                onSearch: (filters) async {
                  final repository = ref.read(productionRepositoryProvider);
                  return repository.api.rentAHumanSearch(filters,
                      organizationId: repository.organizationId);
                },
                onOpen: openRentAHuman),
          );
}

class RentAHumanDirectoryControls extends StatefulWidget {
  const RentAHumanDirectoryControls(
      {super.key,
      required this.status,
      required this.onSearch,
      required this.onOpen});
  final JsonMap status;
  final Future<JsonMap> Function(Map<String, String>) onSearch;
  final Future<void> Function() onOpen;

  @override
  State<RentAHumanDirectoryControls> createState() =>
      _RentAHumanDirectoryControlsState();
}

class _RentAHumanDirectoryControlsState
    extends State<RentAHumanDirectoryControls> {
  final skill = TextEditingController(text: 'electronics assembly');
  final city = TextEditingController();
  final country = TextEditingController();
  final rate = TextEditingController();
  JsonMap? results;
  String? error;
  bool busy = false;

  @override
  void dispose() {
    for (final controller in [skill, city, country, rate]) {
      controller.dispose();
    }
    super.dispose();
  }

  Future<void> search({String? cursor}) async {
    final maximum =
        rate.text.trim().isEmpty ? null : double.tryParse(rate.text);
    if (skill.text.trim().isEmpty ||
        (rate.text.isNotEmpty &&
            (maximum == null ||
                !maximum.isFinite ||
                maximum < 0 ||
                maximum > 10000))) {
      setState(
          () => error = 'Enter a skill and a valid optional hourly budget.');
      return;
    }
    setState(() {
      busy = true;
      error = null;
      results = null;
    });
    try {
      final response = await widget.onSearch({
        'skill': skill.text.trim(),
        'limit': '10',
        if (city.text.trim().isNotEmpty) 'city': city.text.trim(),
        if (country.text.trim().isNotEmpty) 'country': country.text.trim(),
        if (maximum != null) 'maxRate': '$maximum',
        if (cursor != null) 'cursor': cursor
      });
      if (mounted) setState(() => results = response);
    } catch (_) {
      if (mounted) {
        setState(() => error =
            'RentAHuman search is unavailable. No worker was contacted.');
      }
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  @override
  Widget build(BuildContext context) => KCard(
          child: pColumn([
        Wrap(spacing: 10, runSpacing: 8, children: [
          pText('RentAHuman.ai', strong: true),
          const KBadge('Real directory', tone: 'accent'),
          KBadge(widget.status['accountConfigured'] == true
              ? 'Account key installed'
              : 'Public search available')
        ]),
        pText(
            'Find people for prototype assembly, measurements and physical checks. Search does not hire or message anyone.',
            small: true,
            muted: true),
        TextField(
            controller: skill,
            maxLength: 100,
            decoration: const InputDecoration(labelText: 'Skill')),
        TextField(
            controller: city,
            maxLength: 100,
            decoration: const InputDecoration(labelText: 'City (optional)')),
        TextField(
            controller: country,
            maxLength: 100,
            decoration: const InputDecoration(labelText: 'Country (optional)')),
        TextField(
            controller: rate,
            keyboardType: TextInputType.number,
            decoration: const InputDecoration(
                labelText: 'Maximum hourly rate in USD (optional)')),
        if (error != null) pText(error, color: KColors.fail),
        KButton(
            label: 'Find people',
            loading: busy,
            onPressed: busy ? null : () => search()),
        if (results != null && asMaps(results!['humans']).isEmpty)
          pText('No people returned for this search.', muted: true),
        for (final human in asMaps(results?['humans']))
          pColumn([
            pText(human['name'], strong: true),
            pText(human['headline'], small: true),
            pText(
                [
                  human['city'],
                  human['country'],
                  if (human['hourlyRate'] != null)
                    '${human['hourlyRate']} ${human['currency']}/hour'
                ].where((v) => v != null && '$v'.isNotEmpty).join(' · '),
                small: true,
                muted: true)
          ]),
        if (results?['hasMore'] == true && results?['nextCursor'] is String)
          KButton(
              label: 'Next people',
              onPressed: busy
                  ? null
                  : () => search(cursor: '${results!['nextCursor']}')),
        if (widget.status['accountConfigured'] != true)
          pText(
              'Account previews and job status need an organization API key installed by your administrator. Keep keys out of chat and task descriptions.',
              small: true,
              muted: true),
        KButton(
            label: 'Open RentAHuman',
            icon: Icons.open_in_new,
            onPressed: () async {
              try {
                await widget.onOpen();
              } catch (_) {
                if (mounted) {
                  setState(() => error = 'Could not open RentAHuman.');
                }
              }
            }),
        pText(
            'Prepare a task in your project’s physical-check stage. Hiring, worker messages and payment stay on RentAHuman; Kleos physical acceptance remains simulated.',
            small: true,
            muted: true),
      ]));
}

typedef HumanTaskRequest = ({String projectId, int revision});
final humanTaskDraftsProvider = FutureProvider.autoDispose
    .family<JsonMap, HumanTaskRequest>((ref, request) {
  final repository = ref.watch(productionRepositoryProvider);
  return repository.api.humanTaskDrafts(request.projectId,
      organizationId: repository.organizationId);
}, retry: (_, __) => null);

class RentAHumanProjectTasks extends ConsumerWidget {
  const RentAHumanProjectTasks(
      {super.key,
      required this.projectId,
      required this.revision,
      required this.releaseId,
      required this.canWrite});
  final String projectId;
  final int revision;
  final String? releaseId;
  final bool canWrite;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final provider =
        humanTaskDraftsProvider((projectId: projectId, revision: revision));
    final status = ref.watch(rentAHumanStatusProvider).asData?.value;
    return ref.watch(provider).when(
          loading: () => const SizedBox.shrink(),
          error: (_, __) => const KMuted(
              'Human-task preparation unavailable. Simulated review remains available.'),
          data: (data) => RentAHumanTaskControls(
              key: ValueKey('$projectId-$revision'),
              drafts: asMaps(data['drafts']),
              accountConfigured: status?['accountConfigured'] == true,
              releaseId: releaseId,
              revision: revision,
              canWrite: canWrite,
              onSave: (body, key) async {
                final repository = ref.read(productionRepositoryProvider);
                await repository.api.createHumanTaskDraft(projectId, body,
                    key: key, organizationId: repository.organizationId);
                ref.invalidate(provider);
              },
              onAction: (job, kind, bountyId) async {
                final repository = ref.read(productionRepositoryProvider);
                await repository.api.humanTaskAction(
                    projectId,
                    '${job['id']}',
                    kind,
                    {
                      'draftSha256': job['draftSha256'],
                      if (bountyId != null) 'bountyId': bountyId
                    },
                    organizationId: repository.organizationId);
                ref.invalidate(provider);
              },
              onBounties: (cursor) async {
                final repository = ref.read(productionRepositoryProvider);
                return repository.api.rentAHumanBounties(
                    cursor: cursor, organizationId: repository.organizationId);
              },
              onCopy: (task) => Clipboard.setData(ClipboardData(
                  text: const JsonEncoder.withIndent('  ').convert(task))),
              onOpen: openRentAHuman),
        );
  }
}

class RentAHumanTaskControls extends StatefulWidget {
  const RentAHumanTaskControls(
      {super.key,
      required this.drafts,
      required this.accountConfigured,
      required this.releaseId,
      required this.revision,
      required this.canWrite,
      required this.onSave,
      required this.onAction,
      required this.onBounties,
      required this.onCopy,
      required this.onOpen});
  final List<JsonMap> drafts;
  final bool accountConfigured, canWrite;
  final String? releaseId;
  final int revision;
  final Future<void> Function(JsonMap, String) onSave;
  final Future<void> Function(JsonMap, String, String?) onAction;
  final Future<JsonMap> Function(String?) onBounties;
  final Future<void> Function(JsonMap) onCopy;
  final Future<void> Function() onOpen;

  @override
  State<RentAHumanTaskControls> createState() => _RentAHumanTaskControlsState();
}

class _RentAHumanTaskControlsState extends State<RentAHumanTaskControls> {
  final title = TextEditingController(text: 'Prototype assembly and fit check');
  final description = TextEditingController(
      text:
          'Assemble the supplied prototype using the approved assembly instructions. Record fit, access and any problems.');
  final criteria = TextEditingController(
      text:
          'Provide original photos of the assembled prototype and a written report of measured gaps, units and unresolved problems.');
  final price = TextEditingController();
  final hours = TextEditingController();
  final city = TextEditingController();
  final country = TextEditingController();
  List<JsonMap> bounties = [];
  String? bountyCursor;
  final selected = <String, String>{};
  String? message;
  bool busy = false, remote = false;

  @override
  void dispose() {
    for (final controller in [
      title,
      description,
      criteria,
      price,
      hours,
      city,
      country
    ]) {
      controller.dispose();
    }
    super.dispose();
  }

  Future<void> run(Future<void> Function() operation, String success) async {
    setState(() {
      busy = true;
      message = null;
    });
    try {
      await operation();
      if (mounted) setState(() => message = success);
    } catch (_) {
      if (mounted) {
        setState(() => message =
            'This action could not finish. Check the current release, account key and RentAHuman details.');
      }
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  Future<void> save() async {
    final amount = double.tryParse(price.text),
        duration = double.tryParse(hours.text);
    if (title.text.trim().length < 3 ||
        description.text.trim().length < 10 ||
        criteria.text.trim().length < 10 ||
        amount == null ||
        !amount.isFinite ||
        amount <= 0 ||
        amount > 10000 ||
        duration == null ||
        !duration.isFinite ||
        duration < 0.083 ||
        duration > 168 ||
        (!remote &&
            (city.text.trim().isEmpty ||
                !RegExp(r'^[A-Z]{2}$')
                    .hasMatch(country.text.trim().toUpperCase())))) {
      setState(() => message =
          'Complete the task, fixed budget, estimated hours and in-person city/country first.');
      return;
    }
    final body = <String, dynamic>{
      'releaseId': widget.releaseId,
      'expectedEngineeringRevision': widget.revision,
      'task': {
        'title': title.text.trim(),
        'description': description.text.trim(),
        'completionCriteria': criteria.text.trim(),
        'price': amount,
        'priceType': 'fixed',
        'estimatedHours': duration,
        'category': 'labor-gigs',
        'skillsNeeded': ['prototype assembly'],
        'evidenceTypes': ['text', 'photo'],
        'location': {
          'city': city.text.trim(),
          'country': country.text.trim().toUpperCase(),
          'isRemoteAllowed': remote
        }
      }
    };
    final key = 'human-${sha256.convert(utf8.encode(jsonEncode(body)))}';
    await run(() => widget.onSave(body, key.substring(0, 64)),
        'Draft saved locally. No job was posted and nobody was contacted.');
  }

  @override
  Widget build(BuildContext context) {
    final editable = widget.canWrite && widget.releaseId != null && !busy;
    return KCard(
        child: pColumn([
      Wrap(spacing: 10, runSpacing: 8, children: [
        pText('Prepare work for RentAHuman', strong: true),
        const KBadge('Local draft')
      ]),
      pText(
          'Review the exact task and budget here. A provider preview shares this text with RentAHuman, without posting or funding a job.',
          small: true,
          muted: true),
      if (widget.releaseId == null)
        pText(
            'Accept the current digital release before preparing physical work.',
            small: true),
      if (widget.canWrite) ...[
        TextField(
            controller: title,
            maxLength: 200,
            decoration: const InputDecoration(labelText: 'Task title')),
        TextField(
            controller: description,
            maxLength: 5000,
            minLines: 2,
            maxLines: 5,
            decoration: const InputDecoration(labelText: 'Instructions')),
        TextField(
            controller: criteria,
            maxLength: 2000,
            minLines: 2,
            maxLines: 4,
            decoration: const InputDecoration(
                labelText: 'Required evidence and completion criteria')),
        TextField(
            controller: price,
            keyboardType: TextInputType.number,
            decoration: const InputDecoration(
                labelText: 'Fixed worker budget in USD (fees extra)')),
        TextField(
            controller: hours,
            keyboardType: TextInputType.number,
            decoration:
                const InputDecoration(labelText: 'Estimated working hours')),
        SwitchListTile(
            contentPadding: EdgeInsets.zero,
            title: const Text('Remote review allowed'),
            value: remote,
            onChanged: busy ? null : (value) => setState(() => remote = value)),
        TextField(
            controller: city,
            maxLength: 100,
            decoration: const InputDecoration(labelText: 'Work city')),
        TextField(
            controller: country,
            maxLength: 2,
            decoration: const InputDecoration(
                labelText: 'Country code (for example IN)')),
        KButton(
            label: 'Save local task draft',
            loading: busy,
            onPressed: editable ? save : null),
      ],
      if (message != null) pText(message, small: true),
      if (!widget.accountConfigured)
        pText(
            'Install an organization RentAHuman API key for previews and job status. Drafting and manual handoff work without a key.',
            small: true,
            muted: true),
      if (widget.accountConfigured && widget.canWrite)
        KButton(
            label: bountyCursor == null
                ? 'Load my RentAHuman jobs'
                : 'Next RentAHuman jobs',
            onPressed: busy
                ? null
                : () => run(() async {
                      final response = await widget.onBounties(bountyCursor);
                      if (mounted) {
                        setState(() {
                          bounties = asMaps(response['bounties']);
                          selected.clear();
                          bountyCursor = response['hasMore'] == true
                              ? response['nextCursor'] as String?
                              : null;
                        });
                      }
                    }, 'Loaded your account’s jobs. Choose one to link to a saved draft.')),
      for (final job in widget.drafts)
        pColumn([
          pText(asMap(job['task'])['title'], strong: true),
          KBadge(job['status'] == 'linked'
              ? 'Linked provider observation'
              : job['status'] == 'previewed'
                  ? 'Previewed only'
                  : '${job['status']}'),
          pDisclosure(
              title: 'Review saved task',
              child: SelectableText(
                  const JsonEncoder.withIndent('  ').convert(job['task']))),
          KButton(
              label: 'Copy saved task details',
              onPressed: busy
                  ? null
                  : () => run(() => widget.onCopy(asMap(job['task'])),
                      'Task details copied.')),
          if (job['status'] != 'cancelled' &&
              widget.canWrite &&
              widget.accountConfigured &&
              job['bountyId'] == null)
            KButton(
                label: 'Preview on RentAHuman',
                onPressed: busy
                    ? null
                    : () => run(() => widget.onAction(job, 'preview', null),
                        'Preview received. No job was posted or funded.')),
          if (asMap(job['preview'])['fundingTotalUsd'] != null)
            pText(
                'Provider preview: USD ${asMap(job['preview'])['fundingTotalUsd']} including reported fees. This is not a charge.',
                small: true),
          if (job['bountyId'] == null &&
              job['status'] != 'cancelled' &&
              widget.canWrite &&
              bounties.isNotEmpty) ...[
            DropdownButtonFormField<String>(
                key: ValueKey('human-job-${job['id']}-${selected[job['id']]}'),
                initialValue: selected[job['id']],
                isExpanded: true,
                decoration:
                    const InputDecoration(labelText: 'Existing RentAHuman job'),
                items: [
                  for (final bounty in bounties)
                    DropdownMenuItem(
                        value: '${bounty['id']}',
                        child: Text('${bounty['title']}',
                            overflow: TextOverflow.ellipsis))
                ],
                onChanged: busy
                    ? null
                    : (value) => setState(() {
                          if (value != null) selected['${job['id']}'] = value;
                        })),
            KButton(
                label: 'Link selected job',
                onPressed: busy || selected[job['id']] == null
                    ? null
                    : () => run(
                        () => widget.onAction(job, 'link', selected[job['id']]),
                        'Job linked for observation only.')),
          ],
          if (job['bountyId'] != null) ...[
            pText(
                'RentAHuman reports: ${asMap(job['observation'])['status'] ?? 'unknown'}. Physical acceptance: not established.',
                small: true),
            pText(
                'Last checked: ${asMap(job['observation'])['observedAt'] ?? 'unknown'}',
                small: true,
                muted: true),
            if (widget.canWrite &&
                widget.accountConfigured &&
                job['status'] != 'cancelled')
              KButton(
                  label: 'Refresh provider status',
                  onPressed: busy
                      ? null
                      : () => run(() => widget.onAction(job, 'sync', null),
                          'Provider observation refreshed.')),
          ],
        ]),
      KButton(
          label: 'Continue on RentAHuman',
          icon: Icons.open_in_new,
          onPressed: busy
              ? null
              : () => run(
                  widget.onOpen, 'Review, post, hire and pay on RentAHuman.')),
      pText(
          'No source files, private addresses or credentials are attached automatically. Provider completion and submitted evidence do not approve a Kleos release or physical gate.',
          small: true,
          muted: true),
    ]));
  }
}
