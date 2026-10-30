import 'dart:async';

import '../models/json.dart';
import '../models/production.dart';
import '../repositories/production_repository.dart';
import 'api_service.dart';

/// Adapts the original presentation store to the scoped production contracts.
/// This is a view projection: engineering state always comes from the server.
class ProductionLegacyApiService extends ApiService {
  ProductionLegacyApiService(this.repository, {this.simulationService});
  final ApiService? simulationService;
  bool get hasSimulation => simulationService != null;
  JsonMap? _simulationSnapshot;
  Future<void>? _simulationLoading;
  String? _simulationError;
  bool _activeIsSimulation = false;
  final ProductionRepository repository;
  final _updates = StreamController<JsonMap>.broadcast();
  Stream<JsonMap> get updates => _updates.stream;
  final Map<String, ProjectRecord> _projects = {};
  final Set<String> _deletedProjects = {};
  final Map<String, Workspace> _workspaces = {};
  final Map<String, List<JsonMap>> _activity = {};
  List<JsonMap> _connectors = [];
  String? _activeProject;
  StreamSubscription<ProjectEvent>? _events;
  Timer? _refreshTimer;
  Timer? _reconnectTimer;
  Future<JsonMap>? _loading;
  bool _disposed = false;
  bool _connected = false;
  int _revision = 0;
  int _streamEpoch = 0;
  int _cursor = 0;
  String? _streamError;
  String? _connectorError;
  DateTime? _lastRealLoad;

  Future<void> setActiveProject(String? id) async {
    if (_disposed) return;
    if (simulationService != null && _simulationSnapshot == null) {
      await _loadSimulation();
    }
    if (_disposed) return;
    final simulated = id != null && _isSimulatedProject(id);
    if (id == _activeProject && simulated == _activeIsSimulation) return;
    _activeProject = id;
    _activeIsSimulation = simulated;
    final epoch = ++_streamEpoch;
    _connected = false;
    _streamError = null;
    _refreshTimer?.cancel();
    _reconnectTimer?.cancel();
    final previous = _events;
    _events = null;
    if (previous != null) unawaited(previous.cancel());
    if (id == null || simulated) {
      _publish();
      return;
    }
    try {
      final workspace = await repository.workspace(id);
      if (_disposed || epoch != _streamEpoch) return;
      _save(workspace);
      _cursor = workspace.eventCursor;
      _publish();
      _listen(id, epoch);
    } catch (error) {
      if (_disposed || epoch != _streamEpoch) return;
      _streamError = error.toString();
      _publish();
      _reconnect(id, epoch);
    }
  }

  @override
  Future<JsonMap> load() =>
      _loading ??= _load().whenComplete(() => _loading = null);

  Future<JsonMap> _load() async {
    await _loadSimulation();
    if (_disposed) throw const ApiException('The connection is closed.');
    if (simulationService != null &&
        _lastRealLoad != null &&
        DateTime.now().difference(_lastRealLoad!) <
            const Duration(seconds: 15)) {
      return _snapshot();
    }
    final before = {
      for (final entry in _projects.entries) entry.key: entry.value.revision,
    };
    final summaries = <String, ProjectRecord>{};
    String? cursor;
    do {
      final page = await repository.projects(after: cursor);
      for (final project in page.projects) {
        if (_deletedProjects.contains(project.id)) continue;
        summaries[project.id] = project;
      }
      cursor = page.nextCursor;
    } while (cursor != null && !_disposed);
    if (_disposed) throw const ApiException('The connection is closed.');
    for (final entry in summaries.entries) {
      if (_deletedProjects.contains(entry.key)) continue;
      if ((_projects[entry.key]?.revision ?? -1) <= entry.value.revision) {
        _projects[entry.key] = entry.value;
      }
    }
    // A creation or command can complete while the list request is in flight.
    // Do not erase that newer result using an older project-list response.
    _projects.removeWhere((id, project) =>
        before[id] == project.revision && !summaries.containsKey(id));
    final active = _activeProject;
    if (active != null) {
      if (_activeIsSimulation != _isSimulatedProject(active)) {
        await setActiveProject(active);
      } else if (!_activeIsSimulation && !_deletedProjects.contains(active)) {
        try {
          _save(await repository.workspace(active));
        } catch (_) {
          if (!_deletedProjects.contains(active)) rethrow;
        }
      }
    }
    try {
      final connectors = await repository.api
          .get('/connectors', organizationId: repository.organizationId);
      _connectors = asMaps(connectors['connectors']).map(_connector).toList();
      _connectorError = null;
    } catch (error) {
      _connectorError = error.toString();
    }
    if (_disposed) throw const ApiException('The connection is closed.');
    _lastRealLoad = DateTime.now();
    return _snapshot();
  }

  JsonMap get _simulationState => asMap(_simulationSnapshot?['state']);

  bool _isSimulatedProject(String id) =>
      !_projects.containsKey(id) &&
      asMaps(_simulationState['projects'])
          .any((project) => project['id'] == id);

  void _acceptSimulation(JsonMap snapshot) {
    if (_disposed) return;
    if (snapshot['state'] is! Map || snapshot['revision'] is! num) {
      throw const ApiException(
          'The simulation service returned an invalid snapshot.');
    }
    if ((snapshot['revision'] as num) <
        (_simulationSnapshot?['revision'] as num? ?? -1)) {
      return;
    }
    _simulationSnapshot = snapshot;
    _simulationError = null;
  }

  Future<void> _loadSimulation() => _simulationLoading ??=
      _readSimulation().whenComplete(() => _simulationLoading = null);

  Future<void> _readSimulation() async {
    final service = simulationService;
    if (service == null) return;
    try {
      _acceptSimulation(await service.load());
    } catch (error) {
      if (!_disposed) _simulationError = error.toString();
    }
  }

  Future<JsonMap> _simulationCommand(String type, JsonMap payload) async {
    final service = simulationService;
    if (service == null) {
      throw const ApiException(
          'This simulation is not connected. No changes were made.');
    }
    // An explicit real project/connector target can never enter a simulator.
    final projectId = payload['projectId'];
    final connectorId = payload['connectorId'];
    if ((projectId != null && !_isSimulatedProject('$projectId')) ||
        (connectorId != null &&
            _connectors.any((c) => c['id'] == connectorId) &&
            !asMaps(_simulationState['connectors'])
                .any((c) => c['id'] == connectorId))) {
      throw const ApiException(
          'This action is not implemented for the selected backend record.');
    }
    final response = await service.command(type, payload);
    _acceptSimulation(asMap(response['snapshot']));
    if (type == 'project.delete' && _activeProject == payload['projectId']) {
      await setActiveProject(null);
    }
    final snapshot = _snapshot();
    if (!_disposed) _updates.add(snapshot);
    return {'snapshot': snapshot, 'result': asMap(response['result'])};
  }

  void _save(Workspace workspace) {
    final id = workspace.project.id;
    if (_disposed ||
        _deletedProjects.contains(id) ||
        (_workspaces[id]?.project.revision ?? -1) >
            workspace.project.revision) {
      return;
    }
    _workspaces[id] = workspace;
    _projects[id] = workspace.project;
  }

  void _listen(String id, int epoch) {
    if (_disposed || epoch != _streamEpoch) return;
    _events = repository.events(id, _cursor, onConnected: () {
      if (_disposed || epoch != _streamEpoch) return;
      _connected = true;
      _streamError = null;
      _publish();
    }).listen((event) {
      if (_disposed || epoch != _streamEpoch || event.sequence <= _cursor) {
        return;
      }
      _cursor = event.sequence;
      final activity = _activity.putIfAbsent(id, () => []);
      activity.insert(0, {
        't': event.createdAt,
        'text': event.type,
        'data': event.data,
        'sequence': event.sequence,
      });
      if (activity.length > 200) activity.removeLast();
      _refreshTimer?.cancel();
      _refreshTimer = Timer(
          const Duration(milliseconds: 120), () => _refreshActive(id, epoch));
    }, onError: (Object error) {
      if (_disposed || epoch != _streamEpoch) return;
      _streamError = error.toString();
      _reconnect(id, epoch);
    }, onDone: () => _reconnect(id, epoch));
  }

  Future<void> _refreshActive(String id, int epoch) async {
    try {
      final workspace = await repository.workspace(id);
      if (_disposed || epoch != _streamEpoch) return;
      _save(workspace);
      _publish();
    } catch (error) {
      if (_disposed || epoch != _streamEpoch) return;
      _streamError = error.toString();
      _publish();
    }
  }

  void _reconnect(String id, int epoch) {
    if (_disposed || epoch != _streamEpoch) return;
    _connected = false;
    _publish();
    _reconnectTimer?.cancel();
    _reconnectTimer = Timer(const Duration(seconds: 2), () async {
      final previous = _events;
      _events = null;
      if (previous != null) unawaited(previous.cancel());
      // A fresh consistent snapshot also recovers gaps or an expired cursor.
      await _refreshActive(id, epoch);
      if (_disposed || epoch != _streamEpoch) return;
      _cursor = _workspaces[id]?.eventCursor ?? _cursor;
      _listen(id, epoch);
    });
  }

  @override
  Future<JsonMap> command(String type, JsonMap payload) async {
    if (_disposed) throw const ApiException('The connection is closed.');
    if (type == 'project.create' && payload['simulation'] == true) {
      return _simulationCommand(type, {'brief': payload['brief']});
    }
    final id = payload['projectId'] as String?;
    if (type != 'project.create' &&
        (!type.startsWith('project.') ||
            (id != null && _isSimulatedProject(id)))) {
      return _simulationCommand(type, payload);
    }
    int? revision;
    if (type != 'project.create') {
      if (id == null) throw const ApiException('Choose a project first.');
      // Use the revision visible to the user. Never silently rebase a command.
      final current = _workspaces[id]?.project ?? _projects[id];
      if (current == null) {
        throw const ApiException('Reload this project before making a change.');
      }
      revision = current.revision;
    }
    if (type == 'project.delete') {
      final result = await repository.deleteProject(id!, revision: revision!);
      if (_disposed) return {'snapshot': <String, dynamic>{}, 'result': result};
      _deletedProjects.add(id);
      _projects.remove(id);
      _workspaces.remove(id);
      _activity.remove(id);
      _lastRealLoad = null;
      if (_activeProject == id) await setActiveProject(null);
      final snapshot = _snapshot();
      if (!_disposed) _updates.add(snapshot);
      return {'snapshot': snapshot, 'result': result};
    }
    final accepted =
        await repository.command(type, payload, revision: revision);
    try {
      _save(await repository.workspace(accepted.projectId));
    } catch (error) {
      // Submission already succeeded; never invite a duplicate paid action
      // merely because its follow-up status read failed.
      _streamError = 'Command accepted. Refreshing its status failed: $error';
    }
    final snapshot = _snapshot();
    if (!_disposed) _updates.add(snapshot);
    return {
      'snapshot': snapshot,
      'result': {
        'projectId': accepted.projectId,
        'commandId': accepted.commandId,
        'runId': accepted.runId,
        'revision': accepted.revision,
      },
    };
  }

  JsonMap _snapshot() => {
        // The legacy store needs one monotonic view revision across projects.
        'revision': ++_revision,
        'demo': {'speed': asMap(_simulationSnapshot?['demo'])['speed'] ?? 1},
        'state': {
          'humanTasks': <JsonMap>[],
          'demand': <JsonMap>[],
          'providerJobs': <JsonMap>[],
          'agentExchange': <JsonMap>[],
          ..._simulationState,
          'production': true,
          'simulationAvailable': _simulationSnapshot != null,
          'simulationError': _simulationError,
          'projects': [
            for (final p in _projects.values) _project(p),
            for (final p in asMaps(_simulationState['projects']))
              if (!_projects.containsKey(p['id']))
                {...p, 'production': false, 'simulation': true},
          ],
          'connectors': [
            for (final c in asMaps(_simulationState['connectors']))
              {...c, 'production': false, 'simulation': true},
            for (final c in _connectors)
              if (!asMaps(_simulationState['connectors'])
                  .any((fixture) => fixture['id'] == c['id']))
                c,
          ],
          'liveConnected': _connected,
          'liveError': _streamError,
          'connectorError': _connectorError,
        },
      };

  JsonMap _project(ProjectRecord project) {
    final workspace = _workspaces[project.id];
    final data =
        workspace != null && workspace.project.revision >= project.revision
            ? workspace.project
            : project;
    return {
      ...data.data,
      'production': true,
      'simulation': false,
      'productionData': data.data,
      'done': [for (var i = 0; i < 10; i++) i < data.stage],
      'activity': _activity[data.id] ??
          [
            if (workspace != null)
              for (final run in workspace.runs)
                {
                  't': run.data['createdAt'] ?? '',
                  'text': '${run.kind}: ${run.status}',
                },
          ],
      if (workspace != null)
        'productionWorkspace': {
          'project': workspace.project.data,
          'revision': workspace.project.revision,
          'eventCursor': workspace.eventCursor,
          'runs': [for (final run in workspace.runs) run.data],
          'artifacts': [
            for (final artifact in workspace.artifacts) artifact.data
          ],
          'checks': workspace.checks,
          'trainingOptedIn': workspace.trainingOptedIn,
        },
    };
  }

  static JsonMap _connector(JsonMap source) {
    final manifest = asMap(source['manifest']);
    final capabilities = asList(manifest['capabilities']);
    final printer = manifest['transport'] == 'bridge' ||
        capabilities.any((c) => '$c'.startsWith('printer.'));
    final tool = capabilities.any((c) => const [
          'engineering.',
          'mechanics.',
          'electronics.',
          'firmware.',
          'application.',
          'tools.',
        ].any((prefix) => '$c'.startsWith(prefix)));
    return {
      ...source,
      'production': true,
      'simulation': false,
      'kind': printer
          ? 'printer'
          : tool
              ? 'tool'
              : 'service',
      'linked': source['enabled'] == true,
      'category': capabilities.any((c) => '$c'.startsWith('human.'))
          ? 'Human tasks'
          : printer
              ? 'Printers'
              : 'Services',
      'description':
          '${manifest['mode'] ?? 'unavailable'} · ${capabilities.join(', ')}',
      'integration': manifest['transport'],
      'capabilities': capabilities,
      'mode': manifest['mode'],
    };
  }

  void _publish() {
    if (!_disposed) _updates.add(_snapshot());
  }

  @override
  Future<JsonMap> reset() async {
    final service = simulationService;
    if (service == null) {
      throw const ApiException('No simulation is connected.');
    }
    // Reset only the fixture service; accepted engineering records are untouched.
    _simulationSnapshot = null;
    _acceptSimulation(await service.reset());
    return _snapshot();
  }

  @override
  Future<JsonMap> setSpeed(double speed) async {
    final service = simulationService;
    if (service == null) {
      throw const ApiException('No simulation is connected.');
    }
    _acceptSimulation(await service.setSpeed(speed));
    return _snapshot();
  }

  @override
  void close() {
    _disposed = true;
    ++_streamEpoch;
    _refreshTimer?.cancel();
    _reconnectTimer?.cancel();
    unawaited(_events?.cancel());
    unawaited(_updates.close());
    simulationService?.close();
    // The repository and authenticated API are owned by their Riverpod scope.
    super.close();
  }
}
