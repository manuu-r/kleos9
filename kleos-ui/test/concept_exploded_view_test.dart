import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';
import 'dart:ui' as ui;

import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:kleos_ui/data/models/json.dart';
import 'package:kleos_ui/ui/core/concept_preview.dart';

JsonMap overlappingParts() => {
      'schemaVersion': 'concept-scene-v2',
      'title': 'Separate component fixture',
      'parts': [
        for (var i = 0; i < 10; i++)
          {
            'id': 'part-$i',
            'label': 'Component $i',
            'role': i == 0 ? 'shell' : 'other',
            'shape': i == 0 ? 'feature' : 'rounded_box',
            'color': i.isEven ? '#68AACC' : '#CC8866',
            'size': {'x': 1.0, 'y': 1.4, 'z': .2},
            'position': {'x': 0.0, 'y': 0.0, 'z': i * .02},
            'explode': {'x': 0.0, 'y': 0.0, 'z': 0.0},
            if (i == 0)
              'geometry': {
                'kind': 'extrusion',
                'outer': [
                  [-.5, -.5],
                  [.5, -.5],
                  [.5, .5],
                  [-.5, .5]
                ],
                'holes': [
                  [
                    [-.35, -.35],
                    [.35, -.35],
                    [.35, .35],
                    [-.35, .35]
                  ]
                ],
                'floor': .15,
              },
          }
      ],
    };

Future<Uint8List> canvasBytes(WidgetTester tester) async {
  final finder = find.descendant(
      of: find.byKey(const ValueKey('concept-canvas')),
      matching: find.byType(RepaintBoundary));
  final boundary = tester.renderObject<RenderRepaintBoundary>(finder);
  return (await tester.runAsync(() async {
    final image = await boundary.toImage();
    final data = await image.toByteData(format: ui.ImageByteFormat.png);
    image.dispose();
    return data!.buffer.asUint8List();
  }))!;
}

Future<void> selectPose(WidgetTester tester, String key) async {
  final control = find.byKey(ValueKey(key));
  await tester.ensureVisible(control);
  await tester.tap(control);
  await tester.pumpAndSettle();
}

void main() {
  for (final width in [360.0, 1100.0]) {
    testWidgets(
        'zero-offset components visibly separate and retain selection at $width',
        (tester) async {
      tester.view.physicalSize = Size(width, 1600);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.reset);
      final scene = overlappingParts();
      final before = jsonEncode(scene);
      final selected = <String?>[];
      await tester.pumpWidget(MaterialApp(
          home: Scaffold(
              body: SingleChildScrollView(
                  child: ConceptPreview(
                      scene: scene,
                      height: 500,
                      onPartSelected: selected.add)))));
      expect(find.text('10 concept parts'), findsOneWidget);
      await selectPose(tester, 'concept-assembled');
      final assembled = await canvasBytes(tester);
      await selectPose(tester, 'concept-exploded');
      final exploded = await canvasBytes(tester);
      expect(exploded, isNot(orderedEquals(assembled)));
      for (var i = 0; i < 10; i++) {
        final chip = find.byKey(ValueKey('concept-part-part-$i'));
        await tester.ensureVisible(chip);
        await tester.tap(chip);
        await tester.pumpAndSettle();
        expect(selected.last, 'part-$i');
      }
      expect(jsonEncode(scene), before);
      expect(tester.takeException(), isNull);
    });
  }

  final savedScene = Platform.environment['KLEOS_CONCEPT_QA_SCENE'];
  testWidgets('saved scene renders assembled and exploded without regeneration',
      (tester) async {
    tester.view.physicalSize = const Size(1100, 1300);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    final scene = asMap(jsonDecode(File(savedScene!).readAsStringSync()));
    final before = jsonEncode(scene);
    String? selected;
    await tester.pumpWidget(MaterialApp(
        home: Scaffold(
            body: SingleChildScrollView(
                child: ConceptPreview(
                    scene: scene,
                    height: 650,
                    onPartSelected: (id) => selected = id)))));
    expect(find.byKey(const ValueKey('concept-canvas')), findsOneWidget);
    await selectPose(tester, 'concept-assembled');
    final assembled = await canvasBytes(tester);
    await selectPose(tester, 'concept-exploded');
    final exploded = await canvasBytes(tester);
    expect(exploded, isNot(orderedEquals(assembled)));
    await tester.runAsync(() async {
      await File('/tmp/kleos-current-concept-assembled.png')
          .writeAsBytes(assembled);
      await File('/tmp/kleos-current-concept-exploded.png')
          .writeAsBytes(exploded);
    });
    for (final raw in scene['parts'] as List) {
      final id = asMap(raw)['id'] as String;
      final chip = find.byKey(ValueKey('concept-part-$id'));
      await tester.ensureVisible(chip);
      await tester.tap(chip);
      await tester.pumpAndSettle();
      expect(selected, id);
    }
    expect(jsonEncode(scene), before);
    expect(tester.takeException(), isNull);
  }, skip: savedScene == null);
}
