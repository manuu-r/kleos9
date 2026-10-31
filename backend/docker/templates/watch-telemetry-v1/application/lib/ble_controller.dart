import 'dart:async';

import 'package:flutter/foundation.dart';

import 'watch_protocol.dart';

enum BleLinkState { connecting, connected, disconnected }

enum CompanionState {
  idle,
  requestingPermission,
  scanning,
  connecting,
  connected,
  error,
  preview,
}

class BleDevice {
  const BleDevice(this.id, this.name, this.rssi);
  final String id;
  final String name;
  final int rssi;
}

class BleConfig {
  const BleConfig(this.serviceUuid, this.characteristicUuid);
  // Example custom service, not a claim that the supplied firmware exposes BLE.
  static const example = BleConfig(
    '76f13000-f4a8-4f7b-a572-a3aa92c16841',
    '76f13001-f4a8-4f7b-a572-a3aa92c16841',
  );
  final String serviceUuid;
  final String characteristicUuid;
  static final _uuid = RegExp(
    r'^[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$',
  );
  static BleConfig parse(String service, String characteristic) {
    service = service.trim().toLowerCase();
    characteristic = characteristic.trim().toLowerCase();
    if (!_uuid.hasMatch(service) || !_uuid.hasMatch(characteristic)) {
      throw const FormatException(
        'Enter full 128-bit service and notification UUIDs.',
      );
    }
    return BleConfig(service, characteristic);
  }
}

abstract interface class BleSettingsStore {
  Future<BleConfig?> load();
  Future<void> save(BleConfig config);
}

abstract interface class BleTransport {
  bool get supported;
  Future<void> prepare();
  Stream<BleDevice> scan(String serviceUuid);
  Stream<BleLinkState> connect(String deviceId);
  Stream<List<int>> subscribe(String deviceId, BleConfig config);
}

class BleController extends ChangeNotifier {
  BleController(
    this.transport,
    this.store, {
    this.scanDuration = const Duration(seconds: 12),
  }) {
    if (!transport.supported) {
      state = CompanionState.preview;
      message =
          'Web preview — Bluetooth is available in the Android/iOS companion.';
    }
  }
  final BleTransport transport;
  final BleSettingsStore store;
  final Duration scanDuration;
  BleConfig config = BleConfig.example;
  CompanionState state = CompanionState.idle;
  String message =
      'Disconnected. Configure matching firmware UUIDs before scanning.';
  WatchTelemetry? telemetry;
  String? connectedDeviceId;
  int invalidFrames = 0;
  final Map<String, BleDevice> _devices = {};
  List<BleDevice> get devices => List.unmodifiable(_devices.values);
  bool get supported => transport.supported;
  bool get busy => {
    CompanionState.requestingPermission,
    CompanionState.connecting,
  }.contains(state);
  StreamSubscription<BleDevice>? _scan;
  StreamSubscription<BleLinkState>? _connection;
  StreamSubscription<List<int>>? _notifications;
  Timer? _scanTimer;
  int _generation = 0;
  bool _disposed = false;

  void _changed() {
    if (!_disposed) {
      notifyListeners();
    }
  }

  void _fail(String message) {
    if (_disposed) {
      return;
    }
    telemetry = null;
    connectedDeviceId = null;
    state = CompanionState.error;
    this.message = message;
    _changed();
  }

  Future<void> initialize() async {
    try {
      final saved = await store.load();
      if (!_disposed && saved != null) {
        config = saved;
        _changed();
      }
    } catch (_) {
      // Sandboxed previews may disallow local storage. No BLE call is needed.
      if (!_disposed) {
        message = '$message Settings are available for this session.';
        _changed();
      }
    }
  }

  Future<void> configure(String service, String characteristic) async {
    final next = BleConfig.parse(service, characteristic);
    await disconnect();
    var persisted = true;
    try {
      await store.save(next);
    } catch (_) {
      if (supported) {
        rethrow;
      }
      persisted = false;
    }
    if (_disposed) {
      return;
    }
    config = next;
    message = !persisted
        ? 'Web preview — settings saved for this session; persistent storage is unavailable.'
        : supported
        ? 'Saved. Scan for devices advertising this service.'
        : 'Web preview — configuration saved; Bluetooth stays disabled.';
    _changed();
  }

  Future<void> stopScan() async {
    _scanTimer?.cancel();
    _scanTimer = null;
    await _scan?.cancel();
    _scan = null;
    if (state == CompanionState.scanning) {
      state = CompanionState.idle;
      message = 'Scan finished. Select a device or scan again.';
      _changed();
    }
  }

  Future<void> startScan() async {
    if (!supported || _disposed) {
      return;
    }
    await disconnect();
    final generation = ++_generation;
    _devices.clear();
    state = CompanionState.requestingPermission;
    message = 'Requesting Bluetooth access…';
    _changed();
    try {
      await transport.prepare();
      if (_disposed || generation != _generation) {
        return;
      }
      state = CompanionState.scanning;
      message = 'Scanning for the configured service…';
      _scan = transport
          .scan(config.serviceUuid)
          .listen(
            (device) {
              if (_disposed || generation != _generation) {
                return;
              }
              if (_devices.length < 50 || _devices.containsKey(device.id)) {
                _devices[device.id] = device;
              }
              _changed();
            },
            onError: (Object error) {
              if (generation != _generation) {
                return;
              }
              unawaited(stopScan());
              _fail(
                'Scan failed. Check Bluetooth, permissions and firmware advertising.',
              );
            },
          );
      _scanTimer = Timer(scanDuration, () {
        unawaited(stopScan());
      });
      _changed();
    } catch (error) {
      if (!_disposed && generation == _generation) {
        _fail('Bluetooth unavailable: $error');
      }
    }
  }

  Future<void> _disconnectWithError(String message) async {
    final expectedGeneration = _generation + 1;
    await disconnect();
    if (_generation == expectedGeneration) {
      _fail(message);
    }
  }

  Future<void> connect(BleDevice device) async {
    if (!supported || _disposed) {
      return;
    }
    await disconnect();
    final generation = ++_generation;
    state = CompanionState.connecting;
    message = 'Connecting to ${device.name.isEmpty ? device.id : device.name}…';
    _changed();
    try {
      _connection = transport
          .connect(device.id)
          .listen(
            (update) {
              if (_disposed || generation != _generation) {
                return;
              }
              if (update == BleLinkState.connected) {
                connectedDeviceId = device.id;
                state = CompanionState.connected;
                message = 'Connected — waiting for a valid eight-byte telemetry packet.';
                try {
                  _notifications ??= transport
                      .subscribe(device.id, config)
                      .listen(
                        (bytes) {
                          if (_disposed || generation != _generation) {
                            return;
                          }
                          final reading = WatchTelemetry.decode(bytes);
                          if (reading == null) {
                            invalidFrames++;
                            message =
                                'Ignored an invalid telemetry packet ($invalidFrames).';
                          } else {
                            telemetry = reading;
                            message = 'Live device telemetry — received over Bluetooth.';
                          }
                          _changed();
                        },
                        onError: (Object error) {
                          if (generation == _generation) {
                            unawaited(
                              _disconnectWithError(
                                'Cannot subscribe. Verify the characteristic UUID and notification support.',
                              ),
                            );
                          }
                        },
                        onDone: () {
                          if (generation == _generation) {
                            unawaited(
                              _disconnectWithError(
                                'Telemetry notifications ended. Reconnect to receive live readings.',
                              ),
                            );
                          }
                        },
                      );
                } catch (_) {
                  unawaited(
                    _disconnectWithError(
                      'Cannot subscribe. Verify the notification characteristic.',
                    ),
                  );
                }
                _changed();
              } else if (update == BleLinkState.disconnected) {
                unawaited(disconnect());
              }
            },
            onError: (Object error) {
              if (generation == _generation) {
                unawaited(
                  _disconnectWithError(
                    'Connection failed. Move closer and verify the device firmware.',
                  ),
                );
              }
            },
          );
    } catch (_) {
      await _disconnectWithError(
        'Connection failed. Verify Bluetooth access and device firmware.',
      );
    }
  }

  Future<void> disconnect() async {
    final generation = ++_generation;
    _scanTimer?.cancel();
    _scanTimer = null;
    final scan = _scan,
        notifications = _notifications,
        connection = _connection;
    _scan = null;
    _notifications = null;
    _connection = null;
    telemetry = null;
    connectedDeviceId = null;
    await Future.wait([
      if (scan != null) scan.cancel(),
      if (notifications != null) notifications.cancel(),
      if (connection != null) connection.cancel(),
    ]);
    if (_disposed || generation != _generation) {
      return;
    }
    state = supported ? CompanionState.idle : CompanionState.preview;
    message = supported
        ? 'Disconnected. No live device readings.'
        : 'Web preview — Bluetooth is available in the Android/iOS companion.';
    _changed();
  }

  @override
  void dispose() {
    _disposed = true;
    _generation++;
    _scanTimer?.cancel();
    unawaited(_scan?.cancel());
    unawaited(_notifications?.cancel());
    unawaited(_connection?.cancel());
    super.dispose();
  }
}
