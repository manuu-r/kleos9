# Backend review — 30 October 2026

Reviewed the application boundary, project/tenant authorization, commands/outbox,
worker admission, inference and native jobs, retries, validation/release gates,
storage/retrieval, connectors, consent/training, retention/deletion, telemetry and
deployment configuration. This is a code and local verification review, not a
production security certification or a complete engineering qualification.

## Fixed findings

| Finding | Result |
|---|---|
| CAD/PCB contracts and execution were hard-coded | Versioned per-build tool policies, qualified installed adapters, source contracts, immutable installation binding and a Flutter selector; current defaults retained |
| Supplied plugin output files could be relabeled as native exports after a no-op command | Reserved output inputs are rejected and the adapter must create its declared files afresh |
| Worker mapping changes could alter an already selected custom implementation | Installation hashes plus immutable custom image/task revisions; resolved execution identity pinned before generation |
| Native work could be picked up by a worker for a different execution backend | Dispatch, cancellation and reconciliation respect the stored backend; legacy ECS jobs remain compatible |
| Research retry allowance was exhausted across legitimate conversational turns | Bounded retries belong to each explicit research task; engineering repair limits remain baseline-scoped |
| Failed inference calls could occupy live capacity permanently | Completed failures release live capacity while retaining uncertain financial reservations; no blind billed replay |
| A transient deletion failure could leave a hidden project permanently queued | Bounded durable retries, retry leases and periodic reconciliation, including conservative recovery of legacy requests |
| Training could dispatch after consent changed between dataset materialization and admission | Consent/version binding, serialized admission and dispatch claim, output fencing and durable cancellation |
| Cross-project withdrawal/deletion could acquire dataset/project locks in opposite order | Shared organization-first lock ordering for deletion and training consent |
| Malformed URL-looking text could crash log/transcript redaction | Safe URL parsing with privacy regressions |
| Upload contracts allowed CAD-sized files but middleware stopped them at 1 MiB | Authorized upload route supports 16 MiB decoded files; authentication/CSRF/project checks precede buffering; other public bodies remain 1 MiB |
| Early middleware errors could bypass CORS | CORS wraps bounded/authentication errors, allowing Flutter to display them |
| Large native-result envelopes were buffered before job-token authorization | Only the exact result route receives the larger limit, after scoped job authorization; completion rechecks authority |
| Application edits invalidated expensive dependency/toolchain image layers | Control dependencies and native toolchains build before the small application package |

## Practices retained

- PostgreSQL is authoritative; commands use expected revisions, durable receipts
  and an outbox. Streams are scoped and reconnect from committed cursors.
- Model choice and engineering tool choice are separate versioned decisions.
  Active work stays pinned; stale/cancelled work cannot publish into a new baseline.
- Native jobs receive scoped inputs, short-lived job authority, bounded resources
  and no provider/database credentials. Generated subprocesses have a separate UID.
- Qualified generators return candidates. Separate validators and exact artifact
  closures determine digital release; synthetic physical evidence cannot pass live gates.
- Retrieval is derived and rechecked against tenant/project authority. Immutable
  artifacts retain hashes and provenance. Accepted baselines survive revisions.
- AI records, product events and traces share IDs while remaining distinct. Logs
  are redacted and detailed records have retention controls.
- Gemini and Meta are the only hosted model providers. Daily allowance remains
  disabled locally at the user's request; project budgets and resource ceilings remain.

## Verification and remaining release work

Local verification covers the backend regression suite, Flutter analysis/tests,
PostgreSQL concurrency and worker recovery, infrastructure validation, and a real
isolated reference CAD plugin producing STEP/STL with independent geometry inspection.
The individual verification records distinguish live work from injected fixtures.

Final results: **441 backend tests passed**, with nine optional external-service
checks skipped; **142 Flutter tests passed**, with three optional checks skipped.
The backend run enabled PostgreSQL tests, including six deterministic consent and
deletion concurrency cases and the worker crash/recovery drill. Ruff, Flutter
analysis, generated transport consistency, Terraform validation, the release web
build and native Dockerfile graph checks passed. Desktop and phone browser checks
verified the real tool selector and durable save. No hosted inference, Modal
training, AWS deployment or physical action was performed during this review.

Remaining material limits:

- PCB circuit correspondence and qualified component/layout templates still block
  complete release. ERC/DRC alone cannot prove the requested circuit exists.
- Renode/peripheral behavior, physical fit/function, manufacturing, purchases,
  printing and shipping are not newly qualified by this review.
- Custom engines require installed, operator-qualified adapters. Arbitrary hosted
  CAD/PCB APIs are not automatically compatible or granted write authority.
- Consent withdrawal after a persisted dispatch claim cannot undo compute already
  authorized externally; the result is fenced and cancellation is retried.
- Unknown billed provider outcomes retain financial holds until reconciliation.
- AWS deployment, account quotas, live identity/integrations, backups and the full
  engineering acceptance journey remain separate deployment/release checks.

See [engineering tool adapters](engineering-tool-plugins.md) for installation,
selection, artifact requirements and qualification rules.
