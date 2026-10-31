# ngspice simulation M1: deterministic compiler

Date: 1 November 2026. Branch: `feature/ngspice-simulation`.
Parent checkpoint: `745b355` (M0). Scope: compiler and pure unit tests only.

## Implementation exercised

`kleos/circuit_simulation.py` compiles accepted circuit/power interfaces with
explicit terminal bindings into deterministic nominal/tolerance decks. Generated
numeric nodes and element IDs, whitelisted values, fixed model/control tables,
input/deck hashes and bounded scenario policy are covered. Unsupported parts,
missing/ambiguous bindings, malformed inputs, DC-floating/disconnected nodes and
scope ceilings return UNKNOWN with no partial deck.

The 60 new tests include independent resistive nodal analysis versus a divider
formula, numerical integration of emitted RC topology against closed-form ramp
and step answers, and a bisection solution of the emitted LED/resistor Shockley
equation. They also cover injection strings, permutations, hashes, strict numeric
power input, model selection, terminal polarity, exhaustive corners, resource
policy metadata and non-mutation of accepted data.

## Commands and results

From `backend/`, using the existing Python 3.12.11 development environment:

```sh
.venv/bin/ruff check kleos tests
.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_circuit_simulation.py
.venv/bin/python -m pytest -q -p no:cacheprovider
```

Ruff passed. Focused final compiler suite: **60 passed**. Full final backend
suite: **1066 passed, 23 skipped**, with two warnings (local Qdrant payload
indexes and unavailable pricing for a mocked model stream). The full suite was
rerun after the final compiler metadata/test changes.

From `kleos-ui/`:

```sh
flutter analyze
flutter test
```

Analysis: no issues. Tests: **217 passed, 3 skipped**. Flutter 3.47.8 / Dart
3.13.7. No Flutter source, API schema, generated DTO or dependency changes.
`git diff --check` passed. Documentation links were checked locally.

## Evidence boundaries

These commands really executed tests and static analysis. Circuit measurements
are **analytic/numerical fixtures**, not ngspice results or digital device
validation. No ngspice process, native container, service-backed end-to-end
journey, physical test or model/provider call was run for this milestone.
The backend suite used its normal fixture providers; opted-in suites stayed skipped.
Tests did not require creating or editing a local `.env`.

The M0 environment had no ngspice binary and no running Docker daemon. M2 still
needs a pinned installation and real offline sandbox smoke. M3 still needs
runtime bounds/output parsing, authoritative checks and waveform artifact storage.
API exposure, viewer, failing/unknown solver examples and final qualification
remain M4–M7 work. Existing release and physical gates are unchanged.
