import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:kleos_ui/data/models/json.dart';
import 'package:kleos_ui/ui/core/concept_preview.dart';

final actualScene = <String, dynamic>{
  'title': 'Requested bicycle light',
  'parts': [
    {
      'id': 'lamp',
      'label': 'Lamp enclosure',
      'shape': 'rounded_box',
      'size': {'x': 2, 'y': 1, 'z': 1}
    }
  ]
};

void main() {
  for (final width in [320.0, 1100.0]) {
    testWidgets(
        'capture waits without invented geometry then renders actual concept at $width',
        (tester) async {
      tester.view.physicalSize = Size(width, 1600);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.reset);
      Future<void> mount(JsonMap scene,
              {bool pending = false,
              bool stale = false,
              String message = 'Your concept will appear after capture'}) =>
          tester.pumpWidget(MaterialApp(
              home: Scaffold(
                  body: SingleChildScrollView(
                      child: ConceptPreview(
                          scene: scene,
                          conceptVersion: scene.isEmpty ? 0 : 1,
                          updating: pending,
                          stale: stale,
                          emptyMessage: message)))));
      await mount({});
      expect(
          find.text('Your concept will appear after capture'), findsOneWidget);
      expect(find.byKey(const ValueKey('concept-canvas')), findsNothing);
      expect(find.text('Version 0'), findsNothing);
      expect(find.byType(ChoiceChip), findsNothing);
      await mount({},
          pending: true,
          message: 'Creating a concept from your brief and reference images…');
      expect(find.textContaining('Creating a concept'), findsOneWidget);
      expect(find.byKey(const ValueKey('concept-canvas')), findsNothing);
      await mount({},
          message:
              'Your concept is not available yet. Review the planning error above, then retry the run.');
      expect(find.textContaining('retry the run'), findsOneWidget);
      expect(find.byKey(const ValueKey('concept-canvas')), findsNothing);
      await mount(actualScene);
      expect(find.text('Requested bicycle light'), findsOneWidget);
      expect(find.byKey(const ValueKey('concept-canvas')), findsOneWidget);
      expect(find.text('Version 1'), findsOneWidget);
      await mount(actualScene, pending: true);
      expect(find.byKey(const ValueKey('concept-canvas')), findsOneWidget);
      await mount(actualScene, stale: true);
      expect(
          find.textContaining('Showing your previous concept'), findsOneWidget);
      expect(find.byKey(const ValueKey('concept-canvas')), findsOneWidget);
      expect(tester.takeException(), isNull);
    });
  }
}
