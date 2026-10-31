# M5: Flutter waveform viewer

1 November 2026. The existing PCB view now includes a separately labelled
power-on panel reading the M4 endpoint. A custom painter uses the existing Signal
theme; no chart dependency or route was added. Existing PCB, CAD and simulation
controls remain in place.

The viewer supports recorded rail/net/current selection, tap/drag and accessible
slider scrubbing, six-second replay/pause/reset, limit lines, nominal trace,
raw/corner envelope, per-check PASS/FAIL/UNKNOWN/ERROR/NOT_APPLICABLE markers and
nominal measured values versus limits. An illustrative LED indication follows
its recorded current (on at >=0.1 mA), not a fabricated animation. Check statuses
refer to the full run/all corners, independently of the replay position.

It labels data simulated, not measured, and advisory; limitations are expandable.
Reduced motion disables automatic replay while preserving manual inspection.
Malformed, absent and stale evidence does not create a replacement waveform.
Changing the artifact resets playback; a stale engineering preview suppresses
its circuit panel. Parsing is cached across animation frames.

Actual verification: Flutter analyze passed; full Flutter suite **226 passed,
3 skipped**. Nine new tests cover malformed/nonfinite/band/time data, phone widths
320/390 and desktop 900 with enlarged text, replay/scrub/reset/net selection/LED,
reduced motion, UNKNOWN and stale revision reads. The optional phone render was
captured and inspected; test-runner icon/font fallback limits that render, so it
does not qualify a physical phone or browser. Its asynchronous image export was
corrected to use WidgetTester's real-I/O context after two interrupted attempts.
All interactions/status labels are separately asserted in widget tests.

Tests followed the installed flutter-add-widget-test skill's build, initial
assertion, interaction, pump, updated assertion and run/feedback workflow.
The new network path was not tested against a deployed server or native image;
M6 joins real solver evidence to the local storage/API/viewer tests.
