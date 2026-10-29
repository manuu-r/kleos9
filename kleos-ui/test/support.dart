import 'package:flutter/widgets.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:kleos_ui/data/models/json.dart';
import 'package:kleos_ui/data/services/api_service.dart';
import 'package:kleos_ui/state/kleos_store.dart';

/// A real Riverpod container over a fake [ApiService], with polling off so
/// tests decide when data moves. Always reads the latest published view.
class TestKleos {
  TestKleos(ApiService service)
      : container = ProviderContainer(overrides: [
          apiServiceProvider.overrideWithValue(service),
          pollingEnabledProvider.overrideWithValue(false),
        ]) {
    container.listen(kleosProvider, (_, __) {});
  }
  final ProviderContainer container;
  KleosController get view => container.read(kleosProvider);
  JsonMap get state => view.state;
  String? get error => view.error;
  int? get revision => view.snapshot?.revision;
  bool isPending(String type) => view.isPending(type);
  Future<void> refresh() => view.refresh();
  Future<CommandResult> execute(String type, [JsonMap payload = const {}]) =>
      view.execute(type, payload);
  Future<void> setSpeed(double s) => view.setSpeed(s);
  void dispose() => container.dispose();

  Widget scope(Widget child) =>
      UncontrolledProviderScope(container: container, child: child);
}
