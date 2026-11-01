import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:kleos_ui/data/models/json.dart';
import 'package:kleos_ui/ui/core/widgets.dart';
import 'package:kleos_ui/ui/features/project/bambu_print.dart';

JsonMap data(
        {bool detected = false,
        bool qualified = true,
        bool released = true,
        bool ready = false}) =>
    {
      'catalog': {
        'models': asMap(jsonDecode(File('../backend/kleos/bambu-models.json')
            .readAsStringSync()))['models'],
        'profiles': [
          {
            'id': 'p1s-pla',
            'printerModelId': 'C12',
            'nozzleMm': 0.4,
            'material': 'PLA',
            'qualified': qualified
          }
        ],
      },
      'workspace': {
        'project': {
          'acceptedReleaseId': released ? 'release' : null,
          'engineeringRevision': 1
        },
        'artifacts': [
          {
            'id': 'part',
            'name': 'case.stl',
            'lane': 'mechanics',
            'revision': 1,
            'status': released ? 'accepted' : 'candidate'
          }
        ],
      },
      'connectors': {
        'connectors': [
          if (detected)
            {
              'telemetry': {
                'kind': 'bambu',
                'connected': true,
                'stale': false,
                'profile': {'modelId': 'C12'},
              }
            }
        ]
      },
      'preparations': {
        'preparations': [
          if (ready)
            {
              'id': 'preparation',
              'status': 'ready_for_operator',
              'preparedArtifactId': 'sliced',
              'profile': {'material': 'PLA', 'nozzleMm': 0.4},
            }
        ]
      },
    };

Future<void> mount(WidgetTester tester, Widget child,
    {double width = 390}) async {
  tester.view.physicalSize = Size(width, 1500);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(MaterialApp(
      theme: kTheme(),
      home: Scaffold(
          body: SingleChildScrollView(
              padding: const EdgeInsets.all(16), child: child))));
  await tester.pumpAndSettle();
}

void main() {
  testWidgets(
      'Connections model selection supplies preparation without a second model dropdown',
      (tester) async {
    await mount(
        tester,
        BambuPrintControls(
            data: data(),
            initialModelId: 'C12',
            showModelSelector: false,
            onPrepare: (_, __) async => <String, dynamic>{},
            onDownload: (_) async {}));
    expect(find.byKey(const ValueKey('bambu-model-C12')), findsNothing);
    expect(
        find.byKey(const ValueKey('bambu-profile-C12-null')), findsOneWidget);
    tester
        .widget<DropdownButtonFormField<String>>(
            find.byKey(const ValueKey('bambu-profile-C12-null')))
        .onChanged!('p1s-pla');
    await tester.pump();
    expect(find.text('0.4 mm · PLA'), findsOneWidget);
  });
  for (final width in [320.0, 390.0, 1000.0]) {
    testWidgets('all pinned printer models selectable at $width',
        (tester) async {
      await mount(
          tester,
          BambuPrintControls(
              data: data(),
              onPrepare: (_, __) async => <String, dynamic>{},
              onDownload: (_) async {}),
          width: width);
      final dropdown = tester.widget<DropdownButton<String>>(find.descendant(
          of: find.byKey(const ValueKey('bambu-model-null')),
          matching: find.byType(DropdownButton<String>)));
      expect(dropdown.items!.length, 14);
      expect(find.text('Real preparation'), findsOneWidget);
      expect(tester.takeException(), isNull);
    });
  }

  testWidgets(
      'reported model auto-selects and preparation uses an accepted part and stable retry key',
      (tester) async {
    final requests = <JsonMap>[];
    final keys = <String>[];
    await mount(
        tester,
        BambuPrintControls(
            data: data(detected: true),
            onPrepare: (body, key) async {
              requests.add(body);
              keys.add(key);
              return <String, dynamic>{};
            },
            onDownload: (_) async {}));
    expect(find.textContaining('Detected from your printer bridge'),
        findsOneWidget);
    tester
        .widget<DropdownButtonFormField<String>>(
            find.byKey(const ValueKey('bambu-profile-C12-null')))
        .onChanged!('p1s-pla');
    await tester.pump();
    await tester.tap(find.text('Prepare print'));
    await tester.pumpAndSettle();
    expect(requests.single, {
      'releaseId': 'release',
      'artifactId': 'part',
      'profileId': 'p1s-pla',
      'expectedEngineeringRevision': 1
    });
    await tester.tap(find.text('Prepare print'));
    await tester.pumpAndSettle();
    expect(keys.first, keys.last);
    expect(keys.first.length, lessThanOrEqualTo(64));
  });

  testWidgets('manual selection works when model cannot be detected',
      (tester) async {
    await mount(
        tester,
        BambuPrintControls(
            data: data(),
            onPrepare: (_, __) async => <String, dynamic>{},
            onDownload: (_) async {}));
    tester
        .widget<DropdownButtonFormField<String>>(
            find.byKey(const ValueKey('bambu-model-null')))
        .onChanged!('C12');
    await tester.pump();
    expect(
        find.byKey(const ValueKey('bambu-profile-C12-null')), findsOneWidget);
  });

  testWidgets(
      'unqualified profiles and unreleased parts cannot start preparation',
      (tester) async {
    await mount(
        tester,
        BambuPrintControls(
            data: data(detected: true, qualified: false, released: false),
            onPrepare: (_, __) async => throw StateError('Must not prepare'),
            onDownload: (_) async {}));
    expect(
        tester
            .widget<KButton>(find.widgetWithText(KButton, 'Prepare print'))
            .onPressed,
        isNull);
    expect(find.textContaining('approved nozzle/material'), findsOneWidget);
  });

  testWidgets(
      'prepared plate can be downloaded but is never labelled physically accepted',
      (tester) async {
    String? download;
    await mount(
        tester,
        BambuPrintControls(
            data: data(detected: true, ready: true),
            onPrepare: (_, __) async => <String, dynamic>{},
            onDownload: (id) async {
              download = id;
            }));
    await tester.ensureVisible(find.text('Download sliced plate'));
    await tester.tap(find.text('Download sliced plate'));
    await tester.pump();
    expect(download, 'sliced');
    expect(find.text('Ready for review'), findsOneWidget);
    expect(find.textContaining('not physical acceptance'), findsOneWidget);
  });
}
