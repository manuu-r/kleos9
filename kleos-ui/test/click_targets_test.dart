import 'package:flutter/material.dart';
import 'package:flutter/semantics.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:kleos_ui/ui/core/widgets.dart';

void main() {
  testWidgets('a standalone tappable control cannot claim the page background',
      (tester) async {
    final semantics = tester.ensureSemantics();
    var calls = 0;
    await tester.pumpWidget(MaterialApp(
        theme: kTheme(),
        home: Scaffold(
            body: Center(
                child: SizedBox(
                    width: 160,
                    height: 48,
                    child: KTap(
                        semanticLabel: 'Open item',
                        onTap: () => calls++,
                        builder: (_, __) => const Text('Open item')))))));
    await tester.pumpAndSettle();
    final target = tester.getSemantics(find.byType(KTap));
    expect(target.rect.size, const Size(160, 48));
    expect(target.getSemanticsData().hasAction(SemanticsAction.tap), isTrue);
    await tester.tapAt(const Offset(5, 100));
    expect(calls, 0);
    tester.binding.rootPipelineOwner.visitChildren((owner) {
      owner.semanticsOwner?.performAction(target.id, SemanticsAction.tap);
    });
    await tester.pump();
    expect(calls, 1);
    await tester.pumpWidget(const SizedBox.shrink());
    semantics.dispose();
  });

  testWidgets('tabs and chips expose bounded enabled accessibility actions',
      (tester) async {
    final semantics = tester.ensureSemantics();
    final selected = <String>[];
    await tester.pumpWidget(MaterialApp(
        theme: kTheme(),
        home: Scaffold(
            body: Column(children: [
          KTabStrip(selected: 0, tabs: [
            KTab(label: 'First', onTap: () => selected.add('First')),
            KTab(label: 'Second', onTap: () => selected.add('Second')),
            const KTab(label: 'Locked'),
          ]),
          KChip(
              label: 'Choice',
              selected: false,
              onTap: () => selected.add('Choice')),
          const KChip(label: 'Disabled choice', selected: false, onTap: null),
        ]))));
    await tester.pumpAndSettle();
    for (final label in ['First', 'Second', 'Choice']) {
      final node = tester.getSemantics(find.bySemanticsLabel(label));
      expect(node.getSemanticsData().hasAction(SemanticsAction.tap), isTrue,
          reason: '$label must respond to web and assistive clicks');
      expect(node.rect.height, lessThan(100));
      tester.binding.rootPipelineOwner.visitChildren((owner) {
        owner.semanticsOwner?.performAction(node.id, SemanticsAction.tap);
      });
      await tester.pumpAndSettle();
    }
    expect(selected, ['First', 'Second', 'Choice']);
    for (final label in ['Locked', 'Disabled choice']) {
      expect(
          tester
              .getSemantics(find.bySemanticsLabel(label))
              .getSemanticsData()
              .hasAction(SemanticsAction.tap),
          isFalse);
    }
    await tester.pumpWidget(const SizedBox.shrink());
    semantics.dispose();
  });
}
