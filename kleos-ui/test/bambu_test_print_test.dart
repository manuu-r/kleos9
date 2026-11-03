import 'dart:async';
import 'dart:convert';

import 'package:crypto/crypto.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:kleos_ui/data/models/json.dart';
import 'package:kleos_ui/data/services/production_api.dart';
import 'package:kleos_ui/ui/core/widgets.dart';
import 'package:kleos_ui/ui/features/production/bambu_test_print.dart';

JsonMap profile(String id, double nozzle, String filament, String plate) => {
      'id': id,
      'printerModelId': 'BL-P001',
      'nozzleMm': nozzle,
      'material': 'PLA',
      'filamentPreset': filament,
      'bedType': plate,
      'qualified': false,
      'testPreparation': true,
    };
List<JsonMap> get profiles => [
      profile('textured', .4, 'Bambu PLA Basic', 'Textured PEI Plate'),
      profile('smooth', .4, 'Bambu PLA Basic', 'High Temp Plate'),
      profile('large', .6, 'Generic PLA', 'Textured PEI Plate'),
    ];
JsonMap job([String status = 'ready']) => {
      'id': 'test-job',
      'status': status,
      'printerModelId': 'BL-P001',
      'profileId': 'textured',
      'profile': profiles.first,
      'dimensionsMm': [10, 10, 2],
      'sourceSha256': 'a' * 64,
      'packageSha256': status == 'ready' ? 'b' * 64 : null,
      'estimatedSeconds': status == 'ready' ? 60 : null,
      'filamentGrams': status == 'ready' ? .25 : null,
      'message': status == 'ready' ? 'Ready for review' : 'Slicing in Orca',
    };

Future<void> mount(WidgetTester tester,
    {double width = 390,
    String? model = 'BL-P001',
    Future<JsonMap> Function(JsonMap)? prepare,
    Future<JsonMap> Function(String)? read,
    Future<void> Function(JsonMap, bool)? download}) async {
  tester.view.physicalSize = Size(width, 1600);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(MaterialApp(
      theme: kTheme(),
      home: Scaffold(
          body: SingleChildScrollView(
              child: BambuTestPrintControls(
                  modelId: model,
                  profiles: profiles,
                  onPrepare: prepare ?? (_) async => job(),
                  onRead: read ?? (_) async => job(),
                  onDownload: download ?? (_, __) async {})))));
  await tester.pumpAndSettle();
}

Future<void> tap(WidgetTester tester, String label) async {
  await tester.ensureVisible(find.text(label));
  await tester.tap(find.text(label));
  await tester.pump();
}

void choose(WidgetTester tester, String label, String value) {
  final field = tester.widget<DropdownButtonFormField<String>>(
      find.byWidgetPredicate((w) =>
          w is DropdownButtonFormField<String> &&
          w.decoration.labelText == label));
  field.onChanged!(value);
}

Future<void> selectProfile(WidgetTester tester) async {
  await tap(tester, 'Small test print');
  choose(tester, 'Test nozzle', '0.4');
  await tester.pump();
  choose(tester, 'Test build plate', 'Textured PEI Plate');
  await tester.pump();
}

void main() {
  for (final width in [320.0, 1000.0]) {
    testWidgets(
        'small test slices unqualified installed profile without a device build at $width',
        (tester) async {
      JsonMap? sent;
      final downloads = <bool>[];
      await mount(tester,
          width: width,
          prepare: (body) async {
            sent = body;
            return job();
          },
          download: (_, source) async => downloads.add(source));
      await tap(tester, 'Small test print');
      expect(find.text('10 × 10 × 2 mm test tile'), findsOneWidget);
      expect(
          tester
              .widget<KButton>(find.widgetWithText(KButton, 'Slice test part'))
              .onPressed,
          isNull);
      choose(tester, 'Test nozzle', '0.4');
      await tester.pump();
      expect(
          tester
              .widget<KButton>(find.widgetWithText(KButton, 'Slice test part'))
              .onPressed,
          isNull);
      choose(tester, 'Test build plate', 'Textured PEI Plate');
      await tester.pump();
      await tap(tester, 'Slice test part');
      await tester.pumpAndSettle();
      expect(sent, {'profileId': 'textured', 'printerModelId': 'BL-P001'});
      expect(find.text('Estimated print time: 1.0 min'), findsOneWidget);
      expect(find.text('Estimated filament: 0.25 g'), findsOneWidget);
      await tap(tester, 'Download sliced test plate');
      await tap(tester, 'Download test STL');
      expect(downloads, [false, true]);
      expect(
          find.textContaining(
              'does not approve the profile or start the printer'),
          findsOneWidget);
      expect(tester.takeException(), isNull);
    });
  }

  testWidgets(
      'polls real preparation callback until sliced result without repeating preparation',
      (tester) async {
    var preparations = 0, reads = 0;
    await mount(tester, prepare: (_) async {
      preparations++;
      return job('slicing');
    }, read: (_) async {
      reads++;
      return job();
    });
    await selectProfile(tester);
    await tap(tester, 'Slice test part');
    await tester.pump();
    expect(find.text('Download sliced test plate'), findsNothing);
    await tester.pump(const Duration(seconds: 2));
    await tester.pump();
    expect(find.text('Download sliced test plate'), findsOneWidget);
    expect(preparations, 1);
    expect(reads, 1);
    await tester.pump(const Duration(seconds: 10));
    expect(reads, 1);
  });

  testWidgets('model change discards an old in-flight slicing response',
      (tester) async {
    final pending = Completer<JsonMap>();
    await mount(tester, prepare: (_) => pending.future);
    await selectProfile(tester);
    await tap(tester, 'Slice test part');
    await mount(tester, model: 'N1');
    pending.complete(job());
    await tester.pumpAndSettle();
    expect(find.text('Download sliced test plate'), findsNothing);
    expect(find.textContaining('No installed Orca'), findsOneWidget);
  });

  testWidgets('failed slicing is visible and never offers a sliced download',
      (tester) async {
    await mount(tester,
        prepare: (_) async =>
            {...job('failed'), 'message': 'Slicer image is unavailable'});
    await selectProfile(tester);
    await tap(tester, 'Slice test part');
    await tester.pumpAndSettle();
    expect(find.text('Slicer image is unavailable'), findsOneWidget);
    expect(find.text('Download sliced test plate'), findsNothing);
  });

  testWidgets('no printer selection cannot prepare a test', (tester) async {
    await mount(tester, model: null);
    await tap(tester, 'Small test print');
    expect(find.text('Select your printer model above.'), findsOneWidget);
    expect(find.text('Slice test part'), findsNothing);
  });

  test('test download is organization scoped and checksum verified', () async {
    final bytes = utf8.encode('fixture sliced plate');
    final checksum = sha256.convert(bytes).toString();
    final client = MockClient((request) async {
      expect(request.url.path,
          '/api/v1/printers/bambu/local/test-prints/job-1/plate');
      expect(request.headers['x-organization-id'], 'org-a');
      return http.Response.bytes(bytes, 200);
    });
    final api =
        ProductionApi(client: client, baseUrl: 'http://localhost/api/v1');
    expect(
        await api.bambuTestPrintBytes('job-1',
            source: false, checksum: checksum, organizationId: 'org-a'),
        bytes);
    await expectLater(
        api.bambuTestPrintBytes('job-1',
            source: false, checksum: 'a' * 64, organizationId: 'org-a'),
        throwsA(isA<ProductionApiException>()));
    client.close();
  });
}
