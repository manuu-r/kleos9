import 'dart:convert';
import 'package:crypto/crypto.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter_appauth/flutter_appauth.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:url_launcher/url_launcher.dart';
import '../models/json.dart';
import '../models/production.dart';
import 'production_api.dart';
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
      FlutterAppAuth? oauth,
      Future<String?> Function(String)? readPreference,
      Future<void> Function(String, String)? writePreference})
      : _storage = storage ?? const FlutterSecureStorage(),
        _oauth = oauth ?? const FlutterAppAuth(),
        _readPreference = readPreference ?? local.readJournal,
        _writePreference = writePreference ?? local.writeJournal {
    api.accessToken = token;
  }
  final ProductionApi api;
  final FlutterSecureStorage _storage;
  final FlutterAppAuth _oauth;
  final Future<String?> Function(String) _readPreference;
  final Future<void> Function(String, String) _writePreference;
  JsonMap? _config;
  JsonMap? _credentials;
  Future<String?>? _refreshing;
  String get _storageKey =>
      'kleos.auth.${sha256.convert(utf8.encode(api.baseUrl))}';

  Future<JsonMap> config() async => _config ??= await api.get('/auth/config');

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
    final available = await api.get('/auth/organizations');
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
      ...await api.get('/auth/session'),
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

  Future<AccountSession?> signIn({String? developmentToken}) async {
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
    if (kIsWeb) {
      await launchUrl(api.uri('/auth/login'), webOnlyWindowName: '_self');
      return null;
    }
    final result =
        await _oauth.authorizeAndExchangeCode(AuthorizationTokenRequest(
      settings['clientId'] as String,
      settings['nativeRedirectUrl'] as String? ?? 'kleos9://auth',
      issuer: settings['issuer'] as String,
      scopes: const ['openid', 'email', 'profile'],
    ));
    _credentials = {
      'accessToken': result.accessToken,
      'refreshToken': result.refreshToken,
      'expiresAt': result.accessTokenExpirationDateTime?.millisecondsSinceEpoch,
    };
    await _persist();
    return session();
  }

  Future<String?> token() async {
    if (_credentials == null) return null;
    final expiration = _credentials!['expiresAt'] as int?;
    if (!kIsWeb &&
        _credentials!['development'] != true &&
        expiration != null &&
        expiration <
            DateTime.now()
                .add(const Duration(minutes: 1))
                .millisecondsSinceEpoch) {
      return _refreshing ??= _refresh().whenComplete(() => _refreshing = null);
    }
    return _credentials!['accessToken'] as String?;
  }

  Future<String?> _refresh() async {
    // Config requests must not recursively request the expired token.
    final settings = _config;
    final refreshToken = _credentials?['refreshToken'] as String?;
    if (settings == null || refreshToken == null) return null;
    final result = await _oauth.token(TokenRequest(
      settings['clientId'] as String,
      settings['nativeRedirectUrl'] as String? ?? 'kleos9://auth',
      issuer: settings['issuer'] as String,
      refreshToken: refreshToken,
    ));
    _credentials = {
      'accessToken': result.accessToken,
      'refreshToken': result.refreshToken ?? refreshToken,
      'expiresAt': result.accessTokenExpirationDateTime?.millisecondsSinceEpoch,
    };
    await _persist();
    return result.accessToken;
  }

  Future<void> _persist() async {
    // Browser sessions are HttpOnly cookies; development tokens stay in memory on web.
    if (!kIsWeb) {
      await _storage.write(key: _storageKey, value: jsonEncode(_credentials));
    }
  }

  Future<void> signOut() async {
    try {
      await api.post('/auth/logout', {});
    } finally {
      _credentials = null;
      api.csrfToken = null;
      api.tenantId = null;
      if (!kIsWeb) await _storage.delete(key: _storageKey);
    }
  }
}
