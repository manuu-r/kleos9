import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../data/models/json.dart';
import '../../state/kleos_store.dart';
import '../../state/production_store.dart';
import 'widgets.dart';

/// Common build actions on dashboard cards and inside the workspace.
class BuildActionsMenu extends ConsumerWidget {
  const BuildActionsMenu(
      {super.key,
      required this.project,
      required this.controller,
      this.onDeleted});
  final JsonMap project;
  final KleosController controller;
  final VoidCallback? onDeleted;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final real = project['production'] == true;
    final role = real ? ref.watch(accountProvider).asData?.value?.role : null;
    final canDelete = !real || const {'owner', 'admin'}.contains(role);
    if (!canDelete) return const SizedBox.shrink();
    return PopupMenuButton<String>(
      key: ValueKey('build-actions-${project['id']}'),
      tooltip: 'Build actions',
      icon: const Icon(Icons.more_horiz, size: 20),
      onSelected: (_) async {
        final deleted = await showDialog<bool>(
            context: context,
            barrierDismissible: false,
            builder: (_) =>
                _DeleteBuildDialog(project: project, controller: controller));
        // The menu can disappear when the accepted deletion updates the list.
        // Let the surviving caller decide whether navigation is still valid.
        if (deleted == true) onDeleted?.call();
      },
      itemBuilder: (_) => [
        const PopupMenuItem(
            value: 'delete',
            child: Row(children: [
              Icon(Icons.delete_outline, size: 18, color: KColors.fail),
              SizedBox(width: 10),
              Text('Delete build'),
            ])),
      ],
    );
  }
}

class _DeleteBuildDialog extends ConsumerStatefulWidget {
  const _DeleteBuildDialog({required this.project, required this.controller});
  final JsonMap project;
  final KleosController controller;
  @override
  ConsumerState<_DeleteBuildDialog> createState() => _DeleteBuildDialogState();
}

class _DeleteBuildDialogState extends ConsumerState<_DeleteBuildDialog> {
  bool deleting = false;
  String? error;
  Future<void> remove() async {
    if (deleting) return;
    setState(() {
      deleting = true;
      error = null;
    });
    try {
      final result = await widget.controller
          .execute('project.delete', {'projectId': widget.project['id']});
      if (!mounted) return;
      if (result.ok) {
        Navigator.of(context).pop(true);
      } else {
        setState(() => error = ref.read(kleosProvider).error ??
            'The build could not be deleted. Please try again.');
      }
    } catch (_) {
      if (mounted) {
        setState(() => error = 'Connection interrupted. Please try again.');
      }
    } finally {
      if (mounted) setState(() => deleting = false);
    }
  }

  @override
  Widget build(BuildContext context) => PopScope(
      canPop: !deleting,
      child: AlertDialog(
        title: const Text('Delete this build?'),
        content: SizedBox(
            width: 420,
            child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text('${widget.project['name'] ?? 'This build'}',
                      style: const TextStyle(fontWeight: FontWeight.w600)),
                  const SizedBox(height: 12),
                  Text(widget.project['production'] == true
                      ? 'This removes the build from your workspace immediately and permanently clears its project data and generated files in the background. This cannot be undone.'
                      : 'This permanently clears this simulated build and its generated simulation records. This cannot be undone.'),
                  if (error != null) ...[
                    const SizedBox(height: 16),
                    Text(error!, style: const TextStyle(color: KColors.fail))
                  ],
                  if (deleting) ...[
                    const SizedBox(height: 16),
                    const KLineLoader()
                  ],
                ])),
        actions: [
          TextButton(
              onPressed:
                  deleting ? null : () => Navigator.of(context).pop(false),
              child: const Text('Keep build')),
          FilledButton(
              onPressed: deleting ? null : remove,
              style: FilledButton.styleFrom(
                  backgroundColor: KColors.fail, foregroundColor: Colors.white),
              child: Text(deleting ? 'Deleting…' : 'Delete and clear build')),
        ],
      ));
}
