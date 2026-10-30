import 'package:flutter/material.dart';
import 'watch_protocol.dart';

void main() => runApp(const WatchCompanion());

class WatchCompanion extends StatelessWidget {
  const WatchCompanion({super.key});
  @override
  Widget build(BuildContext context) {
    final sample = WatchTelemetry.decode([1, 85, 72, 0, 232, 3, 0, 0])!;
    return MaterialApp(
      home: Scaffold(
        appBar: AppBar(title: const Text('Watch telemetry')),
        body: Padding(
          padding: const EdgeInsets.all(24),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Text('Digital sample — device connection is not configured'),
              Text('Battery: ${sample.batteryPercent}%'),
              Text('Heart rate: ${sample.heartRateBpm} bpm'),
              Text('Steps: ${sample.steps}'),
            ],
          ),
        ),
      ),
    );
  }
}
