# Firmware/application export checker qualification

This directory retains raw attempts. Raw reports remain `qualified: false` until
a separate reviewer approves the exact image, sources, fixture scope and evidence.
No deployment installation is implicit.

The new checker image is
`sha256:995f1bea80f8f66a48aa12773d60c95fdb3f56706a323d1bcc85b98092948503`.
It was built offline with a minimal context containing only `Dockerfile.exports`
and `export_check.py`, from the exact existing fresh PCB checker dependency image
`sha256:cb1cfd18a6b0ca4fb9246f2b9e77801556e4272ed14384304a30c536764a3b5a`.
Prior checker/generator/application locations were removed. No old backend,
source override, model call, firmware execution or physical operation was used.

## Inputs and preserved investigation

The original retained NRF output blobs were missing. The unchanged trusted sources
in `execution/qualification/qualify_firmware.py` were verified against their
historical source fingerprint and regenerated through the original exact image
`sha256:1ea0aa4e85133be3a85fd7399d53c94cb2d1dc6d426b603235fcf44beb93e3bc`.
The new helper uses current control-plane authority and durable capacity; it does
not claim recovery of historical ELF bytes or qualification of current native
source changes.

- `firmware-export-preparation-attempt-1.json`: a transient ccache file disappeared
  during the original image's scratch scanner. The failure was preserved and the
  fixture cancelled/cleaned, with capacity returning to zero.
- `firmware-export-preparation-attempt-2.json`: unchanged source/image retry
  succeeded and capacity returned to zero. BIN SHA-256
  `08c7e4096b9df38b4872f8422212232983a8d2a2cf7a2e9678dcfa231c9742c2`
  matches the historical BIN; the new ELF hash
  `40b947f43c17cca0f02493a137942dec331a8dd46dca5e7d70851436747a8d82`
  identifies the newly regenerated ELF rather than the previous debug-linked file.
- `export-check-firmware-attempt-1.json`: the first checker image
  `sha256:479a474120db8edb8970fc49634584ae27b79db6cda77d54150147fd9e8137d0`
  returned UNKNOWN for an actual allocated ARM EXIDX section. This was not promoted
  to a successful qualification. A separately rereviewed parser now admits bounded
  aligned file-backed EXIDX word pairs and the explicit Zephyr `0xFF` gap-fill
  contract. The resulting new image is the `995f1bea…` image above.

The application positive fixture is the real corrected-font Flutter export at
`backend/.data/qualification/companion-browser-fonts-20261103/companion-web.zip`,
SHA-256 `556f0b9a9fb3c12d9a51ffb30909d02709c0db46062558810db2312710072e2f`.
It comes from the separate exact native application/font/browser verification;
this checker never executes its JavaScript or substitutes a synthetic ZIP positive.

## Exact checked scope

Firmware checks cover the explicit NRF52840 board's ELF-to-BIN bytes, allocated
section/load memory bounds and entry/vector relationship. Application checks cover
a strict bounded non-ZIP64 single-disk archive, raw local/central file identity,
CRC, file/directory/case-prefix aliases, HTML resource/duplicate attributes and
required local Flutter/font packaging. These are digital export integrity claims,
not source-build reproduction, functional behavior, firmware execution, browser
rendering, security, electrical correctness or physical/manufacturing acceptance.

`qualify_export_check.py` records all per-lane `EXPECTED_CASES`, exact input
hashes/sizes and request bindings, source/image hashes, container policy,
termination and resource ledger results. Qualification jobs execute serially.
Each persistent fixture SQLite ledger coordinates its own run only; it does not
claim cross-database global admission. The root integrator reserved this native
execution window and no other native job ran concurrently.

The 175-second timeout cases replace the parser workload with trusted sleep under
the exact fixed kill wrapper. They verify bounded termination and cleanup, not
that a particular malformed file hangs a parser. The hard wrapper remains in the
normal entrypoint. The checker root/input are read-only; UID/GID10001, denied
internet sockets, cleared credential environment, no capabilities, no-new-privileges,
verified Docker Desktop seccomp, 2CPU/2GiB/64pids and bounded 256MiB temporary storage
are recorded. Exact-image qualification is local Docker Desktop only.

Persistent private inputs and ledgers are under
`backend/.data/qualification/export-check-firmware-attempt-2` and
`backend/.data/qualification/export-check-application-attempt-1`. These paths are
not tracked source/build contexts. Do not delete them before evidence review.
The firmware raw report completed all ten expected cases, including watchdog
termination at 175.32 seconds. The application raw report completed all sixteen
expected cases, including watchdog termination at 175.29 seconds. Both recorded
`qualificationPassed: true`, `containerChecksPassed: true`, and zero final capacity
reservations while keeping `qualified: false`. The native execution window was
released after confirming only the separate disposable PostgreSQL container remained.


## Independent review and derived reports

Both final evidence sets were independently reviewed by `rewrite_bambu` for the
narrow scopes above. See [the review record](export-check-review-2026-11-03.md).
New `export-check-firmware-exact-image.json` and
`export-check-application-exact-image.json` reports preserve their raw references,
add reviewed scope and separate reviewer digests, and pass the actual installer
validator in an ephemeral registry. This validation did not install either
checker into Runtime or change deployment settings. Raw reports remain unchanged.
