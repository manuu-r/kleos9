import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:kleos_ui/data/models/json.dart';
import 'package:kleos_ui/ui/core/widgets.dart';
import 'package:kleos_ui/ui/features/project/cad_assembly_preview.dart';

JsonMap _part(String id, String label, double z) => {
      'id': id,
      'label': label,
      'quantity': id == 'base' ? 2 : 1,
      'material': 'PLA',
      'mesh': {
        'triangles': [
          [
            [0, 0, z],
            [30, 0, z],
            [0, 20, z]
          ],
          [
            [30, 0, z],
            [30, 20, z],
            [0, 20, z]
          ],
        ]
      },
      'transform': {
        'translation': {'x': 0, 'y': 0, 'z': 0}
      },
    };

Future<void> _mount(WidgetTester tester, JsonMap assembly) async {
  tester.view.physicalSize = const Size(320, 700);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(MaterialApp(
      theme: kTheme(),
      home: Scaffold(
          body: Padding(
              padding: const EdgeInsets.all(16),
              child: CadAssemblyPreview(
                  assembly: assembly,
                  height: 300,
                  fallback: const Text('Whole mesh fallback'))))));
  await tester.pumpAndSettle();
}

void main() {
  testWidgets(
      'explosion separates display parts and never edits source geometry',
      (tester) async {
    final assembly = <String, dynamic>{
      'schemaVersion': 'cad-assembly-v1',
      'separation': 'named_parts',
      'parts': [_part('base', 'Base', 0), _part('lid', 'Lid', 12)]
    };
    final original = jsonEncode(assembly);
    await _mount(tester, assembly);
    expect(find.text('Base ×2'), findsOneWidget);
    expect(find.byKey(const ValueKey('cad-assembly-canvas')), findsOneWidget);
    final slider = find.byKey(const ValueKey('cad-explosion-slider'));
    await tester.drag(slider, const Offset(100, 0));
    await tester.pumpAndSettle();
    expect(tester.widget<Slider>(slider).value, greaterThan(0));
    await tester.tap(find.byKey(const ValueKey('cad-part-base')));
    await tester.pumpAndSettle();
    expect(find.text('Base · PLA'), findsOneWidget);
    await tester.drag(find.byKey(const ValueKey('cad-assembly-canvas')),
        const Offset(50, 20));
    await tester.pump();
    await tester.tap(find.byTooltip('Assemble CAD parts'));
    await tester.pumpAndSettle();
    expect(tester.widget<Slider>(slider).value, 0);
    expect(jsonEncode(assembly), original);
    expect(tester.takeException(), isNull);
  });

  testWidgets('a monolithic CAD body is never given fabricated separated parts',
      (tester) async {
    await _mount(tester, {
      'separation': 'monolithic',
      'parts': [_part('body', 'Body', 0)]
    });
    expect(find.byKey(const ValueKey('cad-explosion-slider')), findsNothing);
    expect(find.text('Single CAD body · drag to orbit'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('malformed part meshes fall back to the original whole mesh',
      (tester) async {
    await _mount(tester, {
      'parts': [
        {
          'id': 'broken',
          'mesh': {'triangles': <Object>[]}
        }
      ]
    });
    expect(find.text('Whole mesh fallback'), findsOneWidget);
    expect(find.byKey(const ValueKey('cad-explosion-slider')), findsNothing);
    expect(tester.takeException(), isNull);
  });

  JsonMap twoParts() => <String, dynamic>{
        'schemaVersion': 'cad-assembly-v1',
        'separation': 'named_parts',
        'parts': [_part('base', 'Base', 0), _part('lid', 'Lid', 12)]
      };

  testWidgets('every control is at least 44px so thumbs can hit it',
      (tester) async {
    await _mount(tester, twoParts());
    for (final tooltip in [
      'Expand CAD viewer',
      'Zoom in CAD',
      'Zoom out CAD',
      'Reset CAD view',
      'Assemble CAD parts'
    ]) {
      final size = tester.getSize(find.byTooltip(tooltip));
      expect(size.width, greaterThanOrEqualTo(44), reason: tooltip);
      expect(size.height, greaterThanOrEqualTo(44), reason: tooltip);
    }
    for (final id in ['base', 'lid']) {
      expect(tester.getSize(find.byKey(ValueKey('cad-part-$id'))).height,
          greaterThanOrEqualTo(40));
    }
    expect(tester.takeException(), isNull);
  });

  testWidgets('two fingers pinch to zoom and the buttons follow the limit',
      (tester) async {
    await _mount(tester, twoParts());
    final canvas = find.byKey(const ValueKey('cad-assembly-canvas'));
    final center = tester.getCenter(canvas);
    IconButton zoomOut() => tester.widget<IconButton>(find.ancestor(
        of: find.byIcon(Icons.remove), matching: find.byType(IconButton)));
    expect(zoomOut().onPressed, isNotNull);
    // Pull two fingers together far enough to hit the minimum zoom.
    final a = await tester.startGesture(center + const Offset(-60, 0));
    final b = await tester.startGesture(center + const Offset(60, 0));
    await a.moveTo(center + const Offset(-4, 0));
    await b.moveTo(center + const Offset(4, 0));
    await a.up();
    await b.up();
    await tester.pumpAndSettle();
    expect(zoomOut().onPressed, isNull);
    // Reset restores the default zoom, so zooming out is possible again.
    await tester.tap(find.byTooltip('Reset CAD view'));
    await tester.pumpAndSettle();
    expect(zoomOut().onPressed, isNotNull);
    expect(tester.takeException(), isNull);
  });

  testWidgets('double tap resets the explosion and the selection',
      (tester) async {
    await _mount(tester, twoParts());
    await tester.tap(find.byKey(const ValueKey('cad-part-base')));
    await tester.pumpAndSettle();
    expect(find.text('Base · PLA'), findsOneWidget);
    final canvas = find.byKey(const ValueKey('cad-assembly-canvas'));
    final at = tester.getCenter(canvas);
    await tester.tapAt(at);
    await tester.pump(const Duration(milliseconds: 60));
    await tester.tapAt(at);
    await tester.pumpAndSettle();
    expect(find.text('Base · PLA'), findsNothing);
    expect(find.text('2 parts · drag to orbit'), findsOneWidget);
  });

  testWidgets('expand opens the viewer full screen with the same parts',
      (tester) async {
    await _mount(tester, twoParts());
    await tester.tap(find.byTooltip('Expand CAD viewer'));
    await tester.pumpAndSettle();
    expect(find.text('CAD assembly · 2 parts'), findsOneWidget);
    // The full-screen viewer fills the display and has no expand button.
    expect(find.byTooltip('Expand CAD viewer'), findsNothing);
    final canvas =
        tester.getSize(find.byKey(const ValueKey('cad-assembly-canvas')));
    expect(canvas.height, greaterThan(400));
    expect(find.byKey(const ValueKey('cad-explosion-slider')), findsOneWidget);
    expect(tester.takeException(), isNull);
    await tester.tap(find.byType(CloseButton));
    await tester.pumpAndSettle();
    expect(find.byTooltip('Expand CAD viewer'), findsOneWidget);
  });
}
