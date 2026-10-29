import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../data/models/json.dart';
import '../../../state/kleos_store.dart';
import '../../core/widgets.dart';
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
          _pinned = (_pinned ?? widget.stage ?? latest).clamp(0, latest);
          final view = _pinned!.clamp(0, latest);
          if (widget.stage == null) {
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
                              ? constraints.maxWidth - 150
                              : constraints.maxWidth,
                          child: pColumn([
                            Text('${project['name'] ?? 'Your build'}',
                                style:
                                    Theme.of(context).textTheme.headlineMedium),
                            pText(project['brief'], muted: true),
                          ], gap: 8)),
                      KButton(
                          label: 'Activity',
                          compact: true,
                          icon: Icons.list_alt_outlined,
                          onPressed: () => _activity(project)),
                    ])),
            LayoutBuilder(builder: (context, constraints) {
              final compact = constraints.maxWidth < 840;
              if (compact) {
                return KCard(
                  padding: const EdgeInsets.all(16),
                  child: pColumn([
                    pText('Step ${view + 1} of ${stageNames.length}',
                        muted: true, small: true),
                    DropdownButtonFormField<int>(
                      key: ValueKey('stage-picker-$view'),
                      initialValue: view,
                      isExpanded: true,
                      decoration:
                          const InputDecoration(labelText: 'Project stage'),
                      items: [
                        for (var i = 0; i < stageNames.length; i++)
                          DropdownMenuItem(
                            value: i,
                            enabled: i <= latest,
                            child: Text('${i + 1}. ${stageNames[i]}',
                                overflow: TextOverflow.ellipsis,
                                style: TextStyle(
                                    color: i <= latest
                                        ? KColors.text
                                        : KColors.faint)),
                          ),
                      ],
                      onChanged: (i) {
                        if (i != null && i <= latest) _go(i);
                      },
                    ),
                    KProgress((view + 1) * 10.0),
                  ], gap: 12),
                );
              }
              return SingleChildScrollView(
                  scrollDirection: Axis.horizontal,
                  child: SizedBox(
                    width: compact ? 1030 : constraints.maxWidth,
                    child: Semantics(
                        label: 'Project stages',
                        child: Row(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              for (var i = 0; i < stageNames.length; i++)
                                Expanded(
                                    flex: i == view ? 3 : 1,
                                    child: Padding(
                                        padding:
                                            const EdgeInsets.only(right: 8),
                                        child: InkWell(
                                          borderRadius:
                                              BorderRadius.circular(5),
                                          onTap:
                                              i <= latest ? () => _go(i) : null,
                                          child: Semantics(
                                              button: true,
                                              selected: i == view,
                                              enabled: i <= latest,
                                              label: stageNames[i],
                                              child: Padding(
                                                padding:
                                                    const EdgeInsets.symmetric(
                                                        vertical: 8),
                                                child: Column(
                                                    crossAxisAlignment:
                                                        CrossAxisAlignment
                                                            .start,
                                                    children: [
                                                      AnimatedContainer(
                                                          duration:
                                                              const Duration(
                                                                  milliseconds:
                                                                      220),
                                                          height: 3,
                                                          decoration:
                                                              BoxDecoration(
                                                                  color: i ==
                                                                          view
                                                                      ? KColors
                                                                          .accent
                                                                      : i <
                                                                              latest
                                                                          ? KColors.text.withValues(
                                                                              alpha:
                                                                                  .5)
                                                                          : KColors
                                                                              .border,
                                                                  borderRadius:
                                                                      BorderRadius
                                                                          .circular(
                                                                              3))),
                                                      const SizedBox(
                                                          height: 10),
                                                      Row(
                                                          crossAxisAlignment:
                                                              CrossAxisAlignment
                                                                  .start,
                                                          children: [
                                                            if (i < done.length &&
                                                                done[i] == true)
                                                              const Padding(
                                                                  padding: EdgeInsets
                                                                      .only(
                                                                          right:
                                                                              4,
                                                                          top:
                                                                              2),
                                                                  child: Icon(
                                                                      Icons
                                                                          .check,
                                                                      size: 12,
                                                                      color: KColors
                                                                          .pass)),
                                                            Expanded(
                                                                child: Text(
                                                                    i == view
                                                                        ? stageNames[
                                                                            i]
                                                                        : '${i + 1}',
                                                                    maxLines: 2,
                                                                    overflow:
                                                                        TextOverflow
                                                                            .ellipsis,
                                                                    style: TextStyle(
                                                                        fontSize: 12,
                                                                        height: 1.3,
                                                                        color: i == view
                                                                            ? KColors.text
                                                                            : i <= latest
                                                                                ? KColors.muted
                                                                                : KColors.faint))),
                                                          ]),
                                                    ]),
                                              )),
                                        ))),
                            ])),
                  ));
            }),
            ProjectStage(
                key: ValueKey('${project['id']}:$view'),
                controller: widget.controller,
                project: project,
                stage: view,
                onNext: () => _go((view + 1).clamp(0, 9))),
          ], gap: 30);
        },
      );
}
