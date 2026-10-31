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

## Companion Bluetooth support

The Android/iOS companion now implements a user-initiated BLE scan, explicit device
selection, connection, and notification subscription. Values remain blank until a
valid eight-byte notification arrives from the selected device. Disconnection or
subscription failure clears readings. The web preview never requests Bluetooth
permissions, constructs the BLE client, scans, or connects; its configuration UI
remains usable. No sample readings appear in the application.

Service UUID `76f13000-f4a8-4f7b-a572-a3aa92c16841` and notification UUID
`76f13001-f4a8-4f7b-a572-a3aa92c16841` are **examples**, not an assertion that any
hardware or the starter firmware exposes them. Edit and save both fields to match
your firmware. Settings persist on the device; sandboxed previews may deny local
storage and then keep settings only for the current session. The starter firmware
qualifies the protocol decoder only and does not implement a BLE GATT service.
A matching firmware service, radio hardware and physical testing are still needed.

Android 12+ requests Bluetooth scan/connect permission. Android 11 and earlier
requests foreground location permission, which Android requires for BLE scans;
location services may also need to be enabled. Location permission is capped at
API30 in the manifest. iOS includes Bluetooth purpose strings and the corresponding
permission-handler CocoaPods macro. There is no background scanning, advertising,
location tracking, automatic connection or data upload.

Pinned packages are flutter_reactive_ble **5.4.2** (with mobile/platform-interface
also pinned), permission_handler **12.0.1**, shared_preferences **2.5.3**, and
device_info_plus **11.5.0**. BLE5.5+ requires a newer Flutter/Dart toolchain than the
qualified Flutter3.35.4/Dart3.9 image. Package metadata and setup were checked against
[the BLE package](https://pub.dev/packages/flutter_reactive_ble/versions/5.4.2),
[its changelog](https://pub.dev/packages/flutter_reactive_ble/changelog),
[permission-handler setup](https://pub.dev/packages/permission_handler/versions/12.0.1),
and [the device-info requirements](https://pub.dev/packages/device_info_plus/versions/11.5.0).

The template includes fake-transport controller and widget tests. They exercise
permission denial, cancellation, selected-device subscriptions, malformed frames,
uint32 boundaries, saved settings and the web guard. These are **software tests**,
not proof of physical BLE connectivity, sensor accuracy or a medically usable
heart-rate monitor. Physical BLE/sensor/radio outcomes remain UNKNOWN until
independently tested. iOS source is delivered for a later signed macOS/Xcode build;
no IPA or physical iOS result is claimed.
