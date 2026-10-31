import 'dart:async';
import 'dart:convert';
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
      {JsonMap? body, String? key, String? organizationId}) async {
    try {
      final request = http.Request(method, uri(path));
      request.headers.addAll(await headers(organizationId: organizationId));
      if (body != null) {
        request.headers['Content-Type'] = 'application/json';
        request.body = jsonEncode(body);
      }
      if (key != null) request.headers['Idempotency-Key'] = key;
      final response = await http.Response.fromStream(
              await client.send(request).timeout(const Duration(seconds: 20)))
          .timeout(const Duration(seconds: 20));
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
      throw const ProductionApiException(
          'Connection interrupted. Your request is saved and can be safely retried.');
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
