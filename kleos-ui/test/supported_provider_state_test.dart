import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:kleos_ui/data/models/production.dart';
import 'package:kleos_ui/ui/features/production/production_app.dart';

void main() {
  test('historical unsupported providers keep generic labels', () {
    expect(providerLabel('retired-provider'), 'Unsupported provider');
    expect(providerLabel('meta'), 'Muse');
    expect(providerLabel('modal'), 'Muse on Modal');
  });

  testWidgets('stale catalog entries cannot become selectable presets',
      (tester) async {
    const retired = ModelChoice('retired-provider', 'retired-model');
    final catalog = ModelCatalog([
      CatalogModel(retired, true, engineeringLanes.keys.toList()),
    ], {
      for (final lane in engineeringLanes.keys) lane: retired
    });
    var changes = 0;
    await tester.pumpWidget(MaterialApp(
        home: Scaffold(
            body: SingleChildScrollView(
                child: ModelPolicyEditor(
                    catalog: catalog,
                    policy: catalog.defaults,
                    onChanged: (_) => changes++)))));
    expect(find.text('Use retired-model for all lanes'), findsNothing);
    expect(find.byType(DropdownMenuItem<String>), findsNothing);
    expect(changes, 0);
  });
}
