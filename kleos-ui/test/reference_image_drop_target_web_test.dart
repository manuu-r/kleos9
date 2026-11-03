@TestOn('browser')
library;

import 'dart:js_interop';
import 'dart:typed_data';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:web/web.dart' as web;
import 'package:kleos_ui/ui/core/reference_image_drop_target.dart';

void main() {
  testWidgets(
      'browser image drops are scoped, bounded, disabled and cleaned up',
      (tester) async {
    final received = <String>[];
    final errors = <Object>[];
    Future<void> mount(bool enabled) => tester.pumpWidget(MaterialApp(
        home: Align(
            alignment: Alignment.topLeft,
            child: ReferenceImageDropTarget(
                enabled: enabled,
                onDrop: (files) async {
                  received.addAll(files.map((file) => file.name));
                },
                onError: errors.add,
                child: const SizedBox(
                    width: 200, height: 100, child: Text('Brief'))))));
    web.DataTransfer transfer(int count, {int size = 1}) {
      final data = web.DataTransfer();
      for (var i = 0; i < count; i++) {
        data.items.add(web.File([Uint8List(size).toJS].toJS, 'photo$i.png',
            web.FilePropertyBag(type: 'image/png')));
      }
      return data;
    }

    web.DragEvent event(String type, web.DataTransfer data, {int x = 20}) =>
        web.DragEvent(
            type,
            web.DragEventInit(
                dataTransfer: data, clientX: x, clientY: 20, cancelable: true));
    await mount(true);
    final one = transfer(1);
    web.document.dispatchEvent(event('dragover', one));
    await tester.pump();
    expect(find.text('Drop sketches or photos here'), findsOneWidget);
    final outside = event('drop', one, x: 300);
    web.document.dispatchEvent(outside);
    expect(outside.defaultPrevented, isTrue);
    expect(received, isEmpty);
    web.document.dispatchEvent(event('drop', transfer(5)));
    await tester.pump();
    expect(errors.single, isA<FormatException>());
    web.document
        .dispatchEvent(event('drop', transfer(1, size: 6 * 1024 * 1024 + 1)));
    await tester.pump();
    expect(errors, hasLength(2));
    web.document.dispatchEvent(event('drop', one));
    await tester.runAsync(
        () => Future<void>.delayed(const Duration(milliseconds: 100)));
    await tester.pump();
    expect(received, ['photo0.png']);
    await mount(false);
    web.document.dispatchEvent(event('drop', one));
    await tester.pump();
    expect(received, hasLength(1));
    await tester.pumpWidget(const SizedBox());
    final removed = event('drop', one);
    web.document.dispatchEvent(removed);
    expect(removed.defaultPrevented, isFalse);
  });
}
