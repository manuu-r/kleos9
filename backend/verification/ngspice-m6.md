# M6: real solver → independent evidence → API → Flutter

1 November 2026. [Machine-readable run](ngspice-m6-runtime.json) and
[recorded power-on envelope](ngspice-power-on-sample.json).

The opt-in integration test runs six specimens through the real ngspice 47 binary
using the numeric-only direct fallback, then through independent native-job
result handling, temporary SQLite/artifact storage, workspace checks, the
authorized current-evidence endpoint and artifact content download. Duplicate
callbacks publish only one artifact. Project/model setup uses fixtures; no
candidate source program is executed on the host. The direct executor asserts
that every invoked program is ngspice.

| Specimen | Actual result |
|---|---|
| Divider | PASS; nominal output 2.2 V agrees with analytic answer |
| RC | PASS; finite-ramp analytic reference agrees within 5 mV at 1 ms |
| LED + 1 kohm | PASS; 1.25–1.27 mA agrees with Shockley + resistor KVL |
| RC + LED power-on | PASS; rails rise and LED current reaches steady state |
| LED + 100 ohm | **FAIL** LED current limit; waveform still inspectable |
| Unmodelled LED | **UNKNOWN** on all affected checks; zero solver commands |

All supported cases enumerate their full passive endpoint corners. Each records
deck/raw-file/result/artifact hashes and 501 waveform points. No physical
acceptance is claimed. Overall native jobs remain unsupported because this
specimen does not supply the other engineering lanes or actual KiCad
correspondence; a circuit simulation PASS is not full-device release approval.

Actual commands from `backend/`:

```sh
KLEOS_TEST_DIRECT_NGSPICE=1 \
KLEOS_NGSPICE_REPORT="$PWD/verification/ngspice-m6-runtime.json" \
.venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_ngspice_end_to_end.py tests/test_circuit_simulation_runtime.py
```

**27 passed**: six real solver/API scenarios and 21 runtime regressions. Ruff
passed. Ordinary test runs skip these six direct-solver scenarios unless opted
in. Exact image/ngspice 39.3/Linux sandbox execution remains **not run** because
Docker's daemon is unavailable. The direct 47 result is not substituted for it.

Flutter analysis passed; ten focused viewer tests passed. One consumes the exact
recorded power-on envelope, asserts four PASS markers, scrubs to LED-on and
switches between 5 ms startup and full 100 ms. The real trace exposed how a fixed
100 ms viewport compresses startup, so M6 adds a 5 ms startup zoom without
changing evidence or checks. Graph taps now account for the plotted axis inset.
The actual recorded trace was rendered and visually inspected on the phone test
layout (with the M5 test-runner font/icon caveat). This is a connected local
solver/storage/API plus recorded-artifact widget test, not a deployed browser or
physical phone end-to-end qualification.

To reproduce that viewer render from `kleos-ui/`:

```sh
flutter test test/circuit_waveform_test.dart --plain-name 'phone visual specimen' \
  --dart-define=WAVEFORM_RESULT_PATH="$PWD/../backend/verification/ngspice-power-on-sample.json" \
  --dart-define=WAVEFORM_SCREENSHOT_PATH=/tmp/kleos-real-power-on.png
```
