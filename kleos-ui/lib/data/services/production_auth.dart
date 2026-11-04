import 'dart:convert';
import 'package:crypto/crypto.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import '../models/json.dart';
import '../models/production.dart';
import 'production_api.dart';
import 'api_routes.dart';
import 'platform/journal_storage.dart' as local;

class OrganizationSelectionRequired implements Exception {
  const OrganizationSelectionRequired(this.organizations);
  final List<OrganizationRecord> organizations;
  @override
  String toString() =>
      'Your selected organization is no longer available. Choose an organization to continue.';
}

class ProductionAuth {
  ProductionAuth(this.api,
      {FlutterSecureStorage? storage,
      Future<String?> Function(String)? readPreference,
      Future<void> Function(String, String)? writePreference})
      : _storage = storage ?? const FlutterSecureStorage(),
        _readPreference = readPreference ?? local.readJournal,
        _writePreference = writePreference ?? local.writeJournal {
    api.accessToken = token;
  }
  final ProductionApi api;
  final FlutterSecureStorage _storage;
  final Future<String?> Function(String) _readPreference;
  final Future<void> Function(String, String) _writePreference;
  JsonMap? _config;
  JsonMap? _credentials;
  String get _storageKey =>
      'kleos.auth.${sha256.convert(utf8.encode(api.baseUrl))}';

  Future<JsonMap> config() async =>
      _config ??= await api.get(ApiRoutes.authConfig);

  Future<AccountSession?> restore() async {
    if (!kIsWeb) {
      final saved = await _storage.read(key: _storageKey);
      if (saved != null) _credentials = asMap(jsonDecode(saved));
    }
    await config();
    try {
      return await session();
    } on ProductionApiException catch (error) {
      if (error.status == 401) return null;
      rethrow;
    }
  }

  Future<AccountSession> session() async {
    final available = await api.get(ApiRoutes.authOrganizations);
    final organizations = asMaps(available['organizations'])
        .map(OrganizationRecord.fromJson)
        .toList();
    if (organizations.isEmpty) {
      throw const ProductionApiException(
          'This account has not been invited to an organization.',
          status: 403);
    }
    final subject = available['subject'] as String;
    final key = sha256
        .convert(utf8.encode('organization|${api.baseUrl}|$subject'))
        .toString();
    final saved = await _readPreference(key);
    final selected =
        _config?['loginRequired'] == false && organizations.length == 1
            ? organizations.single.id
            : api.tenantId ??
                (saved == null
                    ? organizations.first.id
                    : jsonDecode(saved) as String);
    if (!organizations.any((org) => org.id == selected)) {
      api.tenantId = null;
      throw OrganizationSelectionRequired(organizations);
    }
    api.tenantId = selected;
    await _writePreference(key, jsonEncode(selected));
    final account = AccountSession.fromJson({
      ...await api.get(ApiRoutes.authSession),
      'organizations': organizations.map((org) => org.toJson()).toList()
    });
    api.tenantId = account.tenantId;
    api.csrfToken = account.csrfToken;
    return account;
  }

  Future<AccountSession> selectOrganization(String id) async {
    api.tenantId = id;
    return session();
  }

  /// Signs in with a development token or an email and password.
  Future<AccountSession?> signIn(
      {String? developmentToken, String? email, String? password}) async {
    final settings = await config();
    if (settings['loginRequired'] == false) return session();
    if (settings['authMode'] == 'development') {
      if (developmentToken == null || developmentToken.trim().isEmpty) {
        throw const ProductionApiException(
            'Enter the development access token.');
      }
      _credentials = {
        'accessToken': developmentToken.trim(),
        'development': true
      };
      await _persist();
      return session();
    }
    if (email == null || email.trim().isEmpty || password == null) {
      throw const ProductionApiException('Enter your email and password.');
    }
    return _authenticate(
        ApiRoutes.authLogin, {'email': email.trim(), 'password': password});
  }

  /// Redeems a single-use invite code and creates the account.
  Future<AccountSession> signUp(
      {required String inviteCode,
      required String email,
      required String password,
      String name = ''}) {
    return _authenticate(ApiRoutes.authSignup, {
      'inviteCode': inviteCode.trim(),
      'email': email.trim(),
      'password': password,
      'name': name.trim()
    });
  }

  /// Creates the first owner while the server has no accounts.
  Future<AccountSession> setUp(
      {required String setupToken,
      required String organization,
      required String email,
      required String password,
      String name = ''}) {
    return _authenticate(ApiRoutes.authSetup, {
      'setupToken': setupToken.trim(),
      'organization': organization.trim(),
      'email': email.trim(),
      'password': password,
      'name': name.trim()
    });
  }

  Future<AccountSession> _authenticate(String path, JsonMap body) async {
    // Browsers keep the session in an HttpOnly cookie. Native apps hold a bearer token.
    final result = await api.post(path, {...body, 'native': !kIsWeb});
    final token = result['token'] as String?;
    if (!kIsWeb && token != null) {
      _credentials = {'accessToken': token};
      await _persist();
    }
    return session();
  }

  Future<String?> token() async => _credentials?['accessToken'] as String?;

  Future<void> _persist() async {
    // Browser sessions are HttpOnly cookies; development tokens stay in memory on web.
    if (!kIsWeb) {
      await _storage.write(key: _storageKey, value: jsonEncode(_credentials));
    }
  }

  Future<void> signOut() async {
    try {
      await api.post(ApiRoutes.authLogout, {});
    } finally {
      _credentials = null;
      api.csrfToken = null;
      api.tenantId = null;
      if (!kIsWeb) await _storage.delete(key: _storageKey);
    }
  }
}
