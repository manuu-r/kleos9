class WatchTelemetry {
  const WatchTelemetry(this.batteryPercent, this.heartRateBpm, this.steps);
  final int batteryPercent;
  final int heartRateBpm;
  final int steps;
  static WatchTelemetry? decode(List<int> bytes) {
    if (bytes.length != 8 ||
        bytes.any((n) => n < 0 || n > 255) ||
        bytes[0] != 1 ||
        bytes[1] > 100) {
      return null;
    }
    final bpm = bytes[2] | (bytes[3] << 8);
    if (bpm > 240) {
      return null;
    }
    return WatchTelemetry(
      bytes[1],
      bpm,
      bytes[4] + bytes[5] * 256 + bytes[6] * 65536 + bytes[7] * 16777216,
    );
  }
}
