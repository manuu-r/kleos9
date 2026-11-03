import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';
import 'package:crypto/crypto.dart';
import 'package:flutter/foundation.dart';
import 'package:http/http.dart' as http;
import '../models/json.dart';
import '../models/api.generated.dart';
import '../models/production.dart';
import 'platform/http_client.dart';

class ProductionApiException implements Exception {
  const ProductionApiException(this.message, {this.status, this.code});
  final String message;
  final int? status;
  final String? code;
  @override
  String toString() => message;
}

class ProductionApi {
  static const _printerCheckInterrupted =
      'Printer check interrupted. Refresh status to check whether it connected, or try again.';

  ProductionApi(
      {http.Client? client,
      String? baseUrl,
      http.Client Function()? streamClientFactory})
      : client = client ?? createClient(),
        streamClientFactory = streamClientFactory ?? createClient,
        baseUrl = (baseUrl ?? defaultBaseUrl()).replaceAll(RegExp(r'/$'), '');

  final http.Client client;
  final http.Client Function() streamClientFactory;
  final String baseUrl;
  Future<String?> Function()? accessToken;
  String? csrfToken;
  String? tenantId;

  static String defaultBaseUrl() {
    const configured = String.fromEnvironment('API_BASE_URL');
    if (configured.isNotEmpty) return configured;
    if (kIsWeb && Uri.base.scheme == 'https') {
      return Uri.base.resolve('/api/v1').toString();
    }
    final host = !kIsWeb && defaultTargetPlatform == TargetPlatform.android
        ? '10.0.2.2'
        : 'localhost';
    return 'http://$host:8000/api/v1';
  }

  Uri uri(String path) => Uri.parse('$baseUrl$path');

  Future<Map<String, String>> headers({String? organizationId}) async {
    final selectedOrganization = organizationId ?? tenantId;
    final sessionCsrf = csrfToken;
    final token = await accessToken?.call();
    return {
      'Accept': 'application/json',
      if (token != null && token.isNotEmpty) 'Authorization': 'Bearer $token',
      if (sessionCsrf != null) 'X-CSRF-Token': sessionCsrf,
      if (selectedOrganization != null)
        'X-Organization-Id': selectedOrganization,
    };
  }

  Future<JsonMap> get(String path, {String? organizationId}) =>
      _request('GET', path, organizationId: organizationId);
  Future<JsonMap> engineeringPreview(String projectId,
          {String? organizationId}) =>
      get('/projects/${Uri.encodeComponent(projectId)}/engineering-preview',
          organizationId: organizationId);
  Future<JsonMap> circuitSimulation(String projectId,
          {String? organizationId}) async =>
      ApiCircuitSimulationView.fromJson(await get(
              '/projects/${Uri.encodeComponent(projectId)}/circuit-simulation',
              organizationId: organizationId))
          .toJson();
  Future<JsonMap> bambuCatalog({String? organizationId}) async =>
      ApiBambuCatalogView.fromJson(await get('/printers/bambu/catalog',
              organizationId: organizationId))
          .toJson();
  Future<JsonMap> rentAHumanStatus({String? organizationId}) async =>
      ApiRentAHumanStatusView.fromJson(
              await get('/rentahuman/status', organizationId: organizationId))
          .toJson();
  Future<JsonMap> bambuLocalState({String? organizationId}) async =>
      ApiBambuLocalState.fromJson(await get('/printers/bambu/local',
              organizationId: organizationId))
          .toJson();
  Future<JsonMap> discoverLocalBambu({String? organizationId}) async =>
      ApiBambuDiscoveryView.fromJson(await post(
              '/printers/bambu/local/discover', const {},
              organizationId: organizationId))
          .toJson();

  Future<JsonMap> bambuCertificate(JsonMap body,
          {String? organizationId}) async =>
      ApiBambuCertificateView.fromJson(await post(
              '/printers/bambu/local/certificate',
              ApiBambuAddressRequest.fromJson(body).toJson(),
              organizationId: organizationId))
          .toJson();
  Future<JsonMap> connectLocalBambu(JsonMap body,
          {String? organizationId}) async =>
      ApiBambuLocalState.fromJson(await _request(
              'POST', '/printers/bambu/local/connect',
              body: ApiBambuConnectionRequest.fromJson(body).toJson(),
              organizationId: organizationId,
              timeout: const Duration(seconds: 60),
              interruptionMessage: _printerCheckInterrupted))
          .toJson();
  Future<JsonMap> refreshLocalBambu({String? organizationId}) async =>
      ApiBambuLocalState.fromJson(await _request(
              'POST', '/printers/bambu/local/refresh',
              body: const {},
              organizationId: organizationId,
              timeout: const Duration(seconds: 60),
              interruptionMessage: _printerCheckInterrupted))
          .toJson();
  Future<JsonMap> forgetLocalBambu({String? organizationId}) async =>
      ApiBambuLocalState.fromJson(await delete('/printers/bambu/local',
              organizationId: organizationId))
          .toJson();

  Future<JsonMap> prepareBambuTestPrint(JsonMap body,
          {String? organizationId}) async =>
      ApiBambuTestPrintView.fromJson(await _request(
              'POST', '/printers/bambu/local/test-prints',
              body: ApiBambuTestPrintRequest.fromJson(body).toJson(),
              organizationId: organizationId,
              interruptionMessage:
                  'The test slicing request was interrupted. Check the local backend and try again.'))
          .toJson();

  Future<JsonMap> bambuTestPrint(String id, {String? organizationId}) async =>
      ApiBambuTestPrintView.fromJson(await get(
              '/printers/bambu/local/test-prints/${Uri.encodeComponent(id)}',
              organizationId: organizationId))
          .toJson();

  Future<Uint8List> bambuTestPrintBytes(String id,
      {required bool source,
      required String checksum,
      String? organizationId}) async {
    if (!RegExp(r'^[a-f0-9]{64}$').hasMatch(checksum)) {
      throw const ProductionApiException(
          'The test file has no verified checksum.');
    }
    final request = http.Request('GET',
        uri('/printers/bambu/local/test-prints/${Uri.encodeComponent(id)}/${source ? 'source' : 'plate'}'));
    request.headers.addAll(await headers(organizationId: organizationId));
    final response =
        await client.send(request).timeout(const Duration(seconds: 30));
    if (response.statusCode != 200) {
      throw ProductionApiException(
          'The test file is unavailable. Prepare it again.',
          status: response.statusCode);
    }
    const maxBytes = 16 * 1024 * 1024;
    final bytes = BytesBuilder(copy: false);
    await for (final chunk
        in response.stream.timeout(const Duration(seconds: 30))) {
      if (bytes.length + chunk.length > maxBytes) {
        throw const ProductionApiException(
            'The test file exceeds its download limit.');
      }
      bytes.add(chunk);
    }
    final result = bytes.takeBytes();
    if (sha256.convert(result).toString() != checksum) {
      throw const ProductionApiException(
          'The test file failed its integrity check. Prepare it again.');
    }
    return result;
  }

  Future<JsonMap> rentAHumanSearch(Map<String, String> filters,
          {String? organizationId}) async =>
      ApiHumanSearchView.fromJson(await get(
              Uri(path: '/rentahuman/humans', queryParameters: filters)
                  .toString(),
              organizationId: organizationId))
          .toJson();
  Future<JsonMap> rentAHumanBounties(
          {String? cursor, String? organizationId}) async =>
      ApiHumanBountiesView.fromJson(await get(
              Uri(
                      path: '/rentahuman/bounties',
                      queryParameters:
                          cursor == null ? null : {'cursor': cursor})
                  .toString(),
              organizationId: organizationId))
          .toJson();
  Future<JsonMap> humanTaskDrafts(String projectId,
          {String? organizationId}) async =>
      ApiHumanTaskDraftsView.fromJson(await get(
              '/projects/${Uri.encodeComponent(projectId)}/human-task-drafts',
              organizationId: organizationId))
          .toJson();
  Future<JsonMap> createHumanTaskDraft(String projectId, JsonMap body,
          {required String key, String? organizationId}) async =>
      ApiHumanTaskDraftView.fromJson(await post(
              '/projects/${Uri.encodeComponent(projectId)}/human-task-drafts',
              ApiHumanTaskDraftRequest.fromJson(body).toJson(),
              idempotencyKey: key,
              organizationId: organizationId))
          .toJson();
  Future<JsonMap> humanTaskAction(
      String projectId, String id, String action, JsonMap body,
      {String? organizationId}) async {
    if (!const ['preview', 'link', 'sync'].contains(action)) {
      throw ArgumentError('Unsupported task action');
    }
    final request = action == 'link'
        ? ApiHumanTaskLinkRequest.fromJson(body).toJson()
        : ApiHumanTaskDraftAction.fromJson(body).toJson();
    return ApiHumanTaskDraftView.fromJson(await post(
            '/projects/${Uri.encodeComponent(projectId)}/human-task-drafts/${Uri.encodeComponent(id)}/$action',
            request,
            organizationId: organizationId))
        .toJson();
  }

  Future<JsonMap> printPreparations(String projectId,
          {String? organizationId}) async =>
      ApiPrintPreparationsView.fromJson(await get(
              '/projects/${Uri.encodeComponent(projectId)}/print-preparations',
              organizationId: organizationId))
          .toJson();
  Future<JsonMap> prepareBambuPrint(String projectId, JsonMap body,
          {required String key, String? organizationId}) async =>
      ApiPrintPreparationView.fromJson(await post(
              '/projects/${Uri.encodeComponent(projectId)}/print-preparations',
              ApiPreparePrint.fromJson(body).toJson(),
              idempotencyKey: key,
              organizationId: organizationId))
          .toJson();
  Future<JsonMap> bambuDispatches(String projectId,
          {String? organizationId}) async =>
      ApiBambuDispatchesView.fromJson(await get(
              '/projects/${Uri.encodeComponent(projectId)}/print-dispatches',
              organizationId: organizationId))
          .toJson();
  Future<JsonMap> createBambuDispatch(String projectId, JsonMap body,
          {required String key, String? organizationId}) async =>
      ApiBambuDispatchView.fromJson(await post(
              '/projects/${Uri.encodeComponent(projectId)}/print-dispatches',
              ApiBambuDispatchRequest.fromJson(body).toJson(),
              idempotencyKey: key,
              organizationId: organizationId))
          .toJson();
  Future<JsonMap> bambuDispatchAction(
      String projectId, String id, String action,
      {JsonMap body = const {}, String? organizationId}) async {
    if (!{'send', 'cancel', 'reconcile'}.contains(action)) {
      throw ArgumentError.value(action, 'action');
    }
    return ApiBambuDispatchView.fromJson(await post(
            '/projects/${Uri.encodeComponent(projectId)}/print-dispatches/${Uri.encodeComponent(id)}/$action',
            action == 'reconcile'
                ? ApiBambuReconcileRequest.fromJson(body).toJson()
                : const {},
            organizationId: organizationId))
        .toJson();
  }

  Future<JsonMap> companionPreview(String projectId,
          {required String artifactId, String? organizationId}) =>
      post('/projects/${Uri.encodeComponent(projectId)}/companion-preview',
          {'artifactId': artifactId},
          organizationId: organizationId);
  Future<JsonMap> delete(String path, {String? organizationId}) =>
      _request('DELETE', path, organizationId: organizationId);
  Future<JsonMap> post(String path, JsonMap body,
          {String? idempotencyKey, String? organizationId}) =>
      _request('POST', path,
          body: body, key: idempotencyKey, organizationId: organizationId);

  Future<JsonMap> _request(String method, String path,
      {JsonMap? body,
      String? key,
      String? organizationId,
      Duration timeout = const Duration(seconds: 20),
      String interruptionMessage =
          'Connection interrupted. Your request is saved and can be safely retried.'}) async {
    try {
      final request = http.Request(method, uri(path));
      request.headers.addAll(await headers(organizationId: organizationId));
      if (body != null) {
        request.headers['Content-Type'] = 'application/json';
        request.body = jsonEncode(body);
      }
      if (key != null) request.headers['Idempotency-Key'] = key;
      final response = await http.Response.fromStream(
              await client.send(request).timeout(timeout))
          .timeout(timeout);
      final json = response.body.isEmpty
          ? <String, dynamic>{}
          : asMap(jsonDecode(response.body));
      if (response.statusCode < 200 || response.statusCode >= 300) {
        throw ProductionApiException(
            (json['message'] ?? json['detail'] ?? 'Request failed').toString(),
            status: response.statusCode,
            code: json['code'] as String?);
      }
      return json;
    } on ProductionApiException {
      rethrow;
    } on FormatException {
      throw const ProductionApiException(
          'The server returned an unreadable response.');
    } catch (_) {
      throw ProductionApiException(interruptionMessage);
    }
  }

  Stream<ProjectEvent> events(String projectId, int after,
      {String? organizationId, void Function()? onConnected}) async* {
    final streaming = streamClientFactory();
    try {
      final request = http.Request('GET',
          uri('/projects/${Uri.encodeComponent(projectId)}/events?after=$after'));
      request.headers.addAll(await headers(organizationId: organizationId));
      request.headers['Accept'] = 'text/event-stream';
      final response =
          await streaming.send(request).timeout(const Duration(seconds: 20));
      if (response.statusCode != 200) {
        final body = await response.stream.bytesToString();
        JsonMap error = {};
        try {
          error = asMap(jsonDecode(body));
        } on FormatException {/* Proxy error. */}
        throw ProductionApiException(
          (error['message'] ?? 'Live updates disconnected').toString(),
          status: response.statusCode,
          code: error['code'] as String?,
        );
      }
      onConnected?.call();
      yield* decodeEvents(response.stream.timeout(const Duration(seconds: 60)));
    } finally {
      streaming.close();
    }
  }

  Future<Uint8List> referenceImageBytes(String projectId, String imageId,
      {String? organizationId}) async {
    final response = await client
        .get(
            uri('/projects/${Uri.encodeComponent(projectId)}/reference-images/${Uri.encodeComponent(imageId)}/content'),
            headers: await headers(organizationId: organizationId))
        .timeout(const Duration(seconds: 30));
    if (response.statusCode != 200) {
      throw ProductionApiException('Reference image unavailable',
          status: response.statusCode);
    }
    return response.bodyBytes;
  }

  Future<Uint8List> artifactBytes(String id) async {
    final info = ApiArtifactDownloadView.fromJson(
        await get('/artifacts/${Uri.encodeComponent(id)}/download'));
    final target = Uri.parse(info.url);
    final resolved =
        target.hasScheme ? target : Uri.parse(baseUrl).resolveUri(target);
    // Never forward session authorization to a signed storage URL.
    final sameOrigin = resolved.origin == Uri.parse(baseUrl).origin;
    final response = await client
        .get(resolved, headers: sameOrigin ? await headers() : {})
        .timeout(const Duration(seconds: 60));
    if (response.statusCode != 200) {
      throw ProductionApiException('Artifact download failed',
          status: response.statusCode);
    }
    if (sha256.convert(response.bodyBytes).toString() != info.sha256) {
      throw const ProductionApiException(
          'Artifact integrity check failed. Download it again.');
    }
    return response.bodyBytes;
  }

  void close() => client.close();

  Future<Uint8List> releaseBytes(String id) async {
    final response = await client
        .get(uri('/releases/${Uri.encodeComponent(id)}/download'),
            headers: await headers())
        .timeout(const Duration(seconds: 60));
    if (response.statusCode != 200) {
      throw ProductionApiException(
          'The release package could not be downloaded.',
          status: response.statusCode);
    }
    return response.bodyBytes;
  }
}

Stream<ProjectEvent> decodeEvents(Stream<List<int>> bytes) async* {
  final data = <String>[];
  var eventType = '';
  await for (final line in bytes
      .cast<List<int>>()
      .transform(utf8.decoder)
      .transform(const LineSplitter())) {
    if (line.isEmpty) {
      if (data.isNotEmpty) {
        if (eventType == 'auth_expired') {
          throw const ProductionApiException(
              'Your session expired. Sign in again to receive updates.',
              status: 401);
        }
        yield ProjectEvent.fromJson(
            ApiEventView.fromJson(asMap(jsonDecode(data.join('\n')))).toJson());
        data.clear();
      }
      eventType = '';
    } else if (line.startsWith('event:')) {
      eventType = line.substring(6).trim();
    } else if (line.startsWith('data:')) {
      data.add(line.substring(5).replaceFirst(RegExp(r'^ '), ''));
    }
  }
  if (data.isNotEmpty) {
    if (eventType == 'auth_expired') {
      throw const ProductionApiException(
          'Your session expired. Sign in again to receive updates.',
          status: 401);
    }
    yield ProjectEvent.fromJson(
        ApiEventView.fromJson(asMap(jsonDecode(data.join('\n')))).toJson());
  }
}
