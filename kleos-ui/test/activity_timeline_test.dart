import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:kleos_ui/data/models/json.dart';
import 'package:kleos_ui/ui/core/widgets.dart';
import 'package:kleos_ui/ui/features/project/activity_timeline.dart';

final t0 = DateTime.utc(2026, 11, 4, 10);
String at(int seconds) => t0.add(Duration(seconds: seconds)).toIso8601String();

JsonMap stepOf(String kind, String phase, int seconds,
        {String task = 'case', int turn = 0, String? tool, JsonMap extra = const {}}) =>
    {
      'id': '$task:$turn:$kind:${tool ?? '-'}:$phase',
      'kind': kind,
      'phase': phase,
      'at': at(seconds),
      'taskId': task,
      'turn': turn,
      'lane': 'cad',
      if (tool != null) 'tool': tool,
      ...extra,
    };

final steps = <JsonMap>[
  {
    'id': 'objective:0:plan:-:started',
    'kind': 'plan',
    'phase': 'started',
    'at': at(0),
    'turn': 0,
    'summary': 'Planning how to reach the objective'
  },
  {
    'id': 'objective:0:plan:-:finished',
    'kind': 'plan',
    'phase': 'finished',
    'at': at(4),
    'turn': 0,
    'summary': 'Build the case first',
    'tasks': [
      {'id': 'case', 'lane': 'cad', 'objective': 'Model the 38 mm case'}
    ]
  },
  stepOf('model', 'started', 5, extra: {'summary': 'Reading the task and project context'}),
  stepOf('model', 'finished', 9,
      extra: {'summary': 'Write the case script', 'decision': 'tool', 'next': 'cad.patch'}),
  stepOf('tool', 'started', 9, tool: 'cad.patch', extra: {
    'arguments': {'path': 'cad/case.py', 'content': '4120 characters'}
  }),
  stepOf('tool', 'finished', 12, tool: 'cad.patch', extra: {
    'summary': 'Wrote cad/case.py',
    'checks': [
      {'name': 'native.patch', 'status': 'PASS'}
    ]
  }),
  stepOf('tool', 'started', 12, turn: 1, tool: 'cad.build'),
];

Widget screen(List<JsonMap> items, {bool running = true, int visible = 10}) =>
    MaterialApp(
        theme: kTheme(),
        home: Scaffold(
            body: SingleChildScrollView(
                child: ActivityTimeline(
                    steps: items, running: running, visible: visible))));

void main() {
  testWidgets('a live run shows merged steps, tool inputs and results',
      (tester) async {
    await tester.pumpWidget(screen(steps));
    await tester.pump();
    expect(find.text('What Kleos is doing'), findsOneWidget);
    expect(find.text('Live'), findsOneWidget);
    // Seven raw steps collapse into four rows: plan, model decision, patch, build.
    expect(find.text('4 steps'), findsOneWidget);
    expect(find.text('Planned 1 task'), findsOneWidget);
    expect(find.text('cad · Model the 38 mm case'), findsOneWidget);
    expect(find.text('Write the case script'), findsOneWidget);
    expect(find.text('Next: cad.patch'), findsOneWidget);
    expect(find.text('cad.patch'), findsOneWidget);
    expect(find.text('path: cad/case.py'), findsOneWidget);
    expect(find.text('content: 4120 characters'), findsOneWidget);
    expect(find.text('Wrote cad/case.py'), findsOneWidget);
    expect(find.text('PASS'), findsOneWidget);
    expect(find.text('3s'), findsOneWidget); // the patch took three seconds
    // The build is still running: it spins instead of showing a check.
    expect(find.text('cad.build'), findsOneWidget);
    expect(find.byType(KSpinner), findsOneWidget);
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox.shrink());
  });

  testWidgets('a finished run is history and an unfinished step is not shown as live',
      (tester) async {
    await tester.pumpWidget(screen(steps, running: false));
    await tester.pump();
    expect(find.text('What Kleos did'), findsOneWidget);
    expect(find.text('Live'), findsNothing);
    expect(find.byType(KSpinner), findsNothing);
    expect(find.byIcon(Icons.remove), findsOneWidget); // the interrupted build
  });

  testWidgets('a failed tool shows its reason and older steps collapse',
      (tester) async {
    final failed = [
      ...steps.take(6),
      stepOf('tool', 'started', 12, turn: 1, tool: 'cad.build'),
      stepOf('tool', 'failed', 40, turn: 1, tool: 'cad.build',
          extra: {'summary': 'Build exceeded its time limit', 'error': 'deadline_exceeded'}),
    ];
    await tester.pumpWidget(screen(failed, running: false, visible: 2));
    await tester.pump();
    expect(find.text('Build exceeded its time limit'), findsOneWidget);
    expect(find.byIcon(Icons.close), findsOneWidget);
    expect(find.text('Planned 1 task'), findsNothing);
    await tester.tap(find.text('Show 2 earlier steps'));
    await tester.pump();
    expect(find.text('Planned 1 task'), findsOneWidget);
  });
}
