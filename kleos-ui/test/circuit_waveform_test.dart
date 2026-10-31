import 'dart:convert';
import 'dart:io';
import 'dart:ui' as ui;

import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:kleos_ui/data/models/json.dart';
import 'package:kleos_ui/ui/core/widgets.dart';
import 'package:kleos_ui/ui/features/project/circuit_waveform.dart';

JsonMap specimen() => {
      'schemaVersion': 'circuit-waveform-v1',
      'evidenceKind': 'digital',
      'physicalAcceptance': false,
      'metadata': {
        'limitations': ['Ideal parts; no layout, thermal or RF effects.'],
        'devices': [
          {'componentId': 'D1', 'safeCurrentMa': 5}
        ],
      },
      'waveform': {
        'timeSeconds': [0, 0.05, 0.1],
        'signals': [
          {
            'name': 'LED rail',
            'unit': 'V',
            'values': [0, 2, 2.1],
            'low': [0, 1.9, 2],
            'high': [0, 2.2, 2.3],
            'limits': {'min': 1.8, 'max': 2.4}
          },
          {
            'name': 'D1 current',
            'unit': 'mA',
            'values': [0, 1.9, 2],
            'low': [0, 1.8, 1.9],
            'high': [0, 2, 2.1],
            'limits': {'max': 5}
          },
        ],
      },
      'checks': [
        {
          'name': 'circuit_dc_rails',
          'status': 'PASS',
          'details': <String, dynamic>{}
        },
        {
          'name': 'circuit_startup',
          'status': 'UNKNOWN',
          'details': <String, dynamic>{}
        },
        {
          'name': 'circuit_led_current',
          'status': 'FAIL',
          'details': <String, dynamic>{}
        },
        {
          'name': 'circuit_total_current',
          'status': 'ERROR',
          'details': <String, dynamic>{}
        },
      ],
    };

Future<void> mount(WidgetTester tester, Widget child,
    {double width = 390, double scale = 1, bool reduced = false}) async {
  tester.view.physicalSize = Size(width, 1500);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(MaterialApp(
      theme: kTheme(),
      builder: (context, child) => MediaQuery(
          data: MediaQuery.of(context).copyWith(
              textScaler: TextScaler.linear(scale), disableAnimations: reduced),
          child: child!),
      home: Scaffold(
          body: SingleChildScrollView(
              padding: const EdgeInsets.all(16), child: child))));
  await tester.pumpAndSettle();
}

void main() {
  test('rejects malformed dimensions, nonfinite values and unsafe envelopes',
      () {
    for (final change in ['nan', 'length', 'time', 'band', 'physical']) {
      final data = specimen();
      final signal = data['waveform']['signals'][0];
      switch (change) {
        case 'nan':
          signal['values'][1] = double.nan;
        case 'length':
          signal['values'] = [0];
        case 'time':
          data['waveform']['timeSeconds'] = [0, 0, 0.1];
        case 'band':
          signal['low'][1] = 3;
        case 'physical':
          data['physicalAcceptance'] = true;
      }
      expect(() => CircuitWaveData.parse(data), throwsFormatException);
    }
  });

  for (final width in [320.0, 390.0, 900.0]) {
    testWidgets('waveform controls and statuses fit $width px', (tester) async {
      await mount(tester, CircuitWaveformViewer(result: specimen()),
          width: width, scale: 1.3);
      expect(find.text('Simulated · not measured'), findsOneWidget);
      for (final status in ['PASS', 'FAIL', 'UNKNOWN', 'ERROR']) {
        expect(find.text(status), findsOneWidget);
      }
      expect(find.byKey(const ValueKey('waveform-plot')), findsOneWidget);
      expect(tester.takeException(), isNull);
    });
  }

  testWidgets(
      'scrub, replay, reset, net selection and LED follow recorded samples',
      (tester) async {
    await mount(tester, CircuitWaveformViewer(result: specimen()));
    expect(find.textContaining('LED off'), findsOneWidget);
    final slider =
        tester.widget<Slider>(find.byKey(const ValueKey('waveform-time')));
    slider.onChanged!(1);
    await tester.pump();
    expect(find.text('100.00 ms'), findsOneWidget);
    expect(find.textContaining('LED on'), findsOneWidget);
    await tester.tap(find.text('Reset time'));
    await tester.pump();
    expect(find.text('0.00 ms'), findsOneWidget);
    await tester.tap(find.text('Replay power-on'));
    await tester.pump();
    await tester.pump(const Duration(seconds: 3));
    expect(find.text('Pause'), findsOneWidget);
    expect(
        tester
            .widget<Slider>(find.byKey(const ValueKey('waveform-time')))
            .value,
        greaterThan(0));
    await tester.tap(find.text('Pause'));
    await tester.pump();
    final dropdown = tester.widget<DropdownButtonFormField<int>>(
        find.byKey(const ValueKey('waveform-net')));
    dropdown.onChanged!(1);
    await tester.pump();
    expect(find.text('max 5.000 mA'), findsOneWidget);
    await tester.pumpWidget(const SizedBox.shrink());
    expect(tester.takeException(), isNull);
  });

  testWidgets('reduced motion disables replay but allows time inspection',
      (tester) async {
    await mount(tester, CircuitWaveformViewer(result: specimen()),
        reduced: true);
    final button =
        tester.widget<KButton>(find.widgetWithText(KButton, 'Replay power-on'));
    expect(button.onPressed, isNull);
    tester
        .widget<Slider>(find.byKey(const ValueKey('waveform-time')))
        .onChanged!(1);
    await tester.pump();
    expect(find.text('100.00 ms'), findsOneWidget);
  });

  testWidgets('unknown evidence never invents a waveform', (tester) async {
    final data = specimen();
    data['waveform'] = {'timeSeconds': <double>[], 'signals': <JsonMap>[]};
    await mount(tester, CircuitWaveformViewer(result: data));
    expect(find.byKey(const ValueKey('waveform-plot')), findsNothing);
    expect(find.textContaining('No supported waveform'), findsOneWidget);
  });

  testWidgets('panel reads current evidence and drops old revision data',
      (tester) async {
    await mount(
        tester,
        ProviderScope(
            overrides: [
              circuitSimulationProvider.overrideWith((ref, request) async => {
                    'status': 'available',
                    'revision': request.revision - 1,
                    'result': specimen(),
                    'artifactSha256': 'fixture',
                  }),
            ],
            child: const CircuitSimulationPanel(
                projectId: 'project', revision: 3)));
    expect(find.byKey(const ValueKey('waveform-plot')), findsNothing);
  });

  testWidgets('recorded real ngspice power-on renders, replays and zooms',
      (tester) async {
    final recorded = asMap(jsonDecode(
        File('../backend/verification/ngspice-power-on-sample.json')
            .readAsStringSync()));
    await mount(tester, CircuitWaveformViewer(result: recorded), width: 320);
    expect(find.text('PASS'), findsNWidgets(4));
    tester
        .widget<Slider>(find.byKey(const ValueKey('waveform-time')))
        .onChanged!(1);
    await tester.pump();
    expect(find.text('5.00 ms'), findsOneWidget);
    expect(find.textContaining('LED on'), findsOneWidget);
    await tester.ensureVisible(find.text('Show full 100 ms'));
    await tester.tap(find.text('Show full 100 ms'));
    await tester.pump();
    tester
        .widget<Slider>(find.byKey(const ValueKey('waveform-time')))
        .onChanged!(1);
    await tester.pump();
    expect(find.text('100.00 ms'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('phone visual specimen', (tester) async {
    const output = String.fromEnvironment('WAVEFORM_SCREENSHOT_PATH');
    if (output.isNotEmpty) {
      await tester.runAsync(() async {
        for (final family in ['Geist', 'GeistMono']) {
          await (FontLoader(family)
                ..addFont(rootBundle.load('assets/fonts/$family.ttf')))
              .load();
        }
      });
    }
    final key = GlobalKey();
    final data = specimen();
    // Optional fixture export for visual QA; never loaded by production.
    const fixture = String.fromEnvironment('WAVEFORM_RESULT_PATH');
    final result = fixture.isEmpty
        ? data
        : asMap(jsonDecode(File(fixture).readAsStringSync()));
    await mount(
        tester,
        RepaintBoundary(
            key: key, child: CircuitWaveformViewer(result: result)));
    tester
        .widget<Slider>(find.byKey(const ValueKey('waveform-time')))
        .onChanged!(1);
    await tester.pumpAndSettle();
    if (output.isNotEmpty) {
      await tester.runAsync(() async {
        final boundary =
            key.currentContext!.findRenderObject()! as RenderRepaintBoundary;
        final image = await boundary.toImage();
        final bytes = await image.toByteData(format: ui.ImageByteFormat.png);
        File(output).writeAsBytesSync(bytes!.buffer.asUint8List());
        image.dispose();
      });
    }
    expect(tester.takeException(), isNull);
  });
}
