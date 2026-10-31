import 'package:flutter/foundation.dart';
import 'json.dart';

const engineeringLanes = <String, String>{
  'coordination': 'Coordination',
  'research': 'Research',
  'mechanics': 'Mechanical design',
  'electronics': 'Electronics',
  'firmware': 'Firmware',
  'application': 'Companion app',
  'review': 'Review',
};

String providerLabel(String provider) => switch (provider) {
      'meta' => 'Muse',
      'modal' => 'Muse on Modal',
      'google' => 'Gemini',
      _ => provider,
    };

@immutable
class ModelChoice {
  const ModelChoice(this.provider, this.model);
  factory ModelChoice.fromJson(JsonMap json) =>
      ModelChoice(json['provider'] as String, json['model'] as String);
  final String provider;
  final String model;
  JsonMap toJson() => {'provider': provider, 'model': model};
}

@immutable
class CatalogModel {
  const CatalogModel(this.choice, this.configured, this.lanes);
  factory CatalogModel.fromJson(JsonMap json) => CatalogModel(
      ModelChoice.fromJson(json),
      json['configured'] == true,
      List<String>.unmodifiable(asList(json['enabledLanes']).cast<String>()));
  final ModelChoice choice;
  final bool configured;
  final List<String> lanes;
}

@immutable
class ModelCatalog {
  const ModelCatalog(this.models, this.defaults);
  factory ModelCatalog.fromJson(JsonMap json) => ModelCatalog(
        List.unmodifiable(asMaps(json['models']).map(CatalogModel.fromJson)),
        Map.unmodifiable(asMap(json['defaultPolicy']).map(
            (key, value) => MapEntry(key, ModelChoice.fromJson(asMap(value))))),
      );
  final List<CatalogModel> models;
  final Map<String, ModelChoice> defaults;
}

@immutable
class ProjectRecord {
  ProjectRecord(JsonMap json) : data = immutableJson(json) as JsonMap;
  final JsonMap data;
  String get id => data['id'] as String;
  String get name => data['name'] as String;
  String get brief => data['brief'] as String;
  int get revision => (data['revision'] as num).toInt();
  int get engineeringRevision =>
      (data['engineeringRevision'] as num? ?? 1).toInt();
  int get stage => (data['stage'] as num? ?? 0).toInt();
  String get status => data['status'] as String? ?? 'draft';
  JsonMap get state => asMap(data['state']);
  JsonMap get interfaces => asMap(data['interfaces']);
  JsonMap get budget => asMap(data['budget']);
  bool get isActive => const {'queued', 'running', 'waiting'}.contains(status);
  Map<String, ModelChoice> get modelPolicy =>
      Map.unmodifiable(asMap(data['modelPolicy']).map(
          (key, value) => MapEntry(key, ModelChoice.fromJson(asMap(value)))));
}

@immutable
class Workspace {
  Workspace.fromJson(JsonMap json)
      : project = ProjectRecord(asMap(json['project'])),
        eventCursor = (json['eventCursor'] as num).toInt(),
        runs = List.unmodifiable(asMaps(json['runs']).map(RunRecord.new)),
        artifacts = List.unmodifiable(
            asMaps(json['artifacts']).map(ArtifactRecord.new)),
        checks = immutableJson(asList(json['checks'])) as List<Object?>,
        trainingOptedIn = json['trainingOptedIn'] == true;
  final ProjectRecord project;
  final int eventCursor;
  final List<RunRecord> runs;
  final List<ArtifactRecord> artifacts;
  final List<Object?> checks;
  final bool trainingOptedIn;
}

@immutable
class RunRecord {
  RunRecord(JsonMap json) : data = immutableJson(json) as JsonMap;
  final JsonMap data;
  String get id => data['id'] as String;
  String get kind => data['kind'] as String;
  String get status => data['status'] as String;
  String? get blocker => data['blocker'] as String?;
  String get traceId => data['traceId'] as String? ?? '';
  List<JsonMap> get tasks => asMaps(data['tasks']);
}

@immutable
class ArtifactRecord {
  ArtifactRecord(JsonMap json) : data = immutableJson(json) as JsonMap;
  final JsonMap data;
  String get id => data['id'] as String;
  String get name => data['name'] as String;
  String get lane => data['lane'] as String? ?? '';
  String get status => data['status'] as String;
  String get sha256 => data['sha256'] as String;
  String get mediaType =>
      data['mediaType'] as String? ?? 'application/octet-stream';
  int get size => (data['size'] as num).toInt();
}

@immutable
class ProjectEvent {
  ProjectEvent.fromJson(JsonMap json)
      : sequence = (json['sequence'] as num).toInt(),
        type = json['type'] as String,
        data = immutableJson(asMap(json['data'])) as JsonMap,
        createdAt = json['createdAt'] as String? ?? '';
  final int sequence;
  final String type;
  final JsonMap data;
  final String createdAt;
}

@immutable
class AcceptedCommand {
  AcceptedCommand.fromJson(JsonMap json)
      : commandId = json['commandId'] as String,
        projectId = json['projectId'] as String,
        runId = json['runId'] as String?,
        revision = (json['revision'] as num).toInt();
  final String commandId;
  final String projectId;
  final String? runId;
  final int revision;
}

@immutable
class AccountSession {
  AccountSession.fromJson(JsonMap json)
      : subject = json['subject'] as String,
        tenantId = json['tenantId'] as String,
        role = json['role'] as String? ?? 'member',
        loginRequired = json['loginRequired'] != false,
        csrfToken = json['csrfToken'] as String?,
        organizations = List.unmodifiable(
            asMaps(json['organizations']).map(OrganizationRecord.fromJson));
  final String subject;
  final String tenantId;
  final String role;
  final bool loginRequired;
  final String? csrfToken;
  final List<OrganizationRecord> organizations;
  bool get canWrite => const {'owner', 'admin', 'member'}.contains(role);
}

@immutable
class OrganizationRecord {
  const OrganizationRecord(this.id, this.name, this.role);
  factory OrganizationRecord.fromJson(JsonMap json) => OrganizationRecord(
      json['id'] as String, json['name'] as String, json['role'] as String);
  final String id;
  final String name;
  final String role;
  JsonMap toJson() => {'id': id, 'name': name, 'role': role};
}
