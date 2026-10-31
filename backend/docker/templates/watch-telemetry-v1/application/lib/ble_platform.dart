import 'dart:convert';

import 'package:device_info_plus/device_info_plus.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter_reactive_ble/flutter_reactive_ble.dart';
import 'package:permission_handler/permission_handler.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'ble_controller.dart';

class PreferencesBleSettings implements BleSettingsStore {
  @override
  Future<BleConfig?> load() async {
    final prefs = await SharedPreferences.getInstance();
    final raw = prefs.getString('ble.configuration');
    if (raw == null) {
      return null;
    }
    final value = jsonDecode(raw) as Map<String, dynamic>;
    return BleConfig.parse(
      value['serviceUuid'] as String,
      value['characteristicUuid'] as String,
    );
  }

  @override
  Future<void> save(BleConfig config) async {
    final prefs = await SharedPreferences.getInstance();
    if (!await prefs.setString(
      'ble.configuration',
      jsonEncode({
        'serviceUuid': config.serviceUuid,
        'characteristicUuid': config.characteristicUuid,
      }),
    )) {
      throw StateError('Device configuration could not be saved.');
    }
  }
}

class ReactiveBleTransport implements BleTransport {
  FlutterReactiveBle? _client;
  @override
  bool get supported =>
      !kIsWeb &&
      {
        TargetPlatform.android,
        TargetPlatform.iOS,
      }.contains(defaultTargetPlatform);
  FlutterReactiveBle get _ble {
    if (!supported) {
      throw UnsupportedError('Bluetooth is disabled in this preview.');
    }
    return _client ??= FlutterReactiveBle();
  }

  @override
  Future<void> prepare() async {
    if (!supported) {
      throw UnsupportedError('Use the Android or iOS companion.');
    }
    final permissions = <Permission>[];
    if (defaultTargetPlatform == TargetPlatform.android) {
      final sdk = (await DeviceInfoPlugin().androidInfo).version.sdkInt;
      permissions.addAll(
        sdk >= 31
            ? [Permission.bluetoothScan, Permission.bluetoothConnect]
            : [Permission.locationWhenInUse],
      );
    } else {
      permissions.add(Permission.bluetooth);
    }
    final granted = await permissions.request();
    if (granted.values.any((status) => !status.isGranted)) {
      throw StateError(
        'Bluetooth access denied. Enable it in system app settings and retry.',
      );
    }
    final state = await _ble.statusStream
        .firstWhere((status) => status != BleStatus.unknown)
        .timeout(const Duration(seconds: 8));
    if (state != BleStatus.ready) {
      throw StateError(
        'Turn on Bluetooth and, on older Android, location services.',
      );
    }
  }

  @override
  Stream<BleDevice> scan(String serviceUuid) => _ble
      .scanForDevices(
        withServices: [Uuid.parse(serviceUuid)],
        scanMode: ScanMode.lowLatency,
      )
      .map((device) => BleDevice(device.id, device.name, device.rssi));
  @override
  Stream<BleLinkState> connect(String deviceId) => _ble
      .connectToDevice(
        id: deviceId,
        connectionTimeout: const Duration(seconds: 15),
      )
      .map(
        (update) => switch (update.connectionState) {
          DeviceConnectionState.connected => BleLinkState.connected,
          DeviceConnectionState.disconnected => BleLinkState.disconnected,
          _ => BleLinkState.connecting,
        },
      );
  @override
  Stream<List<int>> subscribe(String deviceId, BleConfig config) =>
      _ble.subscribeToCharacteristic(
        QualifiedCharacteristic(
          deviceId: deviceId,
          serviceId: Uuid.parse(config.serviceUuid),
          characteristicId: Uuid.parse(config.characteristicUuid),
        ),
      );
}
