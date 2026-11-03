# Independent review of firmware and application checker evidence

Reviewer: `rewrite_bambu`, separate from the implementation/qualification agent.
Both scoped approvals were received on 2026-11-03. The reviewed derived reports
preserve their raw attempts unchanged and bind their full payloads with separate
review records. No checker was installed or enabled by creating these files.

| Lane | Raw report | Raw SHA-256 | Canonical JSON SHA-256 |
|---|---|---|---|
| Firmware | `export-check-firmware-attempt-2.json` | `6ef116a1e84c6a878dae1e4f5e369d1aa6cbd15a32f4a84d2cc4e85b8d4f9525` | `f977b3bc966f997d05e423144015c6461506e1bd8b9882040855ba21f095c955` |
| Application | `export-check-application-attempt-1.json` | `0c5de23fbb13356fc1157be1d60d1dd6bf9e6af0f6a19fc47571f2c00ca83a27` | `d4beff2818e35940849886dde458832c11491c5928d92c455587dbe3abfdef47` |

The reviewer inspected the actual image identity/protocol/platform and current
four source hashes; checked the real regenerated NRF inputs and corrected-font
Flutter ZIP hashes and sizes; verified all ten firmware and sixteen application
request/input byte bindings; and checked recorded resource/isolation fields and
all durable capacity reservations' exact identity removal proofs. Both final
capacity counts were zero. Firmware watchdog termination was observed at 175.32
seconds and application at 175.293 seconds, both exit137. These trusted sleep
workloads prove the fixed kill wrapper, not a specific malformed-parser hang.

The firmware review approved only NRF52840 ELF/BIN digital export integrity.
The application review approved only strict Flutter ZIP/local packaging integrity;
actual payload CRC, raw filename aliases, file/directory/case-prefix collisions
and duplicate HTML attributes had meaningful rejecting cases. Prior transient
firmware preparation failure and checker UNKNOWN were preserved. The static
review findings and subsequent ARM EXIDX/fixed-gap changes were reviewed before
building the final checker image.

These approvals do not certify source-build reproduction, firmware execution,
functional behavior, browser rendering, application security, electrical behavior,
manufacturing or physical acceptance. Local Docker Desktop is the qualified
execution environment. The derived reports are
`export-check-firmware-exact-image.json` and
`export-check-application-exact-image.json`; deployment installation remains an
explicit separate configuration choice.
