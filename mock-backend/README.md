# Kleos mock backend — FastAPI

Python/FastAPI implementation of the local simulation. It contains no real engineering, purchasing, printer, OAuth, worker-marketplace or external-service integration. All results and progress are seeded or simulated in memory. No Node or JavaScript runtime is required.

## Setup and run

Python 3.11 or newer is required. From this folder:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

- State: http://127.0.0.1:8000/api/state
- Health: http://127.0.0.1:8000/api/health
- Interactive API documentation: http://127.0.0.1:8000/docs

Use `requirements.txt` for runtime dependencies only. `requirements.lock` records the fully resolved development environment used for verification.

## API

| Endpoint | Purpose |
|---|---|
| `GET /api/state` | Versioned state and demo metadata |
| `GET /api/health` | Backend health, explicitly reporting mock mode |
| `POST /api/commands` | Validated command envelope, `{ "type": "project.create", "payload": { "brief": "A device" } }` |
| `POST /api/demo/reset` | Reset all simulated records and cancel pending jobs |
| `POST /api/demo/speed` | Change simulation speed, e.g. `{ "speed": 4 }` |

Commands require an `Idempotency-Key` header. Identical retries return the same result; using a key for a different command returns 409. Failures return a non-2xx response with a `message`. Command and snapshot field names remain camelCase for the Flutter client. `/docs` includes each command's payload shape.

The default CORS origins are `http://127.0.0.1:5180` and `http://localhost:5180`. Set `KLEOS9_UI_PORT` before starting the server to change the allowed UI port.

## Layout

- `app/main.py`: FastAPI transport, lifecycle, CORS, request limits and idempotency.
- `app/models.py`: strict Pydantic command payloads and response models.
- `app/simulation.py`: in-memory state, immutable response snapshots and cancellable asyncio jobs.
- `app/projects.py`: the ten project stages, routing, mock generation, verification repair and procurement.
- `app/tasks.py`: simulated worker applications, assignment, evidence review and revisions.
- `app/other.py`: simulated connections, provider jobs and connector qualification CLI.
- `app/data/fixtures.json`: the original demo records, templates, evidence and generated sample source.
- `contracts`: exported OpenAPI and command JSON schemas. Regenerate with `.venv/bin/python export_contract.py`.

State survives browser reloads and is shared by connected tabs while this process is running. Restarting the backend resets the demo. Jobs are cancelled on reset and shutdown; there is intentionally no database or durable job queue.

## Verify

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q app
```

Tests exercise all ten stages with both ordering and home printing, stale-artifact repair, human evidence revisions, connector reposting, all developer qualification kinds, provider outcomes, command validation, HTTP idempotency/concurrency, CORS and job cancellation. The test scheduler completes simulations without wall-clock waits. All HTTP tests run against the in-process ASGI app; they do not contact external services.
