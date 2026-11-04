import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:kleos_ui/ui/features/project/bambu_print.dart';
import 'package:kleos_ui/data/models/json.dart';

JsonMap fixture() => {
      'catalog': {
        'localPrintingEnabled': true,
        'models': <JsonMap>[],
        'profiles': <JsonMap>[]
      },
      'workspace': {
        'project': {
          'id': 'p',
          'engineeringRevision': 1,
          'acceptedReleaseId': 'r'
        },
        'artifacts': <JsonMap>[]
      },
      'local': {
        'modelId': 'N1',
        'connected': true,
        'directPrinting': true,
        'serial': 'PRINTER123',
        'firmwareVersion': '1.2'
      },
      'preparations': {
        'preparations': [
          {
            'id': 'prep',
            'status': 'ready_for_operator',
            'engineeringRevision': 1,
            'preparedSha256': 'a' * 64,
            'sourceArtifactId': 'source',
            'automaticNativePrepared': true,
            'profileId': 'orca-pla',
            'profile': {
              'printerModelId': 'N1',
              'material': 'PLA',
              'filamentPreset': 'Bambu PLA Basic',
              'bedType': 'Textured PEI Plate',
              'nozzleMm': 0.4
            }
          }
        ]
      }
    };
void main() {
  for (final width in [320.0, 1000.0]) {
    testWidgets(
        'review requires spool consent and uncertain send cannot repeat at $width',
        (tester) async {
      tester.view.physicalSize = Size(width, 1800);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.reset);
      var creates = 0, sends = 0;
      String? key;
      await tester.pumpWidget(MaterialApp(
          home: Scaffold(
              body: SingleChildScrollView(
                  child: BambuPrintControls(
                      data: fixture(),
                      onPrepare: (_, __) async => {},
                      onDownload: (_) async {},
                      onCreateDispatch: (body, idempotency) async {
                        creates++;
                        key = idempotency;
                        expect(body['authorizePrint'], isTrue);
                        expect(body['externalSpoolConfirmed'], isTrue);
                        return {
                          'id': 'd',
                          'status': 'authorized',
                          'preparationId': 'prep'
                        };
                      },
                      onDispatchAction: (_, action, __) async {
                        sends++;
                        expect(action, 'send');
                        throw StateError('lost ack');
                      })))));
      await tester.tap(find.text('Upload and start print'));
      await tester.pumpAndSettle();
      expect(tester.widget<FilledButton>(find.byType(FilledButton)).onPressed,
          isNull);
      expect(find.textContaining('Firmware 1.2'), findsOneWidget);
      expect(find.textContaining('Bambu PLA Basic (PLA)'), findsWidgets);
      expect(find.textContaining('Build plate: Textured PEI Plate'), findsWidgets);
      tester
          .widget<CheckboxListTile>(find.byType(CheckboxListTile))
          .onChanged!(true);
      await tester.pump();
      await tester.tap(find.text('Upload and start'));
      await tester.pumpAndSettle();
      expect(creates, 1);
      expect(sends, 1);
      expect(key, startsWith('print-'));
      expect(find.text('Upload and start print'), findsNothing);
      expect(find.textContaining('will not be resent'), findsOneWidget);
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox());
    });
  }
  testWidgets('unqualified prepared source never exposes direct start',
      (tester) async {
    final data = fixture();
    (data['preparations']['preparations'][0]
        as Map)['automaticNativePrepared'] = false;
    await tester.pumpWidget(MaterialApp(
        home: Scaffold(
            body: SingleChildScrollView(
                child: BambuPrintControls(
                    data: data,
                    onPrepare: (_, __) async => {},
                    onDownload: (_) async {},
                    onCreateDispatch: (_, __) async => {},
                    onDispatchAction: (_, __, ___) async => {})))));
    expect(find.text('Upload and start print'), findsNothing);
    await tester.pumpWidget(const SizedBox());
  });
  testWidgets(
      'leaving project while authorization is pending never sends print',
      (tester) async {
    final pending = Completer<JsonMap>();
    var sends = 0;
    await tester.pumpWidget(MaterialApp(
        home: Scaffold(
            body: SingleChildScrollView(
                child: BambuPrintControls(
                    data: fixture(),
                    onPrepare: (_, __) async => {},
                    onDownload: (_) async {},
                    onCreateDispatch: (_, __) => pending.future,
                    onDispatchAction: (_, __, ___) async {
                      sends++;
                      return {};
                    })))));
    await tester.ensureVisible(find.text('Upload and start print'));
    await tester.tap(find.text('Upload and start print'));
    await tester.pumpAndSettle();
    tester
        .widget<CheckboxListTile>(find.byType(CheckboxListTile))
        .onChanged!(true);
    await tester.pump();
    await tester.tap(find.text('Upload and start'));
    await tester.pump();
    await tester.pumpWidget(const SizedBox());
    pending.complete({'id': 'old-project-dispatch', 'status': 'authorized'});
    await tester.pump();
    expect(sends, 0);
    expect(tester.takeException(), isNull);
  });
  testWidgets(
      'reconciliation requires observed idle and records only chosen outcome',
      (tester) async {
    final data = fixture();
    data['dispatches'] = {
      'dispatches': [
        {'id': 'd', 'preparationId': 'prep', 'status': 'start_requested'}
      ]
    };
    Map<String, dynamic>? sent;
    await tester.pumpWidget(MaterialApp(
        home: Scaffold(
            body: SingleChildScrollView(
                child: BambuPrintControls(
                    data: data,
                    onPrepare: (_, __) async => {},
                    onDownload: (_) async {},
                    onDispatchAction: (id, action, body) async {
                      expect(id, 'd');
                      expect(action, 'reconcile');
                      sent = body;
                      return {'id': id, 'status': 'reconciled_not_started'};
                    })))));
    expect(find.textContaining('Printing has not yet been confirmed'),
        findsOneWidget);
    await tester.ensureVisible(find.text('Confirm printer outcome'));
    await tester.tap(find.text('Confirm printer outcome'));
    await tester.pumpAndSettle();
    expect(tester.widget<FilledButton>(find.byType(FilledButton)).onPressed,
        isNull);
    tester
        .widget<CheckboxListTile>(find.byType(CheckboxListTile))
        .onChanged!(true);
    await tester.pump();
    await tester.tap(find.text('Record outcome'));
    await tester.pumpAndSettle();
    expect(sent, {'confirmPrinterIdle': true, 'outcome': 'not_started'});
    expect(find.text('Print: reconciled not started'), findsOneWidget);
    await tester.pumpWidget(const SizedBox());
  });
  for (final model in ['N1', 'N2S', 'C12', 'BL-P001']) {
    testWidgets(
        'exact $model profile enables review; cross-model and disabled catalog do not',
        (tester) async {
      final data = fixture();
      (data['local'] as Map)['modelId'] = model;
      final job = data['preparations']['preparations'][0] as Map;
      (job['profile'] as Map)['printerModelId'] = model;
      (data['catalog'] as Map)['directPrintModelIds'] = [model];
      Future<void> mount() => tester.pumpWidget(MaterialApp(
          home: Scaffold(
              body: SingleChildScrollView(
                  child: BambuPrintControls(
                      data: data,
                      onPrepare: (_, __) async => {},
                      onDownload: (_) async {},
                      onCreateDispatch: (_, __) async => {},
                      onDispatchAction: (_, __, ___) async => {})))));
      await mount();
      expect(find.text('Upload and start print'), findsOneWidget);
      (job['profile'] as Map)['qualified'] = false;
      await mount();
      expect(find.text('Upload and start print'), findsNothing);
      (job['profile'] as Map)['qualified'] = true;
      (data['catalog'] as Map).remove('directPrintModelIds');
      await mount();
      expect(find.text('Upload and start print'), findsOneWidget);
      (data['catalog'] as Map)['directPrintModelIds'] = [model];
      (job['profile'] as Map)['printerModelId'] = 'unmatched';
      await mount();
      expect(find.text('Upload and start print'), findsNothing);
      (job['profile'] as Map)['printerModelId'] = model;
      (data['catalog'] as Map)['directPrintModelIds'] = <String>[];
      await mount();
      expect(find.text('Upload and start print'), findsNothing);
      await tester.pumpWidget(const SizedBox());
    });
  }
}
