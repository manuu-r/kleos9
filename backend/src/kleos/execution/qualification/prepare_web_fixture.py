"""Trusted offline fixture setup, run only inside the fresh bounded application image."""
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, '/opt/kleos-native')
from kleos.execution.native_runner import run_fixed  # noqa: E402

SOURCES = {
    'pubspec.yaml': '''name: fresh_companion
description: Fresh hardware companion build fixture
publish_to: none
version: 1.0.0+1
environment:
  sdk: ">=3.9.0 <4.0.0"
dependencies:
  flutter:
    sdk: flutter
flutter:
  uses-material-design: true
''',
    'lib/main.dart': '''import 'package:flutter/material.dart';
void main() => runApp(const Companion());
class Companion extends StatefulWidget {
  const Companion({super.key});
  @override
  State<Companion> createState() => _CompanionState();
}
class _CompanionState extends State<Companion> {
  int samples = 0;
  @override
  Widget build(BuildContext context) => MaterialApp(
    home: Scaffold(
      appBar: AppBar(title: const Text('Hardware companion')),
      body: Center(child: Text('Local samples: $samples')),
      floatingActionButton: FloatingActionButton(
        onPressed: () => setState(() => samples++),
        child: const Icon(Icons.add),
      ),
    ),
  );
}
''',
    'web/index.html': '''<!DOCTYPE html>
<html><head><base href="$FLUTTER_BASE_HREF"><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Hardware companion</title></head>
<body><script src="flutter_bootstrap.js" async></script></body></html>
''',
}


def main():
    work = Path(tempfile.mkdtemp(prefix='fresh-web-fixture-', dir='/tmp'))
    for path, value in SOURCES.items():
        target = work / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(value)
    for name in ('.home', '.tmp'):
        (work / name).mkdir()
    for name in ('flutter', 'pub-cache'):
        shutil.copytree('/opt/' + name, work / '.runtime' / name, symlinks=True)
    run_fixed(['flutter', 'pub', 'get', '--offline'], work, 180,
              {'pids': 256, 'scratchBytes': 4 * 1024**3}, True)
    sources = {**SOURCES, 'pubspec.lock': (work / 'pubspec.lock').read_text()}
    print(json.dumps(sources))


if __name__ == '__main__':
    main()
