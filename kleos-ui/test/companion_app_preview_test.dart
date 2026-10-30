import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:kleos_ui/data/models/json.dart';
import 'package:kleos_ui/data/services/production_api.dart';
import 'package:kleos_ui/ui/core/widgets.dart';
import 'package:kleos_ui/ui/features/project/companion_app_preview.dart';
import 'package:kleos_ui/ui/features/project/engineering_preview.dart';

const sha = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa';
const request =
    (projectId: 'project', artifactId: 'compiled-build', sha256: sha);
JsonMap _leaseData() => {
      'url': 'https://preview.example/p/build-ticket/index.html',
      'expiresAt':
          DateTime.now().add(const Duration(minutes: 15)).toIso8601String(),
      'artifactId': 'compiled-build',
      'sha256': sha,
      'mode': 'isolated-web-build',
    };
CompanionPreviewLease _lease({DateTime? expiry}) => CompanionPreviewLease(
    url: Uri.parse('https://preview.example/p/build-ticket/index.html'),
    expiresAt: expiry ?? DateTime.now().add(const Duration(minutes: 15)),
    artifactId: request.artifactId,
    sha256: sha);

Future<void> _mount(WidgetTester tester, Widget child) async {
  tester.view.physicalSize = const Size(320, 900);
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
  test('only a pinned compiled build on a separate origin can be embedded', () {
    CompanionPreviewLease read(JsonMap data) =>
        CompanionPreviewLease.fromJson(data, request,
            apiOrigin: Uri.parse('https://api.example/api/v1'),
            appOrigin: Uri.parse('https://kleos.example'));
    expect(read(_leaseData()).artifactId, 'compiled-build');
    for (final change in <JsonMap>[
      {'url': 'javascript:alert(1)'},
      {'url': 'https://kleos.example/unsafe'},
      {'url': 'https://api.example/unsafe'},
      {'url': 'https://user:password@preview.example/app'},
      {'mode': 'html-source'},
      {'artifactId': 'different-build'},
      {
        'sha256':
            'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'
      },
    ]) {
      expect(() => read({..._leaseData(), ...change}),
          throwsA(isA<ProductionApiException>()));
    }
  });

  testWidgets(
      'built App tab mints lazily and never displays a sketch as the build',
      (tester) async {
    var mints = 0;
    final data = <String, dynamic>{
      'mechanics': {'envelope': <String, dynamic>{}},
      'application': {
        'source': 'artifact',
        'status': 'candidate',
        'previewAvailable': true,
        'previewArtifactId': 'compiled-build',
        'previewSha256': sha,
        'app': {'title': 'Sketch should stay hidden', 'widgets': <Object>[]},
      },
    };
    await _mount(
        tester,
        ProviderScope(overrides: [
          companionPreviewProvider(request).overrideWith((ref) async {
            mints++;
            return _lease();
          }),
          companionFrameBuilderProvider.overrideWithValue(
              (url, onLoaded, onError) => const SizedBox.expand(
                  key: ValueKey('isolated-test-frame'),
                  child: Center(child: Text('Actual compiled app')))),
        ], child: EngineeringPreviewPanel(projectId: 'project', data: data)));
    expect(mints, 0);
    await tester.tap(find.byKey(const ValueKey('preview-tab-application')));
    await tester.pumpAndSettle();
    expect(mints, 1);
    expect(find.text('Built companion app'), findsOneWidget);
    expect(find.text('Actual file'), findsOneWidget);
    expect(find.byKey(const ValueKey('isolated-test-frame')), findsOneWidget);
    expect(find.text('Sketch should stay hidden'), findsNothing);
    await tester.pump();
    expect(mints, 1);
    expect(tester.takeException(), isNull);
  });

  testWidgets(
      'expired build tickets stop the frame and refresh only on request',
      (tester) async {
    var mints = 0;
    await _mount(
        tester,
        ProviderScope(
            overrides: [
              companionPreviewProvider(request).overrideWith((ref) async {
                mints++;
                return _lease(
                    expiry: mints == 1
                        ? DateTime.now().add(const Duration(seconds: 1))
                        : null);
              }),
              companionFrameBuilderProvider.overrideWithValue(
                  (url, onLoaded, onError) => const SizedBox(
                      key: ValueKey('isolated-test-frame'), height: 200)),
            ],
            child: const SizedBox(
                height: 300,
                child: CompanionAppPreview(
                    projectId: 'project',
                    artifactId: 'compiled-build',
                    sha256: sha))));
    expect(find.byKey(const ValueKey('isolated-test-frame')), findsOneWidget);
    await tester.pump(const Duration(seconds: 2));
    expect(find.byKey(const ValueKey('isolated-test-frame')), findsNothing);
    expect(find.text('This preview link has expired.'), findsOneWidget);
    expect(mints, 1);
    await tester.tap(find.text('Refresh preview link'));
    await tester.pumpAndSettle();
    expect(mints, 2);
    expect(find.byKey(const ValueKey('isolated-test-frame')), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  test('mint API sends credentials only to the authorized backend', () async {
    late http.Request sent;
    final api = ProductionApi(
        baseUrl: 'https://api.example/api/v1',
        client: MockClient((r) async {
          sent = r;
          return http.Response(jsonEncode(_leaseData()), 200,
              headers: {'content-type': 'application/json'});
        }));
    api.accessToken = () async => 'backend-session';
    api.csrfToken = 'csrf';
    addTearDown(api.close);
    await api.companionPreview('project',
        artifactId: 'compiled-build', organizationId: 'org');
    expect(sent.method, 'POST');
    expect(sent.url.path, '/api/v1/projects/project/companion-preview');
    expect(jsonDecode(sent.body), {'artifactId': 'compiled-build'});
    expect(sent.headers['Authorization'], 'Bearer backend-session');
    expect(sent.headers['X-CSRF-Token'], 'csrf');
  });
}
