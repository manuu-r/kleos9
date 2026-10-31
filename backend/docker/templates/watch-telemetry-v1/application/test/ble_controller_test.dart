import 'dart:async';

import 'package:flutter_test/flutter_test.dart';
import 'package:kleos_watch_companion/ble_controller.dart';
import 'package:kleos_watch_companion/watch_protocol.dart';

class MemorySettings implements BleSettingsStore {
  BleConfig? value;
  @override
  Future<BleConfig?> load() async => value;
  @override
  Future<void> save(BleConfig config) async {
    value = config;
  }
}

class FakeBle implements BleTransport {
  FakeBle({this.supported = true});
  @override
  final bool supported;
  final scans = StreamController<BleDevice>.broadcast();
  final links = StreamController<BleLinkState>.broadcast();
  final packets = StreamController<List<int>>.broadcast();
  int prepares = 0, scanCalls = 0, connects = 0, subscribes = 0;
  bool denied = false;
  Completer<void>? permission;
  String? service, device;
  BleConfig? subscribedConfig;
  @override
  Future<void> prepare() async {
    prepares++;
    if (permission != null) await permission!.future;
    if (denied) throw StateError('Bluetooth access denied');
  }

  @override
  Stream<BleDevice> scan(String uuid) {
    scanCalls++;
    service = uuid;
    return scans.stream;
  }

  @override
  Stream<BleLinkState> connect(String id) {
    connects++;
    device = id;
    return links.stream;
  }

  @override
  Stream<List<int>> subscribe(String id, BleConfig config) {
    subscribes++;
    subscribedConfig = config;
    return packets.stream;
  }

  Future<void> close() async {
    await scans.close();
    await links.close();
    await packets.close();
  }
}

Future<void> tick() => Future<void>.delayed(Duration.zero);

void main() {
  late FakeBle transport;
  late MemorySettings store;
  late BleController controller;
  setUp(() {
    transport = FakeBle();
    store = MemorySettings();
    controller = BleController(transport, store);
  });
  tearDown(() async {
    controller.dispose();
    await transport.close();
  });

  test('no fabricated telemetry before a valid notification', () async {
    expect(controller.telemetry, isNull);
    await controller.startScan();
    transport.scans.add(const BleDevice('real-device', 'Selected watch', -40));
    await tick();
    await controller.connect(controller.devices.single);
    transport.links.add(BleLinkState.connected);
    await tick();
    expect(controller.telemetry, isNull);
    expect(
      transport.subscribedConfig!.serviceUuid,
      BleConfig.example.serviceUuid,
    );
    transport.packets.add([1, 85, 72, 0, 232, 3, 0, 0]);
    await tick();
    expect(controller.telemetry!.steps, 1000);
    expect(controller.telemetry!.heartRateBpm, 72);
    expect(controller.message, contains('Live device telemetry'));
    await controller.disconnect();
    expect(controller.telemetry, isNull);
    expect(controller.connectedDeviceId, isNull);
  });
  test('denied permissions prevent scan and connection calls', () async {
    transport.denied = true;
    await controller.startScan();
    expect(controller.state, CompanionState.error);
    expect(transport.scanCalls, 0);
    expect(transport.connects, 0);
  });
  test(
    'disconnect while permission prompt is pending prevents late scan',
    () async {
      transport.permission = Completer<void>();
      final scan = controller.startScan();
      await tick();
      await controller.disconnect();
      transport.permission!.complete();
      await scan;
      expect(transport.scanCalls, 0);
      expect(controller.state, CompanionState.idle);
    },
  );
  test('web preview guard never calls any BLE platform API', () async {
    final previewTransport = FakeBle(supported: false);
    final preview = BleController(previewTransport, store);
    await preview.initialize();
    await preview.startScan();
    await preview.connect(const BleDevice('x', 'x', -30));
    expect(preview.state, CompanionState.preview);
    expect(preview.telemetry, isNull);
    expect(
      previewTransport.prepares +
          previewTransport.scanCalls +
          previewTransport.connects +
          previewTransport.subscribes,
      0,
    );
    preview.dispose();
    await previewTransport.close();
  });
  test(
    'UUID configuration persists and is restored without BLE initialization',
    () async {
      const service = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
      const characteristic = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb';
      await controller.configure(service.toUpperCase(), characteristic);
      final restored = BleController(transport, store);
      await restored.initialize();
      expect(restored.config.serviceUuid, service);
      expect(restored.config.characteristicUuid, characteristic);
      expect(transport.prepares, 0);
      restored.dispose();
    },
  );
  test('bad UUIDs do not replace saved configuration', () async {
    expect(
      () => BleConfig.parse('../not-a-uuid', 'bad'),
      throwsFormatException,
    );
    await expectLater(
      controller.configure('bad', 'bad'),
      throwsFormatException,
    );
    expect(store.value, isNull);
  });
  test(
    'device discovery is bounded and repeated advertisements update in place',
    () async {
      await controller.startScan();
      for (var i = 0; i < 60; i++) {
        transport.scans.add(BleDevice('$i', 'Watch $i', -70));
      }
      transport.scans.add(const BleDevice('0', 'Renamed watch', -30));
      await tick();
      expect(controller.devices.length, 50);
      expect(controller.devices.first.name, 'Renamed watch');
    },
  );
  test(
    'invalid frames are ignored and subscription failure clears stale readings',
    () async {
      await controller.connect(const BleDevice('device', 'Watch', -40));
      transport.links.add(BleLinkState.connected);
      await tick();
      transport.packets.add([1, 0]);
      await tick();
      expect(controller.invalidFrames, 1);
      expect(controller.telemetry, isNull);
      transport.packets.add([1, 50, 60, 0, 0, 0, 0, 0]);
      await tick();
      expect(controller.telemetry, isNotNull);
      transport.packets.addError(StateError('Notify unavailable'));
      await tick();
      await tick();
      expect(controller.state, CompanionState.error);
      expect(controller.telemetry, isNull);
    },
  );
  test(
    'eight-byte protocol rejects invalid bounds and supports uint32 steps',
    () {
      expect(
        WatchTelemetry.decode([1, 100, 240, 0, 255, 255, 255, 255])!.steps,
        4294967295,
      );
      for (final value in [
        [1],
        [2, 0, 0, 0, 0, 0, 0, 0],
        [1, 101, 0, 0, 0, 0, 0, 0],
        [1, 100, 241, 0, 0, 0, 0, 0],
        [1, -1, 0, 0, 0, 0, 0, 0],
        [1, 0, 0, 0, 0, 0, 0, 256],
      ]) {
        expect(WatchTelemetry.decode(value), isNull);
      }
    },
  );
}
