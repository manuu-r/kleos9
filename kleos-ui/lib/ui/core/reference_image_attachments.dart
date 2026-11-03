import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../data/services/reference_images.dart';
import 'reference_image_drop_target.dart';

class ReferenceImageAttachments extends ConsumerStatefulWidget {
  const ReferenceImageAttachments(
      {super.key,
      required this.images,
      required this.onChanged,
      this.enabled = true,
      this.input,
      this.supported = true});
  final List<ReferenceImageDraft> images;
  final ValueChanged<List<ReferenceImageDraft>> onChanged;
  final bool enabled;
  final Widget? input;
  final bool supported;
  @override
  ConsumerState<ReferenceImageAttachments> createState() =>
      _ReferenceImageAttachmentsState();
}

class _ReferenceImageAttachmentsState
    extends ConsumerState<ReferenceImageAttachments> {
  String? error;
  bool picking = false;
  void addImages(List<ReferenceImageDraft> selected) {
    final images = {
      for (final image in [...widget.images, ...selected]) image.hash: image
    }.values.toList();
    if (images.length > 4) {
      throw const FormatException(
          'Attach up to four images. Remove one to choose another.');
    }
    widget.onChanged(images);
  }

  void showError(Object e) {
    if (mounted) {
      setState(() => error = e is FormatException
          ? e.message
          : 'Could not open the images. Try again.');
    }
  }

  Future<void> pick() async {
    setState(() {
      picking = true;
      error = null;
    });
    try {
      final selected = await ref.read(referenceImagePickerProvider)();
      if (!mounted) return;
      addImages(selected);
    } catch (e) {
      showError(e);
    } finally {
      if (mounted) setState(() => picking = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    if (!widget.supported) return widget.input ?? const SizedBox.shrink();
    return ReferenceImageDropTarget(
      enabled: widget.enabled && !picking,
      onError: showError,
      onDrop: (files) async {
        setState(() {
          picking = true;
          error = null;
        });
        try {
          final selected = await readReferenceImages(files);
          if (mounted && widget.enabled && widget.supported) {
            addImages(selected);
          }
        } finally {
          if (mounted) setState(() => picking = false);
        }
      },
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        if (widget.input != null) widget.input!,
        TextButton.icon(
            key: const ValueKey('attach-reference-images'),
            onPressed: widget.enabled && !picking && widget.images.length < 4
                ? pick
                : null,
            icon: const Icon(Icons.add_photo_alternate_outlined),
            label: Text(picking ? 'Opening images…' : 'Add sketch or photo')),
        if (widget.images.isNotEmpty)
          Wrap(spacing: 10, runSpacing: 10, children: [
            for (final image in widget.images)
              SizedBox(
                  width: 120,
                  child: Column(children: [
                    ClipRRect(
                        borderRadius: BorderRadius.circular(6),
                        child: Image.memory(image.bytes,
                            cacheWidth: 240,
                            width: 120,
                            height: 88,
                            fit: BoxFit.cover,
                            semanticLabel: image.name,
                            errorBuilder: (_, __, ___) => const SizedBox(
                                height: 88,
                                child: Icon(Icons.image_outlined)))),
                    Row(children: [
                      Expanded(
                          child: Text(image.name,
                              maxLines: 1, overflow: TextOverflow.ellipsis)),
                      IconButton(
                          tooltip: 'Remove ${image.name}',
                          onPressed: widget.enabled && !picking
                              ? () => widget.onChanged(widget.images
                                  .where((item) => item != image)
                                  .toList())
                              : null,
                          icon: const Icon(Icons.close, size: 16))
                    ]),
                  ])),
          ]),
        if (error != null)
          Semantics(
              liveRegion: true,
              child: Text(error!,
                  style:
                      TextStyle(color: Theme.of(context).colorScheme.error))),
        const Text('Up to 4 PNG, JPEG or WebP images · 6 MB each',
            style: TextStyle(fontSize: 11)),
      ]),
    );
  }
}
