# Bounded Renode UART profile

The optional renode-validator image contains a real Renode process adapter. It is
disabled by default: qualification.json deliberately says qualified=false. Unit
tests with a fake process verify the contract, and cannot qualify this image.
The complete Renode/Zephyr specimen has not been qualified in this workspace.

The accepted requirements must specify renodeProfile: stm32f4-uart-v1 and the
accepted interface must specify firmware.board: stm32f4_disco. The runner restores
one hashed ELF from a real native build into a fresh directory, loads the pinned
STM32F4 platform, executes for two simulated seconds, and captures USART2.
A successful check requires the exact KLEOS_UART_SMOKE_V1 line on that emulated
UART. Wall time, output size and generated-process privileges are bounded.

This demonstrates instruction execution and UART boot only. It does not validate
watch behavior, BLE, sensors, display, analog/RF, thermal or physical operation.
Host C/Dart protocol tests remain separate checks. Missing tools, another board,
another Renode version, an unknown profile or an unqualified image returns
required UNKNOWN. A qualified emulator that runs without the expected marker
returns FAIL. The resulting check includes ELF/UART hashes and a bounded capture.

## Qualification procedure

1. Review the official [Renode 1.15.3 release](https://github.com/renode/renode/releases/tag/v1.15.3),
   obtain its amd64 Debian package through the approved download process, and
   record the SHA-256. The optional image requires this explicit build argument:

       docker build --platform linux/amd64 -f docker/Dockerfile.native --target renode-validator --build-arg RENODE_DEB_SHA256=<reviewed-sha256> -t kleos-renode:candidate .

2. Build the trusted docker/templates/renode-uart-v1/firmware specimen using the
   pinned firmware image, Zephyr v4.2.0, GNU Arm toolchain and stm32f4_disco board.
   Preserve the resulting build/zephyr/zephyr.elf and the complete build logs.
   The supplied overlay explicitly selects USART2 on PA2/PA3 at 115200 baud.
   This fixture does not contain the separate watch telemetry decoder and is not
   itself a complete release candidate.
3. Execute the actual emulator fixture in the fresh image with no network, a
   read-only root and bounded resources, from backend/:

       docker run --rm --platform linux/amd64 --network none --read-only --cpus 2 --memory 2g --tmpfs /tmp:rw,size=512m --entrypoint python -v "$PWD/docker/smoke_renode.py:/smoke.py:ro" -v "$PWD/qualification-output/zephyr.elf:/fixture.elf:ro" kleos-renode:candidate /smoke.py /fixture.elf

4. Record the image digest, tool versions, scenario hash, compiled specimen hash,
   UART capture/hash, timestamp and operator in the qualification evidence.
   Exercise failure specimens too: wrong board/version, missing boot marker and
   malformed ELF. Fix any image/CLI/platform incompatibility before promotion.
5. Only after observing those real results, update the reviewed qualification
   manifest, rebuild the immutable image and repeat the same smoke against its
   final digest. Configure that digest as the independent validator task image.
   Preserve the evidence with the release's tool qualification record.

The scenario's UART file commands follow the
[Renode UART documentation](https://renode.readthedocs.io/en/latest/host-integration/uart.html).
Scenario changes require new qualification; the runner checks its exact hash.
Generated sources cannot change the manifest or provide Renode monitor scripts.

## Local runtime verification

The official 1.15.3 amd64 Debian package's Mono setup aborted under the developer
machine's amd64 translation. No Docker VM settings or unrelated containers were
changed. Dockerfile.local-renode instead uses the same official release's portable
.NET archive, with verified SHA-256
34b032b181e802cfc3d7d6e77aa9d89bc4f5dd7c402f59dcc5bdf4cb1769da93.
The actual executable returned Renode v1.15.3.15131 / .NET 6.0.16 and exit 0 with
network disabled, read-only root, 512 MiB memory and temporary configuration/cache.
Runtime startup alone does not qualify the UART scenario or firmware.
The supervisor confines bundled-runtime extraction to the job's bounded scratch
directory; 128 MiB scratch was insufficient for the portable runtime.
