import 'json.dart';

class AppSnapshot {
  AppSnapshot({required this.revision, required JsonMap state, this.speed = 1})
      : state = immutableJson(state) as JsonMap;
  AppSnapshot.shared(
      {required this.revision, required this.state, this.speed = 1});
  final int revision;
  final JsonMap state;
  final double speed;
  factory AppSnapshot.fromJson(JsonMap json) {
    if (json['revision'] is! num || json['state'] is! Map) {
      throw const FormatException(
          'The API returned an invalid state snapshot.');
    }
    return AppSnapshot(
        revision: (json['revision'] as num).toInt(),
        state: asMap(json['state']),
        speed: (asMap(json['demo'])['speed'] as num?)?.toDouble() ?? 1);
  }
}

class CommandResult {
  const CommandResult({required this.ok, this.result = const {}});
  final bool ok;
  final JsonMap result;
}
