# Incremental engineering sources

Accepted `validationProfile: watch-telemetry-v1` makes the vetted firmware and
companion sources available as `context.baseFiles`. The control image contains
the original template tree plus a pinned, verified manifest. Only missing files
are seeded; existing project overrides are preserved. Other profiles receive no
watch template. This scope does not qualify BLE peripherals or physical hardware.

Model outputs may contain only changed files. The current source closure retains
unchanged immutable artifact IDs, hashes and original provenance across attempts.
Each native build/session pins the full exact source manifest rather than only
files created by its submitting attempt. Superseded paths and older native build
products become stale. Duplicate paths, missing/stale files and changed hashes
fail before execution. A later independent validation checks the entire current
candidate closure.

A native session returns its exact patched source hashes separately from build
outputs. Sources remain candidates with source provenance and can be reused by
an ordinary build or a native-only retry. Retry manifests may reference sources
from several original attempts; tenant, revision, status, path, hash, provenance
and the recorded complete manifest are rechecked. A native retry never invokes a
model or independently accepts files.

Durable template seeding runs inside the attempt's existing transaction/DBOS step;
it does not create a nested durable step. New jobs pin the resource profile and
sandbox policy at creation. Recorded native-only retry inputs remain immutable.
