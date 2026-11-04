/// Every backend path the production client calls, in one place.
///
/// Path parameters are always percent-encoded here, so no caller interpolates an id into a URL itself. The
/// operations and methods are checked against `backend/contracts/openapi.json` by `test/api_routes_contract_test.dart`.
/// Paths are relative to the API base URL (`.../api/v1`).
abstract final class ApiRoutes {
  static String _e(String value) => Uri.encodeComponent(value);

  // Authentication and administration.
  static const authConfig = '/auth/config';
  static const authOrganizations = '/auth/organizations';
  static const authSession = '/auth/session';
  static const authLogin = '/auth/login';
  static const authLogout = '/auth/logout';
  static const authSetup = '/auth/setup';
  static const authSignup = '/auth/signup';
  static const authMembers = '/auth/members';
  static const authInvites = '/auth/invites';
  static String authInvite(String id) => '/auth/invites/${_e(id)}';
  static String authMember(String subject) => '/auth/members/${_e(subject)}';
  static String authMemberRole(String subject) => '${authMember(subject)}/role';
  static String authMemberResetPassword(String subject) =>
      '${authMember(subject)}/reset-password';

  // Catalogs and commands.
  static const commands = '/commands';
  static const modelCatalog = '/model-catalog';
  static const connectors = '/connectors';
  static const engineeringTools = '/engineering-tools';

  // Projects, runs and diagnostics.
  static const projects = '/projects';
  static String projectsPage({String? after}) =>
      after == null ? projects : '$projects?after=${_e(after)}';
  static String project(String id) => '$projects/${_e(id)}';
  static String projectDelete(String id, {required int expectedRevision}) =>
      '${project(id)}?expectedRevision=$expectedRevision';
  static String workspace(String id, {bool includeAgentState = false}) =>
      '${project(id)}/workspace${includeAgentState ? '?includeAgentState=true' : ''}';
  static String run(String id) => '/runs/${_e(id)}';
  static String nativeJobLogs(String id) => '/native-jobs/${_e(id)}/logs';
  static String invocationTranscript(String id) =>
      '/invocations/${_e(id)}/transcript';
  static String projectEvents(String id, int after) =>
      '${project(id)}/events?after=$after';
  static String referenceImages(String id) => '${project(id)}/reference-images';
  static String referenceImageContent(String id, String imageId) =>
      '${referenceImages(id)}/${_e(imageId)}/content';

  // Engineering previews and companion hosting.
  static String engineeringPreview(String id) =>
      '${project(id)}/engineering-preview';
  static String circuitSimulation(String id) =>
      '${project(id)}/circuit-simulation';
  static String companionPreview(String id) =>
      '${project(id)}/companion-preview';

  // Artifacts and releases.
  static String artifactDownload(String id) => '/artifacts/${_e(id)}/download';
  static String releaseDownload(String id) => '/releases/${_e(id)}/download';

  // Human work (RentAHuman) drafts.
  static const rentAHumanStatus = '/rentahuman/status';
  static const rentAHumanHumans = '/rentahuman/humans';
  static const rentAHumanBounties = '/rentahuman/bounties';
  static String humanTaskDrafts(String id) =>
      '${project(id)}/human-task-drafts';
  static String humanTaskDraftAction(String id, String draft, String action) =>
      '${humanTaskDrafts(id)}/${_e(draft)}/${_e(action)}';

  // Printers and print preparation.
  static const bambuCatalog = '/printers/bambu/catalog';
  static const bambuLocal = '/printers/bambu/local';
  static const bambuDiscover = '$bambuLocal/discover';
  static const bambuCertificate = '$bambuLocal/certificate';
  static const bambuConnect = '$bambuLocal/connect';
  static const bambuRefresh = '$bambuLocal/refresh';
  static const bambuTestPrints = '$bambuLocal/test-prints';
  static String bambuTestPrint(String id) => '$bambuTestPrints/${_e(id)}';
  static String bambuTestPrintFile(String id, {required bool source}) =>
      '${bambuTestPrint(id)}/${source ? 'source' : 'plate'}';
  static String printPreparations(String id) =>
      '${project(id)}/print-preparations';
  static String printDispatches(String id) => '${project(id)}/print-dispatches';
  static String printDispatchAction(
          String id, String dispatch, String action) =>
      '${printDispatches(id)}/${_e(dispatch)}/${_e(action)}';
}

/// Routes of the separate in-memory simulation service (`mock-backend`), relative to its `/api` base URL.
/// They are checked against `mock-backend/contracts/openapi.json` by `test/api_routes_contract_test.dart`.
abstract final class SimulationRoutes {
  static const state = '/state';
  static const commands = '/commands';
  static const demoReset = '/demo/reset';
  static const demoSpeed = '/demo/speed';
}
