import 'dart:async';
import 'package:flutter/widgets.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../data/models/json.dart';
import '../data/models/production.dart';
import '../data/repositories/production_repository.dart';
import '../data/services/command_journal.dart';
import '../data/services/production_api.dart';
import '../data/services/production_auth.dart';

final productionApiProvider = Provider<ProductionApi>((ref) {
  final api = ProductionApi();
  ref.onDispose(api.close);
  return api;
});

final productionAuthProvider = Provider<ProductionAuth>(
    (ref) => ProductionAuth(ref.watch(productionApiProvider)));

final accountProvider =
    AsyncNotifierProvider<AccountController, AccountSession?>(
        AccountController.new);

class AccountController extends AsyncNotifier<AccountSession?> {
  @override
  Future<AccountSession?> build() =>
      ref.watch(productionAuthProvider).restore();

  Future<void> signIn(
      {String? developmentToken, String? email, String? password}) async {
    state = const AsyncLoading();
    state = await AsyncValue.guard(() => ref
        .read(productionAuthProvider)
        .signIn(
            developmentToken: developmentToken,
            email: email,
            password: password));
  }

  Future<void> setUp(
      {required String setupToken,
      required String organization,
      required String email,
      required String password,
      String name = ''}) async {
    state = const AsyncLoading();
    state = await AsyncValue.guard(() => ref.read(productionAuthProvider).setUp(
        setupToken: setupToken,
        organization: organization,
        email: email,
        password: password,
        name: name));
  }

  Future<void> signUp(
      {required String inviteCode,
      required String email,
      required String password,
      String name = ''}) async {
    state = const AsyncLoading();
    state = await AsyncValue.guard(() => ref
        .read(productionAuthProvider)
        .signUp(
            inviteCode: inviteCode,
            email: email,
            password: password,
            name: name));
  }

  Future<void> signOut() async {
    try {
      await ref.read(productionAuthProvider).signOut();
    } finally {
      if (ref.mounted) state = const AsyncData(null);
    }
  }

  Future<void> selectOrganization(String id) async {
    state = const AsyncLoading();
    state = await AsyncValue.guard(
        () => ref.read(productionAuthProvider).selectOrganization(id));
  }
}

final productionRepositoryProvider = Provider<ProductionRepository>((ref) {
  final session = ref.watch(accountProvider).asData?.value;
  if (session == null) {
    throw const ProductionApiException('Sign in to continue.', status: 401);
  }
  final api = ref.watch(productionApiProvider);
  final namespace = [api.baseUrl, session.tenantId, session.subject].join('|');
  return ProductionRepository(api, DurableCommandJournal(namespace),
      organizationId: session.tenantId);
});

final modelCatalogProvider = FutureProvider<ModelCatalog>(
    (ref) => ref.watch(productionRepositoryProvider).catalog());

final productionProjectsProvider =
    AsyncNotifierProvider<ProjectsController, ProjectListState>(
        ProjectsController.new);

class ProjectListState {
  const ProjectListState(this.projects, this.nextCursor, this.pending);
  final List<ProjectRecord> projects;
  final String? nextCursor;
  final List<PendingCommand> pending;
}

class ProjectsController extends AsyncNotifier<ProjectListState> {
  @override
  Future<ProjectListState> build() async {
    final repository = ref.watch(productionRepositoryProvider);
    final result = await repository.projects();
    return ProjectListState(
        result.projects, result.nextCursor, await repository.journal.pending());
  }

  Future<void> refresh() async {
    final repository = ref.read(productionRepositoryProvider);
    final result = await repository.projects();
    if (ref.mounted) {
      state = AsyncData(ProjectListState(result.projects, result.nextCursor,
          await repository.journal.pending()));
    }
  }

  Future<void> nextPage() async {
    final previous = state.asData?.value;
    if (previous?.nextCursor == null) return;
    final result = await ref
        .read(productionRepositoryProvider)
        .projects(after: previous!.nextCursor);
    if (!ref.mounted) return;
    final byId = {
      for (final p in [...previous.projects, ...result.projects]) p.id: p
    };
    state = AsyncData(ProjectListState(
        byId.values.toList(), result.nextCursor, previous.pending));
  }
}

class ProjectView {
  const ProjectView(this.workspace,
      {this.events = const [],
      this.connected = false,
      this.busy = false,
      this.error});
  final Workspace workspace;
  final List<ProjectEvent> events;
  final bool connected;
  final bool busy;
  final String? error;

  ProjectView copy(
          {Workspace? workspace,
          List<ProjectEvent>? events,
          bool? connected,
          bool? busy,
          String? error,
          bool clearError = false}) =>
      ProjectView(workspace ?? this.workspace,
          events: events ?? this.events,
          connected: connected ?? this.connected,
          busy: busy ?? this.busy,
          error: clearError ? null : error ?? this.error);
}

final productionProjectProvider = AsyncNotifierProvider.autoDispose
    .family<ProjectController, ProjectView, String>(ProjectController.new);

class ProjectController extends AsyncNotifier<ProjectView> {
  ProjectController(this.projectId);
  final String projectId;
  StreamSubscription<ProjectEvent>? _subscription;
  Timer? _reconnect;
  Timer? _debounce;
  AppLifecycleListener? _lifecycle;
  bool _foreground = true;
  bool _refreshing = false;
  bool _refreshAgain = false;
  int _cursor = 0;
  int _connectionGeneration = 0;

  @override
  Future<ProjectView> build() async {
    final repository = ref.watch(productionRepositoryProvider);
    ref.onDispose(() {
      _connectionGeneration++;
      _subscription?.cancel();
      _reconnect?.cancel();
      _debounce?.cancel();
      _lifecycle?.dispose();
    });
    _lifecycle = AppLifecycleListener(onStateChange: (value) {
      _foreground = value == AppLifecycleState.resumed;
      if (_foreground) {
        unawaited(refresh().then((_) => _connect()));
      } else {
        _connectionGeneration++;
        _subscription?.cancel();
        _reconnect?.cancel();
        _update((s) => s.copy(connected: false));
      }
    });
    final workspace = await repository.workspace(projectId);
    _cursor = workspace.eventCursor;
    // Start after AsyncNotifier publishes its initial result.
    _reconnect = Timer(Duration.zero, _connect);
    return ProjectView(workspace);
  }

  void _update(ProjectView Function(ProjectView) update) {
    if (!ref.mounted) return;
    final current = state.asData?.value;
    if (current != null) state = AsyncData(update(current));
  }

  void _connect() {
    if (!ref.mounted || !_foreground) return;
    _subscription?.cancel();
    final generation = ++_connectionGeneration;
    _subscription = ref
        .read(productionRepositoryProvider)
        .events(projectId, _cursor, onConnected: () {
      if (ref.mounted && generation == _connectionGeneration) {
        _update((s) => s.copy(connected: true));
      }
    }).listen(
      (event) {
        if (!ref.mounted ||
            generation != _connectionGeneration ||
            event.sequence <= _cursor) {
          return;
        }
        if (event.sequence != _cursor + 1) {
          unawaited(_resync());
          return;
        }
        _cursor = event.sequence;
        _update((s) => s.copy(
            connected: true,
            events: [
              ...s.events.where((e) => e.sequence != event.sequence),
              event
            ].reversed.take(100).toList().reversed.toList()));
        _debounce?.cancel();
        _debounce = Timer(const Duration(milliseconds: 200), refresh);
      },
      onError: (Object error) {
        if (!ref.mounted || generation != _connectionGeneration) return;
        _update((s) => s.copy(connected: false));
        if (error is ProductionApiException &&
            const {409, 410}.contains(error.status)) {
          unawaited(_resync());
        } else if (error is ProductionApiException &&
            const {401, 403, 404}.contains(error.status)) {
          _connectionGeneration++;
          _update((s) => s.copy(error: error.message));
          if (error.status == 401) ref.invalidate(accountProvider);
        } else {
          _scheduleReconnect(generation);
        }
      },
      onDone: () => _scheduleReconnect(generation),
      cancelOnError: true,
    );
  }

  void _scheduleReconnect(int generation) {
    if (!ref.mounted || !_foreground || generation != _connectionGeneration) {
      return;
    }
    _update((s) => s.copy(connected: false));
    _reconnect?.cancel();
    _reconnect = Timer(const Duration(seconds: 3), _connect);
  }

  Future<void> _resync() async {
    _connectionGeneration++;
    unawaited(_subscription?.cancel());
    _subscription = null;
    final recovered = await refresh();
    if (!ref.mounted) return;
    if (!recovered) {
      _scheduleReconnect(_connectionGeneration);
      return;
    }
    _cursor = state.requireValue.workspace.eventCursor;
    _connect();
  }

  Future<bool> refresh() async {
    if (_refreshing) {
      _refreshAgain = true;
      return false;
    }
    _refreshing = true;
    try {
      final latest =
          await ref.read(productionRepositoryProvider).workspace(projectId);
      _update((s) => s.copy(workspace: latest));
      return true;
    } catch (error) {
      _update((s) => s.copy(error: error.toString()));
      return false;
    } finally {
      _refreshing = false;
      if (_refreshAgain && ref.mounted) {
        _refreshAgain = false;
        unawaited(refresh());
      }
    }
  }

  Future<AcceptedCommand?> execute(String type,
      [JsonMap payload = const {}]) async {
    final current = state.asData?.value;
    if (current == null || current.busy) return null;
    _update((s) => s.copy(busy: true, clearError: true));
    try {
      final result = await ref.read(productionRepositoryProvider).command(
            type,
            {'projectId': projectId, ...payload},
            revision: current.workspace.project.revision,
          );
      await refresh();
      if (ref.mounted) ref.invalidate(productionProjectsProvider);
      return result;
    } catch (error) {
      _update((s) => s.copy(error: error.toString()));
      if (error is ProductionApiException && error.status == 409) {
        await refresh();
      }
      return null;
    } finally {
      _update((s) => s.copy(busy: false));
    }
  }
}

final runInspectorProvider = FutureProvider.autoDispose
    .family<RunRecord, String>(
        (ref, id) => ref.watch(productionRepositoryProvider).run(id));
