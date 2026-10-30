// Test-only entry point: the normal application never enables the driver.
import 'dart:convert';
import 'package:flutter/foundation.dart';
import 'package:flutter_driver/driver_extension.dart';
import 'package:kleos_ui/main.dart' as app;

void main() {
  final errors = <String>[];
  enableFlutterDriverExtension(handler: (request) async {
    if (request == 'runtimeErrors') return jsonEncode(errors);
    return 'unsupported';
  });
  final previous = FlutterError.onError;
  FlutterError.onError = (details) {
    errors.add(details.exceptionAsString());
    previous?.call(details);
  };
  app.main();
}
