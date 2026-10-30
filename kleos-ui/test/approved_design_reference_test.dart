import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:kleos_ui/data/models/json.dart';
import 'package:kleos_ui/ui/core/concept_preview.dart';
import 'package:kleos_ui/ui/core/widgets.dart';
import 'package:kleos_ui/ui/features/project/approved_design_reference.dart';

const hash = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa';
JsonMap _approved(String title, {String sceneHash = hash}) => {
      'schemaVersion': 'approved-design-v1',
      'version': 2,
      'sceneVersion': 5,
      'sceneHash': sceneHash,
      'approvedAt': '2026-10-30T10:00:00Z',
      'approvedBy': 'project-owner',
      'engineeringRevision': 3,
      'scene': {
        'schemaVersion': 'concept-scene-v1',
        'units': 'relative',
        'title': title,
        'parts': [
          {
            'id': 'shell',
            'role': 'shell',
            'label': 'Round case',
            'shape': 'cylinder',
            'size': {'x': 4, 'y': 4, 'z': 1},
            'color': '#778899'
          },
          {
            'id': 'board',
            'role': 'board',
            'label': 'Circuit board',
            'shape': 'box',
            'size': {'x': 3, 'y': 3, 'z': .2},
            'color': '#558866'
          },
        ]
      }
    };
JsonMap _intent({String sceneHash = hash}) => {
      'sceneHash': sceneHash,
      'approvalVersion': 2,
      'enclosure': {'conceptPartId': 'shell', 'profile': 'circle', 'axis': 'z'},
    };

Future<void> _mount(WidgetTester tester, Widget child) async {
  tester.view.physicalSize = const Size(320, 900);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(MaterialApp(
      theme: kTheme(),
      builder: (context, child) => MediaQuery(
          data: MediaQuery.of(context)
              .copyWith(textScaler: TextScaler.linear(1.3)),
          child: child!),
      home: Scaffold(
          body: SingleChildScrollView(
              padding: const EdgeInsets.all(16), child: child))));
  await tester.pumpAndSettle();
}

void main() {
  testWidgets(
      'the frozen reference preserves relative proportions and approval provenance',
      (tester) async {
    final approved = _approved('Approved round watch');
    await _mount(
        tester,
        ApprovedDesignReference(
            projectId: 'one',
            approvedDesign: approved,
            designIntent: _intent()));
    expect(find.text('Body shape: Circular'), findsOneWidget);
    expect(find.text('Layout: Circuit board'), findsOneWidget);
    await tester.ensureVisible(find.text('View approved 3D reference'));
    await tester.tap(find.text('View approved 3D reference'));
    await tester.pumpAndSettle();
    final preview = tester.widget<ConceptPreview>(find.byType(ConceptPreview));
    expect(preview.scene, approved['scene']);
    expect(preview.conceptVersion, 5);
    expect(find.textContaining('Relative proportions are not millimetres'),
        findsOneWidget);
    expect(find.textContaining('will be refined during engineering'),
        findsNothing);
    await tester.ensureVisible(find.text('Reference details'));
    await tester.tap(find.text('Reference details'));
    await tester.pumpAndSettle();
    expect(find.text('Scene hash: $hash'), findsOneWidget);
    expect(find.text('Approval version 2 · concept version 5'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('switching projects cannot retain the previous reference scene',
      (tester) async {
    var projectId = 'one';
    var approved = _approved('Approved round watch');
    late StateSetter update;
    await _mount(tester, StatefulBuilder(builder: (context, setState) {
      update = setState;
      return ApprovedDesignReference(
          projectId: projectId,
          approvedDesign: approved,
          designIntent: _intent());
    }));
    await tester.tap(find.text('View approved 3D reference'));
    await tester.pumpAndSettle();
    expect(find.text('Approved round watch'), findsOneWidget);
    update(() {
      projectId = 'two';
      approved = _approved('Another approved device',
          sceneHash: 'different-project-hash');
    });
    await tester.pumpAndSettle();
    expect(find.text('Approved round watch'), findsNothing);
    await tester.tap(find.text('View approved 3D reference'));
    await tester.pumpAndSettle();
    expect(find.text('Another approved device'), findsOneWidget);
    expect(tester.widget<ConceptPreview>(find.byType(ConceptPreview)).scene,
        approved['scene']);
    expect(tester.takeException(), isNull);
  });

  testWidgets(
      'a conflicting design requests a review without granting approval',
      (tester) async {
    var reviews = 0;
    await _mount(
        tester,
        ApprovedDesignReference(
          projectId: 'one',
          approvedDesign: _approved('Approved round watch'),
          designIntent: _intent(),
          check: {
            'status': 'FAIL',
            'details': {
              'reason':
                  'Rectangular engineering enclosure conflicts with circular approval.'
            }
          },
          onRequestReview: () => reviews++,
        ));
    expect(find.text('Engineering conflicts with the approved design'),
        findsOneWidget);
    expect(find.text('FAIL'), findsOneWidget);
    expect(find.text('PASS'), findsNothing);
    await tester.ensureVisible(find.text('Review design reference'));
    await tester.tap(find.text('Review design reference'));
    expect(reviews, 1);
    expect(find.text('Confirm and start design'), findsNothing);
    expect(tester.takeException(), isNull);
  });

  testWidgets(
      'initial UNKNOWN check waits without asking to revise the approved design',
      (tester) async {
    await _mount(
        tester,
        ApprovedDesignReference(
          projectId: 'one',
          approvedDesign: _approved('Approved round watch'),
          designIntent: _intent(),
          check: {
            'status': 'UNKNOWN',
            'details': {
              'reasonCode': 'awaiting_engineering_checks',
              'reason':
                  'Awaiting engineering dimensions and independent checks',
            }
          },
          onRequestReview: () =>
              fail('Pending checks must not request another design review'),
        ));
    expect(find.text('Awaiting engineering checks'), findsOneWidget);
    expect(find.text('Review design reference'), findsNothing);
    expect(find.text('Design alignment needs a decision'), findsNothing);
    expect(find.text('Frozen at confirmation'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('unsupported UNKNOWN geometry keeps an explicit review action',
      (tester) async {
    var reviews = 0;
    await _mount(
        tester,
        ApprovedDesignReference(
          projectId: 'one',
          approvedDesign: _approved('Approved round watch'),
          designIntent: _intent(),
          check: {
            'status': 'UNKNOWN',
            'details': {
              'scope':
                  'Server-derived approved profile and explicit component mapping; geometry requires independent native checks',
              'limitations': [
                'The approved form needs additional qualified geometry checks'
              ],
            }
          },
          onRequestReview: () => reviews++,
        ));
    expect(find.text('Design alignment needs a decision'), findsOneWidget);
    expect(find.text('Awaiting engineering checks'), findsNothing);
    await tester.ensureVisible(find.text('Review design reference'));
    await tester.tap(find.text('Review design reference'));
    expect(reviews, 1);
    expect(tester.takeException(), isNull);
  });

  testWidgets(
      'a superseded approval remains identified as the previous reference',
      (tester) async {
    await _mount(
        tester,
        ApprovedDesignReference(
            projectId: 'one',
            approvedDesign: _approved('Earlier approved device'),
            designIntent: _intent(),
            status: 'superseded'));
    expect(find.text('New approval needed'), findsOneWidget);
    expect(find.textContaining('previous approved reference'), findsOneWidget);
    expect(find.text('Frozen at confirmation'), findsNothing);
    expect(tester.takeException(), isNull);
  });
}
