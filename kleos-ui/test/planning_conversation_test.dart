import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:kleos_ui/data/models/json.dart';
import 'package:kleos_ui/state/kleos_store.dart';
import 'package:kleos_ui/ui/core/theme.dart';
import 'package:kleos_ui/ui/features/project/planning_conversation.dart';

Widget screen({
  required Future<CommandResult> Function(String, String) send,
  bool busy = false,
  bool allowWhileBusy = true,
  List<JsonMap> messages = const [],
}) =>
    MaterialApp(
        theme: kTheme(),
        home: Scaffold(
            body: SingleChildScrollView(
          padding: const EdgeInsets.all(16),
          child: PlanningConversation(
            messages: messages,
            onSend: send,
            canWrite: true,
            busy: busy,
            allowSendWhileBusy: allowWhileBusy,
          ),
        )));

void main() {
  for (final width in [390.0, 1200.0]) {
    testWidgets('chat accepts a new message during model work at $width',
        (tester) async {
      tester.view.physicalSize = Size(width, 900);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.reset);
      final calls = <String>[];
      await tester.pumpWidget(screen(
          busy: true,
          messages: const [
            {'id': 'user-1', 'role': 'user', 'text': 'A compact air monitor'},
            {
              'id': 'assistant-1',
              'role': 'assistant',
              'text': 'I’m shaping its first concept.'
            },
          ],
          send: (text, id) async {
            calls.add(text);
            return const CommandResult(ok: true);
          }));
      final input = find.byKey(const ValueKey('planning-chat-input'));
      await tester.enterText(input, 'Make the enclosure round');
      await tester.pump();
      await tester.tap(find.byKey(const ValueKey('planning-chat-send')));
      await tester.pumpAndSettle();
      expect(calls, ['Make the enclosure round']);
      expect(tester.widget<TextField>(input).controller!.text, isEmpty);
      expect(tester.widget<TextField>(input).enabled, true);
      expect(find.textContaining('plan resumes automatically'), findsOneWidget);
      expect(tester.takeException(), isNull);
    });
  }

  testWidgets('failed message keeps draft and retry uses its original identity',
      (tester) async {
    final identities = <String>[];
    await tester.pumpWidget(screen(send: (text, id) async {
      identities.add(id);
      return const CommandResult(ok: false);
    }));
    final input = find.byKey(const ValueKey('planning-chat-input'));
    await tester.enterText(input, 'Keep the display on the front');
    await tester.pump();
    await tester.tap(find.byKey(const ValueKey('planning-chat-send')));
    await tester.pumpAndSettle();
    expect(tester.widget<TextField>(input).controller!.text,
        'Keep the display on the front');
    expect(find.textContaining('Your draft is kept'), findsOneWidget);
    await tester.tap(find.byKey(const ValueKey('planning-chat-send')));
    await tester.pumpAndSettle();
    expect(identities, hasLength(2));
    expect(identities.first, identities.last);
    await tester.enterText(input, 'Move the display to the top');
    await tester.pump();
    await tester.tap(find.byKey(const ValueKey('planning-chat-send')));
    await tester.pumpAndSettle();
    expect(identities.last, isNot(identities.first));
  });

  testWidgets('one pending HTTP acceptance prevents duplicate sends',
      (tester) async {
    final pending = Completer<CommandResult>();
    var calls = 0;
    await tester.pumpWidget(screen(send: (_, __) {
      calls++;
      return pending.future;
    }));
    await tester.enterText(find.byKey(const ValueKey('planning-chat-input')),
        'Add a carrying loop');
    await tester.pump();
    await tester.tap(find.byKey(const ValueKey('planning-chat-send')));
    await tester.pump();
    expect(
        tester
            .widget<IconButton>(
                find.byKey(const ValueKey('planning-chat-send')))
            .onPressed,
        isNull);
    expect(calls, 1);
    pending.complete(const CommandResult(ok: true));
    await tester.pumpAndSettle();
    expect(calls, 1);
  });

  testWidgets('Enter sends chat and native engineering work blocks edits',
      (tester) async {
    var calls = 0;
    await tester.pumpWidget(screen(send: (_, __) async {
      calls++;
      return const CommandResult(ok: true);
    }));
    final input = find.byKey(const ValueKey('planning-chat-input'));
    await tester.enterText(input, 'Use a larger screen');
    await tester.sendKeyEvent(LogicalKeyboardKey.enter);
    await tester.pumpAndSettle();
    expect(calls, 1);
    await tester.pumpWidget(screen(
        busy: true,
        allowWhileBusy: false,
        send: (_, __) async => const CommandResult(ok: true)));
    expect(tester.widget<TextField>(input).enabled, false);
    expect(find.textContaining('engineering work to finish'), findsOneWidget);
  });

  testWidgets('phone history stays compact and follows the latest reply',
      (tester) async {
    tester.view.physicalSize = const Size(390, 900);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    final messages = <JsonMap>[
      for (var i = 0; i < 4; i++)
        {
          'id': 'reply-$i',
          'role': 'assistant',
          'text': List.filled(30, 'A detail about this device.').join(' '),
        }
    ];
    Future<CommandResult> send(String text, String id) async =>
        const CommandResult(ok: true);
    await tester.pumpWidget(screen(messages: messages, send: send));
    await tester.pumpAndSettle();
    final history = find.byKey(const ValueKey('planning-message-history'));
    expect(tester.getSize(history).height, lessThanOrEqualTo(280));
    final scroll = tester.widget<SingleChildScrollView>(history).controller!;
    expect(scroll.offset, scroll.position.maxScrollExtent);
    await tester.pumpWidget(screen(messages: [
      ...messages,
      {
        'id': 'latest',
        'role': 'assistant',
        'text': 'The new concept is ready.'
      },
    ], send: send));
    await tester.pumpAndSettle();
    expect(scroll.offset, scroll.position.maxScrollExtent);
    expect(
        find.text('The new concept is ready.').hitTestable(), findsOneWidget);
    expect(tester.takeException(), isNull);
  });
}
