import 'dart:io';
import 'dart:ui' as ui;

import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:kleos_ui/data/models/concept_scene.dart';
import 'package:kleos_ui/data/models/json.dart';
import 'package:kleos_ui/ui/core/concept_geometry.dart';
import 'package:kleos_ui/ui/core/concept_preview.dart';
import 'package:kleos_ui/ui/core/theme.dart';

JsonMap previewScene(
        {String title = 'Desk air-quality monitor',
        bool battery = true,
        bool wearable = false}) =>
    {
      'schemaVersion': 'concept-scene-v1',
      'title': title,
      'summary': wearable
          ? 'A compact wrist-worn concept.'
          : 'A small desk device with a readable screen and room for the sensing module.',
      'deviceKind': wearable ? 'wearable' : 'desktop',
      'units': 'relative',
      'parts': [
        {
          'id': 'case',
          'label': 'Enclosure',
          'role': 'shell',
          'shape': 'rounded_box',
          'size': {
            'x': wearable ? 2.7 : 4.5,
            'y': wearable ? 3.0 : 3.1,
            'z': 1.2
          },
          'position': {'x': 0, 'y': 0, 'z': 0},
          'color': '#8c909d',
          'explode': {'x': 0, 'y': 0, 'z': 0},
          'detail': 'Keeps the components together.'
        },
        {
          'id': 'board',
          'label': 'Main board',
          'role': 'board',
          'shape': 'rounded_box',
          'size': {'x': 2.5, 'y': 1.8, 'z': .14},
          'position': {'x': 0, 'y': 0, 'z': -.15},
          'color': '#467e70',
          'explode': {'x': 0, 'y': 0, 'z': 1.7},
          'detail': 'Runs the display and sensors.'
        },
        if (battery)
          {
            'id': 'battery',
            'label': 'Battery',
            'role': 'battery',
            'shape': 'rounded_box',
            'size': {'x': 1.6, 'y': 1.25, 'z': .35},
            'position': {'x': -.65, 'y': -.2, 'z': -.2},
            'color': '#a5a9b0',
            'explode': {'x': -1.5, 'y': 0, 'z': .8},
            'detail': 'Optional rechargeable power for moving between rooms.'
          },
        {
          'id': 'sensor',
          'label': 'Air sensor',
          'role': 'sensor',
          'shape': 'cylinder',
          'size': {'x': .9, 'y': .9, 'z': .6},
          'position': {'x': 1, 'y': .15, 'z': .05},
          'color': '#bdb7a8',
          'explode': {'x': 1.4, 'y': 0, 'z': 1.2},
          'detail': 'Measures the selected air-quality signals.'
        },
        {
          'id': 'display',
          'label': 'Display',
          'role': 'display',
          'shape': 'rounded_box',
          'size': {'x': 2.65, 'y': 1.7, 'z': .16},
          'position': {'x': -.25, 'y': .25, 'z': .5},
          'color': '#545963',
          'explode': {'x': 0, 'y': 0, 'z': 3.2},
          'detail': 'Shows the current readings at a glance.'
        },
        {
          'id': 'cover',
          'label': 'Top cover',
          'role': 'cover',
          'shape': 'rounded_box',
          'size': {
            'x': wearable ? 2.8 : 4.6,
            'y': wearable ? 3.1 : 3.2,
            'z': .15
          },
          'position': {'x': 0, 'y': 0, 'z': .68},
          'color': '#777c88',
          'explode': {'x': 0, 'y': 0, 'z': 4.4},
          'detail':
              'The outside finish and display opening are still being shaped.'
        },
        if (wearable)
          {
            'id': 'strap',
            'label': 'Wrist strap',
            'role': 'strap',
            'shape': 'rounded_box',
            'size': {'x': 1.5, 'y': 6.8, 'z': .15},
            'position': {'x': 0, 'y': 0, 'z': -.72},
            'color': '#454957',
            'explode': {'x': 0, 'y': 0, 'z': -.65},
            'detail': 'A replaceable strap.'
          },
      ],
    };

Widget app(Widget child, {bool still = false}) => MaterialApp(
      theme: kTheme(),
      builder: (context, body) => MediaQuery(
          data: MediaQuery.of(context).copyWith(disableAnimations: still),
          child: body!),
      home: Scaffold(
          body: SingleChildScrollView(
              child: Padding(padding: const EdgeInsets.all(16), child: child))),
    );

void main() {
  test('relative scene parsing bounds malformed inputs and geometry count', () {
    final part = asMap(asList(previewScene()['parts']).first);
    final scene = ConceptScene.fromJson({
      'parts': [
        {
          ...part,
          'size': {'x': double.infinity, 'y': -500, 'z': double.nan},
          'position': {'x': double.nan},
          'color': 'javascript:alert(1)'
        },
        ...List.generate(40, (i) => {...part, 'id': 'part-$i'}),
      ],
    });
    expect(scene.parts, hasLength(24));
    final first = scene.parts.first;
    expect(first.size.x, 1);
    expect(first.size.y, 20);
    expect(first.size.z, .2);
    expect(first.position.x, 0);
    expect(first.color, '#8a8c97');
  });

  test('bounded 3D geometry stays finite at every orbit and explosion endpoint',
      () {
    final geometry = ConceptGeometry(ConceptScene.fromJson(previewScene()));
    for (final angle in [0.0, 45.0, 90.0, 180.0, 270.0, 359.0]) {
      for (final spread in [0.0, .5, 1.0]) {
        final drawing = geometry.project(const Size(360, 290),
            azimuth: angle, exploded: spread);
        expect(drawing.faces, isNotEmpty);
        expect(drawing.faces.length, lessThan(1500));
        for (final face in drawing.faces) {
          expect(face.depth.isFinite, isTrue);
          for (final point in face.points) {
            expect(point.dx.isFinite && point.dy.isFinite, isTrue);
            expect(point.dx, inInclusiveRange(0, 360));
            expect(point.dy, inInclusiveRange(0, 290));
          }
        }
        final front = drawing.faces.last;
        final centre = front.points.reduce((a, b) => a + b) /
            front.points.length.toDouble();
        expect(drawing.partAt(centre), front.partId);
      }
    }
  });

  test('orbit, explosion, and device changes alter the actual projected solids',
      () {
    final first = ConceptGeometry(ConceptScene.fromJson(previewScene()));
    final initial = first.project(const Size(600, 360), exploded: 0);
    final rotated =
        first.project(const Size(600, 360), azimuth: 130, exploded: 0);
    final apart = first.project(const Size(600, 360), exploded: 1);
    expect(rotated.faces.first.points.first,
        isNot(initial.faces.first.points.first));
    expect(apart.bounds.height, greaterThan(initial.bounds.height));
    final watch =
        ConceptGeometry(ConceptScene.fromJson(previewScene(wearable: true)));
    expect(watch.meshes.containsKey('strap'), isTrue);
    expect(first.meshes.containsKey('strap'), isFalse);
  });

  for (final width in [390.0, 1000.0]) {
    testWidgets('concept controls render and select real scene parts at $width',
        (tester) async {
      tester.view.physicalSize = Size(width, 1100);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.reset);
      final selected = <String?>[];
      await tester.pumpWidget(app(ConceptPreview(
          scene: previewScene(),
          conceptVersion: 2,
          onPartSelected: selected.add)));
      await tester.pumpAndSettle();
      expect(find.text('Desk air-quality monitor'), findsOneWidget);
      expect(find.text('Version 2'), findsOneWidget);
      final assembled = find.byKey(const ValueKey('concept-assembled'));
      await tester.ensureVisible(assembled);
      await tester.tap(assembled);
      await tester.pumpAndSettle();
      expect(
          tester
              .widget<Slider>(find.byKey(const ValueKey('concept-spread')))
              .value,
          0);
      await tester.tap(find.byKey(const ValueKey('concept-exploded')));
      await tester.pumpAndSettle();
      expect(
          tester
              .widget<Slider>(find.byKey(const ValueKey('concept-spread')))
              .value,
          1);
      final battery = find.byKey(const ValueKey('concept-part-battery'));
      await tester.ensureVisible(battery);
      await tester.tap(battery);
      await tester.pumpAndSettle();
      expect(selected.last, 'battery');
      expect(
          find.textContaining('Optional rechargeable power'), findsOneWidget);
      expect(tester.takeException(), isNull);
    });
  }

  testWidgets('scene updates retain selection by ID and remove obsolete parts',
      (tester) async {
    tester.view.physicalSize = const Size(1000, 1100);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    await tester.pumpWidget(app(ConceptPreview(scene: previewScene())));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('concept-part-board')));
    await tester.pumpAndSettle();
    await tester.pumpWidget(app(ConceptPreview(
        scene: previewScene(title: 'USB desk monitor', battery: false),
        conceptVersion: 3)));
    await tester.pumpAndSettle();
    expect(
        tester
            .widget<ChoiceChip>(
                find.byKey(const ValueKey('concept-part-board')))
            .selected,
        isTrue);
    expect(find.byKey(const ValueKey('concept-part-battery')), findsNothing);
    expect(
        find.textContaining('Runs the display and sensors.'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('reduced motion reaches the selected pose without animation',
      (tester) async {
    await tester
        .pumpWidget(app(ConceptPreview(scene: previewScene()), still: true));
    await tester.pumpAndSettle();
    final assembled = find.byKey(const ValueKey('concept-assembled'));
    await tester.ensureVisible(assembled);
    await tester.tap(assembled);
    await tester.pump();
    expect(
        tester
            .widget<Slider>(find.byKey(const ValueKey('concept-spread')))
            .value,
        0);
    expect(tester.takeException(), isNull);
  });

  testWidgets(
      'empty scene waits for a concept instead of showing a fixed example',
      (tester) async {
    await tester.pumpWidget(app(const ConceptPreview(scene: {})));
    await tester.pumpAndSettle();
    expect(
        find.textContaining('Your device preview will appear'), findsOneWidget);
    expect(find.byKey(const ValueKey('concept-canvas')), findsNothing);
    expect(tester.takeException(), isNull);
  });

  testWidgets('concept visual QA capture', (tester) async {
    tester.view.physicalSize = const Size(640, 1000);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    const boundary = ValueKey('concept-screenshot');
    await tester.pumpWidget(app(RepaintBoundary(
        key: boundary,
        child: ConceptPreview(scene: previewScene(), conceptVersion: 1))));
    await tester.pumpAndSettle();
    final render =
        tester.renderObject<RenderRepaintBoundary>(find.byKey(boundary));
    await tester.runAsync(() async {
      final image = await render.toImage(pixelRatio: 1.5);
      final bytes = await image.toByteData(format: ui.ImageByteFormat.png);
      final output = File('build/concept-preview-qa.png');
      await output.parent.create(recursive: true);
      await output.writeAsBytes(bytes!.buffer.asUint8List());
      image.dispose();
    });
  });
}
