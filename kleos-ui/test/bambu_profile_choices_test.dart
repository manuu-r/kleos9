import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:kleos_ui/data/models/json.dart';
import 'package:kleos_ui/ui/core/widgets.dart';
import 'package:kleos_ui/ui/features/project/bambu_print.dart';
import 'bambu_print_test.dart' as fixtures;

DropdownButtonFormField<String> dropdown(WidgetTester tester, String label) =>
    tester.widget<DropdownButtonFormField<String>>(find.byWidgetPredicate((w) =>
        w is DropdownButtonFormField<String> && w.decoration.labelText == label));

List<DropdownMenuItem<String>> items(WidgetTester tester, String label) =>
    tester.widget<DropdownButton<String>>(find.descendant(
      of: find.byWidget(dropdown(tester, label)), matching: find.byType(DropdownButton<String>))).items ?? [];

Future<void> choose(WidgetTester tester, String label, String value) async {
  dropdown(tester, label).onChanged!(value);
  await tester.pump();
}

bool prepareEnabled(WidgetTester tester) => tester
    .widget<KButton>(find.widgetWithText(KButton, 'Prepare print')).onPressed != null;

JsonMap choices() {
  final data = fixtures.data();
  data['catalog']['profiles'] = [
    for (final (id, nozzle, material, filament, bed, qualified) in [
      ('pla-textured', 0.4, 'PLA', 'Bambu PLA Basic', 'Textured PEI Plate', true),
      ('pla-smooth', 0.4, 'PLA', 'Bambu PLA Basic', 'High Temp Plate', true),
      ('petg', 0.4, 'PETG', 'Bambu PETG HF', 'Textured PEI Plate', true),
      ('fine', 0.2, 'PLA', 'Bambu PLA Basic', 'Textured PEI Plate', false),
    ])
      {'id': id, 'printerModelId': 'C12', 'nozzleMm': nozzle, 'material': material,
       'filamentPreset': filament, 'bedType': bed, 'qualified': qualified}
  ];
  return data;
}

void main() {
  testWidgets('cascading choices require explicit choices and submit exact profile', (tester) async {
    final data = choices();
    JsonMap? sent;
    await fixtures.mount(tester, BambuPrintControls(data: data, initialModelId: 'C12',
      onPrepare: (body, _) async { sent = body; return {}; }, onDownload: (_) async {}));
    expect(prepareEnabled(tester), isFalse);
    expect(dropdown(tester, 'Filament').onChanged, isNull);
    final nozzles = items(tester, 'Nozzle size');
    expect(nozzles.firstWhere((p) => p.value == '0.6').enabled, isFalse);
    await choose(tester, 'Nozzle size', '0.4');
    expect(dropdown(tester, 'Build plate').onChanged, isNull);
    await choose(tester, 'Filament', jsonEncode(['PLA', 'Bambu PLA Basic']));
    expect(find.text('Slicing profile'), findsNothing);
    await choose(tester, 'Build plate', 'High Temp Plate');
    expect(items(tester, 'Slicing profile').map((p) => p.value), ['pla-smooth']);
    await choose(tester, 'Slicing profile', 'pla-smooth');
    expect(prepareEnabled(tester), isTrue);
    await tester.ensureVisible(find.text('Prepare print'));
    await tester.tap(find.text('Prepare print'));
    await tester.pumpAndSettle();
    expect(sent?['profileId'], 'pla-smooth');
    expect(find.textContaining('Build plate: High Temp Plate'), findsOneWidget);
    await choose(tester, 'Filament', jsonEncode(['PETG', 'Bambu PETG HF']));
    expect(prepareEnabled(tester), isFalse);
    expect(items(tester, 'Slicing profile').map((p) => p.value), ['petg']);
    expect(tester.takeException(), isNull);
  });

  testWidgets('unqualified choices are visible but cannot prepare', (tester) async {
    await fixtures.mount(tester, BambuPrintControls(data: choices(), initialModelId: 'C12',
      onPrepare: (_, __) async => throw StateError('Unqualified'), onDownload: (_) async {}));
    await choose(tester, 'Nozzle size', '0.2');
    await choose(tester, 'Slicing profile', 'fine');
    expect(find.textContaining('Needs qualification'), findsOneWidget);
    expect(prepareEnabled(tester), isFalse);
    expect(find.textContaining('needs qualification before'), findsOneWidget);
  });

  testWidgets('changed options and printer reset selection instead of using a stale profile', (tester) async {
    var data = choices();
    Future<void> mount([String model = 'C12']) => tester.pumpWidget(MaterialApp(home: Scaffold(
      body: SingleChildScrollView(child: BambuPrintControls(data: data, initialModelId: model,
        onPrepare: (_, __) async => {}, onDownload: (_) async {})))));
    await mount();
    await choose(tester, 'Nozzle size', '0.4');
    await choose(tester, 'Filament', jsonEncode(['PETG', 'Bambu PETG HF']));
    await choose(tester, 'Slicing profile', 'petg');
    expect(prepareEnabled(tester), isTrue);
    data = choices();
    (data['catalog']['profiles'] as List).removeWhere((p) => p['id'] == 'petg');
    await mount();
    expect(prepareEnabled(tester), isFalse);
    await mount('BL-P001');
    expect(prepareEnabled(tester), isFalse);
    expect(items(tester, 'Filament'), isEmpty);
    expect(tester.takeException(), isNull);
  });
}
