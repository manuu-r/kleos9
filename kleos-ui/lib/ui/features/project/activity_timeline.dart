import 'dart:async';

import 'package:flutter/material.dart';

import '../../../data/models/json.dart';
import '../../core/widgets.dart';
import 'common.dart';

/// What the current run is doing, step by step: planning, model decisions and tool calls with
/// their observed results. Built from the run's public progress steps; it never shows private
/// model reasoning, raw file contents or credentials (the backend does not send them).
class ActivityTimeline extends StatefulWidget {
  const ActivityTimeline(
      {super.key, required this.steps, required this.running, this.visible = 10});
  final List<JsonMap> steps;
  final bool running;
  final int visible;
  @override
  State<ActivityTimeline> createState() => _ActivityTimelineState();
}

/// A started step and, once it ends, its finished or failed step.
class _Entry {
  _Entry(this.started);
  JsonMap started;
  JsonMap? ended;
  JsonMap get latest => ended ?? started;
  String get phase => '${latest['phase']}';
  String get kind => '${started['kind']}';
}

List<_Entry> _entries(List<JsonMap> steps) {
  final byKey = <String, _Entry>{};
  for (final step in steps) {
    final id = '${step['id']}';
    final cut = id.lastIndexOf(':');
    final key = cut > 0 ? id.substring(0, cut) : id;
    final entry = byKey[key];
    if (entry == null) {
      // A finished step whose start was trimmed from the capped list still shows, with no duration.
      byKey[key] = _Entry(step)..ended = step['phase'] == 'started' ? null : step;
    } else if (step['phase'] == 'started') {
      entry
        ..started = step // a retried step restarts in place
        ..ended = null;
    } else {
      entry.ended = step;
    }
  }
  return byKey.values.toList();
}

class _ActivityTimelineState extends State<ActivityTimeline> {
  Timer? _tick;
  bool _all = false;

  @override
  void initState() {
    super.initState();
    _syncTicker();
  }

  @override
  void didUpdateWidget(ActivityTimeline oldWidget) {
    super.didUpdateWidget(oldWidget);
    _syncTicker();
  }

  /// Ticks once a second only while a step is in progress, so elapsed time stays honest.
  void _syncTicker() {
    final live = widget.running &&
        _entries(widget.steps).any((entry) => entry.ended == null);
    if (live && _tick == null) {
      _tick = Timer.periodic(const Duration(seconds: 1), (_) {
        if (mounted) setState(() {});
      });
    } else if (!live) {
      _tick?.cancel();
      _tick = null;
    }
  }

  @override
  void dispose() {
    _tick?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final entries = _entries(widget.steps);
    if (entries.isEmpty) return const SizedBox.shrink();
    final shown = _all || entries.length <= widget.visible
        ? entries
        : entries.sublist(entries.length - widget.visible);
    final hidden = entries.length - shown.length;
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.fromLTRB(16, 14, 16, 8),
      decoration: BoxDecoration(
          color: KColors.surface,
          borderRadius: BorderRadius.circular(KRadius.card),
          border: Border.all(color: KColors.border)),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(children: [
          Text(widget.running ? 'What Kleos is doing' : 'What Kleos did',
              style: Theme.of(context).textTheme.titleSmall),
          const SizedBox(width: 10),
          if (widget.running) const KBadge('Live', tone: 'accent', dot: true),
          const Spacer(),
          KMuted('${entries.length} step${entries.length == 1 ? '' : 's'}',
              size: 11),
        ]),
        const SizedBox(height: 6),
        if (hidden > 0)
          TextButton(
              onPressed: () => setState(() => _all = true),
              child: Text('Show $hidden earlier step${hidden == 1 ? '' : 's'}',
                  style: const TextStyle(fontSize: 12))),
        for (var i = 0; i < shown.length; i++)
          _StepRow(
              entry: shown[i],
              running: widget.running,
              last: i == shown.length - 1),
      ]),
    );
  }
}

class _StepRow extends StatelessWidget {
  const _StepRow(
      {required this.entry, required this.running, required this.last});
  final _Entry entry;
  final bool running;
  final bool last;

  bool get _inProgress => entry.ended == null && running;
  bool get _interrupted => entry.ended == null && !running;

  String get _title {
    final start = entry.started, end = entry.ended;
    switch (entry.kind) {
      case 'plan':
        if (end == null) return '${start['summary'] ?? 'Planning'}';
        if (end['question'] != null) return 'Needs your answer';
        if (end['done'] == true) return 'Objective complete';
        final count = asList(end['tasks']).length;
        return count == 0
            ? 'Plan updated'
            : 'Planned $count task${count == 1 ? '' : 's'}';
      case 'tool':
        return '${start['tool']}';
      default:
        final summary = '${end?['summary'] ?? ''}'.trim();
        if (summary.isNotEmpty && end?['phase'] != 'failed') return summary;
        return '${start['summary'] ?? 'Thinking'}';
    }
  }

  String? get _detail {
    final end = entry.ended;
    if (end == null) return null;
    if (end['phase'] == 'failed') return '${end['summary'] ?? end['error']}';
    if (entry.kind == 'plan') {
      final question = end['question'];
      if (question != null) return '$question';
      final summary = '${end['summary'] ?? ''}'.trim();
      return summary.isEmpty ? null : summary;
    }
    if (entry.kind == 'tool') {
      final summary = '${end['summary'] ?? ''}'.trim();
      return summary.isEmpty ? null : summary;
    }
    return null;
  }

  Duration? _elapsed() {
    if (entry.started['phase'] != 'started') return null;
    final from = DateTime.tryParse('${entry.started['at']}');
    if (from == null) return null;
    final to = entry.ended == null
        ? (running ? DateTime.now().toUtc() : null)
        : DateTime.tryParse('${entry.ended!['at']}');
    if (to == null) return null;
    final value = to.difference(from);
    return value.isNegative ? Duration.zero : value;
  }

  static String _format(Duration d) {
    if (d.inSeconds < 1) return '<1s';
    if (d.inMinutes < 1) return '${d.inSeconds}s';
    return '${d.inMinutes}m ${(d.inSeconds % 60).toString().padLeft(2, '0')}s';
  }

  Widget _status() {
    if (_inProgress) return const KSpinner(size: 14, color: KColors.accent);
    final (icon, color) = switch (entry.phase) {
      'failed' => (Icons.close, KColors.fail),
      _ when _interrupted => (Icons.remove, KColors.faint),
      _ => (Icons.check, KColors.pass),
    };
    return Icon(icon, size: 15, color: color);
  }

  @override
  Widget build(BuildContext context) {
    final start = entry.started, end = entry.ended;
    final args = asMap(start['arguments']);
    final tasks = asMaps(end?['tasks']);
    final checks = asMaps(end?['checks']);
    final elapsed = _elapsed();
    final detail = _detail;
    final lane = start['lane'];
    final title = Text(_title,
        maxLines: 2,
        overflow: TextOverflow.ellipsis,
        style: TextStyle(
            fontFamily: entry.kind == 'tool' ? 'GeistMono' : null,
            fontSize: 13,
            height: 1.4,
            color: _interrupted ? KColors.faint : KColors.text,
            fontWeight: entry.kind == 'tool' ? FontWeight.w500 : FontWeight.w400));
    return IntrinsicHeight(
        child: Row(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      SizedBox(
          width: 22,
          child: Column(children: [
            Padding(padding: const EdgeInsets.only(top: 9), child: _status()),
            if (!last)
              Expanded(
                  child: Container(
                      width: 1,
                      margin: const EdgeInsets.only(top: 4),
                      color: KColors.border)),
          ])),
      const SizedBox(width: 10),
      Expanded(
          child: Padding(
        padding: const EdgeInsets.symmetric(vertical: 7),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Expanded(child: title),
            if (lane != null) ...[
              const SizedBox(width: 8),
              KBadge('$lane'),
            ],
            if (elapsed != null) ...[
              const SizedBox(width: 8),
              Padding(
                  padding: const EdgeInsets.only(top: 2),
                  child: KMuted(_format(elapsed), size: 11)),
            ],
          ]),
          if (args.isNotEmpty) ...[
            const SizedBox(height: 6),
            Wrap(spacing: 6, runSpacing: 6, children: [
              for (final arg in args.entries)
                Container(
                    padding:
                        const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                    decoration: BoxDecoration(
                        color: KColors.surface3,
                        borderRadius: BorderRadius.circular(KRadius.control)),
                    child: Text('${arg.key}: ${arg.value}',
                        style: const TextStyle(
                            fontFamily: 'GeistMono',
                            fontSize: 11,
                            color: KColors.muted))),
            ]),
          ],
          if (entry.kind == 'model' && end?['next'] != null) ...[
            const SizedBox(height: 4),
            KMuted('Next: ${end!['next']}', size: 11),
          ],
          if (detail != null) ...[
            const SizedBox(height: 4),
            Text(detail,
                maxLines: 3,
                overflow: TextOverflow.ellipsis,
                style: TextStyle(
                    fontSize: 12,
                    height: 1.45,
                    color: entry.phase == 'failed'
                        ? KColors.fail
                        : KColors.muted)),
          ],
          if (tasks.isNotEmpty) ...[
            const SizedBox(height: 6),
            for (final task in tasks)
              Padding(
                  padding: const EdgeInsets.only(bottom: 3),
                  child: Text('${task['lane']} · ${task['objective']}',
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style:
                          const TextStyle(fontSize: 12, color: KColors.muted))),
          ],
          if (checks.isNotEmpty || end?['artifacts'] != null) ...[
            const SizedBox(height: 6),
            Wrap(spacing: 6, runSpacing: 6, children: [
              for (final check in checks)
                Tooltip(
                    message: '${check['name']}',
                    child: evidence(check['status'])),
              if (end?['artifacts'] != null)
                KBadge(
                    '${end!['artifacts']} file${end['artifacts'] == 1 ? '' : 's'}'),
            ]),
          ],
        ]),
      )),
    ]));
  }
}
