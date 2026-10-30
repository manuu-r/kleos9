import 'dart:async';
import 'dart:convert';
import 'package:crypto/crypto.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:kleos_ui/data/services/command_journal.dart';
import 'package:kleos_ui/data/services/production_api.dart';
import 'package:kleos_ui/data/services/production_auth.dart';
import 'package:kleos_ui/data/repositories/production_repository.dart';

void main() {
  test(
      'delete pins revision and organization and clears only its pending commands',
      () async {
    String? disk;
    final journal = DurableCommandJournal('delete-test',
        read: (_) async => disk,
        write: (_, value) async {
          disk = value;
        });
    await journal.prepare(
        'project.answer',
        {
          'projectId': 'p',
          'values': ['yes']
        },
        4);
    await journal.prepare(
        'project.answer',
        {
          'projectId': 'q',
          'values': ['no']
        },
        2);
    final api = ProductionApi(client: MockClient((request) async {
      expect(request.method, 'DELETE');
      expect(request.url.path, '/api/v1/projects/p');
      expect(request.url.queryParameters['expectedRevision'], '4');
      expect(request.headers['X-Organization-Id'], 'org');
      return http.Response('{"projectId":"p","deleted":true}', 202);
    }));
    addTearDown(api.close);
    final repository =
        ProductionRepository(api, journal, organizationId: 'org');
    expect((await repository.deleteProject('p', revision: 4))['deleted'], true);
    final pending = await journal.pending();
    expect(pending, hasLength(1));
    expect((pending.single.body['payload'] as Map)['projectId'], 'q');
  });

  test('development no-login session needs no token', () async {
    final api = ProductionApi(client: MockClient((request) async {
      expect(request.headers.containsKey('Authorization'), false);
      final body = request.url.path.endsWith('/config')
          ? <String, Object>{'authMode': 'development', 'loginRequired': false}
          : request.url.path.endsWith('/organizations')
              ? <String, Object>{
                  'subject': 'development',
                  'organizations': [
                    {'id': 'local', 'name': 'Local', 'role': 'owner'}
                  ]
                }
              : <String, Object>{
                  'subject': 'development',
                  'tenantId': 'local',
                  'role': 'owner',
                  'loginRequired': false
                };
      return http.Response(jsonEncode(body), 200);
    }));
    addTearDown(api.close);
    final auth = ProductionAuth(api,
        readPreference: (_) async => jsonEncode('previous-dev-organization'),
        writePreference: (_, __) async {});
    final session = await auth.signIn();
    expect(session!.tenantId, 'local');
    expect(session.loginRequired, false);
    expect(await auth.token(), isNull);
  });

  test('password sign-in and invite signup keep the native bearer token',
      () async {
    FlutterSecureStorage.setMockInitialValues({});
    final seen = <String>[];
    final api = ProductionApi(client: MockClient((request) async {
      final path = request.url.path.replaceFirst('/api/v1', '');
      seen.add('$path ${request.headers['Authorization'] ?? '-'}');
      if (path == '/auth/config') {
        return http.Response(
            jsonEncode({'authMode': 'password', 'loginRequired': true}), 200);
      }
      if (path == '/auth/login' || path == '/auth/signup') {
        final body = jsonDecode(request.body) as Map<String, dynamic>;
        expect(body['native'], true);
        if (path == '/auth/signup') {
          expect(body['inviteCode'], 'k9-code');
        }
        return http.Response(
            jsonEncode({'subject': 'u', 'token': 'tok-1'}), 200);
      }
      if (path == '/auth/organizations') {
        return http.Response(
            jsonEncode({
              'subject': 'u',
              'organizations': [
                {'id': 'org', 'name': 'Org', 'role': 'member'}
              ]
            }),
            200);
      }
      return http.Response(
          jsonEncode({'subject': 'u', 'tenantId': 'org', 'role': 'member'}),
          200);
    }));
    addTearDown(api.close);
    ProductionAuth auth() => ProductionAuth(api,
        readPreference: (_) async => null, writePreference: (_, __) async {});
    final signedIn = auth();
    expect(
        (await signedIn.signIn(email: ' ada@example.com ', password: 'pw'))!
            .tenantId,
        'org');
    expect(await signedIn.token(), 'tok-1');
    expect(seen, contains('/auth/organizations Bearer tok-1'));
    // The token survives an app restart through secure storage.
    final restored = auth();
    expect(await restored.restore(), isNotNull);
    expect(await restored.token(), 'tok-1');
    expect(
        (await auth().signUp(
                inviteCode: ' k9-code ',
                email: 'new@example.com',
                password: 'long enough password'))
            .tenantId,
        'org');
    await expectLater(auth().signIn(email: '', password: ''),
        throwsA(isA<ProductionApiException>()));
  });

  test(
      'organization choice persists and revocation requires explicit selection',
      () async {
    final preferences = <String, String>{};
    var organizations = ['a', 'b'];
    final api = ProductionApi(client: MockClient((request) async {
      if (request.url.path.endsWith('/organizations')) {
        return http.Response(
            jsonEncode({
              'subject': 'user',
              'organizations': [
                for (final id in organizations)
                  {'id': id, 'name': id, 'role': 'owner'}
              ]
            }),
            200);
      }
      return http.Response(
          jsonEncode({
            'subject': 'user',
            'tenantId': request.headers['X-Organization-Id'],
            'role': 'owner',
            'csrfToken': 'csrf'
          }),
          200);
    }));
    ProductionAuth auth() => ProductionAuth(api,
        readPreference: (key) async => preferences[key],
        writePreference: (key, value) async {
          preferences[key] = value;
        });
    expect((await auth().session()).tenantId, 'a');
    expect((await auth().selectOrganization('b')).tenantId, 'b');
    api.tenantId = null;
    expect((await auth().session()).tenantId, 'b');
    organizations = ['a'];
    await expectLater(
        auth().session(), throwsA(isA<OrganizationSelectionRequired>()));
    expect(api.tenantId, isNull);
    await expectLater(
        auth().session(), throwsA(isA<OrganizationSelectionRequired>()));
    expect((await auth().selectOrganization('a')).tenantId, 'a');
  });

  test('a queued command stays pinned to its organization during a switch',
      () async {
    final api = ProductionApi(client: MockClient((request) async {
      expect(request.headers['X-Organization-Id'], 'a');
      return http.Response(
          '{"commandId":"c","projectId":"p","revision":1}', 202);
    }));
    api.tenantId = 'b';
    final journal = DurableCommandJournal('a',
        read: (_) async => null, write: (_, __) async {});
    await ProductionRepository(api, journal, organizationId: 'a')
        .command('project.create', {'brief': 'watch'});
  });

  test(
      'journal survives restart, reuses original revision, and separates users',
      () async {
    final disk = <String, String>{};
    DurableCommandJournal open(String user) => DurableCommandJournal(user,
        read: (key) async => disk[key],
        write: (key, value) async {
          disk[key] = value;
        });
    final first = await open('tenant/user')
        .prepare('project.generate', {'projectId': 'p'}, 5);
    final restored = await open('tenant/user')
        .prepare('project.generate', {'projectId': 'p'}, 8);
    expect(restored.key, first.key);
    expect(restored.body['expectedRevision'], 5);
    expect(await open('other/user').pending(), isEmpty);
    await open('tenant/user').remove(first.key);
    expect(await open('tenant/user').pending(), isEmpty);
  });

  test(
      'parallel submissions and reordered nested payloads have one durable key',
      () async {
    String? disk;
    final journal = DurableCommandJournal('user',
        read: (_) async => disk,
        write: (_, value) async {
          disk = value;
        });
    final commands = await Future.wait([
      journal.prepare(
          'project.answer',
          {
            'projectId': 'p',
            'nested': {'a': 1, 'b': 2}
          },
          1),
      journal.prepare(
          'project.answer',
          {
            'nested': {'b': 2, 'a': 1},
            'projectId': 'p'
          },
          2),
    ]);
    expect(commands.map((c) => c.key).toSet(), hasLength(1));
    expect(await journal.pending(), hasLength(1));
  });

  test(
      'uncertain HTTP submission retries after restart with exact body and key',
      () async {
    String? disk;
    CommandJournal journal() => DurableCommandJournal('user',
        read: (_) async => disk,
        write: (_, value) async {
          disk = value;
        });
    final requests = <http.Request>[];
    final api = ProductionApi(
        baseUrl: 'https://kleos.test/api/v1',
        client: MockClient((request) async {
          requests.add(request);
          if (requests.length == 1) throw http.ClientException('Response lost');
          return http.Response(
              jsonEncode({
                'commandId': 'c',
                'projectId': 'p',
                'runId': 'r',
                'revision': 6
              }),
              202);
        }));
    await expectLater(
        ProductionRepository(api, journal())
            .command('project.generate', {'projectId': 'p'}, revision: 5),
        throwsA(isA<ProductionApiException>()));
    expect(await journal().pending(), hasLength(1));
    final result = await ProductionRepository(api, journal())
        .command('project.generate', {'projectId': 'p'}, revision: 7);
    expect(result.runId, 'r');
    expect(requests[0].headers['Idempotency-Key'],
        requests[1].headers['Idempotency-Key']);
    expect(requests[0].body, requests[1].body);
    expect(await journal().pending(), isEmpty);
  });

  test('failed durable storage prevents any network submission', () async {
    var calls = 0;
    final journal = DurableCommandJournal('user',
        read: (_) async => null,
        write: (_, __) async => throw StateError('storage unavailable'));
    final api = ProductionApi(client: MockClient((_) async {
      calls++;
      return http.Response('{}', 202);
    }));
    await expectLater(
        ProductionRepository(api, journal)
            .command('project.create', {'brief': 'watch'}),
        throwsStateError);
    expect(calls, 0);
  });

  test('definite stale revision clears journal, but throttling preserves it',
      () async {
    for (final code in [409, 429]) {
      String? disk;
      final journal = DurableCommandJournal('user',
          read: (_) async => disk,
          write: (_, value) async {
            disk = value;
          });
      final api = ProductionApi(
          client: MockClient(
              (_) async => http.Response('{"message":"retry"}', code)));
      await expectLater(
          ProductionRepository(api, journal)
              .command('project.verify', {'projectId': 'p'}, revision: 2),
          throwsA(isA<ProductionApiException>()));
      expect(await journal.pending(), hasLength(code == 429 ? 1 : 0));
    }
  });

  test('SSE decodes arbitrary byte chunks, UTF8, multiline data and heartbeats',
      () async {
    final text =
        ': heartbeat\r\nid: 8\r\nevent: progress\r\ndata: {"sequence":8,\r\n'
        'data: "id":"event","createdAt":"2026-10-30","traceId":null,"type":"task.completed","data":{"summary":"µ device"}}\r\n\r\n';
    final bytes = utf8.encode(text);
    final events =
        await decodeEvents(Stream.fromIterable(bytes.map((b) => [b]))).toList();
    expect(events, hasLength(1));
    expect(events.single.sequence, 8);
    expect(events.single.data['summary'], 'µ device');
  });

  test('SSE sends cursor and auth and exposes expired cursor status', () async {
    var connected = false;
    final api = ProductionApi(
        baseUrl: 'https://kleos.test/api/v1',
        streamClientFactory: () => MockClient((request) async {
              expect(request.url.queryParameters['after'], '12');
              expect(request.headers['Authorization'], 'Bearer access');
              expect(request.headers['Accept'], 'text/event-stream');
              return http.Response(
                  '{"message":"reload workspace","code":"cursor_expired"}',
                  410);
            }));
    api.accessToken = () async => 'access';
    await expectLater(
        api.events('p', 12, onConnected: () => connected = true).toList(),
        throwsA(isA<ProductionApiException>()
            .having((error) => error.status, 'status', 410)));
    expect(connected, isFalse);
  });

  test('idle SSE connection is live before any project event arrives',
      () async {
    var connected = false;
    final api = ProductionApi(
        streamClientFactory: () => MockClient((_) async => http.Response(
            ': heartbeat\n\n', 200,
            headers: {'content-type': 'text/event-stream'})));
    final events =
        await api.events('p', 12, onConnected: () => connected = true).toList();
    expect(connected, isTrue);
    expect(events, isEmpty);
    api.close();
  });

  test('SSE reports expired authentication without treating it as progress',
      () async {
    final stream =
        Stream.value(utf8.encode('event: auth_expired\ndata: {}\n\n'));
    await expectLater(
        decodeEvents(stream).toList(),
        throwsA(isA<ProductionApiException>()
            .having((error) => error.status, 'status', 401)));
  });

  test('signed artifact transfer never receives API bearer or CSRF credentials',
      () async {
    final api = ProductionApi(
        baseUrl: 'https://kleos.test/api/v1',
        client: MockClient((request) async {
          if (request.url.host == 'kleos.test') {
            expect(request.headers['Authorization'], 'Bearer access');
            return http.Response(
                jsonEncode({
                  'url': 'https://artifacts.test/output?signature=abc',
                  'name': 'source.txt',
                  'sha256': sha256.convert(utf8.encode('artifact')).toString(),
                }),
                200);
          }
          expect(request.headers.containsKey('Authorization'), false);
          expect(request.headers.containsKey('X-CSRF-Token'), false);
          return http.Response('artifact', 200);
        }));
    api.accessToken = () async => 'access';
    api.csrfToken = 'csrf';
    expect(utf8.decode(await api.artifactBytes('a')), 'artifact');
  });

  test('out-of-order workspace responses cannot replace a newer revision',
      () async {
    final late = Completer<http.Response>();
    var calls = 0;
    String workspace(int revision) => jsonEncode({
          'project': {
            'id': 'p',
            'name': 'Watch',
            'brief': 'Watch',
            'revision': revision,
            'engineeringRevision': 1,
            'policyRevision': 1,
            'modelPolicy': <String, dynamic>{},
            'stage': 0,
            'status': 'draft',
            'requirements': <String, dynamic>{},
            'interfaces': <String, dynamic>{},
            'state': <String, dynamic>{},
            'acceptedReleaseId': null,
            'budget': {
              'allowanceUsd': 20,
              'spentEstimateUsd': 0,
              'reservedUsd': 0
            },
            'mode': 'demo',
            'createdAt': '2026-10-30',
          },
          'revision': revision,
          'eventCursor': revision,
          'runs': <dynamic>[],
          'artifacts': <dynamic>[],
          'checks': <dynamic>[],
          'trainingOptedIn': false,
        });
    final api = ProductionApi(client: MockClient((_) async {
      if (++calls == 1) return late.future;
      return http.Response(workspace(4), 200);
    }));
    final journal = DurableCommandJournal('u',
        read: (_) async => null, write: (_, __) async {});
    final repository = ProductionRepository(api, journal);
    final first = repository.workspace('p');
    final second = await repository.workspace('p');
    late.complete(http.Response(workspace(2), 200));
    expect(second.project.revision, 4);
    expect((await first).project.revision, 4);
  });
}
