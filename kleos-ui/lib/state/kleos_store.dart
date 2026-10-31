import 'dart:async';
import 'dart:convert';
import 'package:flutter/widgets.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../data/models/app_snapshot.dart';
import '../data/models/json.dart';
import '../data/services/api_service.dart';
import '../data/services/production_legacy_api_service.dart';
import 'production_store.dart';
import 'model_defaults.dart';
export '../data/models/app_snapshot.dart' show CommandResult;

/// Overridden in tests with a client that points at fixtures.
final apiServiceProvider = Provider<ApiService>((ref) {
  final ApiService service =
      const String.fromEnvironment('KLEOS_BACKEND', defaultValue: 'mock') ==
              'production'
          ? ProductionLegacyApiService(ref.watch(productionRepositoryProvider),
              simulationService:
                  const String.fromEnvironment('KLEOS_SIMULATION_API_BASE_URL')
                          .isEmpty
                      ? null
                      : ApiService(
                          baseUrl: const String.fromEnvironment(
                              'KLEOS_SIMULATION_API_BASE_URL')))
          : ApiService();
  ref.onDispose(service.close);
  return service;
});

/// Tests turn polling off and drive [KleosStore.refresh] by hand.
final pollingEnabledProvider = Provider<bool>((ref) => true);

/// Immutable view of the backend. A new instance is published only when
/// something observable changed, so watching it is cheap.
@immutable
class KleosController {
  const KleosController._(this._store,
      {this.snapshot,
      this.error,
      this.pendingTypes = const <String>[],
      this.speed = 1});
  final KleosStore _store;
  final AppSnapshot? snapshot;
  final String? error;
  final List<String> pendingTypes;
  final double speed;

  JsonMap get state => snapshot?.state ?? const {};
  bool get loading => snapshot == null && error == null;
  bool isPending(String type) => pendingTypes.contains(type);

  Future<void> refresh() => _store.refresh();
  Future<CommandResult> execute(String type, [JsonMap payload = const {}]) =>
      _store.execute(type, payload);
  Future<void> setSpeed(double speed) => _store.setSpeed(speed);
  Future<void> resetDemo() => _store.resetDemo();
  void dismissError() => _store.dismissError();

  KleosController _copy(
          {AppSnapshot? snapshot,
          Object? error = _keep,
          List<String>? pendingTypes}) =>
      KleosController._(_store,
          snapshot: snapshot ?? this.snapshot,
          error: identical(error, _keep) ? this.error : error as String?,
          pendingTypes: pendingTypes ?? this.pendingTypes,
          speed: (snapshot ?? this.snapshot)?.speed ?? speed);
}

const Object _keep = Object();

final kleosProvider =
    NotifierProvider<KleosStore, KleosController>(KleosStore.new);

/// Single source of truth for backend state, commands and polling.
class KleosStore extends Notifier<KleosController> {
  static const _fast = Duration(milliseconds: 750);
  static const _slow = Duration(seconds: 3);
  late ApiService _service;
  StreamSubscription<JsonMap>? _updates;
  final Map<String, Future<CommandResult>> _requests = {};
  Timer? _timer;
  bool _refreshing = false;
  bool _foreground = true;
  int _quietPolls = 0;
  int _failures = 0;
  int _generation = 0;
  AppLifecycleListener? _lifecycle;

  @override
  KleosController build() {
    final generation = ++_generation;
    _service = ref.watch(apiServiceProvider);
    _refreshing = false;
    _quietPolls = 0;
    _failures = 0;
    _requests.clear();
    final service = _service;
    if (service is ProductionLegacyApiService) {
      _updates = service.updates.listen((snapshot) {
        if (generation == _generation && ref.mounted) _accept(snapshot);
      }, onError: (Object error) {
        if (generation == _generation && ref.mounted) _fail(error);
      });
    }
    ref.onDispose(() {
      _generation++;
      _timer?.cancel();
      _updates?.cancel();
      _lifecycle?.dispose();
    });
    if (ref.read(pollingEnabledProvider)) {
      _lifecycle = AppLifecycleListener(onStateChange: (s) {
        _foreground = s == AppLifecycleState.resumed;
        if (_foreground) {
          _quietPolls = 0;
          _schedule(Duration.zero);
        } else {
          _timer?.cancel();
        }
      });
      unawaited(Future.microtask(start));
    }
    return KleosController._(this);
  }

  Future<void> start() async {
    final generation = _generation;
    await refresh();
    if (generation == _generation && ref.mounted) _schedule(_fast);
  }

  Future<void> setActiveProject(String? id) async {
    final service = _service;
    if (service is ProductionLegacyApiService) {
      try {
        await service.setActiveProject(id);
      } catch (error) {
        _fail(error);
      }
    }
  }

  void _schedule(Duration delay) {
    if (_service is ProductionLegacyApiService &&
        const String.fromEnvironment('KLEOS_SIMULATION_API_BASE_URL').isEmpty &&
        delay != Duration.zero) {
      delay = const Duration(seconds: 15);
    }
    _timer?.cancel();
    if (!ref.mounted || !_foreground) return;
    _timer = Timer(delay, () async {
      await refresh();
      final backoff = _failures > 0
          ? Duration(seconds: (_failures * 2).clamp(2, 8))
          : _quietPolls > 6
              ? _slow
              : _fast;
      _schedule(backoff);
    });
  }

  /// Returns true when the snapshot changed what widgets can observe.
  bool _accept(JsonMap json) {
    final incoming = AppSnapshot.fromJson(json);
    final current = state.snapshot;
    if (current != null && incoming.revision < current.revision) return false;
    final shared = current == null
        ? incoming
        : AppSnapshot.shared(
            revision: incoming.revision,
            speed: incoming.speed,
            state: shareStructure(current.state, incoming.state) as JsonMap);
    final changed = current == null ||
        !identical(shared.state, current.state) ||
        shared.speed != current.speed;
    if (!ref.mounted) return changed;
    if (changed || state.error != null) {
      state = state._copy(snapshot: shared, error: null);
    }
    return changed;
  }

  void _fail(Object error) {
    if (ref.mounted) state = state._copy(error: error.toString());
  }

  Future<void> refresh() async {
    if (!ref.mounted || _refreshing) return;
    _refreshing = true;
    final generation = _generation;
    final service = _service;
    try {
      final snapshot = await service.load();
      if (generation != _generation || !ref.mounted) return;
      final changed = _accept(snapshot);
      _quietPolls = changed ? 0 : _quietPolls + 1;
      _failures = 0;
    } catch (e) {
      if (generation != _generation || !ref.mounted) return;
      _failures++;
      _fail(e);
    } finally {
      if (generation == _generation) _refreshing = false;
    }
  }

  Future<CommandResult> execute(String type, [JsonMap payload = const {}]) {
    final key = jsonEncode([
      type,
      type == 'connector.authorize'
          ? {...payload, 'apiKey': '[redacted]'}
          : payload
    ]);
    final inflight = _requests[key];
    if (inflight != null) return inflight;
    final generation = _generation;
    final service = _service;
    final request = Future<CommandResult>(() async {
      try {
        final effectivePayload = service is ProductionLegacyApiService &&
                type == 'project.create' &&
                payload['simulation'] != true
            ? {
                ...payload,
                'autoStart': payload['autoStart'] ?? true,
                if (!payload.containsKey('modelPolicy'))
                  'modelPolicy':
                      (await ref.read(newBuildModelPolicyProvider.future))
                          .map((lane, model) => MapEntry(lane, model.toJson()))
              }
            : payload;
        if (generation != _generation || !ref.mounted) {
          return const CommandResult(ok: false);
        }
        final response = await service.command(type, effectivePayload);
        if (generation != _generation || !ref.mounted) {
          return const CommandResult(ok: false);
        }
        _accept(asMap(response['snapshot']));
        _quietPolls = 0;
        return CommandResult(ok: true, result: asMap(response['result']));
      } catch (e) {
        if (generation == _generation && ref.mounted) _fail(e);
        return const CommandResult(ok: false);
      }
    }).whenComplete(() {
      if (generation != _generation || !ref.mounted) return;
      _requests.remove(key);
      if (ref.mounted) state = state._copy(pendingTypes: _pendingTypes());
    });
    _requests[key] = request;
    state = state._copy(pendingTypes: _pendingTypes());
    return request;
  }

  List<String> _pendingTypes() => List.unmodifiable({
        for (final key in _requests.keys)
          asList(jsonDecode(key)).first as String
      });

  Future<void> setSpeed(double speed) async {
    try {
      _accept(await _service.setSpeed(speed));
    } catch (e) {
      _fail(e);
    }
  }

  Future<void> resetDemo() async {
    try {
      _accept(await _service.reset());
    } catch (e) {
      _fail(e);
    }
  }

  void dismissError() {
    if (state.error != null) state = state._copy(error: null);
  }
}

/// Slices of the store. Each rebuilds only when its own data changed.
final projectsProvider = Provider<List<JsonMap>>((ref) {
  final raw = ref.watch(kleosProvider.select((c) => c.state['projects']));
  return asMaps(raw);
});

final storeEmptyProvider = Provider<bool>(
    (ref) => ref.watch(kleosProvider.select((c) => c.state.isEmpty)));
