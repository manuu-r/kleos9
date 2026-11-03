// Static double-check of every backend connection the client can make: the route table against the frozen
// OpenAPI contract and the recorded frontend obligations, plus a guard against paths hardcoded elsewhere.
import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:kleos_ui/data/services/api_routes.dart';

String normalizeRoute(String route) =>
    route.split('?').first.replaceAll(RegExp(r'/(p1|p2|p3)(?=/|$)'), '/{}');
String normalizeTemplate(String path) =>
    path.replaceFirst('/api/v1', '').replaceAll(RegExp(r'\{[^}]+\}'), '{}');

/// Every (method, route) the client issues. Sample ids are p1/p2/p3.
final List<(String, String)> clientRoutes = [
  ('GET', ApiRoutes.authConfig),
  ('GET', ApiRoutes.authOrganizations),
  ('GET', ApiRoutes.authSession),
  ('POST', ApiRoutes.authLogin),
  ('POST', ApiRoutes.authLogout),
  ('POST', ApiRoutes.authSetup),
  ('POST', ApiRoutes.authSignup),
  ('GET', ApiRoutes.authMembers),
  ('GET', ApiRoutes.authInvites),
  ('POST', ApiRoutes.authInvites),
  ('DELETE', ApiRoutes.authInvite('p1')),
  ('DELETE', ApiRoutes.authMember('p1')),
  ('POST', ApiRoutes.authMemberRole('p1')),
  ('POST', ApiRoutes.authMemberResetPassword('p1')),
  ('POST', ApiRoutes.commands),
  ('GET', ApiRoutes.modelCatalog),
  ('GET', ApiRoutes.connectors),
  ('GET', ApiRoutes.engineeringTools),
  ('GET', ApiRoutes.projects),
  ('GET', ApiRoutes.projectsPage(after: 'p1')),
  ('DELETE', ApiRoutes.projectDelete('p1', expectedRevision: 3)),
  ('GET', ApiRoutes.workspace('p1', includeAgentState: true)),
  ('GET', ApiRoutes.run('p1')),
  ('GET', ApiRoutes.nativeJobLogs('p1')),
  ('GET', ApiRoutes.invocationTranscript('p1')),
  ('GET', ApiRoutes.projectEvents('p1', 7)),
  ('POST', ApiRoutes.referenceImages('p1')),
  ('GET', ApiRoutes.referenceImageContent('p1', 'p2')),
  ('GET', ApiRoutes.engineeringPreview('p1')),
  ('GET', ApiRoutes.circuitSimulation('p1')),
  ('POST', ApiRoutes.companionPreview('p1')),
  ('GET', ApiRoutes.artifactDownload('p1')),
  ('GET', ApiRoutes.releaseDownload('p1')),
  ('GET', ApiRoutes.rentAHumanStatus),
  ('GET', ApiRoutes.rentAHumanHumans),
  ('GET', ApiRoutes.rentAHumanBounties),
  ('GET', ApiRoutes.humanTaskDrafts('p1')),
  ('POST', ApiRoutes.humanTaskDrafts('p1')),
  for (final action in ['preview', 'link', 'sync'])
    ('POST', ApiRoutes.humanTaskDraftAction('p1', 'p2', action)),
  ('GET', ApiRoutes.bambuCatalog),
  ('GET', ApiRoutes.bambuLocal),
  ('DELETE', ApiRoutes.bambuLocal),
  ('POST', ApiRoutes.bambuDiscover),
  ('POST', ApiRoutes.bambuCertificate),
  ('POST', ApiRoutes.bambuConnect),
  ('POST', ApiRoutes.bambuRefresh),
  ('POST', ApiRoutes.bambuTestPrints),
  ('GET', ApiRoutes.bambuTestPrint('p1')),
  ('GET', ApiRoutes.bambuTestPrintFile('p1', source: true)),
  ('GET', ApiRoutes.bambuTestPrintFile('p1', source: false)),
  ('GET', ApiRoutes.printPreparations('p1')),
  ('POST', ApiRoutes.printPreparations('p1')),
  ('GET', ApiRoutes.printDispatches('p1')),
  ('POST', ApiRoutes.printDispatches('p1')),
  for (final action in ['send', 'cancel', 'reconcile'])
    ('POST', ApiRoutes.printDispatchAction('p1', 'p2', action)),
];

void main() {
  final contract =
      jsonDecode(File('../backend/contracts/openapi.json').readAsStringSync())
          as Map<String, dynamic>;
  final operations = <String>{
    for (final entry in (contract['paths'] as Map<String, dynamic>).entries)
      for (final method in (entry.value as Map<String, dynamic>).keys)
        '${method.toUpperCase()} ${normalizeTemplate(entry.key)}',
  };

  test(
      'every route the client can build is an operation in the OpenAPI contract',
      () {
    final missing = [
      for (final (method, route) in clientRoutes)
        if (!operations.contains('$method ${normalizeRoute(route)}'))
          '$method $route',
    ];
    expect(missing, isEmpty, reason: 'routes absent from the frozen contract');
  });

  test('every operation the frontend is documented to call has a client route',
      () {
    final obligations = jsonDecode(
        File('../backend/tests/contracts/frontend-obligations.json')
            .readAsStringSync()) as Map<String, dynamic>;
    final covered = {
      for (final (method, route) in clientRoutes)
        '$method ${normalizeRoute(route)}',
    };
    // The artifact content route is reached by following the signed `download` URL, never by a typed path.
    const followedFromDownloadUrl = {'GET /artifacts/{}/content'};
    final uncovered = [
      for (final operation in obligations['operations'] as List)
        if ((operation['frontendSources'] as List?)?.isNotEmpty ?? false)
          if (!covered.contains(
                  '${operation['method']} ${normalizeTemplate(operation['path'] as String)}') &&
              !followedFromDownloadUrl.contains(
                  '${operation['method']} ${normalizeTemplate(operation['path'] as String)}'))
            '${operation['method']} ${operation['path']}',
    ];
    expect(uncovered, isEmpty,
        reason: 'documented operations with no client route');
  });

  test(
      'the simulation service routes are operations in the mock backend contract',
      () {
    final mock = jsonDecode(
            File('../mock-backend/contracts/openapi.json').readAsStringSync())
        as Map<String, dynamic>;
    final mockOperations = <String>{
      for (final entry in (mock['paths'] as Map<String, dynamic>).entries)
        for (final method in (entry.value as Map<String, dynamic>).keys)
          '${method.toUpperCase()} ${entry.key.replaceFirst('/api', '')}',
    };
    for (final operation in [
      'GET ${SimulationRoutes.state}',
      'POST ${SimulationRoutes.commands}',
      'POST ${SimulationRoutes.demoReset}',
      'POST ${SimulationRoutes.demoSpeed}',
    ]) {
      expect(mockOperations, contains(operation));
    }
  });

  test('path parameters are always percent-encoded', () {
    expect(ApiRoutes.workspace('a/b c?d'), '/projects/a%2Fb%20c%3Fd/workspace');
    expect(ApiRoutes.nativeJobLogs('../x'), '/native-jobs/..%2Fx/logs');
    expect(ApiRoutes.authMember('user@example.com/role'),
        '/auth/members/user%40example.com%2Frole');
    expect(ApiRoutes.printDispatchAction('p', 'd/1', 'send'),
        '/projects/p/print-dispatches/d%2F1/send');
  });

  test('no source file outside the route table hardcodes a backend path', () {
    final route = RegExp(
        r'''['"]/(auth|projects|runs|artifacts|releases|commands|connectors|model-catalog|engineering-tools|native-jobs|invocations|printers|rentahuman)\b''');
    final offenders = <String>[];
    for (final file in Directory('lib')
        .listSync(recursive: true)
        .whereType<File>()
        .where((f) => f.path.endsWith('.dart'))) {
      final name = file.path.split('/').last;
      if (name == 'api_routes.dart' || name == 'api.generated.dart') continue;
      final lines = file.readAsLinesSync();
      for (var i = 0; i < lines.length; i++) {
        final line = lines[i];
        // Navigation (go_router) paths share the /projects and /runs names; only API calls are policed.
        final isApiCall = RegExp(
                r'\.(get|post|delete|send)\(|Uri\(|uri\(|_request\(|_authenticate\(')
            .hasMatch(line);
        if (isApiCall && route.hasMatch(line)) {
          offenders.add('${file.path}:${i + 1}');
        }
      }
    }
    expect(offenders, isEmpty,
        reason: 'use ApiRoutes instead of a literal path');
  });
}
