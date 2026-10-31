# M3: independent digital checks and waveform artifacts

1 November 2026, feature/ngspice-simulation. Independent validation now runs the
accepted-interface decks through the existing credential-free native executor.
No build preview is accepted as evidence. Checks cover DC load rails, observed
rise/settling, illustrative LED current and peak DC/transient supply draw across
all endpoint corners. An unresolved startup window is UNKNOWN, not a guessed
PASS or failure. A known exceeded current/voltage limit is FAIL.

The runtime checks tool version, exit/log errors, file type/size, vector columns,
sample count, finite values, time ordering and full observation coverage. ngspice
subprocess files are additionally capped at 2 MB by RLIMIT_FSIZE; each run uses
the existing UID/network/process limits and a 15-second deadline. The scenario
has a 255-second total ceiling. Raw samples determine checks before linearization.
The <=501-point waveform retains nominal values and raw/corner extrema in bins.
Canonical result, deck and raw-output hashes are recorded. The accepted policy
now caps power loads at 16 to bound waveform storage (4 MB maximum).

The immutable JSON artifact has status `evidence` and role `validationEvidence`,
bound to its native job, accepted contract and exact input closure. It is outside
the candidate artifact set, so adding it does not invalidate its own checks.
Cancelled/stale results cannot publish it. Only independent validation jobs may
publish this evidence; build jobs cannot create authoritative checks. Simulation
PASSes can be retained when an unrelated lane is unsupported.

All four new checks are advisory, including FAIL/ERROR. Native job aggregation
exempts only these named checks with explicit advisory release flags; existing
required failures still fail the job. Existing release gating is unchanged.

Verification actually run:

- `ruff check kleos tests`: PASS.
- Runtime fixture regressions: 18 passed, including raw current spikes hidden by
  linearization, malformed/nonfinite/truncated output, missing tools/timeouts,
  release closure/advisory behavior and stale publication.
- Full backend suite: 1084 passed, 23 skipped (local Qdrant / mock pricing warnings).
- Real **direct ngspice 47**, using the bounded M2 fallback: RC, divider and LED
  specimens produced 501-point waveforms and expected checks; the 100-ohm LED
  resistor produced LED-current FAIL; an unmodelled LED produced UNKNOWN without
  execution. These are solver runs, not image or service-backed end-to-end proof.

During development the real solver exposed a wrdata header variation (`i(V1)`
versus `v1#branch`); both fixed known forms are now accepted. Early fixture tests
also exposed an incorrect expected PASS for a 3.3 V RC rail limited to 2.4 V; the
expectation was corrected to FAIL. No image was built or run: Docker remains
unavailable. M4–M7 and exact-image qualification remain outstanding.
