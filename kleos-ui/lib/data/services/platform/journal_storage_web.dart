import 'package:web/web.dart' as web;

Future<String?> readJournal(String key) async =>
    web.window.localStorage.getItem('kleos-commands-$key');

Future<void> writeJournal(String key, String content) async {
  // setItem is synchronous. Failure (quota/private storage) stops dispatch.
  web.window.localStorage.setItem('kleos-commands-$key', content);
}
