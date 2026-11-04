# Project deletion and retained history

The unchanged workbench uses `DELETE /api/v1/projects/{id}?expectedRevision=N`.
The replacement now returns HTTP202 after an owner-authorized transaction. An
editor/viewer cannot delete; missing/stale revisions fail. Requests without an
idempotency header use a deterministic actor-scoped key. Replays recheck current
ownership inside the receipt transaction, including after a project is deleted.

Deletion is a tombstone, not a purge. Normal project listing, commands, queries,
streams, source/artifact/release access, memory retrieval and preview leases use
the project authorization gate and cannot access the deleted project. The response
explicitly reports `dataRetained: true`. Source bytes, hashes, accepted releases,
command/effect receipts, workflow history and audit events remain stored. No
restore or permanent-purge UI is introduced by this change.

The transaction advances engineering context and cancels active objectives,
verification jobs, print preparations and external-job observation. Generation
fences invalidate late publication. Existing container identities, effect
uncertainty and capacity/budget reservations remain intact: the existing workers
must reconcile actual compute termination and settlement. Local cancellation does
not claim a supplier, person or printer was cancelled remotely; the response
reports `externalCancellationConfirmed: false`.

External observation locks an external-job row before its project. Deletion takes
existing external rows in deterministic order before the project, rechecks owner
and revision, and aborts if a new external job appeared between those locks. It
never waits for a newly discovered external row while holding the project lock.
This avoids an inversion without modifying the source-bound external adapters.

Verification: nine ordinary lifecycle tests passed. A separate explicit disposable
PostgreSQL run passed all ten, including a concurrent external observer holding
job→project locks while deletion runs. The composed HTTP test verifies the frozen
response status, replay, disappearance, inaccessible artifacts, retained exact
bytes, cancelled work and retained uncertain effects/container identities.
Independent review cleared the corrected lock order. No existing user project
was deleted during implementation; only newly created test fixtures were used.
