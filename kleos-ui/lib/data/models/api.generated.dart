// GENERATED CODE - DO NOT EDIT.
// Source: backend/contracts/openapi.json
// Schema SHA-256: 046f5eab03ba5306e4e8907105e8b75f3d1a68624b87cb7a80c7e6b49b651241
// Regenerate: python3 tool/generate_transport.py

Map<String, dynamic> _object(Object? value) =>
    Map<String, dynamic>.from(value as Map);

class ApiAgentPluginView {
  const ApiAgentPluginView({
    required this.id,
    required this.version,
    required this.name,
    required this.qualified,
    required this.qualificationReference,
    required this.limitations,
    required this.schemaVersion,
    required this.agentType,
    required this.capabilities,
    required this.supportedInputs,
    required this.resultSchema,
    required this.operationModes,
    required this.mode,
    required this.qualificationFixtures,
    required this.supportsIdempotency,
    required this.supportsCancellation,
    required this.configured,
    required this.isDefault,
  });
  factory ApiAgentPluginView.fromJson(Map<String, dynamic> json) =>
      ApiAgentPluginView(
        id: json['id'] as String,
        version: json['version'] as String,
        name: json['name'] as String,
        qualified: json['qualified'] as bool,
        qualificationReference: json['qualificationReference'] == null
            ? null
            : json['qualificationReference'] as String,
        limitations: List<String>.unmodifiable(
            (json['limitations'] as List).map((v) => v as String)),
        schemaVersion: json['schemaVersion'] as String,
        agentType: json['agentType'] as String,
        capabilities: List<String>.unmodifiable(
            (json['capabilities'] as List).map((v) => v as String)),
        supportedInputs: List<String>.unmodifiable(
            (json['supportedInputs'] as List).map((v) => v as String)),
        resultSchema: json['resultSchema'] as String,
        operationModes: Map<String, String>.unmodifiable(
            _object(json['operationModes'])
                .map((k, v) => MapEntry(k, v as String))),
        mode: json['mode'] as String,
        qualificationFixtures: List<String>.unmodifiable(
            (json['qualificationFixtures'] as List).map((v) => v as String)),
        supportsIdempotency: json['supportsIdempotency'] as bool,
        supportsCancellation: json['supportsCancellation'] as bool,
        configured: json['configured'] as bool,
        isDefault: json['isDefault'] as bool,
      );
  final String id;
  final String version;
  final String name;
  final bool qualified;
  final String? qualificationReference;
  final List<String> limitations;
  final String schemaVersion;
  final String agentType;
  final List<String> capabilities;
  final List<String> supportedInputs;
  final String resultSchema;
  final Map<String, String> operationModes;
  final String mode;
  final List<String> qualificationFixtures;
  final bool supportsIdempotency;
  final bool supportsCancellation;
  final bool configured;
  final bool isDefault;
  Map<String, dynamic> toJson() => {
        'id': id,
        'version': version,
        'name': name,
        'qualified': qualified,
        'qualificationReference': qualificationReference,
        'limitations': limitations.map((v) => v).toList(growable: false),
        'schemaVersion': schemaVersion,
        'agentType': agentType,
        'capabilities': capabilities.map((v) => v).toList(growable: false),
        'supportedInputs':
            supportedInputs.map((v) => v).toList(growable: false),
        'resultSchema': resultSchema,
        'operationModes': operationModes.map((k, v) => MapEntry(k, v)),
        'mode': mode,
        'qualificationFixtures':
            qualificationFixtures.map((v) => v).toList(growable: false),
        'supportsIdempotency': supportsIdempotency,
        'supportsCancellation': supportsCancellation,
        'configured': configured,
        'isDefault': isDefault,
      };
}

class ApiAgentSummary {
  const ApiAgentSummary({
    required this.id,
    required this.agentType,
    required this.lifetime,
    required this.lifecycle,
    required this.engineeringRevision,
    required this.historical,
    required this.graph,
    this.pluginId,
    this.pluginVersion,
    this.generation,
    this.currentNode,
    this.lastUsefulObservation,
    this.nextWakeAt,
    this.deadline,
    this.remainingAllowanceUsd,
    this.blockerReason,
  });
  factory ApiAgentSummary.fromJson(Map<String, dynamic> json) =>
      ApiAgentSummary(
        id: json['id'] as String,
        agentType: json['agentType'] as String,
        lifetime: json['lifetime'] as String,
        lifecycle: json['lifecycle'] as String,
        engineeringRevision: (json['engineeringRevision'] as num).toInt(),
        historical: json['historical'] as bool,
        graph: json['graph'] as String,
        pluginId: json['pluginId'] == null ? null : json['pluginId'] as String,
        pluginVersion: json['pluginVersion'] == null
            ? null
            : json['pluginVersion'] as String,
        generation: json['generation'] == null
            ? null
            : (json['generation'] as num).toInt(),
        currentNode:
            json['currentNode'] == null ? null : json['currentNode'] as String,
        lastUsefulObservation: json['lastUsefulObservation'] == null
            ? null
            : json['lastUsefulObservation'] as String,
        nextWakeAt:
            json['nextWakeAt'] == null ? null : json['nextWakeAt'] as String,
        deadline: json['deadline'] == null ? null : json['deadline'] as String,
        remainingAllowanceUsd: json['remainingAllowanceUsd'] == null
            ? null
            : (json['remainingAllowanceUsd'] as num).toDouble(),
        blockerReason: json['blockerReason'] == null
            ? null
            : json['blockerReason'] as String,
      );
  final String id;
  final String agentType;
  final String lifetime;
  final String lifecycle;
  final int engineeringRevision;
  final bool historical;
  final String graph;
  final String? pluginId;
  final String? pluginVersion;
  final int? generation;
  final String? currentNode;
  final String? lastUsefulObservation;
  final String? nextWakeAt;
  final String? deadline;
  final double? remainingAllowanceUsd;
  final String? blockerReason;
  Map<String, dynamic> toJson() => {
        'id': id,
        'agentType': agentType,
        'lifetime': lifetime,
        'lifecycle': lifecycle,
        'engineeringRevision': engineeringRevision,
        'historical': historical,
        'graph': graph,
        if (pluginId != null) 'pluginId': pluginId,
        if (pluginVersion != null) 'pluginVersion': pluginVersion,
        if (generation != null) 'generation': generation,
        if (currentNode != null) 'currentNode': currentNode,
        if (lastUsefulObservation != null)
          'lastUsefulObservation': lastUsefulObservation,
        if (nextWakeAt != null) 'nextWakeAt': nextWakeAt,
        if (deadline != null) 'deadline': deadline,
        if (remainingAllowanceUsd != null)
          'remainingAllowanceUsd': remainingAllowanceUsd,
        if (blockerReason != null) 'blockerReason': blockerReason,
      };
}

class ApiAgentsPage {
  const ApiAgentsPage({
    required this.agents,
    this.nextCursor,
  });
  factory ApiAgentsPage.fromJson(Map<String, dynamic> json) => ApiAgentsPage(
        agents: List<ApiAgentSummary>.unmodifiable((json['agents'] as List)
            .map((v) => ApiAgentSummary.fromJson(_object(v)))),
        nextCursor:
            json['nextCursor'] == null ? null : json['nextCursor'] as String,
      );
  final List<ApiAgentSummary> agents;
  final String? nextCursor;
  Map<String, dynamic> toJson() => {
        'agents': agents.map((v) => v.toJson()).toList(growable: false),
        if (nextCursor != null) 'nextCursor': nextCursor,
      };
}

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

class ApiBambuAddressRequest {
  const ApiBambuAddressRequest({
    required this.address,
    this.service,
  });
  factory ApiBambuAddressRequest.fromJson(Map<String, dynamic> json) =>
      ApiBambuAddressRequest(
        address: json['address'] as String,
        service: json['service'] == null ? null : json['service'] as String,
      );
  final String address;
  final String? service;
  Map<String, dynamic> toJson() => {
        'address': address,
        if (service != null) 'service': service,
      };
}

class ApiBambuCatalogView {
  const ApiBambuCatalogView({
    required this.studioVersion,
    required this.models,
    required this.profiles,
    required this.limitations,
    this.localPrintingEnabled,
    this.directPrintModelIds,
  });
  factory ApiBambuCatalogView.fromJson(Map<String, dynamic> json) =>
      ApiBambuCatalogView(
        studioVersion: json['studioVersion'] as String,
        models: List<Map<String, dynamic>>.unmodifiable((json['models'] as List)
            .map((v) => Map<String, dynamic>.unmodifiable(_object(v)))),
        profiles: List<Map<String, dynamic>>.unmodifiable(
            (json['profiles'] as List)
                .map((v) => Map<String, dynamic>.unmodifiable(_object(v)))),
        limitations: List<String>.unmodifiable(
            (json['limitations'] as List).map((v) => v as String)),
        localPrintingEnabled: json['localPrintingEnabled'] == null
            ? null
            : json['localPrintingEnabled'] as bool,
        directPrintModelIds: json['directPrintModelIds'] == null
            ? null
            : List<String>.unmodifiable(
                (json['directPrintModelIds'] as List).map((v) => v as String)),
      );
  final String studioVersion;
  final List<Map<String, dynamic>> models;
  final List<Map<String, dynamic>> profiles;
  final List<String> limitations;
  final bool? localPrintingEnabled;
  final List<String>? directPrintModelIds;
  Map<String, dynamic> toJson() => {
        'studioVersion': studioVersion,
        'models': models.map((v) => v).toList(growable: false),
        'profiles': profiles.map((v) => v).toList(growable: false),
        'limitations': limitations.map((v) => v).toList(growable: false),
        if (localPrintingEnabled != null)
          'localPrintingEnabled': localPrintingEnabled,
        if (directPrintModelIds != null)
          'directPrintModelIds':
              directPrintModelIds?.map((v) => v).toList(growable: false),
      };
}

class ApiBambuCertificateView {
  const ApiBambuCertificateView({
    required this.address,
    required this.certificateSha256,
    this.verified,
    this.service,
  });
  factory ApiBambuCertificateView.fromJson(Map<String, dynamic> json) =>
      ApiBambuCertificateView(
        address: json['address'] as String,
        certificateSha256: json['certificateSha256'] as String,
        verified: json['verified'] == null ? null : json['verified'] as bool,
        service: json['service'] == null ? null : json['service'] as String,
      );
  final String address;
  final String certificateSha256;
  final bool? verified;
  final String? service;
  Map<String, dynamic> toJson() => {
        'address': address,
        'certificateSha256': certificateSha256,
        if (verified != null) 'verified': verified,
        if (service != null) 'service': service,
      };
}

class ApiBambuConnectionRequest {
  const ApiBambuConnectionRequest({
    required this.address,
    this.service,
    required this.serial,
    required this.accessCode,
    this.certificateSha256,
    this.certificateVerified,
    required this.modelId,
    this.firmwareVersion,
    this.lanDeveloperMode,
    this.ftpsCertificateSha256,
    this.ftpsCertificateVerified,
  });
  factory ApiBambuConnectionRequest.fromJson(Map<String, dynamic> json) =>
      ApiBambuConnectionRequest(
        address: json['address'] as String,
        service: json['service'] == null ? null : json['service'] as String,
        serial: json['serial'] as String,
        accessCode: json['accessCode'] as String,
        certificateSha256: json['certificateSha256'] == null
            ? null
            : json['certificateSha256'] as String,
        certificateVerified: json['certificateVerified'] == null
            ? null
            : json['certificateVerified'] as bool,
        modelId: json['modelId'] as String,
        firmwareVersion: json['firmwareVersion'] == null
            ? null
            : json['firmwareVersion'] as String,
        lanDeveloperMode: json['lanDeveloperMode'] == null
            ? null
            : json['lanDeveloperMode'] as bool,
        ftpsCertificateSha256: json['ftpsCertificateSha256'] == null
            ? null
            : json['ftpsCertificateSha256'] as String,
        ftpsCertificateVerified: json['ftpsCertificateVerified'] == null
            ? null
            : json['ftpsCertificateVerified'] as bool,
      );
  final String address;
  final String? service;
  final String serial;
  final String accessCode;
  final String? certificateSha256;
  final bool? certificateVerified;
  final String modelId;
  final String? firmwareVersion;
  final bool? lanDeveloperMode;
  final String? ftpsCertificateSha256;
  final bool? ftpsCertificateVerified;
  Map<String, dynamic> toJson() => {
        'address': address,
        if (service != null) 'service': service,
        'serial': serial,
        'accessCode': accessCode,
        if (certificateSha256 != null) 'certificateSha256': certificateSha256,
        if (certificateVerified != null)
          'certificateVerified': certificateVerified,
        'modelId': modelId,
        if (firmwareVersion != null) 'firmwareVersion': firmwareVersion,
        if (lanDeveloperMode != null) 'lanDeveloperMode': lanDeveloperMode,
        if (ftpsCertificateSha256 != null)
          'ftpsCertificateSha256': ftpsCertificateSha256,
        if (ftpsCertificateVerified != null)
          'ftpsCertificateVerified': ftpsCertificateVerified,
      };
}

class ApiBambuDiscoveredPrinter {
  const ApiBambuDiscoveredPrinter({
    required this.address,
    required this.serial,
    this.modelId,
    required this.name,
    this.firmwareVersion,
  });
  factory ApiBambuDiscoveredPrinter.fromJson(Map<String, dynamic> json) =>
      ApiBambuDiscoveredPrinter(
        address: json['address'] as String,
        serial: json['serial'] as String,
        modelId: json['modelId'] == null ? null : json['modelId'] as String,
        name: json['name'] as String,
        firmwareVersion: json['firmwareVersion'] == null
            ? null
            : json['firmwareVersion'] as String,
      );
  final String address;
  final String serial;
  final String? modelId;
  final String name;
  final String? firmwareVersion;
  Map<String, dynamic> toJson() => {
        'address': address,
        'serial': serial,
        if (modelId != null) 'modelId': modelId,
        'name': name,
        if (firmwareVersion != null) 'firmwareVersion': firmwareVersion,
      };
}

class ApiBambuDiscoveryView {
  const ApiBambuDiscoveryView({
    required this.devices,
    this.qualification,
  });
  factory ApiBambuDiscoveryView.fromJson(Map<String, dynamic> json) =>
      ApiBambuDiscoveryView(
        devices: List<ApiBambuDiscoveredPrinter>.unmodifiable(
            (json['devices'] as List)
                .map((v) => ApiBambuDiscoveredPrinter.fromJson(_object(v)))),
        qualification: json['qualification'] == null
            ? null
            : json['qualification'] as String,
      );
  final List<ApiBambuDiscoveredPrinter> devices;
  final String? qualification;
  Map<String, dynamic> toJson() => {
        'devices': devices.map((v) => v.toJson()).toList(growable: false),
        if (qualification != null) 'qualification': qualification,
      };
}

class ApiBambuDispatchRequest {
  const ApiBambuDispatchRequest({
    required this.preparationId,
    required this.preparedSha256,
    required this.expectedEngineeringRevision,
    required this.authorizePrint,
    required this.externalSpoolConfirmed,
  });
  factory ApiBambuDispatchRequest.fromJson(Map<String, dynamic> json) =>
      ApiBambuDispatchRequest(
        preparationId: json['preparationId'] as String,
        preparedSha256: json['preparedSha256'] as String,
        expectedEngineeringRevision:
            (json['expectedEngineeringRevision'] as num).toInt(),
        authorizePrint: json['authorizePrint'] as bool,
        externalSpoolConfirmed: json['externalSpoolConfirmed'] as bool,
      );
  final String preparationId;
  final String preparedSha256;
  final int expectedEngineeringRevision;
  final bool authorizePrint;
  final bool externalSpoolConfirmed;
  Map<String, dynamic> toJson() => {
        'preparationId': preparationId,
        'preparedSha256': preparedSha256,
        'expectedEngineeringRevision': expectedEngineeringRevision,
        'authorizePrint': authorizePrint,
        'externalSpoolConfirmed': externalSpoolConfirmed,
      };
}

class ApiBambuDispatchView {
  const ApiBambuDispatchView({
    required this.id,
    required this.projectId,
    required this.preparationId,
    required this.status,
    required this.preparedSha256,
    required this.printerSerial,
    required this.filename,
    required this.message,
    this.physicalAcceptance,
  });
  factory ApiBambuDispatchView.fromJson(Map<String, dynamic> json) =>
      ApiBambuDispatchView(
        id: json['id'] as String,
        projectId: json['projectId'] as String,
        preparationId: json['preparationId'] as String,
        status: json['status'] as String,
        preparedSha256: json['preparedSha256'] as String,
        printerSerial: json['printerSerial'] as String,
        filename: json['filename'] as String,
        message: json['message'] as String,
        physicalAcceptance: json['physicalAcceptance'] == null
            ? null
            : json['physicalAcceptance'] as bool,
      );
  final String id;
  final String projectId;
  final String preparationId;
  final String status;
  final String preparedSha256;
  final String printerSerial;
  final String filename;
  final String message;
  final bool? physicalAcceptance;
  Map<String, dynamic> toJson() => {
        'id': id,
        'projectId': projectId,
        'preparationId': preparationId,
        'status': status,
        'preparedSha256': preparedSha256,
        'printerSerial': printerSerial,
        'filename': filename,
        'message': message,
        if (physicalAcceptance != null)
          'physicalAcceptance': physicalAcceptance,
      };
}

class ApiBambuDispatchesView {
  const ApiBambuDispatchesView({
    required this.dispatches,
  });
  factory ApiBambuDispatchesView.fromJson(Map<String, dynamic> json) =>
      ApiBambuDispatchesView(
        dispatches: List<ApiBambuDispatchView>.unmodifiable(
            (json['dispatches'] as List)
                .map((v) => ApiBambuDispatchView.fromJson(_object(v)))),
      );
  final List<ApiBambuDispatchView> dispatches;
  Map<String, dynamic> toJson() => {
        'dispatches': dispatches.map((v) => v.toJson()).toList(growable: false),
      };
}

class ApiBambuLocalState {
  const ApiBambuLocalState({
    required this.enabled,
    this.paired,
    this.connected,
    this.status,
    this.message,
    this.address,
    this.serial,
    this.modelId,
    this.observation,
    this.credentialsStored,
    this.expiresAfterSeconds,
    this.firmwareVersion,
    this.directPrinting,
    this.errorCode,
  });
  factory ApiBambuLocalState.fromJson(Map<String, dynamic> json) =>
      ApiBambuLocalState(
        enabled: json['enabled'] as bool,
        paired: json['paired'] == null ? null : json['paired'] as bool,
        connected: json['connected'] == null ? null : json['connected'] as bool,
        status: json['status'] == null ? null : json['status'] as String,
        message: json['message'] == null ? null : json['message'] as String,
        address: json['address'] == null ? null : json['address'] as String,
        serial: json['serial'] == null ? null : json['serial'] as String,
        modelId: json['modelId'] == null ? null : json['modelId'] as String,
        observation: json['observation'] == null
            ? null
            : Map<String, dynamic>.unmodifiable(_object(json['observation'])),
        credentialsStored: json['credentialsStored'] == null
            ? null
            : json['credentialsStored'] as bool,
        expiresAfterSeconds: json['expiresAfterSeconds'] == null
            ? null
            : (json['expiresAfterSeconds'] as num).toInt(),
        firmwareVersion: json['firmwareVersion'] == null
            ? null
            : json['firmwareVersion'] as String,
        directPrinting: json['directPrinting'] == null
            ? null
            : json['directPrinting'] as bool,
        errorCode:
            json['errorCode'] == null ? null : json['errorCode'] as String,
      );
  final bool enabled;
  final bool? paired;
  final bool? connected;
  final String? status;
  final String? message;
  final String? address;
  final String? serial;
  final String? modelId;
  final Map<String, dynamic>? observation;
  final bool? credentialsStored;
  final int? expiresAfterSeconds;
  final String? firmwareVersion;
  final bool? directPrinting;
  final String? errorCode;
  Map<String, dynamic> toJson() => {
        'enabled': enabled,
        if (paired != null) 'paired': paired,
        if (connected != null) 'connected': connected,
        if (status != null) 'status': status,
        if (message != null) 'message': message,
        if (address != null) 'address': address,
        if (serial != null) 'serial': serial,
        if (modelId != null) 'modelId': modelId,
        if (observation != null) 'observation': observation,
        if (credentialsStored != null) 'credentialsStored': credentialsStored,
        if (expiresAfterSeconds != null)
          'expiresAfterSeconds': expiresAfterSeconds,
        if (firmwareVersion != null) 'firmwareVersion': firmwareVersion,
        if (directPrinting != null) 'directPrinting': directPrinting,
        if (errorCode != null) 'errorCode': errorCode,
      };
}

class ApiBambuProfile {
  const ApiBambuProfile({
    required this.printerModelId,
    required this.nozzleMm,
    required this.material,
    this.filamentPreset,
    this.bedType,
    this.studioVersion,
    this.slicer,
    this.slicerVersion,
    required this.bundleSha256,
    required this.image,
    this.qualified,
  });
  factory ApiBambuProfile.fromJson(Map<String, dynamic> json) =>
      ApiBambuProfile(
        printerModelId: json['printerModelId'] as String,
        nozzleMm: (json['nozzleMm'] as num).toDouble(),
        material: json['material'] as String,
        filamentPreset: json['filamentPreset'] == null
            ? null
            : json['filamentPreset'] as String,
        bedType: json['bedType'] == null ? null : json['bedType'] as String,
        studioVersion: json['studioVersion'] == null
            ? null
            : json['studioVersion'] as String,
        slicer: json['slicer'] == null ? null : json['slicer'] as String,
        slicerVersion: json['slicerVersion'] == null
            ? null
            : json['slicerVersion'] as String,
        bundleSha256: json['bundleSha256'] as String,
        image: json['image'] as String,
        qualified: json['qualified'] == null ? null : json['qualified'] as bool,
      );
  final String printerModelId;
  final double nozzleMm;
  final String material;
  final String? filamentPreset;
  final String? bedType;
  final String? studioVersion;
  final String? slicer;
  final String? slicerVersion;
  final String bundleSha256;
  final String image;
  final bool? qualified;
  Map<String, dynamic> toJson() => {
        'printerModelId': printerModelId,
        'nozzleMm': nozzleMm,
        'material': material,
        if (filamentPreset != null) 'filamentPreset': filamentPreset,
        if (bedType != null) 'bedType': bedType,
        if (studioVersion != null) 'studioVersion': studioVersion,
        if (slicer != null) 'slicer': slicer,
        if (slicerVersion != null) 'slicerVersion': slicerVersion,
        'bundleSha256': bundleSha256,
        'image': image,
        if (qualified != null) 'qualified': qualified,
      };
}

class ApiBambuReconcileRequest {
  const ApiBambuReconcileRequest({
    required this.confirmPrinterIdle,
    required this.outcome,
  });
  factory ApiBambuReconcileRequest.fromJson(Map<String, dynamic> json) =>
      ApiBambuReconcileRequest(
        confirmPrinterIdle: json['confirmPrinterIdle'] as bool,
        outcome: json['outcome'] as String,
      );
  final bool confirmPrinterIdle;
  final String outcome;
  Map<String, dynamic> toJson() => {
        'confirmPrinterIdle': confirmPrinterIdle,
        'outcome': outcome,
      };
}

class ApiBambuTestPrintRequest {
  const ApiBambuTestPrintRequest({
    required this.profileId,
    required this.printerModelId,
  });
  factory ApiBambuTestPrintRequest.fromJson(Map<String, dynamic> json) =>
      ApiBambuTestPrintRequest(
        profileId: json['profileId'] as String,
        printerModelId: json['printerModelId'] as String,
      );
  final String profileId;
  final String printerModelId;
  Map<String, dynamic> toJson() => {
        'profileId': profileId,
        'printerModelId': printerModelId,
      };
}

class ApiBambuTestPrintView {
  const ApiBambuTestPrintView({
    required this.id,
    required this.status,
    required this.printerModelId,
    required this.profileId,
    required this.profile,
    required this.dimensionsMm,
    required this.sourceSha256,
    this.packageSha256,
    this.estimatedSeconds,
    this.filamentGrams,
    this.filename,
    required this.message,
    this.physicalAcceptance,
  });
  factory ApiBambuTestPrintView.fromJson(Map<String, dynamic> json) =>
      ApiBambuTestPrintView(
        id: json['id'] as String,
        status: json['status'] as String,
        printerModelId: json['printerModelId'] as String,
        profileId: json['profileId'] as String,
        profile: ApiBambuProfile.fromJson(_object(json['profile'])),
        dimensionsMm: List<double>.unmodifiable(
            (json['dimensionsMm'] as List).map((v) => (v as num).toDouble())),
        sourceSha256: json['sourceSha256'] as String,
        packageSha256: json['packageSha256'] == null
            ? null
            : json['packageSha256'] as String,
        estimatedSeconds: json['estimatedSeconds'] == null
            ? null
            : (json['estimatedSeconds'] as num).toDouble(),
        filamentGrams: json['filamentGrams'] == null
            ? null
            : (json['filamentGrams'] as num).toDouble(),
        filename: json['filename'] == null ? null : json['filename'] as String,
        message: json['message'] as String,
        physicalAcceptance: json['physicalAcceptance'] == null
            ? null
            : json['physicalAcceptance'] as bool,
      );
  final String id;
  final String status;
  final String printerModelId;
  final String profileId;
  final ApiBambuProfile profile;
  final List<double> dimensionsMm;
  final String sourceSha256;
  final String? packageSha256;
  final double? estimatedSeconds;
  final double? filamentGrams;
  final String? filename;
  final String message;
  final bool? physicalAcceptance;
  Map<String, dynamic> toJson() => {
        'id': id,
        'status': status,
        'printerModelId': printerModelId,
        'profileId': profileId,
        'profile': profile.toJson(),
        'dimensionsMm': dimensionsMm.map((v) => v).toList(growable: false),
        'sourceSha256': sourceSha256,
        if (packageSha256 != null) 'packageSha256': packageSha256,
        if (estimatedSeconds != null) 'estimatedSeconds': estimatedSeconds,
        if (filamentGrams != null) 'filamentGrams': filamentGrams,
        if (filename != null) 'filename': filename,
        'message': message,
        if (physicalAcceptance != null)
          'physicalAcceptance': physicalAcceptance,
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
    this.jobId,
    this.agentInstanceId,
    required this.commandId,
    required this.projectId,
    this.runId,
    required this.revision,
  });
  factory ApiCommandAccepted.fromJson(Map<String, dynamic> json) =>
      ApiCommandAccepted(
        jobId: json['jobId'] == null ? null : json['jobId'] as String,
        agentInstanceId: json['agentInstanceId'] == null
            ? null
            : json['agentInstanceId'] as String,
        commandId: json['commandId'] as String,
        projectId: json['projectId'] as String,
        runId: json['runId'] == null ? null : json['runId'] as String,
        revision: (json['revision'] as num).toInt(),
      );
  final String? jobId;
  final String? agentInstanceId;
  final String commandId;
  final String projectId;
  final String? runId;
  final int revision;
  Map<String, dynamic> toJson() => {
        if (jobId != null) 'jobId': jobId,
        if (agentInstanceId != null) 'agentInstanceId': agentInstanceId,
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

class ApiEngineeringToolCatalogView {
  const ApiEngineeringToolCatalogView({
    required this.tools,
    required this.defaultPolicy,
    this.agents,
    this.defaultAgentPolicy,
  });
  factory ApiEngineeringToolCatalogView.fromJson(Map<String, dynamic> json) =>
      ApiEngineeringToolCatalogView(
        tools: List<ApiEngineeringToolView>.unmodifiable((json['tools'] as List)
            .map((v) => ApiEngineeringToolView.fromJson(_object(v)))),
        defaultPolicy: Map<String, ApiEngineeringToolChoice>.unmodifiable(
            _object(json['defaultPolicy']).map((k, v) =>
                MapEntry(k, ApiEngineeringToolChoice.fromJson(_object(v))))),
        agents: json['agents'] == null
            ? null
            : List<ApiAgentPluginView>.unmodifiable((json['agents'] as List)
                .map((v) => ApiAgentPluginView.fromJson(_object(v)))),
        defaultAgentPolicy: json['defaultAgentPolicy'] == null
            ? null
            : Map<String, ApiEngineeringToolChoice>.unmodifiable(
                _object(json['defaultAgentPolicy']).map((k, v) => MapEntry(
                    k, ApiEngineeringToolChoice.fromJson(_object(v))))),
      );
  final List<ApiEngineeringToolView> tools;
  final Map<String, ApiEngineeringToolChoice> defaultPolicy;
  final List<ApiAgentPluginView>? agents;
  final Map<String, ApiEngineeringToolChoice>? defaultAgentPolicy;
  Map<String, dynamic> toJson() => {
        'tools': tools.map((v) => v.toJson()).toList(growable: false),
        'defaultPolicy': defaultPolicy.map((k, v) => MapEntry(k, v.toJson())),
        if (agents != null)
          'agents': agents?.map((v) => v.toJson()).toList(growable: false),
        if (defaultAgentPolicy != null)
          'defaultAgentPolicy':
              defaultAgentPolicy?.map((k, v) => MapEntry(k, v.toJson())),
      };
}

class ApiEngineeringToolChoice {
  const ApiEngineeringToolChoice({
    required this.id,
    required this.version,
  });
  factory ApiEngineeringToolChoice.fromJson(Map<String, dynamic> json) =>
      ApiEngineeringToolChoice(
        id: json['id'] as String,
        version: json['version'] as String,
      );
  final String id;
  final String version;
  Map<String, dynamic> toJson() => {
        'id': id,
        'version': version,
      };
}

class ApiEngineeringToolView {
  const ApiEngineeringToolView({
    required this.id,
    required this.version,
    required this.name,
    required this.lane,
    required this.runner,
    required this.sourceContract,
    required this.outputFiles,
    required this.qualified,
    required this.qualificationReference,
    required this.limitations,
    required this.configured,
    required this.isDefault,
  });
  factory ApiEngineeringToolView.fromJson(Map<String, dynamic> json) =>
      ApiEngineeringToolView(
        id: json['id'] as String,
        version: json['version'] as String,
        name: json['name'] as String,
        lane: json['lane'] as String,
        runner: json['runner'] as String,
        sourceContract: json['sourceContract'] as String,
        outputFiles: List<String>.unmodifiable(
            (json['outputFiles'] as List).map((v) => v as String)),
        qualified: json['qualified'] as bool,
        qualificationReference: json['qualificationReference'] == null
            ? null
            : json['qualificationReference'] as String,
        limitations: List<String>.unmodifiable(
            (json['limitations'] as List).map((v) => v as String)),
        configured: json['configured'] as bool,
        isDefault: json['isDefault'] as bool,
      );
  final String id;
  final String version;
  final String name;
  final String lane;
  final String runner;
  final String sourceContract;
  final List<String> outputFiles;
  final bool qualified;
  final String? qualificationReference;
  final List<String> limitations;
  final bool configured;
  final bool isDefault;
  Map<String, dynamic> toJson() => {
        'id': id,
        'version': version,
        'name': name,
        'lane': lane,
        'runner': runner,
        'sourceContract': sourceContract,
        'outputFiles': outputFiles.map((v) => v).toList(growable: false),
        'qualified': qualified,
        'qualificationReference': qualificationReference,
        'limitations': limitations.map((v) => v).toList(growable: false),
        'configured': configured,
        'isDefault': isDefault,
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

class ApiExternalJobEventSummary {
  const ApiExternalJobEventSummary({
    required this.id,
    required this.jobId,
    required this.disposition,
    required this.engineeringRevision,
    required this.observedAt,
    required this.receivedAt,
    required this.bodyHash,
  });
  factory ApiExternalJobEventSummary.fromJson(Map<String, dynamic> json) =>
      ApiExternalJobEventSummary(
        id: json['id'] as String,
        jobId: json['jobId'] as String,
        disposition: json['disposition'] as String,
        engineeringRevision: (json['engineeringRevision'] as num).toInt(),
        observedAt: json['observedAt'] as String,
        receivedAt: json['receivedAt'] as String,
        bodyHash: json['bodyHash'] as String,
      );
  final String id;
  final String jobId;
  final String disposition;
  final int engineeringRevision;
  final String observedAt;
  final String receivedAt;
  final String bodyHash;
  Map<String, dynamic> toJson() => {
        'id': id,
        'jobId': jobId,
        'disposition': disposition,
        'engineeringRevision': engineeringRevision,
        'observedAt': observedAt,
        'receivedAt': receivedAt,
        'bodyHash': bodyHash,
      };
}

class ApiExternalJobEventsPage {
  const ApiExternalJobEventsPage({
    required this.events,
    this.nextCursor,
  });
  factory ApiExternalJobEventsPage.fromJson(Map<String, dynamic> json) =>
      ApiExternalJobEventsPage(
        events: List<ApiExternalJobEventSummary>.unmodifiable(
            (json['events'] as List)
                .map((v) => ApiExternalJobEventSummary.fromJson(_object(v)))),
        nextCursor:
            json['nextCursor'] == null ? null : json['nextCursor'] as String,
      );
  final List<ApiExternalJobEventSummary> events;
  final String? nextCursor;
  Map<String, dynamic> toJson() => {
        'events': events.map((v) => v.toJson()).toList(growable: false),
        if (nextCursor != null) 'nextCursor': nextCursor,
      };
}

class ApiExternalJobSummary {
  const ApiExternalJobSummary({
    required this.id,
    this.agentInstanceId,
    required this.agentType,
    required this.engineeringRevision,
    required this.historical,
    required this.lifecycle,
    required this.state,
    required this.deadline,
    required this.expired,
    required this.inputHash,
    required this.pluginId,
    required this.pluginVersion,
    required this.remainingAllowanceUsd,
    required this.settlementStatus,
  });
  factory ApiExternalJobSummary.fromJson(Map<String, dynamic> json) =>
      ApiExternalJobSummary(
        id: json['id'] as String,
        agentInstanceId: json['agentInstanceId'] == null
            ? null
            : json['agentInstanceId'] as String,
        agentType: json['agentType'] as String,
        engineeringRevision: (json['engineeringRevision'] as num).toInt(),
        historical: json['historical'] as bool,
        lifecycle: json['lifecycle'] as String,
        state: json['state'] as String,
        deadline: json['deadline'] as String,
        expired: json['expired'] as bool,
        inputHash: json['inputHash'] as String,
        pluginId: json['pluginId'] as String,
        pluginVersion: json['pluginVersion'] as String,
        remainingAllowanceUsd:
            (json['remainingAllowanceUsd'] as num).toDouble(),
        settlementStatus: json['settlementStatus'] as String,
      );
  final String id;
  final String? agentInstanceId;
  final String agentType;
  final int engineeringRevision;
  final bool historical;
  final String lifecycle;
  final String state;
  final String deadline;
  final bool expired;
  final String inputHash;
  final String pluginId;
  final String pluginVersion;
  final double remainingAllowanceUsd;
  final String settlementStatus;
  Map<String, dynamic> toJson() => {
        'id': id,
        if (agentInstanceId != null) 'agentInstanceId': agentInstanceId,
        'agentType': agentType,
        'engineeringRevision': engineeringRevision,
        'historical': historical,
        'lifecycle': lifecycle,
        'state': state,
        'deadline': deadline,
        'expired': expired,
        'inputHash': inputHash,
        'pluginId': pluginId,
        'pluginVersion': pluginVersion,
        'remainingAllowanceUsd': remainingAllowanceUsd,
        'settlementStatus': settlementStatus,
      };
}

class ApiExternalJobsPage {
  const ApiExternalJobsPage({
    required this.externalJobs,
    this.nextCursor,
  });
  factory ApiExternalJobsPage.fromJson(Map<String, dynamic> json) =>
      ApiExternalJobsPage(
        externalJobs: List<ApiExternalJobSummary>.unmodifiable(
            (json['externalJobs'] as List)
                .map((v) => ApiExternalJobSummary.fromJson(_object(v)))),
        nextCursor:
            json['nextCursor'] == null ? null : json['nextCursor'] as String,
      );
  final List<ApiExternalJobSummary> externalJobs;
  final String? nextCursor;
  Map<String, dynamic> toJson() => {
        'externalJobs':
            externalJobs.map((v) => v.toJson()).toList(growable: false),
        if (nextCursor != null) 'nextCursor': nextCursor,
      };
}

class ApiHumanBountiesView {
  const ApiHumanBountiesView({
    required this.bounties,
    required this.hasMore,
    required this.nextCursor,
  });
  factory ApiHumanBountiesView.fromJson(Map<String, dynamic> json) =>
      ApiHumanBountiesView(
        bounties: List<Map<String, dynamic>>.unmodifiable(
            (json['bounties'] as List)
                .map((v) => Map<String, dynamic>.unmodifiable(_object(v)))),
        hasMore: json['hasMore'] as bool,
        nextCursor:
            json['nextCursor'] == null ? null : json['nextCursor'] as String,
      );
  final List<Map<String, dynamic>> bounties;
  final bool hasMore;
  final String? nextCursor;
  Map<String, dynamic> toJson() => {
        'bounties': bounties.map((v) => v).toList(growable: false),
        'hasMore': hasMore,
        'nextCursor': nextCursor,
      };
}

class ApiHumanSearchView {
  const ApiHumanSearchView({
    required this.humans,
    required this.hasMore,
    required this.nextCursor,
    this.source,
  });
  factory ApiHumanSearchView.fromJson(Map<String, dynamic> json) =>
      ApiHumanSearchView(
        humans: List<Map<String, dynamic>>.unmodifiable((json['humans'] as List)
            .map((v) => Map<String, dynamic>.unmodifiable(_object(v)))),
        hasMore: json['hasMore'] as bool,
        nextCursor:
            json['nextCursor'] == null ? null : json['nextCursor'] as String,
        source: json['source'] == null ? null : json['source'] as String,
      );
  final List<Map<String, dynamic>> humans;
  final bool hasMore;
  final String? nextCursor;
  final String? source;
  Map<String, dynamic> toJson() => {
        'humans': humans.map((v) => v).toList(growable: false),
        'hasMore': hasMore,
        'nextCursor': nextCursor,
        if (source != null) 'source': source,
      };
}

class ApiHumanTaskDraftAction {
  const ApiHumanTaskDraftAction({
    required this.draftSha256,
  });
  factory ApiHumanTaskDraftAction.fromJson(Map<String, dynamic> json) =>
      ApiHumanTaskDraftAction(
        draftSha256: json['draftSha256'] as String,
      );
  final String draftSha256;
  Map<String, dynamic> toJson() => {
        'draftSha256': draftSha256,
      };
}

class ApiHumanTaskDraftRequest {
  const ApiHumanTaskDraftRequest({
    required this.releaseId,
    required this.expectedEngineeringRevision,
    required this.task,
  });
  factory ApiHumanTaskDraftRequest.fromJson(Map<String, dynamic> json) =>
      ApiHumanTaskDraftRequest(
        releaseId: json['releaseId'] as String,
        expectedEngineeringRevision:
            (json['expectedEngineeringRevision'] as num).toInt(),
        task: ApiHumanTaskSpec.fromJson(_object(json['task'])),
      );
  final String releaseId;
  final int expectedEngineeringRevision;
  final ApiHumanTaskSpec task;
  Map<String, dynamic> toJson() => {
        'releaseId': releaseId,
        'expectedEngineeringRevision': expectedEngineeringRevision,
        'task': task.toJson(),
      };
}

class ApiHumanTaskDraftView {
  const ApiHumanTaskDraftView({
    required this.id,
    required this.projectId,
    required this.engineeringRevision,
    required this.releaseId,
    required this.status,
    required this.draftSha256,
    required this.task,
    required this.preview,
    required this.observation,
    required this.bountyId,
    this.physicalAcceptance,
  });
  factory ApiHumanTaskDraftView.fromJson(Map<String, dynamic> json) =>
      ApiHumanTaskDraftView(
        id: json['id'] as String,
        projectId: json['projectId'] as String,
        engineeringRevision: (json['engineeringRevision'] as num).toInt(),
        releaseId: json['releaseId'] as String,
        status: json['status'] as String,
        draftSha256: json['draftSha256'] as String,
        task: ApiHumanTaskSpec.fromJson(_object(json['task'])),
        preview: Map<String, dynamic>.unmodifiable(_object(json['preview'])),
        observation:
            Map<String, dynamic>.unmodifiable(_object(json['observation'])),
        bountyId: json['bountyId'] == null ? null : json['bountyId'] as String,
        physicalAcceptance: json['physicalAcceptance'] == null
            ? null
            : json['physicalAcceptance'] as bool,
      );
  final String id;
  final String projectId;
  final int engineeringRevision;
  final String releaseId;
  final String status;
  final String draftSha256;
  final ApiHumanTaskSpec task;
  final Map<String, dynamic> preview;
  final Map<String, dynamic> observation;
  final String? bountyId;
  final bool? physicalAcceptance;
  Map<String, dynamic> toJson() => {
        'id': id,
        'projectId': projectId,
        'engineeringRevision': engineeringRevision,
        'releaseId': releaseId,
        'status': status,
        'draftSha256': draftSha256,
        'task': task.toJson(),
        'preview': preview,
        'observation': observation,
        'bountyId': bountyId,
        if (physicalAcceptance != null)
          'physicalAcceptance': physicalAcceptance,
      };
}

class ApiHumanTaskDraftsView {
  const ApiHumanTaskDraftsView({
    required this.drafts,
  });
  factory ApiHumanTaskDraftsView.fromJson(Map<String, dynamic> json) =>
      ApiHumanTaskDraftsView(
        drafts: List<ApiHumanTaskDraftView>.unmodifiable(
            (json['drafts'] as List)
                .map((v) => ApiHumanTaskDraftView.fromJson(_object(v)))),
      );
  final List<ApiHumanTaskDraftView> drafts;
  Map<String, dynamic> toJson() => {
        'drafts': drafts.map((v) => v.toJson()).toList(growable: false),
      };
}

class ApiHumanTaskLinkRequest {
  const ApiHumanTaskLinkRequest({
    required this.draftSha256,
    required this.bountyId,
  });
  factory ApiHumanTaskLinkRequest.fromJson(Map<String, dynamic> json) =>
      ApiHumanTaskLinkRequest(
        draftSha256: json['draftSha256'] as String,
        bountyId: json['bountyId'] as String,
      );
  final String draftSha256;
  final String bountyId;
  Map<String, dynamic> toJson() => {
        'draftSha256': draftSha256,
        'bountyId': bountyId,
      };
}

class ApiHumanTaskLocation {
  const ApiHumanTaskLocation({
    this.city,
    this.country,
    this.isRemoteAllowed,
  });
  factory ApiHumanTaskLocation.fromJson(Map<String, dynamic> json) =>
      ApiHumanTaskLocation(
        city: json['city'] == null ? null : json['city'] as String,
        country: json['country'] == null ? null : json['country'] as String,
        isRemoteAllowed: json['isRemoteAllowed'] == null
            ? null
            : json['isRemoteAllowed'] as bool,
      );
  final String? city;
  final String? country;
  final bool? isRemoteAllowed;
  Map<String, dynamic> toJson() => {
        if (city != null) 'city': city,
        if (country != null) 'country': country,
        if (isRemoteAllowed != null) 'isRemoteAllowed': isRemoteAllowed,
      };
}

class ApiHumanTaskSpec {
  const ApiHumanTaskSpec({
    required this.title,
    required this.description,
    required this.completionCriteria,
    required this.price,
    this.priceType,
    required this.estimatedHours,
    this.category,
    this.skillsNeeded,
    required this.evidenceTypes,
    this.location,
  });
  factory ApiHumanTaskSpec.fromJson(Map<String, dynamic> json) =>
      ApiHumanTaskSpec(
        title: json['title'] as String,
        description: json['description'] as String,
        completionCriteria: json['completionCriteria'] as String,
        price: (json['price'] as num).toDouble(),
        priceType:
            json['priceType'] == null ? null : json['priceType'] as String,
        estimatedHours: (json['estimatedHours'] as num).toDouble(),
        category: json['category'] == null ? null : json['category'] as String,
        skillsNeeded: json['skillsNeeded'] == null
            ? null
            : List<String>.unmodifiable(
                (json['skillsNeeded'] as List).map((v) => v as String)),
        evidenceTypes: List<String>.unmodifiable(
            (json['evidenceTypes'] as List).map((v) => v as String)),
        location: json['location'] == null
            ? null
            : ApiHumanTaskLocation.fromJson(_object(json['location'])),
      );
  final String title;
  final String description;
  final String completionCriteria;
  final double price;
  final String? priceType;
  final double estimatedHours;
  final String? category;
  final List<String>? skillsNeeded;
  final List<String> evidenceTypes;
  final ApiHumanTaskLocation? location;
  Map<String, dynamic> toJson() => {
        'title': title,
        'description': description,
        'completionCriteria': completionCriteria,
        'price': price,
        if (priceType != null) 'priceType': priceType,
        'estimatedHours': estimatedHours,
        if (category != null) 'category': category,
        if (skillsNeeded != null)
          'skillsNeeded': skillsNeeded?.map((v) => v).toList(growable: false),
        'evidenceTypes': evidenceTypes.map((v) => v).toList(growable: false),
        if (location != null) 'location': location?.toJson(),
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

class ApiPreparePrint {
  const ApiPreparePrint({
    required this.releaseId,
    required this.artifactId,
    required this.profileId,
    required this.expectedEngineeringRevision,
  });
  factory ApiPreparePrint.fromJson(Map<String, dynamic> json) =>
      ApiPreparePrint(
        releaseId: json['releaseId'] as String,
        artifactId: json['artifactId'] as String,
        profileId: json['profileId'] as String,
        expectedEngineeringRevision:
            (json['expectedEngineeringRevision'] as num).toInt(),
      );
  final String releaseId;
  final String artifactId;
  final String profileId;
  final int expectedEngineeringRevision;
  Map<String, dynamic> toJson() => {
        'releaseId': releaseId,
        'artifactId': artifactId,
        'profileId': profileId,
        'expectedEngineeringRevision': expectedEngineeringRevision,
      };
}

class ApiPreparedPrint {
  const ApiPreparedPrint({
    required this.artifactId,
    required this.sha256,
    required this.bundleSha256,
    this.measurements,
  });
  factory ApiPreparedPrint.fromJson(Map<String, dynamic> json) =>
      ApiPreparedPrint(
        artifactId: json['artifactId'] as String,
        sha256: json['sha256'] as String,
        bundleSha256: json['bundleSha256'] as String,
        measurements: json['measurements'] == null
            ? null
            : Map<String, dynamic>.unmodifiable(_object(json['measurements'])),
      );
  final String artifactId;
  final String sha256;
  final String bundleSha256;
  final Map<String, dynamic>? measurements;
  Map<String, dynamic> toJson() => {
        'artifactId': artifactId,
        'sha256': sha256,
        'bundleSha256': bundleSha256,
        if (measurements != null) 'measurements': measurements,
      };
}

class ApiPrintPreparationView {
  const ApiPrintPreparationView({
    required this.id,
    required this.projectId,
    required this.engineeringRevision,
    required this.status,
    required this.sourceArtifactId,
    required this.sourceSha256,
    required this.profileId,
    required this.profile,
    required this.preparedArtifactId,
    required this.preparedSha256,
    required this.measurements,
    this.physicalAcceptance,
    this.automaticNativePrepared,
  });
  factory ApiPrintPreparationView.fromJson(Map<String, dynamic> json) =>
      ApiPrintPreparationView(
        id: json['id'] as String,
        projectId: json['projectId'] as String,
        engineeringRevision: (json['engineeringRevision'] as num).toInt(),
        status: json['status'] as String,
        sourceArtifactId: json['sourceArtifactId'] as String,
        sourceSha256: json['sourceSha256'] as String,
        profileId: json['profileId'] as String,
        profile: ApiBambuProfile.fromJson(_object(json['profile'])),
        preparedArtifactId: json['preparedArtifactId'] == null
            ? null
            : json['preparedArtifactId'] as String,
        preparedSha256: json['preparedSha256'] == null
            ? null
            : json['preparedSha256'] as String,
        measurements:
            Map<String, dynamic>.unmodifiable(_object(json['measurements'])),
        physicalAcceptance: json['physicalAcceptance'] == null
            ? null
            : json['physicalAcceptance'] as bool,
        automaticNativePrepared: json['automaticNativePrepared'] == null
            ? null
            : json['automaticNativePrepared'] as bool,
      );
  final String id;
  final String projectId;
  final int engineeringRevision;
  final String status;
  final String sourceArtifactId;
  final String sourceSha256;
  final String profileId;
  final ApiBambuProfile profile;
  final String? preparedArtifactId;
  final String? preparedSha256;
  final Map<String, dynamic> measurements;
  final bool? physicalAcceptance;
  final bool? automaticNativePrepared;
  Map<String, dynamic> toJson() => {
        'id': id,
        'projectId': projectId,
        'engineeringRevision': engineeringRevision,
        'status': status,
        'sourceArtifactId': sourceArtifactId,
        'sourceSha256': sourceSha256,
        'profileId': profileId,
        'profile': profile.toJson(),
        'preparedArtifactId': preparedArtifactId,
        'preparedSha256': preparedSha256,
        'measurements': measurements,
        if (physicalAcceptance != null)
          'physicalAcceptance': physicalAcceptance,
        if (automaticNativePrepared != null)
          'automaticNativePrepared': automaticNativePrepared,
      };
}

class ApiPrintPreparationsView {
  const ApiPrintPreparationsView({
    required this.preparations,
  });
  factory ApiPrintPreparationsView.fromJson(Map<String, dynamic> json) =>
      ApiPrintPreparationsView(
        preparations: List<ApiPrintPreparationView>.unmodifiable(
            (json['preparations'] as List)
                .map((v) => ApiPrintPreparationView.fromJson(_object(v)))),
      );
  final List<ApiPrintPreparationView> preparations;
  Map<String, dynamic> toJson() => {
        'preparations':
            preparations.map((v) => v.toJson()).toList(growable: false),
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

class ApiReferenceImageUpload {
  const ApiReferenceImageUpload({
    required this.name,
    required this.mediaType,
    required this.contentBase64,
  });
  factory ApiReferenceImageUpload.fromJson(Map<String, dynamic> json) =>
      ApiReferenceImageUpload(
        name: json['name'] as String,
        mediaType: json['mediaType'] as String,
        contentBase64: json['contentBase64'] as String,
      );
  final String name;
  final String mediaType;
  final String contentBase64;
  Map<String, dynamic> toJson() => {
        'name': name,
        'mediaType': mediaType,
        'contentBase64': contentBase64,
      };
}

class ApiReferenceImageView {
  const ApiReferenceImageView({
    required this.id,
    required this.name,
    required this.mediaType,
    required this.sha256,
    required this.size,
    required this.width,
    required this.height,
  });
  factory ApiReferenceImageView.fromJson(Map<String, dynamic> json) =>
      ApiReferenceImageView(
        id: json['id'] as String,
        name: json['name'] as String,
        mediaType: json['mediaType'] as String,
        sha256: json['sha256'] as String,
        size: (json['size'] as num).toInt(),
        width: (json['width'] as num).toInt(),
        height: (json['height'] as num).toInt(),
      );
  final String id;
  final String name;
  final String mediaType;
  final String sha256;
  final int size;
  final int width;
  final int height;
  Map<String, dynamic> toJson() => {
        'id': id,
        'name': name,
        'mediaType': mediaType,
        'sha256': sha256,
        'size': size,
        'width': width,
        'height': height,
      };
}

class ApiRentAHumanStatusView {
  const ApiRentAHumanStatusView({
    this.provider,
    required this.accountConfigured,
    this.livePostingEnabled,
    required this.capabilities,
    this.setupUrl,
    required this.limitations,
  });
  factory ApiRentAHumanStatusView.fromJson(Map<String, dynamic> json) =>
      ApiRentAHumanStatusView(
        provider: json['provider'] == null ? null : json['provider'] as String,
        accountConfigured: json['accountConfigured'] as bool,
        livePostingEnabled: json['livePostingEnabled'] == null
            ? null
            : json['livePostingEnabled'] as bool,
        capabilities: List<String>.unmodifiable(
            (json['capabilities'] as List).map((v) => v as String)),
        setupUrl: json['setupUrl'] == null ? null : json['setupUrl'] as String,
        limitations: List<String>.unmodifiable(
            (json['limitations'] as List).map((v) => v as String)),
      );
  final String? provider;
  final bool accountConfigured;
  final bool? livePostingEnabled;
  final List<String> capabilities;
  final String? setupUrl;
  final List<String> limitations;
  Map<String, dynamic> toJson() => {
        if (provider != null) 'provider': provider,
        'accountConfigured': accountConfigured,
        if (livePostingEnabled != null)
          'livePostingEnabled': livePostingEnabled,
        'capabilities': capabilities.map((v) => v).toList(growable: false),
        if (setupUrl != null) 'setupUrl': setupUrl,
        'limitations': limitations.map((v) => v).toList(growable: false),
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
    this.activity,
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
        activity: json['activity'] == null
            ? null
            : List<Map<String, dynamic>>.unmodifiable((json['activity'] as List)
                .map((v) => Map<String, dynamic>.unmodifiable(_object(v)))),
      );
  final String id;
  final String projectId;
  final String kind;
  final String status;
  final int revision;
  final String traceId;
  final String? blocker;
  final String createdAt;
  final List<Map<String, dynamic>>? activity;
  Map<String, dynamic> toJson() => {
        'id': id,
        'projectId': projectId,
        'kind': kind,
        'status': status,
        'revision': revision,
        'traceId': traceId,
        'blocker': blocker,
        'createdAt': createdAt,
        if (activity != null)
          'activity': activity?.map((v) => v).toList(growable: false),
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
    this.activity,
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
        activity: json['activity'] == null
            ? null
            : List<Map<String, dynamic>>.unmodifiable((json['activity'] as List)
                .map((v) => Map<String, dynamic>.unmodifiable(_object(v)))),
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
  final List<Map<String, dynamic>>? activity;
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
        if (activity != null)
          'activity': activity?.map((v) => v).toList(growable: false),
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
    this.agents,
    this.externalJobs,
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
        agents: json['agents'] == null
            ? null
            : List<ApiAgentSummary>.unmodifiable((json['agents'] as List)
                .map((v) => ApiAgentSummary.fromJson(_object(v)))),
        externalJobs: json['externalJobs'] == null
            ? null
            : List<ApiExternalJobSummary>.unmodifiable(
                (json['externalJobs'] as List)
                    .map((v) => ApiExternalJobSummary.fromJson(_object(v)))),
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
  final List<ApiAgentSummary>? agents;
  final List<ApiExternalJobSummary>? externalJobs;
  final ApiProjectView project;
  final int revision;
  final int eventCursor;
  final List<ApiRunSummary> runs;
  final List<ApiArtifactView> artifacts;
  final List<ApiValidationView> checks;
  final bool trainingOptedIn;
  Map<String, dynamic> toJson() => {
        if (agents != null)
          'agents': agents?.map((v) => v.toJson()).toList(growable: false),
        if (externalJobs != null)
          'externalJobs':
              externalJobs?.map((v) => v.toJson()).toList(growable: false),
        'project': project.toJson(),
        'revision': revision,
        'eventCursor': eventCursor,
        'runs': runs.map((v) => v.toJson()).toList(growable: false),
        'artifacts': artifacts.map((v) => v.toJson()).toList(growable: false),
        'checks': checks.map((v) => v.toJson()).toList(growable: false),
        'trainingOptedIn': trainingOptedIn,
      };
}
