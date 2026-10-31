// GENERATED CODE - DO NOT EDIT.
// Source: backend/contracts/openapi.json
// Schema SHA-256: 82e48a0e01b9f3537f0e15cf6ac743cc33c5363fb2060cd8b7955ed9477234d3
// Regenerate: python3 tool/generate_transport.py

Map<String, dynamic> _object(Object? value) =>
    Map<String, dynamic>.from(value as Map);

class ApiArtifactDownloadView {
  const ApiArtifactDownloadView({
    required this.url,
    required this.sha256,
    required this.name,
  });
  factory ApiArtifactDownloadView.fromJson(Map<String, dynamic> json) =>
      ApiArtifactDownloadView(
        url: json['url'] as String,
        sha256: json['sha256'] as String,
        name: json['name'] as String,
      );
  final String url;
  final String sha256;
  final String name;
  Map<String, dynamic> toJson() => {
        'url': url,
        'sha256': sha256,
        'name': name,
      };
}

class ApiArtifactView {
  const ApiArtifactView({
    required this.id,
    required this.name,
    required this.lane,
    required this.revision,
    required this.sha256,
    required this.size,
    required this.status,
    required this.mediaType,
    required this.provenance,
  });
  factory ApiArtifactView.fromJson(Map<String, dynamic> json) =>
      ApiArtifactView(
        id: json['id'] as String,
        name: json['name'] as String,
        lane: json['lane'] as String,
        revision: (json['revision'] as num).toInt(),
        sha256: json['sha256'] as String,
        size: (json['size'] as num).toInt(),
        status: json['status'] as String,
        mediaType: json['mediaType'] as String,
        provenance:
            Map<String, dynamic>.unmodifiable(_object(json['provenance'])),
      );
  final String id;
  final String name;
  final String lane;
  final int revision;
  final String sha256;
  final int size;
  final String status;
  final String mediaType;
  final Map<String, dynamic> provenance;
  Map<String, dynamic> toJson() => {
        'id': id,
        'name': name,
        'lane': lane,
        'revision': revision,
        'sha256': sha256,
        'size': size,
        'status': status,
        'mediaType': mediaType,
        'provenance': provenance,
      };
}

class ApiAttemptView {
  const ApiAttemptView({
    required this.id,
    required this.number,
    required this.status,
    required this.provider,
    required this.model,
    required this.policyRevision,
    required this.error,
    required this.contextManifest,
    required this.output,
    required this.calls,
  });
  factory ApiAttemptView.fromJson(Map<String, dynamic> json) => ApiAttemptView(
        id: json['id'] as String,
        number: (json['number'] as num).toInt(),
        status: json['status'] as String,
        provider: json['provider'] as String,
        model: json['model'] as String,
        policyRevision: (json['policyRevision'] as num).toInt(),
        error: json['error'] == null ? null : json['error'] as String,
        contextManifest:
            Map<String, dynamic>.unmodifiable(_object(json['contextManifest'])),
        output: Map<String, dynamic>.unmodifiable(_object(json['output'])),
        calls: List<ApiInvocationView>.unmodifiable((json['calls'] as List)
            .map((v) => ApiInvocationView.fromJson(_object(v)))),
      );
  final String id;
  final int number;
  final String status;
  final String provider;
  final String model;
  final int policyRevision;
  final String? error;
  final Map<String, dynamic> contextManifest;
  final Map<String, dynamic> output;
  final List<ApiInvocationView> calls;
  Map<String, dynamic> toJson() => {
        'id': id,
        'number': number,
        'status': status,
        'provider': provider,
        'model': model,
        'policyRevision': policyRevision,
        'error': error,
        'contextManifest': contextManifest,
        'output': output,
        'calls': calls.map((v) => v.toJson()).toList(growable: false),
      };
}

class ApiBudgetView {
  const ApiBudgetView({
    required this.allowanceUsd,
    this.unlimited,
    required this.spentEstimateUsd,
    required this.reservedUsd,
  });
  factory ApiBudgetView.fromJson(Map<String, dynamic> json) => ApiBudgetView(
        allowanceUsd: json['allowanceUsd'] == null
            ? null
            : (json['allowanceUsd'] as num).toDouble(),
        unlimited: json['unlimited'] == null ? null : json['unlimited'] as bool,
        spentEstimateUsd: (json['spentEstimateUsd'] as num).toDouble(),
        reservedUsd: (json['reservedUsd'] as num).toDouble(),
      );
  final double? allowanceUsd;
  final bool? unlimited;
  final double spentEstimateUsd;
  final double reservedUsd;
  Map<String, dynamic> toJson() => {
        'allowanceUsd': allowanceUsd,
        if (unlimited != null) 'unlimited': unlimited,
        'spentEstimateUsd': spentEstimateUsd,
        'reservedUsd': reservedUsd,
      };
}

class ApiCircuitSimulationView {
  const ApiCircuitSimulationView({
    required this.revision,
    required this.engineeringRevision,
    required this.status,
    required this.artifactId,
    required this.artifactSha256,
    required this.result,
    required this.reason,
  });
  factory ApiCircuitSimulationView.fromJson(Map<String, dynamic> json) =>
      ApiCircuitSimulationView(
        revision: (json['revision'] as num).toInt(),
        engineeringRevision: (json['engineeringRevision'] as num).toInt(),
        status: json['status'] as String,
        artifactId:
            json['artifactId'] == null ? null : json['artifactId'] as String,
        artifactSha256: json['artifactSha256'] == null
            ? null
            : json['artifactSha256'] as String,
        result: json['result'] == null
            ? null
            : Map<String, dynamic>.unmodifiable(_object(json['result'])),
        reason: json['reason'] == null ? null : json['reason'] as String,
      );
  final int revision;
  final int engineeringRevision;
  final String status;
  final String? artifactId;
  final String? artifactSha256;
  final Map<String, dynamic>? result;
  final String? reason;
  Map<String, dynamic> toJson() => {
        'revision': revision,
        'engineeringRevision': engineeringRevision,
        'status': status,
        'artifactId': artifactId,
        'artifactSha256': artifactSha256,
        'result': result,
        'reason': reason,
      };
}

class ApiCommand {
  const ApiCommand({
    required this.type,
    this.payload,
    this.expectedRevision,
  });
  factory ApiCommand.fromJson(Map<String, dynamic> json) => ApiCommand(
        type: json['type'] as String,
        payload: json['payload'] == null
            ? null
            : Map<String, dynamic>.unmodifiable(_object(json['payload'])),
        expectedRevision: json['expectedRevision'] == null
            ? null
            : (json['expectedRevision'] as num).toInt(),
      );
  final String type;
  final Map<String, dynamic>? payload;
  final int? expectedRevision;
  Map<String, dynamic> toJson() => {
        'type': type,
        if (payload != null) 'payload': payload,
        if (expectedRevision != null) 'expectedRevision': expectedRevision,
      };
}

class ApiCommandAccepted {
  const ApiCommandAccepted({
    required this.commandId,
    required this.projectId,
    this.runId,
    required this.revision,
  });
  factory ApiCommandAccepted.fromJson(Map<String, dynamic> json) =>
      ApiCommandAccepted(
        commandId: json['commandId'] as String,
        projectId: json['projectId'] as String,
        runId: json['runId'] == null ? null : json['runId'] as String,
        revision: (json['revision'] as num).toInt(),
      );
  final String commandId;
  final String projectId;
  final String? runId;
  final int revision;
  Map<String, dynamic> toJson() => {
        'commandId': commandId,
        'projectId': projectId,
        if (runId != null) 'runId': runId,
        'revision': revision,
      };
}

class ApiCompanionPreviewRequest {
  const ApiCompanionPreviewRequest({
    required this.artifactId,
  });
  factory ApiCompanionPreviewRequest.fromJson(Map<String, dynamic> json) =>
      ApiCompanionPreviewRequest(
        artifactId: json['artifactId'] as String,
      );
  final String artifactId;
  Map<String, dynamic> toJson() => {
        'artifactId': artifactId,
      };
}

class ApiCompanionPreviewView {
  const ApiCompanionPreviewView({
    required this.url,
    required this.expiresAt,
    required this.artifactId,
    required this.sha256,
    this.mode,
    required this.limitations,
  });
  factory ApiCompanionPreviewView.fromJson(Map<String, dynamic> json) =>
      ApiCompanionPreviewView(
        url: json['url'] as String,
        expiresAt: json['expiresAt'] as String,
        artifactId: json['artifactId'] as String,
        sha256: json['sha256'] as String,
        mode: json['mode'] == null ? null : json['mode'] as String,
        limitations: List<String>.unmodifiable(
            (json['limitations'] as List).map((v) => v as String)),
      );
  final String url;
  final String expiresAt;
  final String artifactId;
  final String sha256;
  final String? mode;
  final List<String> limitations;
  Map<String, dynamic> toJson() => {
        'url': url,
        'expiresAt': expiresAt,
        'artifactId': artifactId,
        'sha256': sha256,
        if (mode != null) 'mode': mode,
        'limitations': limitations.map((v) => v).toList(growable: false),
      };
}

class ApiEventView {
  const ApiEventView({
    required this.id,
    required this.sequence,
    required this.type,
    required this.data,
    required this.traceId,
    required this.createdAt,
  });
  factory ApiEventView.fromJson(Map<String, dynamic> json) => ApiEventView(
        id: json['id'] as String,
        sequence: (json['sequence'] as num).toInt(),
        type: json['type'] as String,
        data: Map<String, dynamic>.unmodifiable(_object(json['data'])),
        traceId: json['traceId'] == null ? null : json['traceId'] as String,
        createdAt: json['createdAt'] as String,
      );
  final String id;
  final int sequence;
  final String type;
  final Map<String, dynamic> data;
  final String? traceId;
  final String createdAt;
  Map<String, dynamic> toJson() => {
        'id': id,
        'sequence': sequence,
        'type': type,
        'data': data,
        'traceId': traceId,
        'createdAt': createdAt,
      };
}

class ApiInvocationView {
  const ApiInvocationView({
    required this.id,
    required this.operation,
    required this.status,
    required this.usage,
    required this.estimatedUsd,
    required this.hasTranscript,
  });
  factory ApiInvocationView.fromJson(Map<String, dynamic> json) =>
      ApiInvocationView(
        id: json['id'] as String,
        operation: json['operation'] as String,
        status: json['status'] as String,
        usage: Map<String, dynamic>.unmodifiable(_object(json['usage'])),
        estimatedUsd: (json['estimatedUsd'] as num).toDouble(),
        hasTranscript: json['hasTranscript'] as bool,
      );
  final String id;
  final String operation;
  final String status;
  final Map<String, dynamic> usage;
  final double estimatedUsd;
  final bool hasTranscript;
  Map<String, dynamic> toJson() => {
        'id': id,
        'operation': operation,
        'status': status,
        'usage': usage,
        'estimatedUsd': estimatedUsd,
        'hasTranscript': hasTranscript,
      };
}

class ApiModelCatalogView {
  const ApiModelCatalogView({
    required this.models,
    required this.defaultPolicy,
    required this.billing,
  });
  factory ApiModelCatalogView.fromJson(Map<String, dynamic> json) =>
      ApiModelCatalogView(
        models: List<ApiModelView>.unmodifiable((json['models'] as List)
            .map((v) => ApiModelView.fromJson(_object(v)))),
        defaultPolicy: Map<String, ApiModelChoice>.unmodifiable(
            _object(json['defaultPolicy']).map(
                (k, v) => MapEntry(k, ApiModelChoice.fromJson(_object(v))))),
        billing: json['billing'] as String,
      );
  final List<ApiModelView> models;
  final Map<String, ApiModelChoice> defaultPolicy;
  final String billing;
  Map<String, dynamic> toJson() => {
        'models': models.map((v) => v.toJson()).toList(growable: false),
        'defaultPolicy': defaultPolicy.map((k, v) => MapEntry(k, v.toJson())),
        'billing': billing,
      };
}

class ApiModelChoice {
  const ApiModelChoice({
    required this.provider,
    required this.model,
  });
  factory ApiModelChoice.fromJson(Map<String, dynamic> json) => ApiModelChoice(
        provider: json['provider'] as String,
        model: json['model'] as String,
      );
  final String provider;
  final String model;
  Map<String, dynamic> toJson() => {
        'provider': provider,
        'model': model,
      };
}

class ApiModelView {
  const ApiModelView({
    required this.provider,
    required this.model,
    required this.configured,
    required this.qualification,
    required this.enabledLanes,
    required this.capabilities,
    required this.creditBalance,
    required this.contextCharacterCeiling,
    required this.pricingVersion,
    required this.contextLimit,
  });
  factory ApiModelView.fromJson(Map<String, dynamic> json) => ApiModelView(
        provider: json['provider'] as String,
        model: json['model'] as String,
        configured: json['configured'] as bool,
        qualification: json['qualification'] as String,
        enabledLanes: List<String>.unmodifiable(
            (json['enabledLanes'] as List).map((v) => v as String)),
        capabilities: List<String>.unmodifiable(
            (json['capabilities'] as List).map((v) => v as String)),
        creditBalance: json['creditBalance'] == null
            ? null
            : (json['creditBalance'] as num).toDouble(),
        contextCharacterCeiling:
            (json['contextCharacterCeiling'] as num).toInt(),
        pricingVersion: json['pricingVersion'] as String,
        contextLimit: json['contextLimit'] == null
            ? null
            : (json['contextLimit'] as num).toInt(),
      );
  final String provider;
  final String model;
  final bool configured;
  final String qualification;
  final List<String> enabledLanes;
  final List<String> capabilities;
  final double? creditBalance;
  final int contextCharacterCeiling;
  final String pricingVersion;
  final int? contextLimit;
  Map<String, dynamic> toJson() => {
        'provider': provider,
        'model': model,
        'configured': configured,
        'qualification': qualification,
        'enabledLanes': enabledLanes.map((v) => v).toList(growable: false),
        'capabilities': capabilities.map((v) => v).toList(growable: false),
        'creditBalance': creditBalance,
        'contextCharacterCeiling': contextCharacterCeiling,
        'pricingVersion': pricingVersion,
        'contextLimit': contextLimit,
      };
}

class ApiProjectView {
  const ApiProjectView({
    required this.id,
    required this.name,
    required this.brief,
    required this.revision,
    required this.engineeringRevision,
    required this.policyRevision,
    required this.modelPolicy,
    required this.stage,
    required this.status,
    required this.requirements,
    required this.interfaces,
    required this.state,
    required this.acceptedReleaseId,
    required this.budget,
    required this.mode,
    required this.createdAt,
  });
  factory ApiProjectView.fromJson(Map<String, dynamic> json) => ApiProjectView(
        id: json['id'] as String,
        name: json['name'] as String,
        brief: json['brief'] as String,
        revision: (json['revision'] as num).toInt(),
        engineeringRevision: (json['engineeringRevision'] as num).toInt(),
        policyRevision: (json['policyRevision'] as num).toInt(),
        modelPolicy: Map<String, ApiModelChoice>.unmodifiable(
            _object(json['modelPolicy']).map(
                (k, v) => MapEntry(k, ApiModelChoice.fromJson(_object(v))))),
        stage: (json['stage'] as num).toInt(),
        status: json['status'] as String,
        requirements:
            Map<String, dynamic>.unmodifiable(_object(json['requirements'])),
        interfaces:
            Map<String, dynamic>.unmodifiable(_object(json['interfaces'])),
        state: Map<String, dynamic>.unmodifiable(_object(json['state'])),
        acceptedReleaseId: json['acceptedReleaseId'] == null
            ? null
            : json['acceptedReleaseId'] as String,
        budget: ApiBudgetView.fromJson(_object(json['budget'])),
        mode: json['mode'] as String,
        createdAt: json['createdAt'] as String,
      );
  final String id;
  final String name;
  final String brief;
  final int revision;
  final int engineeringRevision;
  final int policyRevision;
  final Map<String, ApiModelChoice> modelPolicy;
  final int stage;
  final String status;
  final Map<String, dynamic> requirements;
  final Map<String, dynamic> interfaces;
  final Map<String, dynamic> state;
  final String? acceptedReleaseId;
  final ApiBudgetView budget;
  final String mode;
  final String createdAt;
  Map<String, dynamic> toJson() => {
        'id': id,
        'name': name,
        'brief': brief,
        'revision': revision,
        'engineeringRevision': engineeringRevision,
        'policyRevision': policyRevision,
        'modelPolicy': modelPolicy.map((k, v) => MapEntry(k, v.toJson())),
        'stage': stage,
        'status': status,
        'requirements': requirements,
        'interfaces': interfaces,
        'state': state,
        'acceptedReleaseId': acceptedReleaseId,
        'budget': budget.toJson(),
        'mode': mode,
        'createdAt': createdAt,
      };
}

class ApiProjectsView {
  const ApiProjectsView({
    required this.projects,
    required this.nextCursor,
  });
  factory ApiProjectsView.fromJson(Map<String, dynamic> json) =>
      ApiProjectsView(
        projects: List<ApiProjectView>.unmodifiable((json['projects'] as List)
            .map((v) => ApiProjectView.fromJson(_object(v)))),
        nextCursor:
            json['nextCursor'] == null ? null : json['nextCursor'] as String,
      );
  final List<ApiProjectView> projects;
  final String? nextCursor;
  Map<String, dynamic> toJson() => {
        'projects': projects.map((v) => v.toJson()).toList(growable: false),
        'nextCursor': nextCursor,
      };
}

class ApiRunSummary {
  const ApiRunSummary({
    required this.id,
    required this.projectId,
    required this.kind,
    required this.status,
    required this.revision,
    required this.traceId,
    required this.blocker,
    required this.createdAt,
  });
  factory ApiRunSummary.fromJson(Map<String, dynamic> json) => ApiRunSummary(
        id: json['id'] as String,
        projectId: json['projectId'] as String,
        kind: json['kind'] as String,
        status: json['status'] as String,
        revision: (json['revision'] as num).toInt(),
        traceId: json['traceId'] as String,
        blocker: json['blocker'] == null ? null : json['blocker'] as String,
        createdAt: json['createdAt'] as String,
      );
  final String id;
  final String projectId;
  final String kind;
  final String status;
  final int revision;
  final String traceId;
  final String? blocker;
  final String createdAt;
  Map<String, dynamic> toJson() => {
        'id': id,
        'projectId': projectId,
        'kind': kind,
        'status': status,
        'revision': revision,
        'traceId': traceId,
        'blocker': blocker,
        'createdAt': createdAt,
      };
}

class ApiRunView {
  const ApiRunView({
    required this.id,
    required this.projectId,
    required this.kind,
    required this.status,
    required this.revision,
    required this.traceId,
    required this.blocker,
    required this.createdAt,
    required this.tasks,
  });
  factory ApiRunView.fromJson(Map<String, dynamic> json) => ApiRunView(
        id: json['id'] as String,
        projectId: json['projectId'] as String,
        kind: json['kind'] as String,
        status: json['status'] as String,
        revision: (json['revision'] as num).toInt(),
        traceId: json['traceId'] as String,
        blocker: json['blocker'] == null ? null : json['blocker'] as String,
        createdAt: json['createdAt'] as String,
        tasks: List<ApiTaskView>.unmodifiable((json['tasks'] as List)
            .map((v) => ApiTaskView.fromJson(_object(v)))),
      );
  final String id;
  final String projectId;
  final String kind;
  final String status;
  final int revision;
  final String traceId;
  final String? blocker;
  final String createdAt;
  final List<ApiTaskView> tasks;
  Map<String, dynamic> toJson() => {
        'id': id,
        'projectId': projectId,
        'kind': kind,
        'status': status,
        'revision': revision,
        'traceId': traceId,
        'blocker': blocker,
        'createdAt': createdAt,
        'tasks': tasks.map((v) => v.toJson()).toList(growable: false),
      };
}

class ApiTaskView {
  const ApiTaskView({
    required this.id,
    required this.lane,
    required this.status,
    required this.dependencies,
    required this.result,
    required this.attempts,
    required this.nativeJobs,
  });
  factory ApiTaskView.fromJson(Map<String, dynamic> json) => ApiTaskView(
        id: json['id'] as String,
        lane: json['lane'] as String,
        status: json['status'] as String,
        dependencies: List<Object?>.unmodifiable(
            (json['dependencies'] as List).map((v) => v)),
        result: Map<String, dynamic>.unmodifiable(_object(json['result'])),
        attempts: List<ApiAttemptView>.unmodifiable((json['attempts'] as List)
            .map((v) => ApiAttemptView.fromJson(_object(v)))),
        nativeJobs: List<Map<String, dynamic>>.unmodifiable(
            (json['nativeJobs'] as List)
                .map((v) => Map<String, dynamic>.unmodifiable(_object(v)))),
      );
  final String id;
  final String lane;
  final String status;
  final List<Object?> dependencies;
  final Map<String, dynamic> result;
  final List<ApiAttemptView> attempts;
  final List<Map<String, dynamic>> nativeJobs;
  Map<String, dynamic> toJson() => {
        'id': id,
        'lane': lane,
        'status': status,
        'dependencies': dependencies.map((v) => v).toList(growable: false),
        'result': result,
        'attempts': attempts.map((v) => v.toJson()).toList(growable: false),
        'nativeJobs': nativeJobs.map((v) => v).toList(growable: false),
      };
}

class ApiValidationView {
  const ApiValidationView({
    required this.id,
    required this.name,
    required this.status,
    required this.evidenceKind,
    required this.details,
  });
  factory ApiValidationView.fromJson(Map<String, dynamic> json) =>
      ApiValidationView(
        id: json['id'] as String,
        name: json['name'] as String,
        status: json['status'] as String,
        evidenceKind: json['evidenceKind'] as String,
        details: Map<String, dynamic>.unmodifiable(_object(json['details'])),
      );
  final String id;
  final String name;
  final String status;
  final String evidenceKind;
  final Map<String, dynamic> details;
  Map<String, dynamic> toJson() => {
        'id': id,
        'name': name,
        'status': status,
        'evidenceKind': evidenceKind,
        'details': details,
      };
}

class ApiWorkspaceView {
  const ApiWorkspaceView({
    required this.project,
    required this.revision,
    required this.eventCursor,
    required this.runs,
    required this.artifacts,
    required this.checks,
    required this.trainingOptedIn,
  });
  factory ApiWorkspaceView.fromJson(Map<String, dynamic> json) =>
      ApiWorkspaceView(
        project: ApiProjectView.fromJson(_object(json['project'])),
        revision: (json['revision'] as num).toInt(),
        eventCursor: (json['eventCursor'] as num).toInt(),
        runs: List<ApiRunSummary>.unmodifiable((json['runs'] as List)
            .map((v) => ApiRunSummary.fromJson(_object(v)))),
        artifacts: List<ApiArtifactView>.unmodifiable(
            (json['artifacts'] as List)
                .map((v) => ApiArtifactView.fromJson(_object(v)))),
        checks: List<ApiValidationView>.unmodifiable((json['checks'] as List)
            .map((v) => ApiValidationView.fromJson(_object(v)))),
        trainingOptedIn: json['trainingOptedIn'] as bool,
      );
  final ApiProjectView project;
  final int revision;
  final int eventCursor;
  final List<ApiRunSummary> runs;
  final List<ApiArtifactView> artifacts;
  final List<ApiValidationView> checks;
  final bool trainingOptedIn;
  Map<String, dynamic> toJson() => {
        'project': project.toJson(),
        'revision': revision,
        'eventCursor': eventCursor,
        'runs': runs.map((v) => v.toJson()).toList(growable: false),
        'artifacts': artifacts.map((v) => v.toJson()).toList(growable: false),
        'checks': checks.map((v) => v.toJson()).toList(growable: false),
        'trainingOptedIn': trainingOptedIn,
      };
}
