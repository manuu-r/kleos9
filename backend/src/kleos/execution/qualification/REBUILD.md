# Native-only rebuild qualification — 2026-11-02

**Passed with the actual qualified CAD image and production rebuild boundary.**

`rebuild-attempt-1.json` records the exact run of `qualify_rebuild.py` against
`sha256:62280256393a1f822fa1a256eb2991220bb2ec225f7e17894dbb52ec7a02d918`.
The fresh source is the two-part CAD fixture from the existing exact-image check.
All CAD execution occurred inside isolated Docker. No model or retrieval client
was constructed or called, and no hardware or paid service was used.

The check patched, built and finished an original job through `NativeGateway`.
It verified the exact source bytes survived in the immutable blob store and the
owned container had a durable termination receipt. `NativeRebuildService` then
admitted an explicit idempotent linked grant. `configure_native_rebuild` executed
the real deterministic LangGraph build/render/finish flow against actual Docker.

The test deliberately removed current configured images and reduced current
operator resource/time settings before rebuilding. The new job still retained
the original immutable image, 2 CPUs / 4 GiB RAM / 128 PIDs / 2 GiB tmpfs,
network policy, source hashes, allowed paths and source baselines. Its new grant
was bounded to 300 seconds and three actions, linked to the original job/run;
the internal termination receipt did not count as a generation action.

The rebuild produced eight candidate artifacts (source, two STEP files, two STL
files, two SVG projections and parts manifest). Every exported checksum was
verified. Source hashes match exactly; output byte hashes are not required to
match an earlier build because export metadata may contain timestamps. Replaying
the finished action returned its durable result without repeating native work.

Limits: this qualification uses SQLite and an in-memory LangGraph checkpointer.
It proves the native boundary and deterministic rebuild flow for the fixed CAD
fixture, not PostgreSQL failover or arbitrary generated programs. Recovery and
admission unit tests separately cover pending actions, changed snapshots, missing
history/blobs, container loss and uncertain termination. Candidates still require
independent validation and release acceptance.

```bash
PYTHONPATH=backend/src backend/.venv/bin/python \
  backend/src/kleos/execution/qualification/qualify_rebuild.py \
  --image sha256:62280256393a1f822fa1a256eb2991220bb2ec225f7e17894dbb52ec7a02d918 \
  --socket /Users/manu/.docker/run/docker.sock \
  --output /private/tmp/kleos-rebuild-qualification.json
```
