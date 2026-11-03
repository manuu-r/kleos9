import 'dart:convert';
import 'dart:typed_data';
import 'package:file_selector/file_selector.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:kleos_ui/data/models/json.dart';
import 'package:kleos_ui/data/models/production.dart';
import 'package:kleos_ui/data/repositories/production_repository.dart';
import 'package:kleos_ui/data/services/command_journal.dart';
import 'package:kleos_ui/data/services/production_api.dart';
import 'package:kleos_ui/data/services/api_service.dart';
import 'package:kleos_ui/ui/features/home_pages.dart';
import 'package:kleos_ui/data/services/reference_images.dart';
import 'package:kleos_ui/state/kleos_store.dart';
import 'package:kleos_ui/state/production_store.dart';
import 'package:kleos_ui/ui/features/project/planning_conversation.dart';
import 'package:kleos_ui/ui/features/production/production_app.dart';
import 'package:kleos_ui/ui/core/reference_image_drop_target.dart';
import 'production_ui_test.dart' as fixture;

ReferenceImageDraft image() => ReferenceImageDraft(
    name: 'sketch.png',
    mediaType: 'image/png',
    bytes: base64Decode(
        'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a3ioAAAAASUVORK5CYII='));

class ImageRepository extends fixture.TestRepository {
  ImageRepository() : super(fixture.snapshot());
  final order = <String>[];
  bool failUpload = true;
  @override
  Future<AcceptedCommand> command(String type, JsonMap payload,
      {int? revision}) {
    order.add(type);
    return super.command(type, payload, revision: revision);
  }

  @override
  Future<JsonMap> uploadReferenceImage(String projectId,
      {required String name,
      required String mediaType,
      required String contentBase64}) async {
    order.add('upload');
    if (failUpload) throw const ProductionApiException('Upload failed');
    return {'id': 'image-one'};
  }
}

void main() {
  test(
      'picker and drop validation reject unsupported, oversized and excess files',
      () async {
    final valid =
        XFile.fromData(image().bytes, name: 'sketch.png', path: 'sketch.png');
    expect((await readReferenceImages([valid])).single.mediaType, 'image/png');
    await expectLater(
        readReferenceImages(List.filled(5, valid)), throwsFormatException);
    await expectLater(
        readReferenceImages(
            [XFile.fromData(Uint8List(1), name: 'note.txt', path: 'note.txt')]),
        throwsFormatException);
    await expectLater(
        readReferenceImages([
          XFile.fromData(Uint8List(6 * 1024 * 1024 + 1),
              name: 'large.png', path: 'large.png')
        ]),
        throwsFormatException);
  });

  testWidgets('drop into chat input previews and sends image-only request',
      (tester) async {
    final sent = <List<String>>[];
    await tester.pumpWidget(ProviderScope(
        child: MaterialApp(
            home: Scaffold(
      body: SingleChildScrollView(
          child: PlanningConversation(
        messages: const [],
        canWrite: true,
        busy: false,
        onSend: (_, __) async => const CommandResult(ok: true),
        onUploadImage: (_) async => 'dropped-image',
        onSendWithReferences: (text, _, ids) async {
          expect(text, contains('images'));
          sent.add(ids);
          return const CommandResult(ok: true);
        },
      )),
    ))));
    expect(
        find.ancestor(
            of: find.byKey(const ValueKey('planning-chat-input')),
            matching: find.byType(ReferenceImageDropTarget)),
        findsOneWidget);
    final target = tester.widget<ReferenceImageDropTarget>(
        find.byType(ReferenceImageDropTarget));
    await tester.runAsync(() => target.onDrop([
          XFile.fromData(image().bytes, name: 'sketch.png', path: 'sketch.png')
        ]));
    await tester.pumpAndSettle();
    expect(find.byTooltip('Remove sketch.png'), findsOneWidget);
    await tester
        .ensureVisible(find.byKey(const ValueKey('planning-chat-send')));
    await tester.tap(find.byKey(const ValueKey('planning-chat-send')));
    await tester.pumpAndSettle();
    expect(sent, [
      ['dropped-image']
    ]);
    expect(find.byTooltip('Remove sketch.png'), findsNothing);
  });

  test('reference upload is project scoped and retries reuse uploaded identity',
      () async {
    final requests = <http.Request>[];
    final api = ProductionApi(
        baseUrl: 'https://fixture.test/api/v1',
        client: MockClient((request) async {
          requests.add(request);
          return http.Response('{"id":"image-one"}', 200);
        }));
    final repo = ProductionRepository(
        api,
        DurableCommandJournal('images',
            read: (_) async => null, write: (_, __) async {}),
        organizationId: 'org-a');
    final draft = image();
    expect(await draft.upload(repo, 'project-one'), 'image-one');
    expect(await draft.upload(repo, 'project-one'), 'image-one');
    expect(requests, hasLength(1));
    expect(requests.single.url.path,
        '/api/v1/projects/project-one/reference-images');
    expect(requests.single.headers['X-Organization-Id'], 'org-a');
    expect(jsonDecode(requests.single.body)['contentBase64'],
        base64Encode(draft.bytes));
    api.close();
  });

  test('history image content keeps authentication and organization scope',
      () async {
    final api = ProductionApi(
        baseUrl: 'https://fixture.test/api/v1',
        client: MockClient((request) async {
          expect(request.url.path,
              '/api/v1/projects/project-one/reference-images/image-one/content');
          expect(request.headers['Authorization'], 'Bearer fixture-token');
          expect(request.headers['X-Organization-Id'], 'org-a');
          return http.Response.bytes(image().bytes, 200);
        }))
      ..accessToken = () async => 'fixture-token';
    expect(
        await api.referenceImageBytes('project-one', 'image-one',
            organizationId: 'org-a'),
        image().bytes);
    api.close();
  });

  for (final width in [320.0, 1200.0]) {
    testWidgets(
        'chat attaches/removes and retries images with selected part cue at $width',
        (tester) async {
      tester.view.physicalSize = Size(width, 1200);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.reset);
      var fail = true;
      final sent = <List<String>>[];
      await tester.pumpWidget(ProviderScope(
          overrides: [
            referenceImagePickerProvider
                .overrideWithValue(() async => [image()])
          ],
          child: MaterialApp(
              home: Scaffold(
                  body: SingleChildScrollView(
                      child: PlanningConversation(
            messages: const [],
            canWrite: true,
            busy: false,
            selectedPartId: 'case',
            selectedPartLabel: 'Case',
            onSend: (_, __) async => const CommandResult(ok: true),
            onUploadImage: (_) async {
              if (fail) throw Exception('offline');
              return 'image-one';
            },
            onSendWithReferences: (_, __, ids) async {
              sent.add(ids);
              return const CommandResult(ok: true);
            },
          ))))));
      expect(find.text('Redo Case'), findsOneWidget);
      await tester.tap(find.byKey(const ValueKey('attach-reference-images')));
      await tester.pumpAndSettle();
      expect(find.byTooltip('Remove sketch.png'), findsOneWidget);
      await tester.tap(find.byTooltip('Remove sketch.png'));
      await tester.pumpAndSettle();
      expect(find.byTooltip('Remove sketch.png'), findsNothing);
      await tester.tap(find.byKey(const ValueKey('attach-reference-images')));
      await tester.pumpAndSettle();
      await tester
          .ensureVisible(find.byKey(const ValueKey('planning-chat-send')));
      await tester.tap(find.byKey(const ValueKey('planning-chat-send')));
      await tester.pumpAndSettle();
      expect(sent, isEmpty);
      expect(find.byTooltip('Remove sketch.png'), findsOneWidget);
      fail = false;
      await tester.tap(find.byKey(const ValueKey('planning-chat-send')));
      await tester.pumpAndSettle();
      expect(sent, [
        ['image-one']
      ]);
      expect(find.byTooltip('Remove sketch.png'), findsNothing);
      expect(tester.takeException(), isNull);
    });
  }

  testWidgets(
      'new project uploads before planning and retry does not recreate it',
      (tester) async {
    tester.view.physicalSize = const Size(1200, 2200);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    final repo = ImageRepository();
    final router = GoRouter(routes: [
      GoRoute(
          path: '/',
          builder: (_, __) => const Scaffold(body: ProductionNewProjectPage())),
      GoRoute(
          path: '/projects/:id',
          builder: (_, __) => const Scaffold(body: Text('Created workspace'))),
    ]);
    await tester.pumpWidget(ProviderScope(overrides: [
      productionRepositoryProvider.overrideWithValue(repo),
      modelCatalogProvider.overrideWith((_) async => fixture.testCatalog),
      referenceImagePickerProvider.overrideWithValue(() async => [image()]),
    ], child: MaterialApp.router(routerConfig: router)));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('attach-reference-images')));
    await tester.pumpAndSettle();
    final create = find.text('Create project');
    await tester.ensureVisible(create);
    await tester.tap(create);
    await tester.pumpAndSettle();
    expect(repo.order, ['project.create', 'upload']);
    expect(asMap(repo.commands.first['payload'])['autoStart'], false);
    expect(asMap(repo.commands.first['payload'])['brief'],
        contains('reference images'));
    repo.failUpload = false;
    await tester.ensureVisible(create);
    await tester.tap(create);
    await tester.pumpAndSettle();
    expect(
        repo.order, ['project.create', 'upload', 'upload', 'project.capture']);
    expect(asMap(repo.commands.last['payload'])['referenceImageIds'],
        ['image-one']);
    expect(asMap(repo.commands.last['payload'])['autoResume'], true);
    expect(find.text('Created workspace'), findsOneWidget);
    await tester.pumpWidget(const SizedBox());
    router.dispose();
    repo.dispose();
  });
  testWidgets(
      'original new-build composer supports image-only planning at phone width',
      (tester) async {
    tester.view.physicalSize = const Size(320, 1400);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    final repo = ImageRepository()..failUpload = false;
    final creates = <JsonMap>[];
    final snapshot = {
      'revision': 1,
      'state': {
        'production': true,
        'projects': <Object>[],
        'connectors': <Object>[]
      }
    };
    final api = ApiService(client: MockClient((request) async {
      if (request.method == 'POST') {
        creates.add(asMap(jsonDecode(request.body)));
      }
      return http.Response(
          jsonEncode(request.method == 'POST'
              ? {
                  'snapshot': snapshot,
                  'result': {'projectId': 'p'}
                }
              : snapshot),
          200);
    }));
    final container = ProviderContainer(overrides: [
      apiServiceProvider.overrideWithValue(api),
      pollingEnabledProvider.overrideWithValue(false),
      productionRepositoryProvider.overrideWithValue(repo),
      referenceImagePickerProvider.overrideWithValue(() async => [image()]),
    ]);
    container.listen(kleosProvider, (_, __) {});
    await container.read(kleosProvider).refresh();
    final router = GoRouter(routes: [
      GoRoute(
          path: '/',
          builder: (_, __) => Scaffold(
              body: SingleChildScrollView(
                  child: NewBuildPage(
                      controller: container.read(kleosProvider))))),
      GoRoute(
          path: '/projects/:id',
          builder: (_, __) => const Scaffold(body: Text('Image build ready'))),
    ]);
    await tester.pumpWidget(UncontrolledProviderScope(
        container: container, child: MaterialApp.router(routerConfig: router)));
    await tester.pumpAndSettle();
    await tester
        .ensureVisible(find.byKey(const ValueKey('attach-reference-images')));
    await tester.tap(find.byKey(const ValueKey('attach-reference-images')));
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.text('Start build'));
    await tester.tap(find.text('Start build'));
    await tester.pumpAndSettle();
    expect(creates, hasLength(1));
    expect(asMap(creates.single['payload'])['autoStart'], false);
    expect(repo.order, ['upload', 'project.capture']);
    expect(find.text('Image build ready'), findsOneWidget);
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox());
    router.dispose();
    container.dispose();
    repo.dispose();
    api.close();
  });
}
