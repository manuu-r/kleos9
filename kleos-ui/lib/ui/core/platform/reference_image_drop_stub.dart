import 'package:file_selector/file_selector.dart';
import 'package:flutter/widgets.dart';

VoidCallback registerImageDrop({
  required Rect? Function() bounds,
  required bool Function() enabled,
  required ValueChanged<bool> onHover,
  required Future<void> Function(List<XFile>) onDrop,
  required ValueChanged<Object> onError,
}) =>
    () {};
