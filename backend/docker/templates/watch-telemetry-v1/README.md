# watch-telemetry-v1 bounded digital profile

This profile must be explicitly included in accepted requirements as
validationProfile: watch-telemetry-v1. Firmware and companion validation exercise
the fixed eight-byte telemetry protocol with 30 independent vectors, including
invalid frames and numeric boundaries. Firmware also needs a real compiled
Zephyr ELF containing the decoder symbol. Companion code also needs real Flutter
web output. The sample decoder source is a starter fixture, not physical evidence.

The wire format is version byte 1, unsigned battery percentage (0–100), unsigned
16-bit little-endian heart rate (0–240), and unsigned 32-bit little-endian steps.
Bad length, version or ranges return invalid. C and Dart APIs are shown here.
Sources must be compiled into the generated project; copying test reports is
never evidence. These fixtures do not qualify a PCB, enclosure or complete watch.

BLE, peripherals, display/sensor interactions, radio, thermal, real heart-rate
accuracy and physical operation remain UNKNOWN. Nonblocking limitations require
explicitly accepted requiredPeripheralCoverage: [] and excludedCoverage:
["ble", "sensor", "display", "radio", "thermal", "physicalOperation"] in requirements.
Missing, malformed or conflicting scope remains required UNKNOWN. Put any
requested peripheral checks in requirements.requiredPeripheralCoverage; they
block release until a matching independent scenario is installed.
No fabricated simulation success is emitted.
