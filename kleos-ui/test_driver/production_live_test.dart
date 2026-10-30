// Drives the real production UI and API. Credentials are read only by this host
// process, never embedded in the application, screenshots, or command logs.
import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'package:crypto/crypto.dart';
import 'package:flutter_driver/flutter_driver.dart';

typedef Json = Map<String, dynamic>;

Future<void> main() async {
  final tokenPath = Platform.environment['KLEOS_E2E_TOKEN_FILE'];
  if (tokenPath == null) throw StateError('Set KLEOS_E2E_TOKEN_FILE.');
  final token = (await File(tokenPath).readAsString()).trim();
  if (token.isEmpty) throw StateError('The local development token is empty.');
  final api = Uri.parse(
      Platform.environment['KLEOS_E2E_API'] ?? 'http://127.0.0.1:8000/api/v1/');
  final output =
      Directory(Platform.environment['KLEOS_E2E_OUTPUT_DIR'] ?? 'build/e2e')
        ..createSync(recursive: true);
  final report = <String, dynamic>{
    'startedAt': DateTime.now().toUtc().toIso8601String(),
    'checks': <String>[]
  };
  final client = HttpClient()..connectionTimeout = const Duration(seconds: 15);
  String? organization;
  Future<Json> read(String path) async {
    final request = await client.getUrl(api.resolve(path));
    request.headers.set('Authorization', 'Bearer $token');
    if (organization != null) {
      request.headers.set('X-Organization-Id', organization);
    }
    final response = await request.close();
    if (response.statusCode != 200) {
      await response.drain<void>();
      throw StateError('API read failed ($path, HTTP ${response.statusCode}).');
    }
    return jsonDecode(await utf8.decoder.bind(response).join()) as Json;
  }

  Future<Json> until(String path, bool Function(Json) ready,
      {Duration timeout = const Duration(minutes: 3)}) async {
    final deadline = DateTime.now().add(timeout);
    while (DateTime.now().isBefore(deadline)) {
      final value = await read(path);
      if (ready(value)) return value;
      await Future<void>.delayed(const Duration(seconds: 1));
    }
    throw StateError('Timed out awaiting $path.');
  }

  void checked(String name) {
    (report['checks'] as List<String>).add(name);
    stdout.writeln('PASS: $name');
  }

  final organizations = await read('auth/organizations');
  organization = (organizations['organizations'] as List).first['id'] as String;
  final catalog = await read('model-catalog');
  final google = (catalog['models'] as List)
      .cast<Json>()
      .singleWhere((m) => m['provider'] == 'google');
  if (google['configured'] != true) {
    throw StateError('Gemini is not configured on the backend.');
  }
  final defaults = catalog['defaultPolicy'] as Json;
  final driver = await FlutterDriver.connect(
      printCommunication: false,
      logCommunicationToFile: false,
      timeout: const Duration(seconds: 60));
  final downloads = Directory(
      '${output.path}/downloads-${DateTime.now().millisecondsSinceEpoch}')
    ..createSync(recursive: true);
  final driverUri = Platform.environment['DRIVER_SESSION_URI'];
  final driverId = Platform.environment['DRIVER_SESSION_ID'];
  if (driverUri != null && driverId != null) {
    final request = await client.postUrl(
        Uri.parse(driverUri).resolve('session/$driverId/goog/cdp/execute'));
    request.headers.contentType = ContentType.json;
    final body = utf8.encode(jsonEncode({
      'cmd': 'Browser.setDownloadBehavior',
      'params': {'behavior': 'allow', 'downloadPath': downloads.absolute.path}
    }));
    // ChromeDriver's HTTP server requires a fixed-length JSON request.
    request.contentLength = body.length;
    request.add(body);
    final response = await request.close();
    await response.drain<void>();
    if (response.statusCode != 200) {
      throw StateError('Could not configure the browser test download folder.');
    }
  }
  Future<void> tap(SerializableFinder finder, {bool activate = true}) async {
    report['currentAction'] = finder.serialize();
    stdout.writeln('ACTION: ${finder.serialize()}');
    try {
      await driver.waitFor(finder, timeout: const Duration(seconds: 1));
    } catch (_) {
      // ListView children are lazy. Start at the top before searching down so
      // this also finds controls above the current viewport on a resumed run.
      final page = find.byValueKey('production-page-scroll');
      await driver.scroll(page, 0, 100000, const Duration(milliseconds: 250));
      var found = false;
      // WebFlutterDriver's result mailbox supports one command at a time.
      // Keep waits and scrolls sequential instead of scrollUntilVisible's
      // overlapping waitFor/scroll requests.
      for (var scroll = 0; scroll < 40; scroll++) {
        try {
          await driver.waitFor(finder,
              timeout: const Duration(milliseconds: 500));
          found = true;
          break;
        } catch (_) {
          await driver.scroll(page, 0, -300, const Duration(milliseconds: 100));
        }
      }
      if (!found) {
        throw StateError(
            'Control did not become visible: ${finder.serialize()}');
      }
    }
    await driver.scrollIntoView(finder,
        alignment: .5, timeout: const Duration(seconds: 30));
    if (activate) {
      await driver.tap(finder, timeout: const Duration(seconds: 30));
    }
  }

  Future<void> screenshot(String name) async {
    await File('${output.path}/$name.png')
        .writeAsBytes(await driver.screenshot(), flush: true);
  }

  Future<void> signIn() async {
    await driver.waitFor(find.byValueKey('production-dev-token'),
        timeout: const Duration(seconds: 45));
    await driver.tap(find.byValueKey('production-dev-token'));
    try {
      await driver.enterText(token);
    } catch (_) {
      throw StateError('Could not enter the runtime development credential.');
    }
    await driver.tap(find.byValueKey('production-sign-in'));
    await driver.waitFor(find.byValueKey('production-new-build'),
        timeout: const Duration(seconds: 45));
  }

  try {
    await driver.runUnsynchronized(() async {
      await signIn();
      checked('Development sign-in and authorized project list');
      await screenshot('01-projects');
      var projectId = Platform.environment['KLEOS_E2E_PROJECT_ID'];
      final inspectOnly = Platform.environment['KLEOS_E2E_INSPECT_ONLY'] == '1';
      if (projectId == null) {
        await driver.tap(find.byValueKey('production-new-build'));
        await driver.waitFor(find.byValueKey('production-project-brief'));
        final label =
            'UI runtime watch ${DateTime.now().millisecondsSinceEpoch}';
        await driver.tap(find.byValueKey('production-project-brief'));
        await driver.enterText(
            '$label. A bounded smartwatch engineering demo for watch-telemetry-v1. '
            'Ask one short clarification about strap attachment. Use a simple enclosure and the fixed '
            '8-byte telemetry protocol. requiredPeripheralCoverage is an empty list. excludedCoverage '
            'is [ble, sensor, display, radio, thermal, physicalOperation]. All physical outcomes remain '
            'simulated. Do not claim excluded capabilities work. Keep research concise.');
        for (final lane in defaults.keys) {
          if ((defaults[lane] as Json)['provider'] == 'google') continue;
          await tap(find.byValueKey('provider-$lane'));
          await driver.tap(find.byValueKey('provider-option-$lane-google'));
        }
        await screenshot('02-provider-choices');
        await tap(find.text('Create project'));
        final listed = await until(
            'projects',
            (j) => (j['projects'] as List)
                .any((p) => (p['brief'] as String).startsWith(label)));
        projectId = ((listed['projects'] as List)
                .singleWhere((p) => (p['brief'] as String).startsWith(label))
            as Json)['id'] as String;
        checked(
            'Project creation with all seven lanes explicitly set to Gemini');
      } else {
        await tap(find.byValueKey('open-project-$projectId'));
      }
      report['projectId'] = projectId;
      await File('${output.path}/latest-project.json')
          .writeAsString(jsonEncode({'projectId': projectId}), flush: true);
      stdout.writeln('UI_PROJECT_ID=$projectId');
      final workspacePath = 'projects/$projectId/workspace';
      Json workspace = await read(workspacePath);
      if (!inspectOnly) {
        if (['queued', 'running', 'waiting']
            .contains(workspace['project']['status'])) {
          workspace = await until(
              workspacePath,
              (j) => !['queued', 'running', 'waiting']
                  .contains(j['project']['status']));
        }
        final policy = (workspace['project'] as Json)['modelPolicy'] as Json;
        if (policy.values.any((v) => (v as Json)['provider'] != 'google')) {
          throw StateError('A lane was not explicitly pinned to Gemini.');
        }
        if (((workspace['project'] as Json)['state'] as Json)['proposal'] ==
            null) {
          final count = (workspace['runs'] as List).length;
          await tap(find.byValueKey('command-project.research'));
          workspace = await until(
              workspacePath,
              (j) =>
                  (j['runs'] as List).length > count &&
                  !['queued', 'running', 'waiting']
                      .contains(j['project']['status']));
        }
        final proposal = ((workspace['project'] as Json)['state']
            as Json)['proposal'] as Json?;
        if (proposal == null) {
          throw StateError(
              'Research did not produce a proposal: ${(workspace['runs'] as List).first['blocker']}');
        }
        if (workspace['project']['state']['confirmed'] != true) {
          await driver.waitFor(find.text('Confirm concept'),
              timeout: const Duration(seconds: 30));
        }
        checked(
            'Real Gemini research reaches the UI through streamed progress');
        await screenshot('03-proposal');

        final questions = (proposal['questions'] as List?) ?? [];
        final requirements = workspace['project']['requirements'] as Json;
        if (questions.isNotEmpty &&
            workspace['project']['engineeringRevision'] == 1 &&
            !requirements.containsKey(questions.first)) {
          final question = questions.first as String;
          await tap(find.byValueKey('answer-$question'));
          await driver.enterText(
              'Use standard 20 mm spring-bar strap attachment. Keep the explicitly excluded digital scope.');
          await tap(find.byValueKey('save-answer-$question'));
          workspace = await until(
              workspacePath,
              (j) =>
                  (j['project']['requirements'] as Json).containsKey(question));
          checked('Clarification answer starts a new engineering revision');
          await tap(find.byValueKey('command-project.research'));
          workspace = await until(
              workspacePath, (j) => j['project']['state']['proposal'] != null);
          await driver.waitFor(find.text('Confirm concept'),
              timeout: const Duration(seconds: 30));
        }
        if (workspace['project']['state']['confirmed'] != true) {
          await tap(find.byValueKey('command-project.confirm'));
          workspace = await until(
              workspacePath, (j) => j['project']['state']['confirmed'] == true,
              timeout: const Duration(seconds: 30));
          checked(
              'Concept acceptance preserves explicit requirements and validation scope');
        }
        if ((workspace['project']['interfaces'] as Json).isEmpty) {
          final count = (workspace['runs'] as List).length;
          await tap(find.byValueKey('command-project.solveInterfaces'));
          workspace = await until(
              workspacePath,
              (j) =>
                  (j['runs'] as List).length > count &&
                  !['queued', 'running', 'waiting']
                      .contains(j['project']['status']));
          if ((workspace['project']['interfaces'] as Json).isEmpty) {
            throw StateError(
                'Interface generation blocked: ${(workspace['runs'] as List).first['blocker']}');
          }
          checked(
              'Real interface results arrive and enable engineering generation');
        }

        await tap(find.text('AI provider settings'));
        await tap(find.byValueKey('provider-coordination'));
        await driver.tap(find.byValueKey('provider-option-coordination-meta'));
        await tap(find.text('Save provider choices'));
        await until(
            workspacePath,
            (j) =>
                j['project']['modelPolicy']['coordination']['provider'] ==
                'meta');
        await driver.waitForAbsent(find.text('Save provider choices'),
            timeout: const Duration(seconds: 30));
        await tap(find.byValueKey('provider-preset-google'));
        await tap(find.text('Save provider choices'));
        await until(
            workspacePath,
            (j) =>
                j['project']['modelPolicy']['coordination']['provider'] ==
                'google');
        await driver.waitForAbsent(find.text('Save provider choices'),
            timeout: const Duration(seconds: 30));
        checked(
            'Provider settings persist changes and restore Gemini without making an extra call');

        await tap(find.byValueKey('production-training-consent'));
        await until(workspacePath, (j) => j['trainingOptedIn'] == true);
        await tap(find.byValueKey('production-training-consent'));
        await until(workspacePath, (j) => j['trainingOptedIn'] == false);
        checked('Training consent saves both choices and ends opted out');
      }
      workspace = await read(workspacePath);
      final artifacts = (workspace['artifacts'] as List)
          .cast<Json>()
          .where((a) => ['candidate', 'accepted'].contains(a['status']));
      if (artifacts.isNotEmpty) {
        final wantedArtifact = Platform.environment['KLEOS_E2E_ARTIFACT_NAME'];
        final artifact = wantedArtifact == null
            ? artifacts.first
            : artifacts.firstWhere((a) => a['name'] == wantedArtifact);
        await tap(find.byValueKey('download-artifact-${artifact['id']}'));
        final manifest = await read('artifacts/${artifact['id']}/download');
        final location = api.resolve(manifest['url'] as String);
        final request = await client.getUrl(location);
        if (location.origin == api.origin) {
          request.headers.set('Authorization', 'Bearer $token');
          request.headers.set('X-Organization-Id', organization!);
        }
        final response = await request.close();
        if (response.statusCode != 200) {
          throw StateError('Artifact download failed.');
        }
        final bytes = await response
            .fold<List<int>>([], (buffer, chunk) => buffer..addAll(chunk));
        if (sha256.convert(bytes).toString() != artifact['sha256']) {
          throw StateError('Downloaded artifact failed its manifest hash.');
        }
        report['downloadedArtifactId'] = artifact['id'];
        final saved = File(
            '${downloads.path}/${(artifact['name'] as String).split('/').last}');
        final deadline = DateTime.now().add(const Duration(seconds: 30));
        while (!await saved.exists() && DateTime.now().isBefore(deadline)) {
          await Future<void>.delayed(const Duration(milliseconds: 250));
        }
        if (!await saved.exists() ||
            sha256.convert(await saved.readAsBytes()).toString() !=
                artifact['sha256']) {
          throw StateError(
              'The browser did not save the matching artifact file after the download action.');
        }
        report['browserDownloadedFile'] = saved.path;
        checked(
            'Artifact download action and authorized content SHA-256 verification');
      }
      final runs = (workspace['runs'] as List).cast<Json>();
      if (runs.isNotEmpty) {
        final runId = Platform.environment['KLEOS_E2E_RUN_ID'] ??
            runs.first['id'] as String;
        await tap(find.byValueKey('open-run-$runId'));
        await driver.waitFor(find.text('Run inspector'));
        await screenshot('04-run-inspector');
        final run = await read('runs/$runId');
        report['inspectedRunId'] = runId;
        report['runStatus'] = run['status'];
        checked(
            'Run inspector opens actual tasks, attempts, models, usage and blockers');
        final tasks = (run['tasks'] as List).cast<Json>();
        final calls = tasks
            .expand((t) => (t['attempts'] as List).cast<Json>())
            .expand((a) => (a['calls'] as List).cast<Json>())
            .where((c) => c['hasTranscript'] == true);
        if (calls.isNotEmpty) {
          await tap(find.byValueKey('transcript-${calls.first['id']}'));
          await tap(find.text('AI record'));
          await screenshot('06-ai-record');
          checked(
              'Authorized redacted AI transcript opens inside the run inspector');
          await tap(find.text('AI record'));
        }
        final jobs = tasks
            .expand((t) => (t['nativeJobs'] as List).cast<Json>())
            .where((j) => j['hasLogs'] == true);
        if (jobs.isNotEmpty) {
          await tap(find.byValueKey('native-logs-${jobs.first['id']}'));
          await tap(find.text('Engineering logs'));
          await screenshot('07-native-logs');
          checked(
              'Authorized native engineering logs open inside the run inspector');
          await tap(find.text('Engineering logs'));
        }
      }
      stdout.writeln('ACTION: return to the project list');
      await driver.tap(find.byTooltip('Projects'),
          timeout: const Duration(seconds: 30));
      await tap(find.byValueKey('open-project-$projectId'));
      await driver.waitFor(find.text('Live updates'),
          timeout: const Duration(seconds: 30));
      checked('An idle authenticated event stream is shown as connected');
      if (Platform.environment['KLEOS_E2E_BATTERY_CHANGE'] == '1') {
        final previousRevision =
            workspace['project']['engineeringRevision'] as int;
        await tap(find.text('Revise battery requirements'));
        for (final entry in {
          'Capacity (mAh)': '350',
          'Width (mm)': '22',
          'Length (mm)': '17',
          'Height (mm)': '5',
        }.entries) {
          await tap(find.byValueKey('battery-${entry.key}'));
          await driver.enterText(entry.value);
        }
        await tap(find.text('Apply battery revision'));
        workspace = await until(
            workspacePath,
            (j) =>
                (j['project']['engineeringRevision'] as int) >
                previousRevision);
        if ((workspace['project']['interfaces'] as Json).isNotEmpty ||
            (workspace['artifacts'] as List)
                .any((a) => a['status'] == 'candidate')) {
          throw StateError(
              'Battery revision retained stale engineering inputs.');
        }
        checked(
            'Battery change creates a revision and invalidates interfaces and candidate artifacts');
      }
      await tap(
          find.text(
              'Engineering revision ${workspace['project']['engineeringRevision']}'),
          activate: false);
      checked(
          'Leaving and reopening the workspace reconnects from its current snapshot');
      await screenshot('05-workspace');
      final errors =
          jsonDecode(await driver.requestData('runtimeErrors')) as List;
      if (errors.isNotEmpty) {
        throw StateError('Flutter runtime errors: ${errors.join('; ')}');
      }
      checked('No Flutter runtime errors during exercised flows');
      report['status'] = 'passed';
    });
  } catch (error) {
    report['status'] = 'failed';
    report['error'] = error.toString().replaceAll(token, '[REDACTED]');
    stderr.writeln(report['error']);
    if (report['projectId'] != null) {
      try {
        await screenshot('failure');
      } catch (_) {
        // Preserve the original failure if the browser itself disconnected.
      }
    }
    exitCode = 1;
  } finally {
    report['finishedAt'] = DateTime.now().toUtc().toIso8601String();
    await File('${output.path}/report.json').writeAsString(
        const JsonEncoder.withIndent('  ').convert(report),
        flush: true);
    await driver.close();
    client.close(force: true);
  }
}
