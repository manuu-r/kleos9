import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:kleos_ui/data/models/json.dart';
import 'package:kleos_ui/state/kleos_store.dart';
import 'package:kleos_ui/ui/core/widgets.dart';
import 'package:kleos_ui/ui/features/project/requirements_interview.dart';

const questions = <JsonMap>[
  {
    'id': 'air',
    'prompt': 'What should it measure?',
    'helpText': 'Choose what matters to you.',
    'type': 'multi_select',
    'required': true,
    'allowCustom': true,
    'options': <JsonMap>[
      {'value': 'co2', 'label': 'CO₂ levels'},
      {'value': 'dust', 'label': 'Fine dust'}
    ]
  },
  {
    'id': 'power',
    'prompt': 'How will you power it?',
    'type': 'single_select',
    'required': true,
    'options': <JsonMap>[
      {'value': 'usb', 'label': 'USB cable'},
      {'value': 'battery', 'label': 'Rechargeable battery'}
    ]
  },
];
JsonMap data(
        {List<JsonMap> rows = questions,
        JsonMap answers = const {},
        bool needsReview = false,
        bool ready = false}) =>
    {
      'questions': rows,
      'answers': answers,
      'needsReview': needsReview,
      'readyToConfirm': ready,
    };
Widget screen(
  JsonMap state, {
  Future<CommandResult> Function(String, List<String>, bool)? answer,
  Future<CommandResult> Function()? review,
  Future<CommandResult> Function()? confirm,
  bool active = false,
  bool autoResume = false,
  bool acceptWhileActive = false,
  bool updatePending = false,
}) =>
    MaterialApp(
        theme: kTheme(),
        home: Scaffold(
            body: SingleChildScrollView(
                padding: const EdgeInsets.all(16),
                child: RequirementsInterview(
                  key: const ValueKey('interview'),
                  clarification: state,
                  proposal: const {},
                  legacyAnswers: const {},
                  canWrite: true,
                  active: active,
                  autoResume: autoResume,
                  acceptWhileActive: acceptWhileActive,
                  updatePending: updatePending,
                  confirmed: false,
                  onAnswer: answer ??
                      (_, __, ___) async => const CommandResult(ok: true),
                  onReview: review ?? () async => const CommandResult(ok: true),
                  onConfirm:
                      confirm ?? () async => const CommandResult(ok: true),
                ))));
Future<void> tapButton(WidgetTester tester, String label) async {
  final button = find.widgetWithText(KButton, label);
  await tester.ensureVisible(button);
  await tester.tap(button);
  await tester.pumpAndSettle();
}

void main() {
  testWidgets(
      'automatic answers continue planning without a manual review button',
      (tester) async {
    var saves = 0;
    var reviews = 0;
    await tester.pumpWidget(screen(data(rows: [questions.first]),
        autoResume: true,
        active: true,
        acceptWhileActive: true, answer: (_, __, ___) async {
      saves++;
      return const CommandResult(ok: true);
    }, review: () async {
      reviews++;
      return const CommandResult(ok: true);
    }));
    await tester.tap(find.byKey(const ValueKey('requirement-option-co2')));
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.widgetWithText(KButton, 'Send answer'));
    await tester.tap(find.widgetWithText(KButton, 'Send answer'));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 250));
    expect(saves, 1);
    expect(reviews, 0);
    expect(find.text('Review my requirements'), findsNothing);
    expect(find.textContaining('plan will update automatically'), findsNothing);
    expect(find.text('Updating your requirements…'), findsOneWidget);
    expect(find.text('Confirm and start design'), findsNothing);
  });

  testWidgets(
      'pending automatic planning never offers engineering confirmation',
      (tester) async {
    await tester.pumpWidget(screen(data(rows: const [], ready: true),
        autoResume: true, updatePending: true));
    expect(find.text('Confirm and start design'), findsNothing);
    expect(find.text('Review my requirements'), findsNothing);
    expect(find.text('Updating your requirements…'), findsOneWidget);
  });

  for (final width in [390.0, 1200.0]) {
    testWidgets('one question at a time saves selected values at $width',
        (tester) async {
      tester.view.physicalSize = Size(width, 900);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.reset);
      final calls = <JsonMap>[];
      await tester
          .pumpWidget(screen(data(), answer: (id, values, legacy) async {
        calls.add({'id': id, 'values': values, 'legacy': legacy});
        return const CommandResult(ok: true);
      }));
      expect(find.text('What should it measure?'), findsOneWidget);
      expect(find.text('How will you power it?'), findsNothing);
      await tester.tap(find.byKey(const ValueKey('requirement-option-co2')));
      await tester.tap(find.byKey(const ValueKey('requirement-option-dust')));
      await tester.pumpAndSettle();
      await tapButton(tester, 'Continue');
      expect(calls.single, {
        'id': 'air',
        'values': ['co2', 'dust'],
        'legacy': false
      });
      expect(find.text('How will you power it?'), findsOneWidget);
      expect(find.text('1 of 2 saved'), findsOneWidget);
      await tester.tap(find.byKey(const ValueKey('requirement-option-usb')));
      await tester.pumpAndSettle();
      await tapButton(tester, 'Review answers');
      expect(find.text('Here’s what we’ll build around'), findsOneWidget);
      expect(find.text('CO₂ levels, Fine dust'), findsOneWidget);
      expect(find.text('USB cable'), findsOneWidget);
      expect(find.text('Review my requirements'), findsOneWidget);
      expect(find.text('Confirm and start design'), findsNothing);
      expect(tester.takeException(), isNull);
    });
  }
  testWidgets('server answers resume and summary supports editing',
      (tester) async {
    await tester.pumpWidget(screen(data(answers: const {
      'air': ['dust']
    })));
    expect(find.text('How will you power it?'), findsOneWidget);
    await tester.tap(find.byKey(const ValueKey('requirement-option-battery')));
    await tester.pumpAndSettle();
    await tapButton(tester, 'Review answers');
    await tester.ensureVisible(find.byTooltip('Edit answer 1'));
    await tester.tap(find.byTooltip('Edit answer 1'));
    await tester.pumpAndSettle();
    expect(find.text('What should it measure?'), findsOneWidget);
    expect(find.text('How will you power it?'), findsNothing);
    await tapButton(tester, 'Continue');
    expect(find.text('How will you power it?'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });
  testWidgets('failed saves keep answer available for retry', (tester) async {
    await tester.pumpWidget(screen(data(),
        answer: (_, __, ___) async => const CommandResult(ok: false)));
    await tester.tap(find.byKey(const ValueKey('requirement-option-co2')));
    await tester.pumpAndSettle();
    await tapButton(tester, 'Continue');
    expect(find.text('Your answer could not be saved. Please try again.'),
        findsOneWidget);
    expect(find.text('What should it measure?'), findsOneWidget);
    expect(find.text('0 of 2 saved'), findsOneWidget);
    expect(find.text('How will you power it?'), findsNothing);
  });
  testWidgets('review gates confirmation and displays running state',
      (tester) async {
    var reviewed = 0;
    var confirmed = 0;
    final answered = data(answers: const {
      'air': ['co2'],
      'power': ['usb']
    }, needsReview: true);
    await tester.pumpWidget(screen(answered, review: () async {
      reviewed++;
      return const CommandResult(ok: true);
    }));
    expect(find.text('Confirm and start design'), findsNothing);
    await tapButton(tester, 'Review my requirements');
    expect(reviewed, 1);
    await tester.pumpWidget(screen(answered, active: true));
    expect(find.text('Updating your requirements…'), findsOneWidget);
    expect(find.text('Confirm and start design'), findsNothing);
    await tester.pumpWidget(screen(
        data(answers: const {
          'air': ['co2'],
          'power': ['usb']
        }, ready: true), confirm: () async {
      confirmed++;
      return const CommandResult(ok: true);
    }));
    await tester.pumpAndSettle();
    await tapButton(tester, 'Confirm and start design');
    expect(confirmed, 1);
  });
  testWidgets('changed options discard acknowledged old choices',
      (tester) async {
    final row = questions.first;
    await tester.pumpWidget(screen(data(rows: [row])));
    await tester.tap(find.byKey(const ValueKey('requirement-option-co2')));
    await tester.pumpAndSettle();
    await tapButton(tester, 'Review answers');
    expect(find.text('CO₂ levels'), findsOneWidget);
    await tester.pumpWidget(screen(data(rows: [
      {
        ...row,
        'prompt': 'Which pollutant matters most?',
        'options': <JsonMap>[
          {'value': 'voc', 'label': 'Volatile compounds'}
        ]
      }
    ])));
    await tester.pumpAndSettle();
    expect(find.text('Which pollutant matters most?'), findsOneWidget);
    expect(find.text('0 of 1 saved'), findsOneWidget);
    expect(find.text('CO₂ levels'), findsNothing);
  });
  testWidgets('optional skip is saved and remains complete after reopening',
      (tester) async {
    final optional = <JsonMap>[
      {
        'id': 'notes',
        'prompt': 'Anything else?',
        'type': 'text',
        'required': false
      }
    ];
    List<String>? stored;
    await tester.pumpWidget(
        screen(data(rows: optional), answer: (id, values, legacy) async {
      stored = values;
      return const CommandResult(ok: true);
    }));
    await tapButton(tester, 'Review answers');
    expect(stored, isEmpty);
    await tester.pumpWidget(const SizedBox());
    await tester.pumpWidget(
        screen(data(rows: optional, answers: const {'notes': <String>[]})));
    expect(find.text('Here’s what we’ll build around'), findsOneWidget);
    expect(find.text('No preference'), findsOneWidget);
    expect(find.text('1 of 1 saved'), findsOneWidget);
  });

  testWidgets(
      'numeric question explains units and rejects an out-of-range value',
      (tester) async {
    final row = <JsonMap>[
      {
        'id': 'budget',
        'prompt': 'What is your parts budget?',
        'type': 'number',
        'required': true,
        'min': 10,
        'max': 200,
        'unit': 'USD'
      }
    ];
    var saved = 0;
    await tester.pumpWidget(screen(data(rows: row), answer: (_, __, ___) async {
      saved++;
      return const CommandResult(ok: true);
    }));
    expect(find.text('USD'), findsOneWidget);
    await tester.enterText(find.byType(TextField), '500');
    await tester.pumpAndSettle();
    await tapButton(tester, 'Review answers');
    expect(find.text('Enter 200 or less.'), findsOneWidget);
    expect(saved, 0);
    await tester.enterText(find.byType(TextField), '100');
    await tester.pumpAndSettle();
    await tapButton(tester, 'Review answers');
    expect(saved, 1);
  });
  testWidgets(
      'reviewed questions remain an editable summary after leaving active questions',
      (tester) async {
    final state = {
      ...data(rows: const [], answers: const {
        'air': ['co2'],
        'power': ['usb']
      }, ready: true),
      'resolvedQuestions': questions
    };
    String? edited;
    List<String>? changed;
    await tester.pumpWidget(screen(state, answer: (id, values, legacy) async {
      edited = id;
      changed = values;
      return const CommandResult(ok: true);
    }));
    expect(find.text('Here’s what we’ll build around'), findsOneWidget);
    expect(find.text('CO₂ levels'), findsOneWidget);
    expect(find.text('USB cable'), findsOneWidget);
    expect(find.text('2 of 2 saved'), findsOneWidget);
    expect(find.text('Confirm and start design'), findsOneWidget);
    await tester.ensureVisible(find.byTooltip('Edit answer 1'));
    await tester.tap(find.byTooltip('Edit answer 1'));
    await tester.pumpAndSettle();
    expect(find.text('What should it measure?'), findsOneWidget);
    await tester.tap(find.byKey(const ValueKey('requirement-option-dust')));
    await tester.pumpAndSettle();
    await tapButton(tester, 'Review answers');
    expect(edited, 'air');
    expect(changed, ['co2', 'dust']);
    expect(find.text('Here’s what we’ll build around'), findsOneWidget);
    expect(find.text('CO₂ levels, Fine dust'), findsOneWidget);
    expect(find.text('Review my requirements'), findsOneWidget);
    expect(find.text('Confirm and start design'), findsNothing);
  });

  testWidgets('active follow-up wins over resolved history and is asked first',
      (tester) async {
    final followUp = {...questions.first, 'prompt': 'What matters most now?'};
    await tester.pumpWidget(screen({
      ...data(rows: [
        followUp
      ], answers: const {
        'power': ['usb']
      }),
      'resolvedQuestions': questions
    }));
    expect(find.text('What matters most now?'), findsOneWidget);
    expect(find.text('What should it measure?'), findsNothing);
    expect(find.text('How will you power it?'), findsNothing);
    expect(find.text('1 of 2 saved'), findsOneWidget);
    await tester.tap(find.byKey(const ValueKey('requirement-option-dust')));
    await tester.pumpAndSettle();
    await tapButton(tester, 'Review answers');
    expect(find.text('Here’s what we’ll build around'), findsOneWidget);
    expect(find.text('USB cable'), findsOneWidget);
    expect(find.byTooltip('Edit answer 3'), findsNothing);
  });
}
