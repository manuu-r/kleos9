import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../data/models/json.dart';
import '../../../state/kleos_store.dart';
import '../../core/widgets.dart';
import '../../core/build_actions.dart';
import 'common.dart';
import 'stages.dart';

class ProjectWorkspacePage extends StatefulWidget {
  const ProjectWorkspacePage(
      {super.key,
      required this.controller,
      required this.projectId,
      this.stage});
  final KleosController controller;
  final String projectId;
  final int? stage;

  @override
  State<ProjectWorkspacePage> createState() => _ProjectWorkspacePageState();
}

class _ProjectWorkspacePageState extends State<ProjectWorkspacePage> {
  int? _pinned;

  @override
  void didUpdateWidget(covariant ProjectWorkspacePage oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.projectId != widget.projectId ||
        oldWidget.stage != widget.stage) {
      _pinned = widget.stage;
    }
  }

  void _afterDeletion() {
    if (mounted) context.go('/');
  }

  void _go(int index) {
    setState(() => _pinned = index);
    context.go('/projects/${Uri.encodeComponent(widget.projectId)}?s=$index');
  }

  void _activity(JsonMap project) {
    showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      backgroundColor: KColors.surface,
      showDragHandle: true,
      constraints: const BoxConstraints(maxWidth: 660),
      builder: (context) => SafeArea(
          child: SizedBox(
        height: MediaQuery.sizeOf(context).height * .78,
        child: Padding(
            padding: const EdgeInsets.fromLTRB(24, 4, 24, 24),
            child: Consumer(
              builder: (context, ref, _) {
                final latest = ref
                        .watch(projectsProvider)
                        .where((p) => p['id'] == widget.projectId)
                        .firstOrNull ??
                    project;
                final activity = asMaps(latest['activity']);
                return Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(children: [
                        Expanded(
                            child: Text('Activity',
                                style: Theme.of(context).textTheme.titleLarge)),
                        IconButton(
                            onPressed: () => Navigator.pop(context),
                            tooltip: 'Close',
                            icon: const Icon(Icons.close, size: 18))
                      ]),
                      const SizedBox(height: 14),
                      Expanded(
                          child: ListView.separated(
                              itemCount: activity.length,
                              separatorBuilder: (_, __) =>
                                  const Divider(height: 24),
                              itemBuilder: (_, i) => Row(
                                      crossAxisAlignment:
                                          CrossAxisAlignment.start,
                                      children: [
                                        SizedBox(
                                            width: 70,
                                            child: pText(activity[i]['t'],
                                                muted: true,
                                                small: true,
                                                mono: true)),
                                        Expanded(
                                            child: pText(activity[i]['text'],
                                                small: true)),
                                      ]))),
                    ]);
              },
            )),
      )),
    );
  }

  @override
  Widget build(BuildContext context) => Consumer(
        builder: (context, ref, _) {
          if (widget.controller.loading && widget.controller.state.isEmpty) {
            return const KPageSkeleton();
          }
          final matches = ref
              .watch(projectsProvider)
              .where((p) => p['id'] == widget.projectId);
          if (matches.isEmpty) {
            return KCard(
                child: pColumn([
              pText('Project not found.'),
              KButton(
                  label: 'Start a new build',
                  primary: true,
                  onPressed: () => context.go('/new'))
            ]));
          }
          final project = matches.first;
          final latest = number(project['stage']).toInt().clamp(0, 9);
          final followPlanning = widget.stage == null &&
              asMap(asMap(project['state'])['planning'])['autoResume'] == true;
          _pinned =
              (followPlanning ? latest : (_pinned ?? widget.stage ?? latest))
                  .clamp(0, latest);
          final view = _pinned!.clamp(0, latest);
          if (widget.stage == null && !followPlanning) {
            WidgetsBinding.instance.addPostFrameCallback((_) {
              if (!mounted) return;
              final uri = GoRouterState.of(context).uri;
              if (uri.path == '/projects/${widget.projectId}' &&
                  !uri.queryParameters.containsKey('s')) {
                context.replace(Uri(path: uri.path, queryParameters: {
                  ...uri.queryParameters,
                  's': '$view',
                }).toString());
              }
            });
          }
          final done = asList(project['done']);
          return pColumn([
            LayoutBuilder(
                builder: (context, constraints) => pActions([
                      SizedBox(
                          width: constraints.maxWidth > 680
                              ? constraints.maxWidth - 204
                              : constraints.maxWidth,
                          child: pColumn([
                            Text('${project['name'] ?? 'Your build'}',
                                maxLines: 2,
                                overflow: TextOverflow.ellipsis,
                                style: KType.h1.copyWith(
                                    fontSize: KSpace.phone(context) ? 26 : 30)),
                            ConstrainedBox(
                                constraints:
                                    const BoxConstraints(maxWidth: 720),
                                child: Text('${project['brief'] ?? ''}',
                                    maxLines: 2,
                                    overflow: TextOverflow.ellipsis,
                                    style: const TextStyle(
                                        color: KColors.muted,
                                        fontSize: 14,
                                        height: 1.55))),
                          ], gap: 10)),
                      KButton(
                          label: 'Activity',
                          compact: true,
                          icon: Icons.list_alt_outlined,
                          onPressed: () => _activity(project)),
                      BuildActionsMenu(
                          project: project,
                          controller: widget.controller,
                          onDeleted: _afterDeletion),
                    ])),
            LayoutBuilder(builder: (context, constraints) {
              final compact = constraints.maxWidth < 840;
              if (compact) {
                return _StageTabs(
                    view: view, latest: latest, done: done, onSelect: _go);
              }
              return _StageRail(
                  view: view, latest: latest, done: done, onSelect: _go);
            }),
            KReveal(
                key: ValueKey('reveal-${project['id']}:$view'),
                child: ProjectStage(
                    key: ValueKey('${project['id']}:$view'),
                    controller: widget.controller,
                    project: project,
                    stage: view,
                    onNext: () => _go((view + 1).clamp(0, 9)))),
          ], gap: KSpace.phone(context) ? 20 : 30);
        },
      );
}

/// Ten stages as one continuous rail. The current stage names itself, finished
/// stages are quiet and tappable, and stages not yet reached stay dark.
class _StageRail extends StatelessWidget {
  const _StageRail(
      {required this.view,
      required this.latest,
      required this.done,
      required this.onSelect});
  final int view, latest;
  final List<Object?> done;
  final ValueChanged<int> onSelect;

  @override
  Widget build(BuildContext context) => Semantics(
      label: 'Project stages',
      child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        for (var i = 0; i < stageNames.length; i++)
          Expanded(
              flex: i == view ? 4 : 1,
              child: Padding(
                  padding: EdgeInsets.only(
                      right: i == stageNames.length - 1 ? 0 : 6),
                  child: _RailStop(
                      index: i,
                      current: i == view,
                      reached: i <= latest,
                      finished: i < done.length && done[i] == true,
                      onTap: i <= latest ? () => onSelect(i) : null))),
      ]));
}

class _RailStop extends StatelessWidget {
  const _RailStop(
      {required this.index,
      required this.current,
      required this.reached,
      required this.finished,
      required this.onTap});
  final int index;
  final bool current, reached, finished;
  final VoidCallback? onTap;
  @override
  Widget build(BuildContext context) => KTap(
      flat: true,
      radius: 6,
      onTap: onTap,
      builder: (context, hovered) => Semantics(
          button: true,
          selected: current,
          enabled: reached,
          label: stageNames[index],
          child: Tooltip(
              message: stageNames[index],
              child: Padding(
                  padding: const EdgeInsets.symmetric(vertical: 8),
                  child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        AnimatedContainer(
                            duration: KMotion.base,
                            curve: KMotion.out,
                            height: 3,
                            decoration: BoxDecoration(
                              borderRadius: BorderRadius.circular(3),
                              color: current
                                  ? KColors.accent
                                  : finished || reached
                                      ? KColors.text
                                          .withValues(alpha: hovered ? .6 : .38)
                                      : KColors.border,
                            )),
                        const SizedBox(height: 11),
                        Row(children: [
                          if (finished && !current)
                            const Padding(
                                padding: EdgeInsets.only(right: 4),
                                child: Icon(Icons.check,
                                    size: 12, color: KColors.pass)),
                          Expanded(
                              child: Text(
                                  current ? stageNames[index] : '${index + 1}',
                                  maxLines: 1,
                                  overflow: TextOverflow.ellipsis,
                                  style: current
                                      ? const TextStyle(
                                          fontSize: 13,
                                          fontWeight: FontWeight.w500,
                                          color: KColors.text)
                                      : KType.monoSmall.copyWith(
                                          color: reached
                                              ? KColors.muted
                                              : KColors.faint))),
                        ]),
                      ])))));
}

/// Phone and tablet stage navigation: every stage is a tab in one row that
/// scrolls sideways inside its own frame, with the same gliding indicator as
/// every other tab row. Finished stages carry a check, stages not reached yet
/// are dim and inert, and the current tab stays in view.
class _StageTabs extends StatelessWidget {
  const _StageTabs(
      {required this.view,
      required this.latest,
      required this.done,
      required this.onSelect});
  final int view, latest;
  final List<Object?> done;
  final ValueChanged<int> onSelect;

  @override
  Widget build(BuildContext context) => Semantics(
      label: 'Project stages',
      child: KTabStrip(selected: view, gap: 24, textSize: 14, tabs: [
        for (var i = 0; i < stageNames.length; i++)
          KTab(
              label: stageNames[i],
              onTap: i <= latest ? () => onSelect(i) : null,
              leading: (ink) {
                final finished = i < done.length && done[i] == true;
                return Padding(
                    padding: const EdgeInsets.only(right: 7),
                    child: finished && i != view
                        ? const Icon(Icons.check, size: 14, color: KColors.pass)
                        : Text('${i + 1}',
                            style: KType.monoSmall
                                .copyWith(color: ink, fontSize: 12.5)));
              }),
      ]));
}
