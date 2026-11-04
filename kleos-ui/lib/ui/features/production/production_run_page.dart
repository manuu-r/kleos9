import 'dart:async';
import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import '../../../data/models/json.dart';
import '../../../data/models/production.dart';
import '../../../state/production_store.dart';
import '../../core/widgets.dart';
import 'production_widgets.dart';

class ProductionRunPage extends ConsumerStatefulWidget {
  const ProductionRunPage({super.key, required this.runId});
  final String runId;
  @override
  ConsumerState<ProductionRunPage> createState() => _ProductionRunPageState();
}

class _ProductionRunPageState extends ConsumerState<ProductionRunPage> {
  Timer? timer;
  @override
  void initState() {
    super.initState();
    timer = Timer.periodic(const Duration(seconds: 3), (_) {
      final run = ref.read(runInspectorProvider(widget.runId)).asData?.value;
      if (run != null &&
          const {'running', 'queued', 'waiting'}.contains(run.status)) {
        ref.invalidate(runInspectorProvider(widget.runId));
      }
    });
  }

  @override
  void dispose() {
    timer?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => ProductionPage(children: [
        KSectionHeader(
            title: 'Run inspector',
            description: 'Trace the work from inputs to evidence.',
            trailing: IconButton(
                tooltip: 'Refresh run',
                onPressed: () =>
                    ref.invalidate(runInspectorProvider(widget.runId)),
                icon: const Icon(Icons.refresh))),
        ref.watch(runInspectorProvider(widget.runId)).when(
              loading: () => const ProductionLoading(),
              error: (e, _) => ProductionRetry(e.toString(),
                  retry: () =>
                      ref.invalidate(runInspectorProvider(widget.runId))),
              data: (run) => kColumn([
                Wrap(spacing: 10, runSpacing: 8, children: [
                  KBadge(run.status),
                  Text(run.kind.replaceFirst('project.', '')),
                  KButton(
                      label: 'Project',
                      compact: true,
                      onPressed: () =>
                          context.go('/projects/${run.data['projectId']}')),
                ]),
                if (run.blocker != null)
                  ProductionNotice(run.blocker!, error: true),
                SelectableText('Trace: ${run.traceId}',
                    style: const TextStyle(
                        fontFamily: 'GeistMono',
                        fontSize: 12,
                        color: KColors.muted)),
                for (final task in run.tasks)
                  KCard(
                      title: engineeringLanes[task['lane']] ??
                          task['lane'].toString(),
                      trailing: KBadge(task['status'].toString()),
                      child: kColumn([
                        if (asList(task['dependencies']).isNotEmpty)
                          ProductionJsonDetails(
                              'Dependencies', task['dependencies']),
                        for (final attempt in asMaps(task['attempts']))
                          kColumn([
                            Text(
                                'Attempt ${attempt['number']} · ${attempt['status']}'),
                            KMuted(
                                '${providerLabel(attempt['provider'].toString())} · ${attempt['model']}'),
                            if (attempt['error'] != null)
                              ProductionNotice(attempt['error'].toString(),
                                  error: true),
                            if (asMap(attempt['output'])['summary'] != null)
                              SelectionArea(
                                  child: Text(
                                      asMap(attempt['output'])['summary']
                                          .toString())),
                            ProductionJsonDetails(
                                'Context and evidence references',
                                attempt['contextManifest']),
                            ProductionJsonDetails(
                                'Output and changes', attempt['output']),
                            for (final call in asMaps(attempt['calls']))
                              _CallRecord(call: call),
                          ]),
                        for (final job in asMaps(task['nativeJobs']))
                          _NativeJobRecord(
                              job: job,
                              projectId: run.data['projectId'] as String),
                      ], gap: 24)),
              ], gap: 24),
            ),
      ]);
}

class _NativeJobRecord extends ConsumerStatefulWidget {
  const _NativeJobRecord({required this.job, required this.projectId});
  final JsonMap job;
  final String projectId;
  @override
  ConsumerState<_NativeJobRecord> createState() => _NativeJobRecordState();
}

class _NativeJobRecordState extends ConsumerState<_NativeJobRecord> {
  Object? logs;
  bool busy = false;
  bool rebuilding = false;
  String? rebuildError;

  Future<void> rebuildSources() async {
    if (rebuilding) return;
    setState(() {
      rebuilding = true;
      rebuildError = null;
    });
    // Capture the organization-scoped repository before asynchronous work.
    final repository = ref.read(productionRepositoryProvider);
    try {
      final workspace = await repository.workspace(widget.projectId);
      if (!mounted) return;
      final accepted = await repository.command(
          'project.retryNative',
          {
            'projectId': widget.projectId,
            'jobId': widget.job['id'],
          },
          revision: workspace.project.revision);
      if (!mounted) return;
      ref.invalidate(productionProjectProvider(widget.projectId));
      ref.invalidate(productionProjectsProvider);
      context.go(accepted.runId == null
          ? '/projects/${widget.projectId}'
          : '/runs/${accepted.runId}');
    } catch (error) {
      if (mounted) {
        setState(() => rebuildError = error.toString());
        // Uncertain requests remain in the existing durable command journal.
        ref.invalidate(productionProjectsProvider);
      }
    } finally {
      if (mounted) setState(() => rebuilding = false);
    }
  }

  @override
  Widget build(BuildContext context) => kColumn([
        Wrap(spacing: 10, runSpacing: 8, children: [
          Text('Engineering job · ${widget.job['purpose']}'),
          KBadge(widget.job['status'].toString()),
        ]),
        KMuted('Tool version: ${widget.job['toolVersion']}'),
        if (asList(widget.job['limitations']).isNotEmpty)
          ProductionNotice(asList(widget.job['limitations']).join('\n')),
        ProductionJsonDetails('Job inputs', widget.job['inputManifest']),
        if (widget.job['hasLogs'] == true &&
            const {'owner', 'admin'}
                .contains(ref.watch(accountProvider).asData?.value?.role))
          KButton(
              key: ValueKey('native-logs-${widget.job['id']}'),
              label: busy ? 'Loading logs…' : 'View redacted engineering logs',
              onPressed: busy
                  ? null
                  : () async {
                      setState(() => busy = true);
                      try {
                        final result = await ref
                            .read(productionRepositoryProvider)
                            .nativeJobLogs('${widget.job['id']}');
                        if (mounted) setState(() => logs = result);
                      } catch (e) {
                        if (mounted) setState(() => logs = e.toString());
                      } finally {
                        if (mounted) setState(() => busy = false);
                      }
                    }),
        if (logs != null) ProductionJsonDetails('Engineering logs', logs),
        if (widget.job['purpose'] == 'build' &&
            const {'succeeded', 'failed', 'unsupported'}
                .contains(widget.job['status']) &&
            (ref.watch(accountProvider).asData?.value?.canWrite ?? false)) ...[
          const KMuted(
              'Rebuilds the saved sources using compute allowance only. No new AI generation.'),
          KButton(
              key: ValueKey('rebuild-native-${widget.job['id']}'),
              label: rebuilding ? 'Starting rebuild…' : 'Rebuild saved sources',
              onPressed: rebuilding ? null : rebuildSources),
        ],
        if (rebuildError != null) ProductionNotice(rebuildError!, error: true),
      ]);
}

class _CallRecord extends ConsumerStatefulWidget {
  const _CallRecord({required this.call});
  final JsonMap call;
  @override
  ConsumerState<_CallRecord> createState() => _CallRecordState();
}

class _CallRecordState extends ConsumerState<_CallRecord> {
  String? content;
  bool busy = false;
  @override
  Widget build(BuildContext context) => kColumn([
        Wrap(spacing: 12, runSpacing: 8, children: [
          Text(widget.call['operation'].toString()),
          KBadge(widget.call['status'].toString()),
          Text(
              'Allowance debit: ${_allowanceDebit(asMap(widget.call['usage'])['allowanceDebitUsd'])}'),
          Text(
              'Provider estimate: ${_providerEstimate(asMap(widget.call['usage'])['estimatedUsd'])}'),
        ]),
        ProductionJsonDetails('Usage', widget.call['usage']),
        if (widget.call['hasTranscript'] == true &&
            const {'owner', 'admin'}
                .contains(ref.watch(accountProvider).asData?.value?.role))
          KButton(
              key: ValueKey('transcript-${widget.call['id']}'),
              label: busy ? 'Loading…' : 'View redacted AI record',
              onPressed: busy
                  ? null
                  : () async {
                      setState(() => busy = true);
                      try {
                        final value = await ref
                            .read(productionRepositoryProvider)
                            .invocationTranscript('${widget.call['id']}');
                        if (mounted) {
                          setState(() => content =
                              const JsonEncoder.withIndent('  ')
                                  .convert(value));
                        }
                      } catch (e) {
                        if (mounted) setState(() => content = e.toString());
                      } finally {
                        if (mounted) setState(() => busy = false);
                      }
                    }),
        if (content != null) ProductionJsonDetails('AI record', content),
      ]);
}

String _providerEstimate(Object? value) {
  if (value is! num || !value.isFinite || value < 0) return 'Unavailable';
  if (value > 0 && value < .0001) return '<\$0.0001';
  return '\$${value.toStringAsFixed(value < .01 ? 4 : 2)}';
}

String _allowanceDebit(Object? value) =>
    value is num && value.isFinite && value >= 0
        ? productionMoney(value)
        : 'Unavailable';
