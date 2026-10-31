# Bambu Lab print preparation

Kleos can prepare a real sliced plate from an accepted digital-release STL, save
the package and hand it to Bambu Connect for operator review. Optional local LAN
telemetry reports printer status. The platform does not send print-start, motion,
heating, FTP or camera commands. Purchasing, shipping and physical acceptance
remain simulated/blocked by server policy. Slicing never approves a release.

## Models and selection

The pinned [model catalog](../kleos/bambu-models.json) covers all 14 models in
Bambu Studio **2.8.2.61**: X1 Carbon, X1, X1E, P1P, P1S, A1 mini, A1, H2D,
H2D Pro, H2S, P2S, H2C, X2D and A2L. IDs, names and supported nozzle choices
come from the [vendor's versioned profiles](https://github.com/bambulab/BambuStudio/tree/v02.08.02.61/resources/profiles/BBL),
with source checksums and attribution. This is model-selection coverage, not
qualification of every printer/firmware/nozzle/material combination.

The CAD preview and home-print stage show a model dropdown. A single fresh Bambu
bridge that reports an exact known model ID/name selects it automatically.
Absent, unknown or conflicting reports require manual selection. No serial-prefix
guessing or LAN scanning is used. New models need a reviewed catalog update.
Nozzle/material profiles appear only after an operator qualifies that exact bundle.
Multi-extruder printers can be selected; this initial path prepares a single
material and single plate. Multi-material tool mapping remains an operator task
in Bambu Connect; it is not qualified by this connector.

## Offline slicer installation

Build the separate Linux/amd64 image from `backend/`:

```sh
docker build --platform linux/amd64 -f docker/Dockerfile.bambu-slicer -t kleos-bambu:2.8.2.61 .
docker image inspect --format '{{.Id}}' kleos-bambu:2.8.2.61
```

Record that immutable `sha256:...` identity, or a repository digest after your
normal image distribution process. Runtime pulls are disabled. The image downloads
the official [Ubuntu 24.04 AppImage release](https://github.com/bambulab/BambuStudio/releases/tag/v02.08.02.61)
and verifies SHA-256
`d501b103fac5424513ec0e8d6bc145fb30719de2c7d94d7320d723740c81a7fd`.
Review the vendor's AGPL license and accompanying notices when distributing it.

The local adapter uses the existing native supervisor: subprocess UID 10001,
clean environment, Unix IPC only, no credentials, no network, read-only root,
2 CPUs, 4 GiB memory, 128 container processes, 1 GiB disposable scratch and a
300-second tool deadline (330 seconds for the outer container). Only the binary
STL and three checksummed profiles/manifest are mounted, read-only. Final packages
and source transfers are bounded to 16 MiB. No generated code runs on the host.
The explicit `--desktop-compatibility` flag uses the existing verified
network-none fallback when emulated Docker Desktop cannot install seccomp; it is
never an ECS setting. No cloud native-job dispatch or cloud capacity policy is
added by this customer-run adapter.

## Resolve and qualify profiles

Bambu's [CLI documentation](https://github.com/bambulab/BambuStudio/wiki/Command-Line-Usage)
requires complete machine, process and filament configurations. Raw inherited
presets are insufficient. Check out the pinned vendor profile tree, then use the
data-only resolver from `backend/`, for example:

```sh
.venv/bin/python -m kleos.bambu_profiles \
  --preset-root /absolute/vendor/BambuStudio/resources/profiles/BBL \
  --machine 'Bambu Lab P1S 0.4 nozzle' \
  --process '0.20mm Standard @BBL X1C' \
  --filament 'Bambu PLA Basic @BBL P1S 0.4 nozzle' \
  --image 'sha256:<recorded-image-id>' --output /absolute/new-private-profile-bundle
```

Choose vendor-compatible settings for the target machine. The resolver merges
single-parent JSON inheritance and JSON templates, rejects cycles/ambiguous names and produces
`machine.json`, `process.json`, `filament.json` and a checksummed manifest. It
does not execute any preset content or mark a profile qualified. Unresolved
inheritance/template references and post-processing programs are rejected. Printer model, nozzle,
material and build-volume declarations must agree with the resolved machine data.
Do not pass a generated/user-uploaded 3MF as a profile source.

Run the offline image smoke with the emitted bundle hash and immutable image:

```sh
.venv/bin/python docker/smoke_bambu.py --profiles /absolute/new-private-profile-bundle \
  --bundle-sha256 '<manifest-sha256>' --image 'sha256:<recorded-image-id>'
```

It slices a trusted closed 10 mm cube using the actual image and prints bounded
hash/metadata evidence. It sends nothing to a printer and does not promote server
configuration. Independently inspect the plate in Bambu Studio/Connect and verify
the exact printer, firmware, nozzle, material, orientation, supports, bed exclusions,
temperatures, start/end G-code and AMS mapping before qualifying the bundle. A
bounding-box fit alone is insufficient for bed exclusions or printability.

Install the emitted profile in the operator-only `KLEOS_BAMBU_SLICE_PROFILES` JSON
mapping under a stable key, changing `qualified` to `true` only after recording
that scoped qualification. Defaults are empty. Example shape:

```json
{
  "p1s-pla-04-v1": {
    "printerModelId": "C12", "nozzleMm": 0.4, "material": "PLA",
    "studioVersion": "2.8.2.61", "bundleSha256": "<manifest-sha256>",
    "image": "sha256:<recorded-image-id>", "qualified": true
  }
}
```

Keep the exact image and bundle available for saved preparations. Profile changes
apply to new preparations; existing records retain their original snapshot.
Do not put credentials in bundles or server profile metadata.

## Prepare and review a part

1. Accept the current digital engineering release. Choose an accepted binary STL,
   model and qualified nozzle/material profile in **Print with Bambu Lab**.
2. Select **Prepare print**. The API durably records the source/release/revision
   and profile hashes, returning a preparation ID. Repeated identical requests
   reuse that ID; a changed input under the same key is rejected.
3. On the customer's machine, run the local adapter with a normal authenticated
   Kleos bearer-session token (`KLEOS_PLATFORM_TOKEN`) and organization ID
   (`KLEOS_ORGANIZATION_ID`) in the local environment. The read-only bridge token
   cannot prepare or upload files. Do not copy browser cookies or provider keys.

```sh
.venv/bin/python -m kleos.bambu_local --platform https://kleos.example/api/v1 \
  --project '<project-id>' --preparation '<preparation-id>' \
  --profiles /absolute/qualified-profile-bundle --output /absolute/part.gcode.3mf \
  --open-connect
```

The adapter downloads and verifies the accepted mesh, slices offline, uploads the
actual bytes and completes the preparation. The server rechecks authorization,
current release, revision, requirement/interface contract, cancellation and hashes.
It independently checks bounded ZIP/single-plate/G-code structure, then stores a
separate `prepared` artifact with `authority: local_adapter_report`. This artifact
does not enter engineering candidate/release closure or produce a Validation.
The local adapter report is not independent proof of manufacturability or slicer
execution; untrusted users can submit arbitrary packages within the structural
contract. Uploaded G-code is never executed by the server.

`--open-connect` explicitly opens the local Bambu Connect import URI. Without it,
the file is simply saved. Inspect the imported plate, select the physical printer
and filament mapping, then start printing inside Bambu Connect. The protocol is
based on Bambu's [published integration example](https://github.com/SoftFever/OrcaSlicer/pull/8103);
no vendor keys, signature bypass or private cloud API is used. Manual import is
available if OS protocol handling is absent. The UI can also download/share the
saved plate. Physical outcomes remain outside this preparation record.

Cancellation (`DELETE .../print-preparations/{id}`) fences publication; it does
not stop a physical printer or immediately interrupt a detached local process.
Old revisions/contracts/releases cannot publish or be listed as current. Retrying
after successful server completion downloads the saved hashed package without
reslicing, including after an interrupted local save. Rerun the adapter or reload
the panel to see the latest status. There is no background command polling daemon.

## Optional local status and model detection

Bambu's [third-party guidance](https://blog.bambulab.com/updates-and-third-party-integration-with-bambu-connect/)
describes Connect and optional LAN Developer Mode. Raw protocols are not officially
supported; firmware/model availability varies. This bridge is best-effort status
only. Unsupported/absent reports show disconnected or require manual model selection.

Register a read-only `bridge` connector with `printer.status`/`printer.profile`
capabilities and issue its scoped token using the existing administrator route.
Store private bridge configuration locally:

```json
{
  "kind": "bambu", "printerIp": "192.168.1.20", "serial": "YOUR_PRINTER_SERIAL",
  "certificateSha256": "<owner-verified-lowercase-leaf-cert-sha256>",
  "platformUrl": "https://kleos.example", "connectorId": "<bridge-id>",
  "intervalSeconds": 30
}
```

Set `KLEOS_BAMBU_ACCESS_CODE` and `KLEOS_BRIDGE_TOKEN` locally, then run
`.venv/bin/python -m kleos.bridge --config /absolute/private-bridge.json`.
The code permits only literal RFC1918 IPv4 addresses, TLS port 8883 and a
certificate pin verified **before** sending the access code. Obtain/verify that
fingerprint through your own trusted printer enrollment; there is no automatic
trust-on-first-use. Certificate rotation requires explicit owner re-enrollment.
MQTT packets are limited to CONNECT, SUBSCRIBE and DISCONNECT; no PUBLISH is sent.
Reads have time/packet/byte bounds, exclude retained messages and strip all fields
except approved model/status/progress/temperature observations. Credentials stay
on the customer's machine; server telemetry cannot become physical acceptance.

See [scoped verification](../verification/bambu-printer-connector.md) for what
actually ran and which image/hardware qualifications remain outstanding.
