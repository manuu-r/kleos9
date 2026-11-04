import 'dart:convert';
import 'dart:io';
import 'dart:ui' as ui;
import 'package:flutter/rendering.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:kleos_ui/data/models/json.dart';
import 'package:kleos_ui/ui/core/concept_preview.dart';

void main() {
  for (final width in [320.0, 1100.0]) {
    testWidgets(
        'complex fixture preserves parent selection and explodes at $width',
        (tester) async {
      tester.view.physicalSize = Size(width, 1800);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.reset);
      final scene = asMap(jsonDecode(
          File('../backend/tests/fixtures/concept-geometry-v2.json')
              .readAsStringSync()));
      String? selected;
      await tester.pumpWidget(MaterialApp(
          home: Scaffold(
              body: SingleChildScrollView(
                  child: ConceptPreview(
                      scene: scene, onPartSelected: (id) => selected = id)))));
      expect(find.byKey(const ValueKey('concept-canvas')), findsOneWidget);
      await tester
          .ensureVisible(find.byKey(const ValueKey('concept-part-pocket')));
      await tester.tap(find.byKey(const ValueKey('concept-part-pocket')));
      await tester.pump();
      expect(selected, 'pocket');
      await tester
          .ensureVisible(find.byKey(const ValueKey('concept-exploded')));
      await tester.tap(find.byKey(const ValueKey('concept-exploded')));
      await tester.pumpAndSettle();
      expect(find.byKey(const ValueKey('concept-canvas')), findsOneWidget);
      expect(tester.takeException(), isNull);
    });
  }
  testWidgets(
      'invalid v2 feature shows error without a substitute box or controls',
      (tester) async {
    await tester.pumpWidget(MaterialApp(
        home: Scaffold(
            body: ConceptPreview(scene: {
      'schemaVersion': 'concept-scene-v2',
      'parts': [
        {
          'id': 'bad',
          'shape': 'feature',
          'size': {'x': 1, 'y': 1, 'z': 1},
          'geometry': {'kind': 'unknown'}
        }
      ]
    }))));
    expect(find.textContaining('could not be displayed'), findsOneWidget);
    expect(find.byKey(const ValueKey('concept-canvas')), findsNothing);
    expect(find.byType(ChoiceChip), findsNothing);
    expect(tester.takeException(), isNull);
  });
  testWidgets('complex geometry visual QA capture', (tester) async {
    tester.view.physicalSize = const Size(1100, 1300);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    final scene = asMap(jsonDecode(
        File('../backend/tests/fixtures/concept-geometry-v2.json')
            .readAsStringSync()));
    final key = GlobalKey();
    await tester.pumpWidget(MaterialApp(
        home: Scaffold(
            body: RepaintBoundary(
                key: key,
                child: Padding(
                    padding: const EdgeInsets.all(24),
                    child: ConceptPreview(scene: scene, height: 600))))));
    await tester.pumpAndSettle();
    final boundary =
        key.currentContext!.findRenderObject()! as RenderRepaintBoundary;
    await tester.runAsync(() async {
      final image = await boundary.toImage();
      final bytes = await image.toByteData(format: ui.ImageByteFormat.png);
      await File('/tmp/kleos-complex-concept.png')
          .writeAsBytes(bytes!.buffer.asUint8List());
      image.dispose();
    });
    expect(tester.takeException(), isNull);
  });
}
