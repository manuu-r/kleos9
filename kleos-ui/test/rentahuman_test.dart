import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:kleos_ui/data/models/api.generated.dart';
import 'package:kleos_ui/data/models/json.dart';
import 'package:kleos_ui/ui/core/widgets.dart';
import 'package:kleos_ui/ui/features/production/rentahuman.dart';

Future<void> mount(WidgetTester tester, Widget child,
    {double width = 390}) async {
  tester.view.physicalSize = Size(width, 1600);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(MaterialApp(
      theme: kTheme(),
      home: Scaffold(
          body: SingleChildScrollView(
              padding: const EdgeInsets.all(16), child: child))));
  await tester.pumpAndSettle();
}

Future<void> enter(WidgetTester tester, String label, String value) async {
  final finder = find.byWidgetPredicate(
      (w) => w is TextField && w.decoration?.labelText == label);
  await tester.ensureVisible(finder);
  await tester.enterText(finder, value);
}

Future<void> tap(WidgetTester tester, String label) async {
  await tester.ensureVisible(find.text(label));
  await tester.tap(find.text(label));
  await tester.pumpAndSettle();
}

JsonMap job({bool linked = false}) => {
      'id': 'draft',
      'draftSha256': 'a' * 64,
      'status': linked ? 'linked' : 'previewed',
      'task': {'title': 'Saved task', 'description': 'Reviewed details'},
      'preview': {'fundingTotalUsd': 47.2},
      if (linked) 'bountyId': 'b_owned',
      'observation': {
        'status': 'completed',
        'observedAt': '2026-11-01T10:00:00Z'
      },
    };

RentAHumanTaskControls controls(
        {bool configured = false,
        bool released = true,
        bool writable = true,
        List<JsonMap> drafts = const [],
        Future<void> Function(JsonMap, String)? save,
        Future<void> Function(JsonMap, String, String?)? action,
        Future<JsonMap> Function(String?)? bounties,
        Future<void> Function(JsonMap)? copy}) =>
    RentAHumanTaskControls(
        drafts: drafts,
        accountConfigured: configured,
        releaseId: released ? 'release' : null,
        revision: 2,
        canWrite: writable,
        onSave: save ?? (_, __) async {},
        onAction: action ?? (_, __, ___) async {},
        onBounties: bounties ?? (_) async => {},
        onCopy: copy ?? (_) async {},
        onOpen: () async {});

void main() {
  test(
      'generated task DTO preserves omitted defaults and optional nested values',
      () {
    final task = <String, dynamic>{
      'title': 'Fit check',
      'description': 'Reviewed instructions',
      'completionCriteria': 'Photographs and millimetre measurements',
      'price': 40.0,
      'estimatedHours': 1.0,
      'evidenceTypes': ['text', 'photo']
    };
    expect(ApiHumanTaskSpec.fromJson(task).toJson(), task);
    final complete = {
      ...task,
      'skillsNeeded': ['assembly'],
      'location': {'isRemoteAllowed': true}
    };
    expect(ApiHumanTaskSpec.fromJson(complete).toJson(), complete);
  });
  for (final width in [320.0, 390.0, 1000.0]) {
    testWidgets('RentAHuman directory and task form fit $width px',
        (tester) async {
      await mount(
          tester,
          Column(children: [
            RentAHumanDirectoryControls(
                status: const {'accountConfigured': false},
                onSearch: (_) async => {'humans': <JsonMap>[]},
                onOpen: () async {}),
            controls(),
          ]),
          width: width);
      expect(find.text('Real directory'), findsOneWidget);
      expect(find.text('Local draft'), findsOneWidget);
      expect(tester.takeException(), isNull);
    });
  }

  testWidgets('public search is explicit, paginated and never claims a booking',
      (tester) async {
    final requests = <Map<String, String>>[];
    await mount(
        tester,
        RentAHumanDirectoryControls(
            status: const {'accountConfigured': false},
            onOpen: () async {},
            onSearch: (filters) async {
              requests.add(filters);
              return {
                'humans': [
                  {
                    'name': 'Fixture worker',
                    'headline': 'Assembly',
                    'hourlyRate': 20,
                    'currency': 'USD'
                  }
                ],
                'hasMore': requests.length == 1,
                'nextCursor': requests.length == 1 ? 'cursor' : null
              };
            }));
    expect(requests, isEmpty);
    await tap(tester, 'Find people');
    expect(find.text('Fixture worker'), findsOneWidget);
    expect(requests.single['skill'], 'electronics assembly');
    await tap(tester, 'Next people');
    expect(requests.last['cursor'], 'cursor');
    expect(find.textContaining('Search does not hire'), findsOneWidget);
    expect(find.text('Hired'), findsNothing);
  });

  testWidgets('outage is visible and never substitutes simulated workers',
      (tester) async {
    await mount(
        tester,
        RentAHumanDirectoryControls(
            status: const {},
            onOpen: () async {},
            onSearch: (_) async => throw StateError('fixture outage')));
    await tap(tester, 'Find people');
    expect(find.textContaining('search is unavailable'), findsOneWidget);
    expect(find.text('Fixture worker'), findsNothing);
  });

  testWidgets(
      'local draft requires an explicit budget and uses a stable retry key',
      (tester) async {
    final bodies = <JsonMap>[], keys = <String>[];
    await mount(tester, controls(save: (body, key) async {
      bodies.add(body);
      keys.add(key);
    }));
    await tap(tester, 'Save local task draft');
    expect(bodies, isEmpty);
    await enter(tester, 'Fixed worker budget in USD (fees extra)', '40');
    await enter(tester, 'Estimated working hours', '1');
    await enter(tester, 'Work city', 'Bengaluru');
    await enter(tester, 'Country code (for example IN)', 'in');
    await tap(tester, 'Save local task draft');
    expect(bodies.single['releaseId'], 'release');
    expect(bodies.single['expectedEngineeringRevision'], 2);
    expect(asMap(bodies.single['task'])['price'], 40);
    expect(asMap(asMap(bodies.single['task'])['location'])['country'], 'IN');
    await tap(tester, 'Save local task draft');
    expect(keys.first, keys.last);
    expect(keys.first.length, lessThanOrEqualTo(64));
    expect(find.textContaining('No job was posted'), findsOneWidget);
  });

  testWidgets('missing account key and missing release block external actions',
      (tester) async {
    await mount(tester, controls(released: false, drafts: [job()]));
    expect(
        tester
            .widget<KButton>(
                find.widgetWithText(KButton, 'Save local task draft'))
            .onPressed,
        isNull);
    expect(find.text('Preview on RentAHuman'), findsNothing);
    expect(find.text('Load my RentAHuman jobs'), findsNothing);
    expect(find.text('Copy saved task details'), findsOneWidget);
  });

  testWidgets(
      'reviewed preview and owned-job link use explicit separate actions',
      (tester) async {
    final actions = <String>[], links = <String?>[];
    await mount(
        tester,
        controls(
            configured: true,
            drafts: [job()],
            action: (_, kind, id) async {
              actions.add(kind);
              links.add(id);
            },
            bounties: (_) async => {
                  'bounties': [
                    {'id': 'b_owned', 'title': 'Owned provider work'}
                  ]
                }));
    await tap(tester, 'Preview on RentAHuman');
    expect(actions, ['preview']);
    expect(links, [null]);
    expect(find.textContaining('47.2 including reported fees'), findsOneWidget);
    await tap(tester, 'Load my RentAHuman jobs');
    tester
        .widget<DropdownButtonFormField<String>>(
            find.byKey(const ValueKey('human-job-draft-null')))
        .onChanged!('b_owned');
    await tester.pump();
    await tap(tester, 'Link selected job');
    expect(actions, ['preview', 'link']);
    expect(links.last, 'b_owned');
  });

  testWidgets(
      'completed provider work remains an observation and viewers cannot send',
      (tester) async {
    await mount(
        tester,
        controls(
            configured: true, writable: false, drafts: [job(linked: true)]));
    expect(find.textContaining('Physical acceptance: not established'),
        findsOneWidget);
    expect(find.text('Linked provider observation'), findsOneWidget);
    expect(find.text('Refresh provider status'), findsNothing);
    expect(find.text('Save local task draft'), findsNothing);
  });
}
