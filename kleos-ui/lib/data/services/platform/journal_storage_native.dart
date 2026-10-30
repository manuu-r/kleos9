import 'dart:io';
import 'package:path_provider/path_provider.dart';

Future<File> _file(String key) async {
  final dir = await getApplicationSupportDirectory();
  return File('${dir.path}/kleos-commands-$key.json');
}

Future<String?> readJournal(String key) async {
  final file = await _file(key);
  return await file.exists() ? file.readAsString() : null;
}

Future<void> writeJournal(String key, String content) async {
  final file = await _file(key);
  await file.parent.create(recursive: true);
  final temporary = File('${file.path}.tmp');
  await temporary.writeAsString(content, flush: true);
  await temporary.rename(file.path);
}
