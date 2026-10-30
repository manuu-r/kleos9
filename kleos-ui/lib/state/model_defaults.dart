import 'dart:convert';
import 'package:crypto/crypto.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../data/models/json.dart';
import '../data/models/production.dart';
import '../data/services/platform/journal_storage.dart' as preferences;
import 'production_store.dart';

/// User- and organization-scoped choices used only when creating a new build.
final newBuildModelPolicyProvider =
    AsyncNotifierProvider<NewBuildModelPolicy, Map<String, ModelChoice>>(
        NewBuildModelPolicy.new);

class NewBuildModelPolicy extends AsyncNotifier<Map<String, ModelChoice>> {
  late String _key;
  int _generation = 0;

  @override
  Future<Map<String, ModelChoice>> build() async {
    final generation = ++_generation;
    ref.onDispose(() {
      if (_generation == generation) _generation++;
    });
    final account = ref.watch(accountProvider).requireValue!;
    final api = ref.watch(productionApiProvider);
    final catalogFuture = ref.watch(modelCatalogProvider.future);
    final catalog = await catalogFuture;
    if (!ref.mounted || generation != _generation) {
      throw StateError('The workspace changed while loading model defaults.');
    }
    // An explicit operator default change starts a fresh preference namespace;
    // existing projects and manually selected running attempts remain pinned.
    final policyVersion = jsonEncode({
      for (final lane in engineeringLanes.keys)
        lane: catalog.defaults[lane]?.toJson()
    });
    _key =
        'kleos.model-defaults.${sha256.convert(utf8.encode('${api.baseUrl}|${account.subject}|${account.tenantId}|$policyVersion'))}';
    final key = _key;
    final saved = await preferences.readJournal(key);
    if (saved == null) return Map.unmodifiable(catalog.defaults);
    try {
      final values = asMap(jsonDecode(saved));
      return Map.unmodifiable({
        for (final lane in engineeringLanes.keys)
          lane: _availableChoice(catalog, lane, asMap(values[lane])) ??
              catalog.defaults[lane]!,
      });
    } on FormatException {
      return Map.unmodifiable(catalog.defaults);
    }
  }

  ModelChoice? _availableChoice(
      ModelCatalog catalog, String lane, JsonMap value) {
    return catalog.models
        .where((model) =>
            model.lanes.contains(lane) &&
            model.choice.provider == value['provider'] &&
            model.choice.model == value['model'])
        .firstOrNull
        ?.choice;
  }

  Future<void> save(Map<String, ModelChoice> policy) async {
    final generation = _generation;
    final key = _key;
    await future;
    if (!ref.mounted || generation != _generation) {
      throw StateError('The account changed. Reopen Providers before saving.');
    }
    final catalog = await ref.read(modelCatalogProvider.future);
    if (!ref.mounted || generation != _generation) {
      throw StateError('The account changed. Reopen Providers before saving.');
    }
    if (engineeringLanes.keys.any((lane) =>
        policy[lane] == null ||
        _availableChoice(catalog, lane, policy[lane]!.toJson()) == null)) {
      throw const FormatException('Choose an available model for every lane.');
    }
    await preferences.writeJournal(
        key,
        jsonEncode(
            policy.map((lane, choice) => MapEntry(lane, choice.toJson()))));
    if (ref.mounted && generation == _generation) {
      state = AsyncData(Map.unmodifiable(policy));
    }
  }
}
