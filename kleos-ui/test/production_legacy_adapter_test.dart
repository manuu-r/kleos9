import 'dart:async';
import 'dart:convert';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:kleos_ui/data/models/json.dart';
import 'package:kleos_ui/data/models/production.dart';
import 'package:kleos_ui/data/repositories/production_repository.dart';
import 'package:kleos_ui/data/services/command_journal.dart';
import 'package:kleos_ui/data/services/production_api.dart';
import 'package:kleos_ui/data/services/api_service.dart';
import 'package:kleos_ui/data/services/production_legacy_api_service.dart';

ProjectRecord project(String id, int revision) => ProjectRecord({
      'id': id,
      'name': 'Real device $id',
      'brief': 'A device',
      'revision': revision,
      'stage': 2,
      'status': 'blocked',
      'state': {
        'proposal': {'summary': 'Real proposal'}
      },
    });

class Repository extends ProductionRepository {
  Repository({bool failConnectors = false, List<JsonMap> connectors = const []})
      : super(
            ProductionApi(
                client: MockClient((_) async => http.Response(
                    jsonEncode(failConnectors
                        ? {'message': 'Connector catalog unavailable'}
                        : {'connectors': connectors}),
                    failConnectors ? 503 : 200))),
            DurableCommandJournal('adapter-test',
                read: (_) async => null, write: (_, __) async {}));
  final reads = <String>[];
  final controllers = <StreamController<ProjectEvent>>[];
  final cursors = <int>[];
  final commands = <JsonMap>[];
  int revision = 4;
  int summaryReads = 0;
  bool failReads = false;
  bool failCommands = false;
  @override
  Future<({List<ProjectRecord> projects, String? nextCursor})> projects(
      {String? after}) async {
    summaryReads++;
    return (
      projects: [project('p', revision), project('q', 1)],
      nextCursor: null
    );
  }

  @override
  Future<Workspace> workspace(String id) async {
    reads.add(id);
    if (failReads) throw const ProductionApiException('Disconnected');
    return Workspace.fromJson({
      'project': project(id, revision).data,
      'eventCursor': revision,
      'runs': <JsonMap>[],
      'artifacts': <JsonMap>[],
      'checks': <JsonMap>[]
    });
  }

  @override
  Stream<ProjectEvent> events(String id, int cursor,
      {void Function()? onConnected}) {
    cursors.add(cursor);
    final controller = StreamController<ProjectEvent>();
    controllers.add(controller);
    onConnected?.call();
    return controller.stream;
  }

  @override
  Future<AcceptedCommand> command(String type, JsonMap payload,
      {int? revision}) async {
    commands.add({'type': type, 'payload': payload, 'revision': revision});
    if (failCommands) throw const ProductionApiException('Real run rejected');
    return AcceptedCommand.fromJson(
        {'commandId': 'c', 'projectId': 'p', 'runId': 'r', 'revision': 5});
  }

  void close() {
    for (final controller in controllers) {
      unawaited(controller.close());
    }
    api.close();
  }
}

class SimulationService extends ApiService {
  final commands = <JsonMap>[];
  int revision = 1;
  int resets = 0;
  double speed = 1;
  JsonMap get snapshot => {
        'revision': revision,
        'demo': {'speed': speed},
        'state': {
          'projects': [
            {'id': 'fixture', 'name': 'Simulated device', 'stage': 7},
            {'id': 'p', 'name': 'Collision cannot replace real project'},
          ],
          'connectors': [
            {'id': 'fixture-connector', 'name': 'Original connector'}
          ],
          'humanTasks': [
            {'id': 'task', 'state': 'OPEN'}
          ],
          'providerJobs': [
            {'id': 'job'}
          ],
          'dev': {
            'manifest': 'original manifest',
            'terminal': ['ready']
          },
          'agentExchange': [
            {'text': 'simulated exchange'}
          ],
        },
      };
  @override
  Future<JsonMap> load() async => snapshot;
  @override
  Future<JsonMap> command(String type, JsonMap payload) async {
    commands.add({'type': type, 'payload': payload});
    revision++;
    return {
      'snapshot': snapshot,
      'result': {'simulated': true}
    };
  }

  @override
  Future<JsonMap> reset() async {
    resets++;
    revision++;
    return snapshot;
  }

  @override
  Future<JsonMap> setSpeed(double value) async {
    speed = value;
    revision++;
    return snapshot;
  }
}

void main() {
  test('retains all original simulation state beside real records', () async {
    final repository = Repository(connectors: [
      {'id': 'fixture-connector', 'name': 'Collision'},
      {'id': 'registered', 'name': 'Registered connector'},
    ]);
    final simulation = SimulationService();
    final adapter =
        ProductionLegacyApiService(repository, simulationService: simulation);
    addTearDown(() {
      adapter.close();
      repository.close();
    });
    final state = asMap((await adapter.load())['state']);
    expect(
        asMaps(state['projects']).map((p) => p['id']), ['p', 'q', 'fixture']);
    expect(asMaps(state['projects']).first['name'], 'Real device p');
    expect(asMaps(state['projects']).last['simulation'], true);
    expect(asMaps(state['connectors']).map((c) => c['name']),
        ['Original connector', 'Registered connector']);
    expect(asMaps(state['humanTasks']).single['id'], 'task');
    expect(asMaps(state['providerJobs']).single['id'], 'job');
    expect(asMap(state['dev'])['manifest'], 'original manifest');
    expect(asMaps(state['agentExchange']).single['text'], 'simulated exchange');
    simulation.revision++;
    simulation.speed = 4;
    expect(asMap((await adapter.load())['demo'])['speed'], 4);
    expect(repository.summaryReads, 1);
    await adapter.setActiveProject('fixture');
    expect(repository.reads, isEmpty);
  });

  test('simulated commands remain isolated and real failures never fall back',
      () async {
    final repository = Repository();
    final simulation = SimulationService();
    final adapter =
        ProductionLegacyApiService(repository, simulationService: simulation);
    addTearDown(() {
      adapter.close();
      repository.close();
    });
    await adapter.load();
    await adapter.command('project.generate', {'projectId': 'fixture'});
    await adapter
        .command('connector.authorize', {'connectorId': 'fixture-connector'});
    await adapter.command('developer.run', {'step': 'validate'});
    expect(simulation.commands, hasLength(3));
    expect(repository.commands, isEmpty);
    await adapter.command('project.create', {'brief': 'real brief'});
    expect(repository.commands.single['type'], 'project.create');
    repository.failCommands = true;
    await expectLater(adapter.command('project.generate', {'projectId': 'p'}),
        throwsException);
    await expectLater(
        adapter.command('project.generate', {'projectId': 'unknown'}),
        throwsException);
    await expectLater(
        adapter.command('human.accept', {'projectId': 'p'}), throwsException);
    expect(simulation.commands, hasLength(3));
  });

  test('reset and speed affect fixtures without removing real projects',
      () async {
    final repository = Repository();
    final simulation = SimulationService();
    final adapter =
        ProductionLegacyApiService(repository, simulationService: simulation);
    addTearDown(() {
      adapter.close();
      repository.close();
    });
    await adapter.load();
    final faster = await adapter.setSpeed(4);
    expect(asMap(faster['demo'])['speed'], 4);
    final reset = await adapter.reset();
    expect(simulation.resets, 1);
    expect(
        asMaps(asMap(reset['state'])['projects'])
            .where((p) => p['production'] == true),
        hasLength(2));
    expect(repository.commands, isEmpty);
  });

  test('classifies connectors from transport and declared capabilities',
      () async {
    final repository = Repository(connectors: [
      {
        'id': 'bridge',
        'manifest': {'transport': 'bridge'}
      },
      {
        'id': 'printer',
        'manifest': {
          'transport': 'rest',
          'capabilities': ['printer.status']
        }
      },
      {
        'id': 'tool',
        'manifest': {
          'transport': 'mcp',
          'capabilities': ['electronics.simulate']
        }
      },
      {
        'id': 'human',
        'manifest': {
          'transport': 'rest',
          'capabilities': ['human.task']
        }
      },
      {
        'id': 'catalog',
        'manifest': {
          'transport': 'rest',
          'capabilities': ['catalog.search']
        }
      },
      {
        'id': 'describe-tool',
        'manifest': {
          'transport': 'rest',
          'capabilities': ['tools.describe']
        }
      },
    ]);
    final adapter = ProductionLegacyApiService(repository);
    addTearDown(() {
      adapter.close();
      repository.close();
    });
    final snapshot = await adapter.load();
    final connectors = asMaps(asMap(snapshot['state'])['connectors']);
    expect(connectors.map((c) => c['kind']),
        ['printer', 'printer', 'tool', 'service', 'service', 'tool']);
    expect(connectors[3]['category'], 'Human tasks');
    expect(connectors[0]['category'], 'Printers');
  });
  test('connector outage does not hide projects', () async {
    final repository = Repository(failConnectors: true);
    final adapter = ProductionLegacyApiService(repository);
    addTearDown(() {
      adapter.close();
      repository.close();
    });
    final snapshot = await adapter.load();
    final state = asMap(snapshot['state']);
    expect(asMaps(state['projects']), hasLength(2));
    expect(state['connectorError'], contains('Connector catalog unavailable'));
  });
  test('loads summaries and only active workspace; duplicate events ignored',
      () async {
    final repository = Repository();
    final adapter = ProductionLegacyApiService(repository);
    addTearDown(() {
      adapter.close();
      repository.close();
    });
    final initial = await adapter.load();
    expect(repository.reads, isEmpty);
    expect(asMap(initial['state'])['production'], true);
    await adapter.setActiveProject('p');
    await adapter.setActiveProject('p');
    expect(repository.reads, ['p']);
    expect(repository.cursors, [4]);
    final updated = Completer<JsonMap>();
    final subscription = adapter.updates.listen((snapshot) {
      final p = asMaps(asMap(snapshot['state'])['projects']).first;
      if (p['revision'] == 6 && !updated.isCompleted) {
        updated.complete(snapshot);
      }
    });
    repository.revision = 6;
    final event = ProjectEvent.fromJson({
      'sequence': 6,
      'type': 'run.updated',
      'data': <String, dynamic>{},
      'createdAt': '2026-10-30'
    });
    repository.controllers.single.add(event);
    repository.controllers.single.add(event);
    final snapshot = await updated.future.timeout(const Duration(seconds: 2));
    final p = asMaps(asMap(snapshot['state'])['projects']).first;
    expect(asList(p['activity']), hasLength(1));
    expect(asMap(p['productionWorkspace'])['eventCursor'], 6);
    expect(repository.reads, ['p', 'p']);
    await subscription.cancel();
  });
  test(
      'commands use displayed revision and retain accepted result on read failure',
      () async {
    final repository = Repository();
    final adapter = ProductionLegacyApiService(repository);
    addTearDown(() {
      adapter.close();
      repository.close();
    });
    await adapter.load();
    repository.failReads = true;
    final result =
        await adapter.command('project.research', {'projectId': 'p'});
    expect(repository.commands.single['revision'], 4);
    expect(asMap(result['result'])['commandId'], 'c');
    expect(asMap(asMap(result['snapshot'])['state'])['liveError'],
        contains('Command accepted'));
  });
  test('demo reset and unsupported effects never become production commands',
      () async {
    final repository = Repository();
    final adapter = ProductionLegacyApiService(repository);
    addTearDown(() {
      adapter.close();
      repository.close();
    });
    await expectLater(adapter.reset(), throwsException);
    await expectLater(adapter.setSpeed(10), throwsException);
    await expectLater(
        adapter.command('connector.authorize', {}), throwsException);
    expect(repository.commands, isEmpty);
  });
}
