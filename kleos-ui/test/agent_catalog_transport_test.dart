import 'package:flutter_test/flutter_test.dart';
import 'package:kleos_ui/data/models/api.generated.dart';

void main() {
  test('legacy catalog omitting agents stays readable', () {
    final catalog = ApiEngineeringToolCatalogView.fromJson({
      'tools': <Object>[],
      'defaultPolicy': {
        'mechanics': {'id': 'build123d', 'version': '1'}
      },
    });
    expect(catalog.defaultPolicy['mechanics']!.id, 'build123d');
    expect(catalog.agents, isNull);
    expect(catalog.defaultAgentPolicy, isNull);
  });
  test('agent metadata and default selections round trip', () {
    final catalog = ApiEngineeringToolCatalogView.fromJson({
      'tools': <Object>[],
      'defaultPolicy': <String, Object>{},
      'defaultAgentPolicy': {
        'sourcing': {'id': 'sandbox', 'version': '1'}
      },
      'agents': [
        {
          'id': 'sandbox',
          'version': '1',
          'name': 'Sandbox procurement',
          'qualified': true,
          'qualificationReference': 'offline-fixture',
          'limitations': ['No physical orders'],
          'schemaVersion': '1',
          'agentType': 'sourcing',
          'capabilities': ['quote'],
          'supportedInputs': ['SourcingRequest'],
          'resultSchema': 'SourcingResult',
          'operationModes': {'quote': 'reproducible'},
          'mode': 'sandbox',
          'qualificationFixtures': ['duplicate-event'],
          'supportsIdempotency': true,
          'supportsCancellation': false,
          'configured': true,
          'isDefault': true,
        }
      ],
    });
    final restored = ApiEngineeringToolCatalogView.fromJson(catalog.toJson());
    expect(restored.agents!.single.agentType, 'sourcing');
    expect(restored.agents!.single.operationModes['quote'], 'reproducible');
    expect(restored.agents!.single.limitations, ['No physical orders']);
    expect(restored.agents!.single.supportsCancellation, isFalse);
    expect(restored.defaultAgentPolicy!['sourcing']!.version, '1');
  });
}
