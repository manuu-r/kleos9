import 'dart:async';

import 'package:flutter/material.dart';

import 'ble_controller.dart';
import 'ble_platform.dart';

void main() => runApp(const WatchCompanion());

class WatchCompanion extends StatefulWidget {
  const WatchCompanion({super.key, this.controller});
  final BleController? controller;
  @override
  State<WatchCompanion> createState() => _WatchCompanionState();
}

class _WatchCompanionState extends State<WatchCompanion> {
  late final BleController controller =
      widget.controller ??
      BleController(ReactiveBleTransport(), PreferencesBleSettings());
  final service = TextEditingController(text: BleConfig.example.serviceUuid);
  final characteristic = TextEditingController(
    text: BleConfig.example.characteristicUuid,
  );
  @override
  void initState() {
    super.initState();
    unawaited(_initialize());
  }

  Future<void> _initialize() async {
    await controller.initialize();
    if (!mounted) {
      return;
    }
    service.text = controller.config.serviceUuid;
    characteristic.text = controller.config.characteristicUuid;
  }

  @override
  void dispose() {
    service.dispose();
    characteristic.dispose();
    if (widget.controller == null) {
      controller.dispose();
    }
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => MaterialApp(
    home: Scaffold(
      appBar: AppBar(title: const Text('Watch telemetry')),
      body: AnimatedBuilder(
        animation: controller,
        builder: (context, _) {
          final reading = controller.telemetry;
          return SingleChildScrollView(
            padding: const EdgeInsets.all(24),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(controller.message, key: const Key('connection-status')),
                const SizedBox(height: 16),
                Text(
                  'Battery: ${reading == null ? '—' : '${reading.batteryPercent}%'}',
                ),
                Text(
                  'Heart rate: ${reading == null ? '—' : '${reading.heartRateBpm} bpm'}',
                ),
                Text('Steps: ${reading?.steps ?? '—'}'),
                const SizedBox(height: 24),
                const Text(
                  'Device configuration',
                  style: TextStyle(fontWeight: FontWeight.bold),
                ),
                const Text(
                  'The defaults are example UUIDs. Your firmware must advertise this service and notify the exact eight-byte telemetry format. Physical BLE operation has not been verified.',
                ),
                const SizedBox(height: 12),
                TextField(
                  controller: service,
                  key: const Key('service-uuid'),
                  decoration: const InputDecoration(labelText: 'Service UUID'),
                  enabled: !controller.busy,
                ),
                TextField(
                  controller: characteristic,
                  key: const Key('characteristic-uuid'),
                  decoration: const InputDecoration(
                    labelText: 'Notify characteristic UUID',
                  ),
                  enabled: !controller.busy,
                ),
                const SizedBox(height: 12),
                Wrap(
                  spacing: 12,
                  runSpacing: 8,
                  children: [
                    OutlinedButton(
                      onPressed: controller.busy
                          ? null
                          : () async {
                              try {
                                await controller.configure(
                                  service.text,
                                  characteristic.text,
                                );
                              } catch (error) {
                                if (context.mounted) {
                                  ScaffoldMessenger.of(context).showSnackBar(
                                    SnackBar(content: Text('$error')),
                                  );
                                }
                              }
                            },
                      child: const Text('Save configuration'),
                    ),
                    FilledButton(
                      key: const Key('scan-button'),
                      onPressed: !controller.supported || controller.busy
                          ? null
                          : () {
                              if (controller.state == CompanionState.scanning) {
                                unawaited(controller.stopScan());
                              } else {
                                unawaited(controller.startScan());
                              }
                            },
                      child: Text(
                        controller.state == CompanionState.scanning
                            ? 'Stop scan'
                            : 'Scan for device',
                      ),
                    ),
                    if (controller.connectedDeviceId != null ||
                        controller.state == CompanionState.connecting)
                      OutlinedButton(
                        onPressed: () => controller.disconnect(),
                        child: const Text('Disconnect'),
                      ),
                  ],
                ),
                const SizedBox(height: 16),
                for (final device in controller.devices)
                  ListTile(
                    contentPadding: EdgeInsets.zero,
                    title: Text(
                      device.name.isEmpty ? 'Unnamed BLE device' : device.name,
                    ),
                    subtitle: Text('${device.id} · ${device.rssi} dBm'),
                    trailing: TextButton(
                      onPressed: controller.busy
                          ? null
                          : () => controller.connect(device),
                      child: const Text('Connect'),
                    ),
                  ),
              ],
            ),
          );
        },
      ),
    ),
  );
}
