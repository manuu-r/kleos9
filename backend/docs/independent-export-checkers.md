# Independent firmware and application export checks

These checkers have controlled-data tests and actual isolated fixture evidence: ten
firmware cases and sixteen application cases returned their expected results.
Both evidence sets passed separate review for the documented narrow scopes;
new reviewed reports preserve the raw attempts unchanged. Explicit installation
still requires those matching reviewed reports. Neither is enabled by default. See
[the qualification record](../src/kleos/validation/qualification/EXPORTS.md) for
exact image, positive inputs, failures and scope. Native generation qualification
is not independent validation evidence.

`FirmwareChecker` and `ApplicationChecker` use `ExportChecker` authority checks,
a fresh `IsolatedExportProcess`, and the standalone `export_check.py` data reader.
No candidate source is staged into the checker. Candidate source hashes remain
bound to the authoritative subject and native job, and are rechecked after IO.
No candidate firmware, Python, JavaScript, Flutter code or build command executes
on the development host or in these checking containers.

## Supported claims

- `firmware.export-integrity` requires **both** `outputs/firmware.elf` and
  `outputs/firmware.bin` from one native job. The accepted interface and pinned
  native request must agree on `nrf52840dk/nrf52840`. The independent data reader
  checks ELF32 little-endian ARM executable headers, segment/section bounds,
  board flash/RAM limits, entry/vector relationships and a byte-for-byte binary
  reconstructed from allocated file-backed PROGBITS/ARM EXIDX sections and the
  pinned erased-flash gap fill `0xFF`. Unqualified section types,
  layouts or boards remain unsupported. No firmware execution, peripheral
  behavior, flashing, electrical function or source-build reproduction is proved.
- `application.web-bundle-integrity` requires `outputs/companion-web.zip`. The
  independent reader checks bounded ZIP entries, expanded sizes, CRCs, traversal,
  links, raw central/local filename agreement, file/directory and case-prefix
  collisions, duplicate HTML attributes, local HTML resource references and the
  expected Flutter entry/bootstrap/CanvasKit/font files. ZIP64, split archives, data descriptors and alternate Unicode filename fields
  are outside this strict packaging contract. Font checks cover
  declared assets and format headers, not complete font rendering. Arbitrary
  JavaScript dependencies/network behavior, application security and browser
  behavior are not proved. Browser qualification remains separate.

Reports explicitly retain `physicalAcceptance: false` and
`sourceBuildReproduced: false`; app measurements also retain `browserExecuted`,
`networkBehaviorVerified` and `applicationSecurityVerified` as false. A digital
release under these narrow policies does not satisfy a physical or functional
gate. Unsupported layouts become UNKNOWN, invalid bytes FAIL, transport or
checker failures ERROR; none become PASS through generator reports.

## Composition and installation

`validation/policies.py` defines the 15 nonempty combinations of CAD, PCB,
firmware and application lanes. Registration creates only combinations whose
independent checkers are installed and qualified. Complete output sets are
required before admission, including ELF **and** BIN. Existing prepare/accept
coverage fences retain all current nonsuperseded native lanes; selecting only
CAD cannot bypass unavailable firmware/application checking.

Deployment uses explicit `firmware_checker_installation` and
`application_checker_installation` settings with `imageDigest`,
`qualificationReference` and optional `daemonControlRoot`. The report loader
requires exact image/protocol/lane/resource/network limits, current hashes of all
four implementation files, complete per-lane negative cases and a distinct
reviewer's timestamped approval bound to the entire report payload. These
settings default to absent. No image/report in the generation qualification
folder substitutes for checker evidence. Changed checker sources or reviewed
configuration produce a new installation pin.

The new process implementation does not change the already-qualified CAD/PCB
checker files or images. It uses read-only root/input, UID/GID10001, no network,
verified local Docker Desktop socket restrictions, dropped capabilities,
2CPU/2GiB/64-process limits, 256MiB bounded temporary storage and a fixed
175-second child kill wrapper inside the 180-second host bound. It shares durable
capacity admission with native generation and releases only after exact immutable
container identity removal and confirmed name/ID absence. Lost create responses
remain uncertain and retain reservations.

## Exact-image qualification workflow

The completed fixture executions followed this sequence; future changes require
new matching evidence:

1. Inspect local image identity, active work and available capacity/disk. Build a
   new image from the already fresh PCB checker dependency image
   `sha256:cb1cfd18a6b0ca4fb9246f2b9e77801556e4272ed14384304a30c536764a3b5a`
   using only `Dockerfile.exports` and `export_check.py` in its context, no network
   or package updates. Remove prior checker/generator locations. This is a
   proposed parent; availability must be reverified before execution.
2. Use real retained exact NRF52840 ELF/BIN and corrected self-contained Flutter
   ZIP outputs for positive fixtures. If missing, arrange a separately authorized
   isolated fixture regeneration; synthetic parser fixtures alone are insufficient.
3. Run each lane's complete `EXPECTED_CASES` set serially with a persistent
   CapacityService ledger. Record the ledger's scope explicitly; a separate SQLite
   fixture ledger does not coordinate other databases. Record input bytes/hashes,
   image/source hashes, protocol/isolation/resources, bounded outputs and confirmed
   termination. The trusted-sleep watchdog test proves the fixed kill wrapper,
   not a specific malformed-parser hang.
4. Preserve failures and raw reports with `qualified: false`, then obtain an
   independent review. Derive reviewed reports only after approval. Deployment
   configuration and installation remain separate actions.

Tests cover bounded data parsing, incomplete output sets, cross-project
producer/source/request/board changes, stale publication, response binding,
qualification report rejection, capacity uncertainty/rename cleanup and actual
Runtime composition using test-only reviewed reports. Actual qualification used
new isolated checker images and real regenerated firmware exports; the checker
never ran firmware or browser code. No paid provider call or physical action occurred.
