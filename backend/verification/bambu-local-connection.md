# Local Bambu connection form verification

Date: 1 November 2026. Branch: `feature/ngspice-simulation`.

Scope: opt-in loopback development API and Flutter form for read-only TLS/MQTT
status. Existing offline slicing, customer-run bridge and simulation remain intact.
No print, heat, motion, upload, paid inference or deployment was performed.

## Executed checks

- Backend full suite: **1191 passed, 29 skipped**; Ruff passed.
- After adding active credential-expiry timers: **52 focused Bambu tests passed**;
  Ruff passed again. Expiration, isolation, forgetting during an in-flight request,
  offline results, loopback/Host/Origin/proxy guards, role denial, private-IP and
  certificate validation and secret-redacted API errors use fixtures.
- Flutter full suite: **263 passed, 3 skipped**; analysis passed. Seven new widget
  cases cover 320/390/1000 px, untrusted inspection, masked/cleared access code,
  A1 mini selection, connection/refresh/forget and offline state with callbacks.
- Generated transport matches the exported OpenAPI contract. Diff whitespace passed.
- Local development API health and connection-capability GET requests succeeded.
  Flutter serves on loopback port 5180; separate simulation service on port 8002.

## Qualification limits

The owner's A1 mini was powered off. **No authenticated hardware connection was
tested**. Printer transport tests use fake TLS sockets/byte streams; UI connection
tests use callbacks, never a physical printer. No outbound control packet is sent;
successful fresh status still requires compatible firmware and LAN settings.
Certificate inspection does not establish trust or automatically enroll a device.

The pinned slicer image smoke and physical profile qualification remain unrun.
Docker, nozzle/material profiles and an accepted STL are unnecessary for this
connection-only test. Default local enrollment is disabled; hosted APIs reject it.
The local preview uses a separate SQLite database and no worker/provider keys.
