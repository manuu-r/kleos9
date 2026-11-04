import 'package:flutter_test/flutter_test.dart';
import 'package:kleos_ui/data/models/api.generated.dart';
import 'package:kleos_ui/data/models/production.dart';

void main() {
  test('legacy command receipts omit external identities', () {
    final receipt = AcceptedCommand.fromJson(ApiCommandAccepted.fromJson({
      'commandId': 'command',
      'projectId': 'project',
      'revision': 2,
    }).toJson());
    expect(receipt.jobId, isNull);
    expect(receipt.agentInstanceId, isNull);
    expect(receipt.runId, isNull);
    expect(receipt.revision, 2);
  });

  test('external command identities survive transport and state adaptation', () {
    final receipt = AcceptedCommand.fromJson(ApiCommandAccepted.fromJson({
      'commandId': 'command',
      'projectId': 'project',
      'jobId': 'job',
      'agentInstanceId': 'agent',
      'revision': 3,
    }).toJson());
    expect(receipt.jobId, 'job');
    expect(receipt.agentInstanceId, 'agent');
    expect(receipt.projectId, 'project');
    expect(receipt.revision, 3);
  });
}
