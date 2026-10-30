import 'dart:async';
import 'dart:convert';
import 'dart:math';
import 'package:crypto/crypto.dart';
import '../models/json.dart';
import '../models/api.generated.dart';
import 'platform/journal_storage.dart' as storage;

class PendingCommand {
  PendingCommand(
      {required this.key, required this.identity, required this.body});
  factory PendingCommand.fromJson(JsonMap json) => PendingCommand(
      key: json['key'] as String,
      identity: json['identity'] as String,
      body: asMap(json['body']));
  final String key;
  final String identity;
  final JsonMap body;
  JsonMap toJson() => {'key': key, 'identity': identity, 'body': body};
}

abstract interface class CommandJournal {
  Future<PendingCommand> prepare(String type, JsonMap payload, int? revision);
  Future<void> remove(String key);
  Future<List<PendingCommand>> pending();
}

Object? _canonical(Object? value) {
  if (value is Map<String, dynamic>) {
    return {
      for (final key in value.keys.toList()..sort()) key: _canonical(value[key])
    };
  }
  if (value is List) return value.map(_canonical).toList();
  return value;
}

class DurableCommandJournal implements CommandJournal {
  DurableCommandJournal(String namespace,
      {Future<String?> Function(String)? read,
      Future<void> Function(String, String)? write})
      : _namespace = sha256.convert(utf8.encode(namespace)).toString(),
        _read = read ?? storage.readJournal,
        _write = write ?? storage.writeJournal;
  final String _namespace;
  final Future<String?> Function(String) _read;
  final Future<void> Function(String, String) _write;
  Future<void> _tail = Future<void>.value();

  Future<T> _serial<T>(Future<T> Function(List<PendingCommand>) operation) {
    final result = _tail.then((_) async {
      final raw = await _read(_namespace);
      final commands = raw == null
          ? <PendingCommand>[]
          : asMaps(jsonDecode(raw)).map(PendingCommand.fromJson).toList();
      return operation(commands);
    });
    _tail = result.then<void>((_) {}, onError: (Object _, StackTrace __) {});
    return result;
  }

  @override
  Future<PendingCommand> prepare(String type, JsonMap payload, int? revision) =>
      _serial((commands) async {
        final identity =
            jsonEncode(_canonical({'type': type, 'payload': payload}));
        final existing =
            commands.where((c) => c.identity == identity).firstOrNull;
        if (existing != null) return existing;
        if (commands.length >= 100) {
          throw StateError(
              'Resolve pending requests before starting more work.');
        }
        final random = Random.secure();
        final key =
            base64UrlEncode(List.generate(24, (_) => random.nextInt(256)));
        final command = PendingCommand(
            key: key,
            identity: identity,
            body: ApiCommand(
                    type: type, payload: payload, expectedRevision: revision)
                .toJson());
        commands.add(command);
        await _write(
            _namespace, jsonEncode(commands.map((c) => c.toJson()).toList()));
        return command;
      });

  @override
  Future<void> remove(String key) => _serial((commands) async {
        commands.removeWhere((c) => c.key == key);
        await _write(
            _namespace, jsonEncode(commands.map((c) => c.toJson()).toList()));
      });

  @override
  Future<List<PendingCommand>> pending() =>
      _serial((commands) async => List.unmodifiable(commands));
}
