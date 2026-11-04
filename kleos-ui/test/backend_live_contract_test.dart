// Opt-in: exercises every backend route through the app's own ProductionApi against a RUNNING backend.
//   KLEOS_E2E_API=http://127.0.0.1:8000/api/v1 flutter test test/backend_live_contract_test.dart
// The backend must run in development no-login mode on a disposable database. No model call is made: project
// creation uses autoStart=false and no generation command is sent. Responses are validated with the generated DTOs;
// every route must answer with structured JSON (2xx, or a 4xx/501/503 carrying a code) and never a 5xx.
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:kleos_ui/data/models/api.generated.dart';
import 'package:kleos_ui/data/services/api_routes.dart';
import 'package:kleos_ui/data/services/production_api.dart';

// A real 1x1 PNG.
const png =
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==';

void main() {
  final base = Platform.environment['KLEOS_E2E_API'];
  final skip = base == null
      ? 'Set KLEOS_E2E_API to a running development backend'
      : false;
  late ProductionApi api;
  setUp(
      () => api = ProductionApi(baseUrl: base ?? 'http://127.0.0.1:1/api/v1'));

  /// Calls a route and returns its HTTP status plus the structured error code (null on success).
  Future<(int, String?)> probe(Future<Object?> Function() call) async {
    try {
      await call();
      return (200, null);
    } on ProductionApiException catch (error) {
      return (error.status ?? -1, error.code);
    }
  }

  void expectStructured((int, String?) result, String route) {
    final (status, code) = result;
    expect(status, isNot(-1),
        reason: '$route did not answer with an HTTP response');
    expect(status, lessThan(500).or(anyOf(501, 503)),
        reason: '$route returned $status');
    if (status >= 400) {
      expect(code, isNotNull, reason: '$route error carried no code');
    }
  }

  group('read routes parse with the generated DTOs', () {
    test('session, catalogs and connectors', () async {
      final session = await api.get(ApiRoutes.authSession);
      expect(session['loginRequired'], false);
      await api.get(ApiRoutes.authConfig);
      await api.get(ApiRoutes.authOrganizations);
      ApiModelCatalogView.fromJson(await api.get(ApiRoutes.modelCatalog));
      ApiProjectsView.fromJson(await api.get(ApiRoutes.projects));
      ApiEngineeringToolCatalogView.fromJson(
          await api.get(ApiRoutes.engineeringTools));
      await api.get(ApiRoutes.connectors);
      ApiBambuCatalogView.fromJson(await api.bambuCatalog());
      ApiBambuLocalState.fromJson(await api.bambuLocalState());
      ApiRentAHumanStatusView.fromJson(await api.rentAHumanStatus());
      await api.get(ApiRoutes.authMembers);
      await api.get(ApiRoutes.authInvites);
    }, skip: skip);
  });

  group('project lifecycle through the real command path', () {
    test('create, read every project view, upload an image, delete', () async {
      final key = 'live-${DateTime.now().microsecondsSinceEpoch}';
      Future<Map<String, dynamic>> create() => api.post(
          ApiRoutes.commands,
          {
            'type': 'project.create',
            'payload': {
              'brief': 'Backend connection check; no generation',
              'autoStart': false
            },
          },
          idempotencyKey: key);
      final accepted = ApiCommandAccepted.fromJson(await create()).toJson();
      final id = accepted['projectId'] as String;
      expect((await create())['projectId'], id, reason: 'idempotent replay');

      final workspace = ApiWorkspaceView.fromJson(
              await api.get(ApiRoutes.workspace(id, includeAgentState: true)))
          .toJson();
      expect(workspace['revision'], isA<num>());
      ApiCircuitSimulationView.fromJson(await api.circuitSimulation(id));
      await api.engineeringPreview(id);
      ApiHumanTaskDraftsView.fromJson(await api.humanTaskDrafts(id));
      ApiPrintPreparationsView.fromJson(await api.printPreparations(id));
      ApiBambuDispatchesView.fromJson(await api.bambuDispatches(id));

      final stream = HttpClient();
      final events = await stream
          .getUrl(Uri.parse('$base${ApiRoutes.projectEvents(id, 0)}'));
      final response = await events.close();
      expect(response.statusCode, 200);
      expect(response.headers.contentType?.mimeType, 'text/event-stream');
      stream.close(force: true);

      final image = ApiReferenceImageView.fromJson(await api.post(
              ApiRoutes.referenceImages(id),
              ApiReferenceImageUpload(
                      name: 'pixel.png',
                      mediaType: 'image/png',
                      contentBase64: png)
                  .toJson()))
          .toJson();
      final imageId = image['id'] as String?;
      expect(imageId, isNotNull,
          reason: 'the upload response must identify the stored image');
      // The backend re-encodes reference images to strip metadata, so only validity is asserted, not equality.
      final stored = await api.referenceImageBytes(id, imageId!);
      expect(stored.sublist(0, 8), [137, 80, 78, 71, 13, 10, 26, 10],
          reason: 'a valid PNG signature');

      expectStructured(
          await probe(() =>
              api.get(ApiRoutes.run('00000000-0000-0000-0000-000000000000'))),
          'run');
      expectStructured(
          await probe(() => api.get(ApiRoutes.nativeJobLogs('missing'))),
          'native job logs');
      expectStructured(
          await probe(() => api.get(ApiRoutes.invocationTranscript('missing'))),
          'transcript');
      expectStructured(
          await probe(() => api.artifactBytes('missing')), 'artifact download');
      expectStructured(
          await probe(() => api.releaseBytes('missing')), 'release download');

      // The upload advanced the revision; deletion is fenced on the current one.
      final current =
          ApiWorkspaceView.fromJson(await api.get(ApiRoutes.workspace(id)))
              .toJson();
      final deleted = await api.delete(ApiRoutes.projectDelete(id,
          expectedRevision: (current['revision'] as num).toInt()));
      expect(deleted, isA<Map<String, dynamic>>());
      final (status, _) = await probe(() => api.get(ApiRoutes.workspace(id)));
      expect(status, anyOf(404, 410),
          reason: 'a deleted project must not be readable');
    }, skip: skip);
  });

  group('configuration-dependent and write routes answer with structured JSON',
      () {
    test('printers, human work, preparation, dispatch and companion preview',
        () async {
      final key = 'live-w-${DateTime.now().microsecondsSinceEpoch}';
      final created = await api.post(
          ApiRoutes.commands,
          {
            'type': 'project.create',
            'payload': <String, dynamic>{
              'brief': 'Route probe project',
              'autoStart': false
            }
          },
          idempotencyKey: key);
      final id = created['projectId'] as String;
      final Map<String, Future<Object?> Function()> probes = {
        'rentahuman humans': () => api.rentAHumanSearch({'skill': 'assembly'}),
        'rentahuman bounties': () => api.rentAHumanBounties(),
        'bambu discover': () => api.discoverLocalBambu(),
        'bambu refresh': () => api.refreshLocalBambu(),
        'bambu forget': () => api.forgetLocalBambu(),
        'bambu certificate': () =>
            api.post(ApiRoutes.bambuCertificate, {'address': '192.0.2.1'}),
        'bambu connect': () =>
            api.post(ApiRoutes.bambuConnect, {'address': '192.0.2.1'}),
        'bambu test print': () => api.post(ApiRoutes.bambuTestPrints, {}),
        'bambu test print read': () =>
            api.get(ApiRoutes.bambuTestPrint('missing')),
        'draft create': () => api.post(ApiRoutes.humanTaskDrafts(id), {}),
        'draft preview': () => api
            .post(ApiRoutes.humanTaskDraftAction(id, 'missing', 'preview'), {}),
        'draft link': () =>
            api.post(ApiRoutes.humanTaskDraftAction(id, 'missing', 'link'), {}),
        'draft sync': () =>
            api.post(ApiRoutes.humanTaskDraftAction(id, 'missing', 'sync'), {}),
        'preparation create': () =>
            api.post(ApiRoutes.printPreparations(id), {}),
        'dispatch create': () => api.post(ApiRoutes.printDispatches(id), {}),
        'dispatch send': () =>
            api.post(ApiRoutes.printDispatchAction(id, 'missing', 'send'), {}),
        'dispatch cancel': () => api
            .post(ApiRoutes.printDispatchAction(id, 'missing', 'cancel'), {}),
        'dispatch reconcile': () => api.post(
            ApiRoutes.printDispatchAction(id, 'missing', 'reconcile'), {}),
        'companion preview': () =>
            api.companionPreview(id, artifactId: 'missing'),
        'invite revoke': () => api.delete(ApiRoutes.authInvite('missing')),
        'member role': () =>
            api.post(ApiRoutes.authMemberRole('missing'), {'role': 'viewer'}),
        'member reset': () =>
            api.post(ApiRoutes.authMemberResetPassword('missing'), {}),
        'member remove': () => api.delete(ApiRoutes.authMember('missing')),
        'bad command': () => api.post(ApiRoutes.commands,
            {'type': 'project.doesNotExist', 'payload': <String, dynamic>{}}),
      };
      for (final entry in probes.entries) {
        expectStructured(await probe(entry.value), entry.key);
      }
    }, skip: skip);
  });
}

extension on Matcher {
  Matcher or(Matcher other) => anyOf(this, other);
}
