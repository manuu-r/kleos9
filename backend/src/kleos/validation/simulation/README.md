# Circuit simulation evidence (advisory, digital only)

Ideal-circuit power-on simulation for the Flutter PCB view (`circuit-waveform-v1`). It is
**evidence for review, not acceptance**: every check is `required: false`, `scopeExcluded: true`,
`physicalAcceptance: false`, and no simulation result can pass or block a release.

| File | Responsibility |
|---|---|
| `contract.py` | `interfaces.simulation` bindings and the strict R/C value grammar |
| `plan.py` | Pure compiler: accepted circuit + bindings -> nominal and endpoint-corner decks, or `UNKNOWN` with reasons |
| `results.py` | Bounded output parsing, the four advisory checks, waveform envelope |
| `run.py` | Executes a plan through an injected `DeckExecutor`; never runs a solver itself |
| `process.py`, `Dockerfile`, `ngspice_runner.py` | Isolated one-deck-per-container executor and its image |
| `service.py` | Evidence artifact storage, hash-verified reads, revision fences |

## Contract

The user approves `interfaces.simulation` together with the circuit (`project.solveInterfaces` ->
`approve interfaces`). It must name the supply connector pins, classify **every** circuit reference
(`R`, `C`, `LED`, `DIODE`, or `OPEN` for an unused part) and give each load's probe terminals. Nothing is
inferred from net names or reference prefixes. Proposals whose bindings do not compile are rejected with
the compiler's reasons. LED/diode values must be `kleos-red-led-v1` / `kleos-silicon-diode-v1`; resistor and
capacitor values use plain numbers with one suffix (`330`, `1k`, `2.2meg`, `100n`). Expressions, unit text,
`4k7`-style values and candidate SPICE are refused, and no candidate text reaches a deck.

Limits: 16 components, 16 loads, 32 nets, 4 R/C parts (nominal + 2^4 endpoint corners = at most 17 decks),
resistor +/-5 %, capacitor +/-20 %, a fixed 1 us supply ramp, 100 us output grid over 100 ms.

## Checks

`circuit_dc_rails` (DC operating point inside each load's limits at every corner), `circuit_startup` (90 % rise
observed and the final 5 ms within 2 %/1 mV of DC; an unresolved window is `UNKNOWN`),
`circuit_led_current` (transient maximum against the 5 mA advisory LED limit; `NOT_APPLICABLE` without a rated
LED) and `circuit_total_current` (peak supply draw against the declared budget). Checks use the raw solver
output; the plotted view is a <=501-point envelope (nominal value plus raw/corner extrema per 200 us bin).
Malformed, partial, duplicated, non-finite or oversized output is `ERROR`; an untrusted solver version or a
missing installation is `UNKNOWN`. Trusted solver versions are exact: `ngspice-39` (Debian bookworm 39.3) and
`ngspice-47`.

## Evidence and API

`SimulationService.run` stores one immutable artifact (`status=evidence`, outside every candidate/release set)
bound to the accepted circuit/simulation hashes and engineering revision, and is idempotent per binding. It
re-checks the accepted authority after the solver returns and discards the result if it changed.
`GET /projects/{id}/circuit-simulation` only reads: it verifies the stored bytes, `resultsSha256` and bindings
and otherwise answers `UNKNOWN` with a reason. The verification worker runs the simulation after the independent
checks; a failure there never changes the verification result.

## Installation (local development only)

```bash
docker build --network host -f src/kleos/validation/simulation/Dockerfile \
  -t kleos-sim src/kleos/validation/simulation        # run from backend/
docker image inspect kleos-sim --format '{{.Id}}'     # the pin: sha256:<image id>
```

```bash
export KLEOS2_SIMULATION_INSTALLATION='{"imageDigest":"sha256:<image id>","qualificationReference":"<record>"}'
```

Without that setting no simulator is installed and the route reports `UNKNOWN`. Staging and production reject
the setting. On a restricted network the Dockerfile accepts `--build-arg APT_SCHEME=https` and
`--build-context extra-ca=<dir with *.crt>` (used only during the build, not kept in the image).

Container policy: no network, read-only root, all capabilities dropped, `no-new-privileges`, UID 10001, 1 CPU,
1 GiB, 64 processes, 64 MiB noexec tmpfs, one read-only generated deck, output capped, 25 s deadline,
image identity pinned by ID and label, exact-identity cleanup with removal proof before capacity release.

## Verification

```bash
cd backend
.venv/bin/pytest -q tests/test_simulation_plan.py tests/test_simulation_results.py tests/test_simulation_service.py
KLEOS_TEST_NGSPICE=1 .venv/bin/pytest -q tests/test_simulation_ngspice.py              # real host solver
KLEOS_TEST_SIM_IMAGE=sha256:<image id> .venv/bin/pytest -q tests/test_simulation_container.py   # real container
```

The default suite needs no solver. The solver tests compare against independent references (closed-form RC
response, a bisection solution of the Shockley diode equation) and the recorded Flutter fixture is parsed by a
Python mirror of the viewer's validation.

## Qualification record (3 November 2026)

Local Docker Engine 29.9.2 (not Docker Desktop, not ECS), base
`python:3.12.16-slim-bookworm@sha256:e697c818...`, Debian `ngspice 39.3+ds-1` reporting `ngspice-39`, locally
built image ID `sha256:e845ca21c6787a32097e8a6f067538e1cced3d7e835b6dae5bab121796ba3fa8`. Builds are not
bit-reproducible, so a rebuilt image has a new ID that must be re-run through the tests above.
Observed: 5 container tests (nine corners against the closed form, reproducible output hashes, a hostile deck
confined to the sandbox, missing/unpinned image reported unavailable, and the configured installation through
the service), 3 host-solver tests, 84 simulation tests in total, and no leftover containers.

**What this does not establish:** real component models (LED/diode are illustrative), interior tolerance
values, layout, thermal, RF, parasitic or manufacturing behavior, a hosted/ECS deployment, or any physical
acceptance. Only the local-Docker path is qualified.
