# Local Bambu test preparation

The existing small-test UI prepares a fixed 10 × 10 × 2 mm STL tile through the
same durable `PrintPreparation` queue and isolated fabrication worker used for
project meshes. A qualified configured OrcaSlicer installation is required.
A1 mini, A1 and P1S use the existing fixed profiles. The HTTP request never runs
native software and never contacts a printer.

Creation, status and STL/plate downloads require the existing direct-loopback
local administrator boundary. Each tenant/user has one deterministic internal
fixture project, excluded from normal project lists and generic project access.
The dedicated facade checks creator ownership, the fixed source closure and
artifact provenance. No project schema or separate execution lifecycle is added.

Queued/running jobs appear as `slicing`; independently checked digital packages
appear as `ready`; cancellation, stale input and native failures appear as
`failed`. Downloaded STL and 3MF bytes are checked against saved hashes. A ready
package remains a digital candidate: it does not approve the profile, start a
printer or establish physical acceptance. Printer upload/start remains blocked.

The existing UI supplies no idempotency header. Repeated identical requests
therefore reuse the same fixed fixture/profile/image preparation, including a
failed or uncertain result. An explicit new `Idempotency-Key` requests a separate
attempt; at most four local test preparations per principal can remain pending.
The shared worker retains uncertain container identities and capacity until
cleanup is confirmed. Requests do not reset or silently replay such work.

Focused verification uses fixed data and mocked slicer/printer transports:

```sh
cd backend
.venv/bin/pytest tests/test_bambu_test_print.py tests/test_bambu_api.py tests/test_slicing.py -q
```

The test-print API tests include actual Runtime routing, durable worker
completion, private downloads, concurrent first requests, receipt replay and
permission revocation. They perform no Docker, live printer, paid model or
physical action. Existing slicer qualification covers its recorded fixtures;
these API checks do not extend it to physical printing or arbitrary geometry.
