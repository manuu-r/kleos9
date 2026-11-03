# Independent external read-only adapter review

Reviewer: `/root/rewrite_graph_design`, separate from the implementing agent.
Initial review recorded: 2026-11-02 17:16 UTC.

**Final disposition, 2026-11-02 17:24 UTC: corrected implementation passes the
read-only mocked HTTP schema fixture scope below.** All five initial findings
were corrected by the implementation owner and independently retested. This
binds only the final source hashes at the end of this document. It is not live
provider qualification, purchasing/hiring authorization, or physical acceptance.
The initial failing review is retained for audit history.

**Initial disposition: not qualified.** The existing 90 focused tests pass, but
independent bounded probes reproduced the five findings below. An all-PASS
qualification record is not justified for the source hashes recorded here.
The following initial review preceded the corrections and final re-evaluation.

This review covers source inspection and mocked HTTP/database behavior only.
No live DigiKey or RentAHuman account lookup, credential validation, purchase,
hire, message, payment, physical action or device acceptance was performed.
Fixture records whose review fields say PASS are explicitly synthetic test-only
inputs used to test the installation gate; they are not this independent review.

## Reproduced findings

1. **Account changes are not pinned (P1).**
   `infrastructure/external.py:179` hashes principal identity and code/report pins,
   but excludes the account/credential binding. Changing the same principal's
   `accountIdSecret` to another configured reference produces an identical
   installation hash. `ExternalJobs._provider` checks only adapter version/kind.
   Therefore a restored run or existing job can silently use a different provider
   account while appearing to retain its original installation. A nonsecret,
   opaque per-principal installation identity must be pinned in both runs and
   jobs and compared before provider IO. Credential material must not be exposed.

2. **Unrelated accounts invalidate each other's tool pins (P2).**
   The same factory hashes all principals into one global provider installation.
   Adding an unrelated tenant/user account changes the existing provider tool
   installation hash. Existing runs can then fail the capability registry's
   immutable-digest check even though their own adapter/account did not change.
   Keep source/tool identity separate from account identity and select account
   pins for the actual actor. A bounded mocked construction probe reproduced
   `accountReferenceChanged=true, installationHashChanged=false`, followed by
   `unrelatedPrincipalAdded=true, existingToolInstallationHashChanged=true`.

3. **Unbounded decimal formatting before the response bound (P1).**
   `capabilities/sourcing/digikey.py:price` accepts tiny finite values and formats
   them with `f` without bounding exponent/scale. The eight-character input
   `1e-10000` produced 10,002 output characters. A substantially larger negative
   exponent can allocate excessive memory before `bounded_json` runs. The same
   parser handles RentAHuman hourly rates. Bound precision and exponent before
   formatting, and reject malformed/unsupported prices with a redacted error.
   The probe deliberately used a small bounded exponent; no stress/OOM test ran.

4. **Unsuccessful external jobs produce PASS tool evidence (P2).**
   `capabilities/external_tools.py:124` treats `failed`, `cancelled` and `expired`
   as non-waiting, retaining the default observation status PASS and no blockers.
   Actual tool calls against existing fixtures reproduced all three mappings.
   Although the details preserve the job status and `physicalAcceptance=false`,
   the model specialist can finish a task based on this positive observation.
   Preserve unsuccessful outcomes as FAIL/UNKNOWN with explicit blockers rather
   than making them indistinguishable from useful completed work.

5. **Owner-only linking is not rechecked after provider IO (P2).**
   `ExternalJobs.observe_existing` checks owner membership before awaiting the
   ownership lookup; its final transaction checks only write permission. A
   mocked status callback demoted the actor to editor during the lookup, and the
   external link still committed. Recheck owner role in the transaction that
   writes the link. Reproduced output: `roleAtCommit=editor, linkCommitted=true`.

## Verified scope and qualification-check assessment

Command run from `backend`:

```text
.venv/bin/pytest -q tests/test_external_jobs.py tests/test_external_api.py tests/test_external_tools.py tests/test_external_runtime.py
90 passed in 3.25s
```

Independent probes additionally invoked the real price helper, real registered
tool wrapper, real account-installation factory, and real external-link state
transition with mocked provider HTTP/status callbacks. Only temporary fixture
databases/reports were created. No implementation files were changed by this
reviewer.

| Required check | Initial assessment and supporting evidence |
|---|---|
| `principal_scope` | Not sufficient: actor/project tests pass but findings 1, 2 and 5 prevent broad approval. |
| `project_scope` | Scoped reads, path/project mismatch rejection and task artifact bindings verified by `test_scope_and_idempotency_no_release_prerequisite`, `test_project_path_binding_prevents_cross_project_job_or_draft_access`, and `test_principal_project_input_and_provider_version_are_fenced`. |
| `context_fence` | Agent/create/prepare paths enforce current context and exact artifact references. Policy-only revisions preserve the original context. Historical polling intentionally remains possible and is not current engineering acceptance. |
| `credential_redaction` | Explicit server-injected credentials; unknown tool/config fields rejected; known error paths and returned manifests do not expose credentials. This is tested redaction scope, not a claim about every conceivable provider response. |
| `get_only` | Both concrete adapters use fixed-origin GET paths. Redirects are disabled, ambient environment credentials/proxies are disabled, response bodies and elapsed time are bounded. No configured arbitrary URL or executable input is exposed. |
| `no_physical_actions` | Runtime has no effect authorizer and both adapters are read-only. No purchase/hire/message/payment tools are registered. Physical commands are blocked. |
| `provider_errors_fail_closed` | HTTP errors, redirect attempts, oversized/malformed bodies and rejected lookups are covered. Finding 3 prevents a comprehensive bounded-response claim. |
| `durable_observation` | Preparations, polling timestamps and observations persist across service re-creation. Claims precede test-only external effects, and uncertain effects reconcile without re-submission. Finding 4 prevents approval of observation outcome mapping. |
| `exact_sku` | DigiKey exact variation matching, selected SKU and package quantity behavior covered; ambiguous/missing match returns UNKNOWN. |
| `usd_price_breaks` | Normal USD break selection works in mocked fixtures. Finding 3 remains a blocking parser boundary. |
| `bounded_inventory` | Lookup count, variants and breaks are bounded; inventory is validated as nonnegative integer. Response quantity has no explicit semantic upper bound beyond transport/JSON limits, so do not claim an arbitrary business inventory bound. |
| `no_order_api` | No DigiKey order endpoint or order action exists in the registered installation. |
| `profile_allowlist` | Only bounded selected profile fields are retained; private extra fields are dropped in mocked responses. |
| `owned_job_read` | Fixed owner-protected submissions endpoint and `mine=true` listing verified against documented API semantics and mocked requests. Finding 5 blocks approval of the local owner-only linking transition. |
| `poll_waits` | Durable next-poll time coalesces calls, provider failures retain old observations, unknown submissions remain waiting, and no-change polls do not call a model. |
| `no_hire_or_messages` | Concrete human adapter and tool registry expose no hiring, messaging, funding or posting operation. |

The gate itself rejects missing/malformed/symlinked/oversized reports, changed
source hashes, absent review binding, failed required checks, live-access claims
and test-only records outside the test environment. Those checks validate report
structure and binding; they cannot establish that a reviewer actually performed
the claimed checks. This initial review deliberately withheld overall qualification.

## Scope caveats

`ExternalJobs` deliberately uses a job creator's provider account for shared
project operations; an existing test explicitly exercises editor-triggered
operations with creator credentials. Agent tools are stricter and require the
job creator to equal the current actor. Project membership therefore is not a
general guarantee that every API call uses the caller's personal account. The
intended delegation rule remains explicit: the corrected link transition requires
the actor to be both owner and original job creator, including after provider IO.
It cannot link another member's draft.

`ReadOnlyHTTP` is an internal fixed-origin helper; its `path` argument itself is
not an origin allowlist. Qualification here covers the two concrete adapters'
constructed paths, not exposing that helper as an arbitrary HTTP capability.
No real upstream authorization or availability was tested.

Official documentation was consulted read-only. RentAHuman documents
`mine=true` as authenticated owner listing and the submissions-list route as
owner-protected; it also distinguishes submission approval from payment and
completion. See [official REST documentation](https://rentahuman.ai/docs).
DigiKey's [Product Information V4 documentation](https://developer.digikey.com/products/product-information-v4)
provides the product/variation catalog contract. Documentation agreement does not
establish that these particular credentials or live endpoints work.

## Initial source bindings

Manifest protocol: `kleos-external-readonly-qualification-v1`.
Scope: `read-only mocked HTTP schema fixtures`. `liveAccessVerified: false`.

| Module | SHA-256 |
|---|---|
| `kleos.capabilities.external` | `c04e16730c7879c509ebb6bd720d870372a649f8b1c2576f38ab20d165b28210` |
| `kleos.capabilities.external_tools` | `e6800f661878b3dbdecdab1cbad518b5bad8d455289107c43361539ef148731b` |
| `kleos.api.external` | `0d073f7b755c2b3fc9b6e8798581552989b5bf0d4c76b1e2a1b4d746d170ee11` |
| `kleos.capabilities.sourcing.digikey` | `d762ae94d126cae1a3c169f8d2b443d1378117bf6603fae9beb94a878dc4335d` |
| `kleos.capabilities.human_work.rentahuman` | `ce3d4845b161515b2f9252d26dce4a91d75a97ac3946dc707d672db84667ddeb` |
| `kleos.infrastructure.external` | `2748f27466de5cb6bdd2585f4ca4476c4e5a3111c1f1f624d91e04cc102c8318` |

DigiKey aggregate source hash:
`d31a18b92df70ed9f9d28e2f556e63db4fb87a19b6834022629e1f4263da325c`.
RentAHuman aggregate source hash:
`49e9db66fe2a075e630c0fa38b69fcae202b5ea985f527ffbe8ea1082ba3e8e1`.

These hashes describe the reviewed failing baseline, not subsequent corrections.


## Final independent re-evaluation

The reviewer inspected the corrected six modules and root runtime integration,
then independently ran:

```text
.venv/bin/pytest -q tests/test_external_jobs.py tests/test_external_api.py tests/test_external_tools.py tests/test_external_runtime.py
104 passed in 3.82s
```

Ruff passed for the six reviewed modules and four test files. Root runtime uses
`pins_for(scope.actor)` and `tool_keys_for(scope.actor)`, and checks the original
account pin when restoring a run. Global tool pins cover code identity;
per-principal HMAC pins cover code/report/version, principal identity, credential
reference names and their bound values. A persistent server signing key is
required. Only opaque hashes are returned; credential material is not exposed.

The reviewer separately repeated the original bounded probes against real
implementation functions and temporary SQLite fixtures, observing:

```text
decimal 1e-10000 rejected
decimal 0e-999999999 rejected
outcome failed FAIL blocked
outcome cancelled UNKNOWN blocked
outcome expired UNKNOWN blocked
owner demotion rejected before link commit
account change fenced; unrelated account preserves original pins
```

The suite additionally covers credential-reference, credential-value, account-ID
and server-key changes fencing old runs and jobs before IO; missing run pins;
account changes during lookup; and unrelated account additions preserving a
previously pinned tool snapshot and usable job. All HTTP remained mocked.

| Required check | Final result within the stated scope |
|---|---|
| `principal_scope` | PASS: actor-specific pins, job-creator account binding, missing/changed-pin rejection and owner/creator link checks before IO and at commit. |
| `project_scope` | PASS: project/path/membership and exact task artifact bindings; no cross-project draft/job access. |
| `context_fence` | PASS: current engineering context on create/prepare/tool paths; historical reconciliation remains observational only. |
| `credential_redaction` | PASS: reviewed configuration, manifest, errors and mocked return values omit credentials and references. |
| `get_only` | PASS: fixed concrete adapter origins/paths, GET only, no redirects or ambient credential/proxy use; bounded response/time. |
| `no_physical_actions` | PASS: no production effect authorizer or purchase/hire/payment/physical tools. |
| `provider_errors_fail_closed` | PASS: HTTP/shape/size/redirect/error cases fail closed; decimal exponent, precision and encoded length bounded before formatting. |
| `durable_observation` | PASS: durable preparations/poll state; unsuccessful observations preserve FAIL/UNKNOWN and blockers; test-only uncertain effects reconcile without repetition. |
| `exact_sku` | PASS: exact requested variation required; ambiguous or missing results remain UNKNOWN. |
| `usd_price_breaks` | PASS: explicit USD locale, bounded price parsing and supported quantity break selection. |
| `bounded_inventory` | PASS for bounded lookup/record counts, bounded response bytes and nonnegative integer typing. No arbitrary business upper inventory limit is claimed. |
| `no_order_api` | PASS: no order API implementation or registered action. |
| `profile_allowlist` | PASS: only bounded selected profile fields retained. |
| `owned_job_read` | PASS for mocked owner-protected route and local owner/creator checks including membership changes during lookup. Upstream enforcement was not live-tested. |
| `poll_waits` | PASS: durable next-poll coalescing, retained observations on provider error, waiting unknown states, no model invocation. |
| `no_hire_or_messages` | PASS: no posting, funding, hiring or messaging API/tool. |

The concrete adapters can receive a source-bound qualification record for
`read-only mocked HTTP schema fixtures` only, with `liveAccessVerified: false`,
`sourceOverride: false`, and the limitations above. Synthetic qualification inputs
remain test fixtures; this review does not convert them into live evidence.
A gate checks structure and source identity, not the truth of an arbitrary
operator-authored attestation.

### Final source bindings

| Module | SHA-256 |
|---|---|
| `kleos.capabilities.external` | `5b3712fec908aa8a180f17af2ed446e9f8bb6c7f20e3f4b8ffff100cb75807d1` |
| `kleos.capabilities.external_tools` | `858f994a5cfec53ab83e12e820aaedbbb6a192794e20b60a052acb1d5244f2a2` |
| `kleos.api.external` | `0d073f7b755c2b3fc9b6e8798581552989b5bf0d4c76b1e2a1b4d746d170ee11` |
| `kleos.capabilities.sourcing.digikey` | `9f41adc4b07696b9252d540be289d95dfe3ca5203f136435f25720a0cb0c366e` |
| `kleos.capabilities.human_work.rentahuman` | `ce3d4845b161515b2f9252d26dce4a91d75a97ac3946dc707d672db84667ddeb` |
| `kleos.infrastructure.external` | `fe1b1259da98b232eeb2b1cc55ef49eea176fc6c60dde39319c5809643bc21b3` |

DigiKey aggregate source hash:
`b0729b3f737e7a6c2a3bf51ccf5f4442d39cf1a6a91fa6be4b346aa47908e75b`.
RentAHuman aggregate source hash:
`1b43eddd507af36c773b8bc3bb5e683017315eae3df2ab46b9e1a535e6aa0045`.

No implementation files, live accounts or external services were changed by this
reviewer. The only authored repository artifact for this review is this document.
