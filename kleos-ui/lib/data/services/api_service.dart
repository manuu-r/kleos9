import 'dart:convert';
import 'dart:math';
import 'package:flutter/foundation.dart';
import 'package:http/http.dart' as http;
import '../models/json.dart';
import 'api_routes.dart';

class ApiException implements Exception {
  const ApiException(this.message, {this.status});
  final String message;
  final int? status;
  @override
  String toString() => message;
}

class ApiService {
  ApiService({http.Client? client, String? baseUrl})
      : _client = client ?? http.Client(),
        baseUrl =
            (baseUrl ?? defaultApiBaseUrl()).replaceAll(RegExp(r'/$'), '');

  static String defaultApiBaseUrl() {
    const configured = String.fromEnvironment('API_BASE_URL');
    if (configured.isNotEmpty) return configured;
    // Android's emulator reaches the host machine through this loopback alias.
    final host = !kIsWeb && defaultTargetPlatform == TargetPlatform.android
        ? '10.0.2.2'
        : '127.0.0.1';
    return 'http://$host:8000/api';
  }

  final http.Client _client;
  final String baseUrl;
  final Map<String, String> _keys = {};
  bool _closed = false;
  Future<JsonMap> _request(String path, {JsonMap? body, String? key}) async {
    if (_closed) throw const ApiException('The API connection is closed.');
    try {
      final headers = {
        'Accept': 'application/json',
        if (body != null) 'Content-Type': 'application/json',
        if (key != null) 'Idempotency-Key': key
      };
      final response = await (body == null
              ? _client.get(Uri.parse('$baseUrl$path'), headers: headers)
              : _client.post(Uri.parse('$baseUrl$path'),
                  headers: headers, body: jsonEncode(body)))
          .timeout(const Duration(seconds: 15));
      final decoded = asMap(jsonDecode(response.body));
      if (response.statusCode < 200 || response.statusCode >= 300) {
        throw ApiException(
            (decoded['message'] ??
                    decoded['detail'] ??
                    'Request failed (${response.statusCode})')
                .toString(),
            status: response.statusCode);
      }
      return decoded;
    } on ApiException {
      rethrow;
    } on FormatException {
      throw const ApiException('The API returned an invalid response.');
    } catch (error) {
      throw ApiException(
          'Could not reach the local mock backend. ${error.toString()}');
    }
  }

  Future<JsonMap> load() => _request(SimulationRoutes.state);
  Future<JsonMap> command(String type, JsonMap payload) async {
    final identity = jsonEncode([type, payload]);
    final key = _keys.putIfAbsent(
        identity,
        () =>
            '${DateTime.now().microsecondsSinceEpoch}-${Random.secure().nextInt(0x7fffffff)}');
    try {
      final response = await _request(SimulationRoutes.commands,
          body: {'type': type, 'payload': payload}, key: key);
      _keys.remove(identity);
      return response;
    } on ApiException catch (error) {
      if (error.status != null && error.status! >= 400 && error.status! < 500) {
        _keys.remove(identity);
      }
      rethrow;
    }
  }

  Future<JsonMap> reset() => _request(SimulationRoutes.demoReset, body: {});
  Future<JsonMap> setSpeed(double speed) =>
      _request(SimulationRoutes.demoSpeed, body: {'speed': speed});
  void close() {
    _closed = true;
    _client.close();
    _keys.clear();
  }
}
