import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:kleos_ui/data/models/production.dart';
import 'package:kleos_ui/data/services/production_api.dart';
import 'package:kleos_ui/state/production_store.dart';
import 'package:kleos_ui/ui/core/widgets.dart';
import 'package:kleos_ui/ui/features/admin/admin_page.dart';

class _Account extends AccountController {
  _Account(this.role);
  final String role;
  @override
  Future<AccountSession?> build() async => AccountSession.fromJson({
        'subject': 'me',
        'tenantId': 'org',
        'role': role,
        'organizations': [
          {'id': 'org', 'name': 'Acme', 'role': role}
        ]
      });
}

Widget _page(String role, MockClient client) => ProviderScope(
        overrides: [
          accountProvider.overrideWith(() => _Account(role)),
          productionApiProvider
              .overrideWithValue(ProductionApi(client: client)),
        ],
        child: MaterialApp(
            theme: kTheme(),
            home: const Scaffold(
                body: SingleChildScrollView(child: AdminPage()))));

void main() {
  testWidgets('owners create an invite and see its one-time code',
      (tester) async {
    tester.view.physicalSize = const Size(1200, 1600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    final posted = <Map<String, dynamic>>[];
    final client = MockClient((request) async {
      final path = request.url.path.replaceFirst('/api/v1', '');
      if (request.method == 'POST' && path == '/auth/invites') {
        posted.add(jsonDecode(request.body) as Map<String, dynamic>);
        return http.Response(
            jsonEncode({
              'code': 'k9-secret-code',
              'role': 'viewer',
              'email': 'new@example.com',
              'expiresInHours': 168
            }),
            200);
      }
      if (path == '/auth/members') {
        return http.Response(
            jsonEncode({
              'members': [
                {
                  'subject': 'me',
                  'email': 'me@example.com',
                  'name': '',
                  'role': 'owner',
                  'you': true,
                  'locked': false
                },
                {
                  'subject': 'other',
                  'email': 'other@example.com',
                  'name': 'Grace',
                  'role': 'member',
                  'you': false,
                  'locked': true
                }
              ]
            }),
            200);
      }
      return http.Response(
          jsonEncode({
            'invites': [
              {
                'id': 'i1',
                'role': 'member',
                'email': null,
                'status': 'pending',
                'expiresAt': '2030-01-01T00:00:00Z'
              }
            ]
          }),
          200);
    });
    await tester.pumpWidget(_page('owner', client));
    await tester.pumpAndSettle();
    expect(find.text('Invite people to Acme and manage who can use it.'),
        findsOneWidget);
    expect(find.text('Grace'), findsOneWidget);
    expect(find.text('Locked'), findsOneWidget);
    expect(find.text('me@example.com (you)'), findsOneWidget);
    expect(find.byKey(const ValueKey('invite-i1')), findsOneWidget);

    await tester.enterText(
        find.byKey(const ValueKey('admin-invite-email')), 'new@example.com');
    await tester.tap(find.byKey(const ValueKey('admin-create-invite')));
    await tester.pumpAndSettle();
    expect(posted.single,
        {'role': 'member', 'email': 'new@example.com', 'expiresHours': 168});
    expect(find.text('k9-secret-code'), findsOneWidget);
    await tester.tap(find.text('Done'));
    await tester.pumpAndSettle();
    expect(find.text('k9-secret-code'), findsNothing);
  });

  testWidgets('non-admins cannot open the portal and make no admin requests',
      (tester) async {
    var requests = 0;
    final client = MockClient((request) async {
      requests++;
      return http.Response('{}', 200);
    });
    await tester.pumpWidget(_page('member', client));
    await tester.pumpAndSettle();
    expect(find.textContaining('Only organization owners and admins'),
        findsOneWidget);
    expect(requests, 0);
  });

  testWidgets('API errors surface instead of failing silently', (tester) async {
    tester.view.physicalSize = const Size(1200, 1600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    final client = MockClient((request) async {
      final path = request.url.path.replaceFirst('/api/v1', '');
      if (request.method == 'POST') {
        return http.Response(
            jsonEncode(
                {'message': 'Only an owner can manage owners and admins'}),
            403);
      }
      return http.Response(
          jsonEncode(path == '/auth/members'
              ? {'members': <Object>[]}
              : {'invites': <Object>[]}),
          200);
    });
    await tester.pumpWidget(_page('admin', client));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('admin-create-invite')));
    await tester.pumpAndSettle();
    expect(find.text('Only an owner can manage owners and admins'),
        findsOneWidget);
  });
}
