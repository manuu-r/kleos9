import 'package:flutter_test/flutter_test.dart';
import 'package:kleos_ui/data/models/production.dart';

void main() {
  test('a retained job becomes stale when its deadline passes offline', () {
    final job = ExternalJobRecord({
      'id': 'job', 'agentType': 'humanTasks', 'lifecycle': 'active',
      'state': 'IN_PROGRESS', 'historical': false, 'expired': false,
      'deadline': '2026-11-01T12:00:00Z',
    });
    expect(job.expiredAt(DateTime.parse('2026-11-01T11:59:59Z')), false);
    expect(job.expiredAt(DateTime.parse('2026-11-01T12:00:00Z')), true);
    expect(job.state, 'IN_PROGRESS');
  });

  test('optional agent collections preserve older workspace responses', () {
    final workspace = Workspace.fromJson({
      'project': {'id': 'p', 'revision': 1}, 'eventCursor': 2,
      'runs': <Object?>[], 'artifacts': <Object?>[], 'checks': <Object?>[],
    });
    expect(workspace.agents, isEmpty);
    expect(workspace.externalJobs, isEmpty);
    final source = <String, Object?>{
      'id': 'job', 'agentType': 'sourcing', 'lifecycle': 'active',
      'state': 'quoted', 'historical': true,
    };
    final job = ExternalJobRecord(source);
    source['historical'] = false;
    expect(job.historical, true);
    expect(job.stale, true);
  });
}
