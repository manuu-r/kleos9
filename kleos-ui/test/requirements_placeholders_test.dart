import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:kleos_ui/data/models/json.dart';
import 'package:kleos_ui/state/kleos_store.dart';
import 'package:kleos_ui/ui/core/widgets.dart';
import 'package:kleos_ui/ui/features/project/requirements_interview.dart';

// Every question answered: what remains decides whether the user can move on.
const resolved = <JsonMap>[
  {
    'id': 'battery',
    'prompt': 'Which battery?',
    'type': 'single_select',
    'required': true,
    'options': <JsonMap>[
      {'value': 'lipo', 'label': 'Small LiPo'}
    ],
    'questionHash': 'h1',
  }
];
const placeholders = <JsonMap>[
  {
    'id': 'clr_usbc_opening',
    'kind': 'keepout',
    'description': 'USB-C opening and footprint are not measured',
    'certainty': 'unknown',
    'critical': true,
  },
  {
    'id': 'other_lipo_size',
    'kind': 'dimension',
    'description': 'LiPo size is a minimal placeholder',
    'certainty': 'unknown',
    'critical': true,
  },
  {
    'id': 'case_diameter',
    'kind': 'dimension',
    'description': '38 mm case',
    'certainty': 'known',
    'critical': true,
  },
];

Widget screen(
        {required bool ready,
        List<JsonMap> constraints = placeholders,
        Future<CommandResult> Function(List<String>)? accept,
        Future<CommandResult> Function()? review,
        Future<CommandResult> Function()? confirm}) =>
    MaterialApp(
        theme: kTheme(),
        home: Scaffold(
            body: SingleChildScrollView(
                padding: const EdgeInsets.all(16),
                child: RequirementsInterview(
                  clarification: {
                    'questions': const <JsonMap>[],
                    'resolvedQuestions': resolved,
                    'answers': const {
                      'battery': ['lipo']
                    },
                    'needsReview': false,
                    'readyToConfirm': ready,
                  },
                  proposal: {'constraints': constraints},
                  legacyAnswers: const {},
                  canWrite: true,
                  active: false,
                  confirmed: false,
                  autoResume: true,
                  onAnswer: (_, __, ___) async =>
                      const CommandResult(ok: true),
                  onReview:
                      review ?? () async => const CommandResult(ok: true),
                  onConfirm:
                      confirm ?? () async => const CommandResult(ok: true),
                  onAcceptAssumptions: accept,
                ))));

void main() {
  testWidgets('critical placeholders are listed with a way to accept them',
      (tester) async {
    List<String>? sent;
    await tester.pumpWidget(screen(
        ready: false,
        accept: (ids) async {
          sent = ids;
          return const CommandResult(ok: true);
        }));
    await tester.pumpAndSettle();
    expect(find.textContaining('still placeholders'), findsOneWidget);
    expect(find.text('USB-C opening and footprint are not measured'),
        findsOneWidget);
    expect(find.text('LiPo size is a minimal placeholder'), findsOneWidget);
    expect(find.text('38 mm case'), findsNothing);
    expect(find.text('Complete the remaining questions before continuing.'),
        findsNothing);
    expect(find.widgetWithText(KButton, 'Confirm and start design'),
        findsNothing);
    await tester.tap(
        find.widgetWithText(KButton, 'Accept placeholders and continue'));
    await tester.pumpAndSettle();
    expect(sent, ['clr_usbc_opening', 'other_lipo_size']);
  });

  testWidgets('a concept that is not ready without placeholders offers review',
      (tester) async {
    var reviews = 0;
    await tester.pumpWidget(screen(
        ready: false,
        constraints: const [],
        review: () async {
          reviews++;
          return const CommandResult(ok: true);
        }));
    await tester.pumpAndSettle();
    expect(find.textContaining('has not marked this concept ready'),
        findsOneWidget);
    await tester.tap(find.widgetWithText(KButton, 'Review my requirements'));
    await tester.pumpAndSettle();
    expect(reviews, 1);
  });

  testWidgets('a ready concept only offers confirmation', (tester) async {
    await tester.pumpWidget(screen(
        ready: true,
        constraints: const [],
        accept: (_) async => const CommandResult(ok: true)));
    await tester.pumpAndSettle();
    expect(find.widgetWithText(KButton, 'Confirm and start design'),
        findsOneWidget);
    expect(find.widgetWithText(KButton, 'Accept placeholders and continue'),
        findsNothing);
    expect(find.widgetWithText(KButton, 'Review my requirements'),
        findsNothing);
  });
}
