import 'package:flutter_test/flutter_test.dart';
import 'package:kleos_ui/data/models/json.dart';
import 'package:kleos_ui/data/services/production_legacy_api_service.dart';
import 'production_legacy_adapter_test.dart' as fixtures;

class DeleteRepository extends fixtures.Repository {
  final deletions = <JsonMap>[];
  @override
  Future<JsonMap> deleteProject(String id, {required int revision}) async {
    deletions.add({'id': id, 'revision': revision});
    return {'projectId': id, 'deleted': true, 'status': 'deletion_queued'};
  }
}

void main() {
  test(
      'accepted deletion disappears immediately and stale summaries cannot resurrect it',
      () async {
    final repository = DeleteRepository();
    final service = ProductionLegacyApiService(repository);
    addTearDown(() {
      service.close();
      repository.close();
    });
    await service.load();
    await service.setActiveProject('p');
    final result = await service.command('project.delete', {'projectId': 'p'});
    expect(repository.deletions.single, {'id': 'p', 'revision': 4});
    expect(
        asMaps(asMap(asMap(result['snapshot'])['state'])['projects'])
            .map((p) => p['id']),
        ['q']);
    final refreshed = await service.load();
    expect(asMaps(asMap(refreshed['state'])['projects']).map((p) => p['id']),
        ['q']);
  });
}
