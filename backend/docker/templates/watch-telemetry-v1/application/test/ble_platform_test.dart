import 'package:flutter/foundation.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:kleos_watch_companion/ble_controller.dart';
import 'package:kleos_watch_companion/ble_platform.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  test('UUID pair is persisted atomically and restored by a fresh settings adapter', () async {
    SharedPreferences.setMockInitialValues({});
    final config = BleConfig.parse(
      'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
      'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
    );
    await PreferencesBleSettings().save(config);
    final restored = await PreferencesBleSettings().load();
    expect(restored!.serviceUuid, config.serviceUuid);
    expect(restored.characteristicUuid, config.characteristicUuid);
    final prefs = await SharedPreferences.getInstance();
    expect(prefs.getKeys(), {'ble.configuration'});
  });
  test(
    'non-mobile platform guard does not initialize native BLE channels',
    () async {
      debugDefaultTargetPlatformOverride = TargetPlatform.linux;
      try {
        final transport = ReactiveBleTransport();
        expect(transport.supported, isFalse);
        await expectLater(transport.prepare(), throwsUnsupportedError);
        expect(
          () => transport.scan(BleConfig.example.serviceUuid),
          throwsUnsupportedError,
        );
      } finally {
        debugDefaultTargetPlatformOverride = null;
      }
    },
  );
}
