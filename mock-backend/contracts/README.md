# Shared HTTP contract

The shared contract is language neutral. Flutter and FastAPI communicate using JSON, without importing each other's source code or a TypeScript package.

- `openapi.json`: exported FastAPI endpoints, command schemas and response envelopes.
- `commands.schema.json`: the complete JSON Schema union of accepted command types and payloads.

Regenerate from the backend folder:

```bash
.venv/bin/python export_contract.py
```

The server's current schema is also available at http://127.0.0.1:8000/openapi.json and its interactive reference at http://127.0.0.1:8000/docs.

A command is `{ "type": "project.create", "payload": { "brief": "A desk monitor" } }`. POST it to `/api/commands` with an `Idempotency-Key` header. The response is `{ "snapshot": { "revision": 1, "state": { ... }, "demo": { "speed": 1 } }, "result": { "projectId": "..." } }`. Commands without a result return `null`. Errors use a non-2xx status and `{ "message": "..." }`.

Snapshots and record fields retain their existing camelCase names. Revisions increase monotonically for the current backend session. The backend owns state and simulated jobs; the client only sends commands and renders results. Every command is still a mock operation.
