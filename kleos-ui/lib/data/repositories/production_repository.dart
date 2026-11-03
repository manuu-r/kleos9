import '../models/json.dart';
import '../models/api.generated.dart';
import '../models/production.dart';
import '../services/command_journal.dart';
import '../services/production_api.dart';

class ProductionRepository {
  ProductionRepository(this.api, this.journal, {this.organizationId});
  final ProductionApi api;
  final CommandJournal journal;
  final String? organizationId;
  final Map<String, Workspace> _workspaces = {};
  final Map<String, Future<AcceptedCommand>> _inflight = {};

  Future<({List<ProjectRecord> projects, String? nextCursor})> projects(
      {String? after}) async {
    final page = ApiProjectsView.fromJson(await api.get(
            '/projects${after == null ? '' : '?after=${Uri.encodeComponent(after)}'}',
            organizationId: organizationId))
        .toJson();
    return (
      projects: asMaps(page['projects'])
          .map(ProjectRecord.new)
          .toList(growable: false),
      nextCursor: page['nextCursor'] as String?,
    );
  }

  Future<Workspace> workspace(String id) async {
    final incoming = Workspace.fromJson(ApiWorkspaceView.fromJson(await api.get(
            '/projects/${Uri.encodeComponent(id)}/workspace?includeAgentState=true',
            organizationId: organizationId))
        .toJson());
    final current = _workspaces[id];
    if (current != null &&
        current.project.revision > incoming.project.revision) {
      return current;
    }
    _workspaces[id] = incoming;
    return incoming;
  }

  Future<JsonMap> uploadReferenceImage(String projectId,
          {required String name,
          required String mediaType,
          required String contentBase64}) =>
      api.post(
          '/projects/${Uri.encodeComponent(projectId)}/reference-images',
          {
            'name': name,
            'mediaType': mediaType,
            'contentBase64': contentBase64
          },
          organizationId: organizationId);

  Future<JsonMap> deleteProject(String id, {required int revision}) async {
    final result = await api.delete(
        '/projects/${Uri.encodeComponent(id)}?expectedRevision=$revision',
        organizationId: organizationId);
    _workspaces.remove(id);
    // The server has accepted deletion already. A local journal failure must
    // not make a deleted build reappear or imply the server rejected it.
    try {
      for (final pending in await journal.pending()) {
        if (asMap(pending.body['payload'])['projectId'] == id) {
          await journal.remove(pending.key);
        }
      }
    } catch (_) {
      return {...result, 'localJournalCleanupPending': true};
    }
    return result;
  }

  Future<ModelCatalog> catalog() async =>
      ModelCatalog.fromJson(ApiModelCatalogView.fromJson(
              await api.get('/model-catalog', organizationId: organizationId))
          .toJson());
  Future<RunRecord> run(String id) async => RunRecord(ApiRunView.fromJson(
          await api.get('/runs/${Uri.encodeComponent(id)}',
              organizationId: organizationId))
      .toJson());
  Stream<ProjectEvent> events(String id, int cursor,
          {void Function()? onConnected}) =>
      api.events(id, cursor,
          organizationId: organizationId, onConnected: onConnected);

  Future<AcceptedCommand> command(String type, JsonMap payload,
      {int? revision}) async {
    final pending = await journal.prepare(type, payload, revision);
    return retry(pending);
  }

  Future<AcceptedCommand> retry(PendingCommand command) =>
      _inflight.putIfAbsent(
          command.key,
          () => _dispatch(command).whenComplete(() {
                _inflight.remove(command.key);
              }));

  Future<AcceptedCommand> _dispatch(PendingCommand pending) async {
    try {
      final result = AcceptedCommand.fromJson(ApiCommandAccepted.fromJson(
              await api.post('/commands', pending.body,
                  idempotencyKey: pending.key, organizationId: organizationId))
          .toJson());
      await journal.remove(pending.key);
      return result;
    } on ProductionApiException catch (error) {
      if (error.status != null &&
          error.status! >= 400 &&
          error.status! < 500 &&
          error.status != 408 &&
          error.status != 429) {
        await journal.remove(pending.key);
      }
      rethrow;
    }
  }
}
