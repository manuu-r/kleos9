import 'dart:js_interop';
import 'package:file_selector/file_selector.dart';
import 'package:flutter/widgets.dart';
import 'package:web/web.dart' as web;

VoidCallback registerImageDrop({
  required Rect? Function() bounds,
  required bool Function() enabled,
  required ValueChanged<bool> onHover,
  required Future<void> Function(List<XFile>) onDrop,
  required ValueChanged<Object> onError,
}) {
  var disposed = false;
  var reading = false;
  bool contains(web.DragEvent event) =>
      enabled() &&
      (bounds()?.contains(
              Offset(event.clientX.toDouble(), event.clientY.toDouble())) ??
          false);
  bool files(web.DragEvent event) =>
      event.dataTransfer?.types.toDart.any((type) => type.toDart == 'Files') ??
      false;
  final over = ((web.Event raw) {
    final event = raw as web.DragEvent;
    if (!files(event)) return;
    event.preventDefault();
    final inside = contains(event);
    if (inside) event.dataTransfer?.dropEffect = 'copy';
    onHover(inside);
  }).toJS;
  final leave = ((web.Event raw) {
    final event = raw as web.DragEvent;
    if (event.relatedTarget == null) onHover(false);
  }).toJS;
  Future<void> receive(web.DragEvent event) async {
    if (reading) return;
    reading = true;
    try {
      final source = event.dataTransfer?.files;
      if (source == null || source.length == 0) return;
      if (source.length > 4) {
        throw const FormatException('Choose up to four images.');
      }
      final selected = <web.File>[];
      for (var i = 0; i < source.length; i++) {
        final file = source.item(i)!;
        if (file.size > 6 * 1024 * 1024) {
          throw const FormatException('Each image must be 6 MB or smaller.');
        }
        if (!RegExp(r'\.(png|jpe?g|webp)$', caseSensitive: false)
            .hasMatch(file.name)) {
          throw const FormatException('Choose a PNG, JPEG or WebP image.');
        }
        selected.add(file);
      }
      final result = <XFile>[];
      for (final file in selected) {
        final buffer = await file.arrayBuffer().toDart;
        if (disposed || !enabled()) return;
        result.add(XFile.fromData(buffer.toDart.asUint8List(),
            name: file.name, mimeType: file.type));
      }
      if (!disposed && enabled()) await onDrop(result);
    } catch (error) {
      if (!disposed) onError(error);
    } finally {
      reading = false;
    }
  }

  final drop = ((web.Event raw) {
    final event = raw as web.DragEvent;
    if (!files(event)) return;
    event.preventDefault();
    onHover(false);
    if (contains(event)) receive(event);
  }).toJS;
  web.document.addEventListener('dragover', over);
  web.document.addEventListener('dragleave', leave);
  web.document.addEventListener('drop', drop);
  return () {
    disposed = true;
    web.document.removeEventListener('dragover', over);
    web.document.removeEventListener('dragleave', leave);
    web.document.removeEventListener('drop', drop);
  };
}
