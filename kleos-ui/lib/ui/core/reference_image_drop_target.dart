import 'package:file_selector/file_selector.dart';
import 'package:flutter/material.dart';
import 'platform/reference_image_drop_stub.dart'
    if (dart.library.js_interop) 'platform/reference_image_drop_web.dart'
    as platform;

/// Accepts local image drops on web; native platforms retain the image picker.
class ReferenceImageDropTarget extends StatefulWidget {
  const ReferenceImageDropTarget(
      {super.key,
      required this.child,
      required this.enabled,
      required this.onDrop,
      required this.onError});
  final Widget child;
  final bool enabled;
  final Future<void> Function(List<XFile>) onDrop;
  final ValueChanged<Object> onError;

  @override
  State<ReferenceImageDropTarget> createState() =>
      _ReferenceImageDropTargetState();
}

class _ReferenceImageDropTargetState extends State<ReferenceImageDropTarget> {
  final target = GlobalKey();
  late final VoidCallback unregister;
  bool hovering = false;
  bool receiving = false;

  @override
  void initState() {
    super.initState();
    unregister = platform.registerImageDrop(
      bounds: () {
        final box = target.currentContext?.findRenderObject();
        return box is RenderBox && box.hasSize
            ? box.localToGlobal(Offset.zero) & box.size
            : null;
      },
      enabled: () => mounted && widget.enabled && !receiving,
      onHover: (value) {
        if (mounted && hovering != value) setState(() => hovering = value);
      },
      onDrop: (files) async {
        if (!mounted || !widget.enabled || receiving) return;
        setState(() => receiving = true);
        try {
          await widget.onDrop(files);
        } finally {
          if (mounted) setState(() => receiving = false);
        }
      },
      onError: (error) {
        if (mounted) widget.onError(error);
      },
    );
  }

  @override
  void dispose() {
    unregister();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => Stack(key: target, children: [
        widget.child,
        if (hovering && widget.enabled && !receiving)
          Positioned.fill(
              child: IgnorePointer(
                  child: DecoratedBox(
            decoration: BoxDecoration(
                color: Theme.of(context)
                    .colorScheme
                    .primary
                    .withValues(alpha: .08),
                border: Border.all(
                    color: Theme.of(context).colorScheme.primary, width: 2),
                borderRadius: BorderRadius.circular(12)),
            child: Center(
                child: Material(
                    borderRadius: BorderRadius.circular(8),
                    child: const Padding(
                        padding: EdgeInsets.all(12),
                        child: Text('Drop sketches or photos here')))),
          ))),
      ]);
}
