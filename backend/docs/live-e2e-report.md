# Live end-to-end report — real browser, real models, real native containers

Date: 3 November 2026 (09:30–10:55 UTC). Branch `feature/ngspice-simulation`. Run by an automated agent in a
disposable cloud container (4 vCPU, 15 GB RAM, Docker Engine 29.9.2, cgroup v1, PostgreSQL 16, Flutter 3.47.8,
Playwright Chromium). No mock model and no fabricated result was used. No cloud, Terraform, AWS, purchasing or
physical action was touched; `KLEOS_PHYSICAL_ACTIONS_ENABLED=false` throughout. No secret was printed or stored.

## Verdict

**Generation does not work end to end with the credentials and models available in this run.** The platform,
UI, database, queueing, native Docker lanes and cost accounting are real and mostly behave as designed, but
no scenario produced a CAD, PCB, firmware or app artifact from a model-driven run. The first blockers are:

1. the **Meta API key is rejected** by `api.meta.ai` (HTTP 401 `invalid_api_key`), so the default provider cannot be used;
2. the **Modal model (Muse-Spark-Open-816B-A42B) does not follow the specialist action contract**: in 6 of 6
   specialist attempts it returned `{"action":"tool","tool":null,...}`; the specialist treats that as a fatal
   `tool_unavailable` and the objective is permanently blocked;
3. the planner also emitted **directory-style `allowed_paths`** (`"cad/bracket_plate/"`, `"pcb/"`) that the native
   gateway rejects, and occasionally an unavailable capability or an unparsable concept (`invalid_geometry`).

| # | Scenario | Result | One-line evidence |
|---|---|---|---|
| A | Model reachability | **PARTIAL** | Providers page shows Meta and Modal "Connected" (key presence only). Modal answers (37 real attempts). Meta returns 401. |
| B | Simple part (bracket plate) | **FAIL** | Capture + concept preview worked; planning worked; every CAD specialist attempt blocked on `tool: null`. No STEP/STL, no preview, nothing to measure. |
| C | USB LED indicator, 2-layer PCB | **PARTIAL** | Capture, a concrete circuit interface proposal (USB-C, AMS1117-3.3, 0603 R/LED) and the explicit-approval gate worked; PCB generation blocked on `tool: null`. No board, no ERC/DRC. |
| D | Companion app / firmware | **NOT RUN** | Plan said only if A–C went well. |
| E | Cancel and restart | **PARTIAL** | Cancel from the UI: PASS. Graceful worker restart: resumed from the journal without a duplicate call. Hard `kill -9` mid model call: run blocks `effect_uncertain`, no duplicate spend, no automatic resume. |
| — | Native lanes alone (no model) | **PASS** | All four lanes built from `toolchains/Dockerfile` and re-qualified `qualified=true` on this Linux engine; `/ready` lists `application, cad, firmware, pcb`. |

"PASS" for the native lanes means the committed qualification procedure ran against freshly built images in
isolated containers. It is **not** a model-driven generation result and does not count as one.

## Spend

Ceilings were never approached: per-objective cap $5 / 100 calls (largest objective: a few cents metered),
session budget $15.

| Measure | Value |
|---|---|
| Model attempts (`kleos2_model_attempts`) | 43 (Modal 37, Meta 6) |
| Total committed (sum of `committedMicroUsd`) | **$2.1378** |
| of which Meta attempts, all rejected with 401 | $1.2964 (3 capture at $0.1987 + 3 unplanned background calls at $0.2335) |
| of which other full-reservation commitments (one cancelled capture, one crashed capture) | $0.3973 |
| of which metered Modal usage (35 of the 37 Modal attempts, provider-reported tokens) | ≈ $0.44 |

Committed figures are the platform's own accounting, which settles an attempt with no provider usage at its full
worst-case reservation (see D2); real money is lower than the committed total, but the table is the authority the
task asked to track. Wall-clock: environment setup and image builds ≈ 40 min (the four toolchains and four native
images built in ≈ 15 min here), qualification ≈ 10 min, scenarios ≈ 70 min.

## Environment and native images (what was actually done)

* Backend: `uv sync --frozen`; PostgreSQL 16 needed `apt-get update` before `apt-get install postgresql`; fresh DB
  `kleos_live`; `kleos.cli migrate` clean. API and worker run detached with the settings listed in the task.
* Docker: `dockerd --iptables=false --ip6tables=false --bridge=none`; all builds with `--network host` plus the
  proxy CA build context; native images with `--network none`.
* Images and fresh strict-Linux qualification (`qualify_cad|pcb|firmware|web --strict-linux`), all `qualified=true`.
  Rebuilt images have new IDs, so the committed `*-linux-strict-2026-11-03.json` records were not used; the new
  records live in a scratch directory and are **not** committed:

| Lane | Native image (this run) | Result |
|---|---|---|
| CAD | `sha256:e95f56ec010f…` | qualified |
| PCB | `sha256:48d9e95e2948…` | qualified |
| Firmware | `sha256:93c86e5e532c…` | qualified |
| Web (Flutter) | `sha256:e22883ce4a73…` | qualified |

  Caveat learned the hard way: re-running a `docker build` for the same tag changes the image ID, which silently
  invalidates a qualification record and a pinned `KLEOS_NATIVE_TOOLS` entry. The first CAD and PCB records were
  redone after my build script re-tagged the images.
* Frontend built with `flutter build web --dart-define=KLEOS_BACKEND=production --dart-define=API_BASE_URL=http://127.0.0.1:8000/api/v1`
  and served on `localhost:5180`. Driven through the accessibility tree with `tests/browser/e2e_lib.js`.
* Because the Meta key is invalid, the server default was switched with the documented `KLEOS_MODEL_PROVIDER=modal`
  (the Providers UI "Use … for all lanes" + "Save model choices" also works, but the saved defaults appear to be
  per-browser state, so a fresh browser reverted to Meta; see D12). Modal is therefore the only provider that
  produced results.

## Scenario A — model reachability

* UI: Providers → AI models lists **Muse (muse-spark-1.3, Connected)** and **Muse on Modal
  (meta-models/Muse-Spark-Open-816B-A42B, Connected)** and a per-lane picker; default policy for every lane
  (coordination, research, mechanical, electronics, firmware, companion app, review) was Meta. `GET /api/v1/model-catalog`
  agrees. "Connected" only means a key is configured.
* Real reachability: Meta `POST /v1/chat/completions` → **401 `invalid_api_key`**. Modal `POST <base>/v1/chat/completions`
  → 200 (the backend appends `/v1` to `KLEOS_MODAL_BASE_URL`; the bare base path returns 404, which is expected).
* After the switch every lane used Modal.

## Scenario B — "a 40 x 20 x 3 mm mounting bracket plate with two 4.4 mm holes"

UI states reached (screenshots listed below): New build → text box → Start build (202, mode `real`, "Real
engineering" badge) → capture → live concept with a rotatable 3-D preview ("1 concept parts", exploded view toggle)
→ clarification questions (one required: hole spacing; answered 30 mm) → "The concept is ready for your review" →
"Confirm and start design" → objective run.

Result: **FAIL at generation**. Five builds were started over the session; the same sentence behaved differently each time.

| Build | Outcome | Cause |
|---|---|---|
| 5ac8191c, f320088e, 24d62dd7 | capture blocked `model_provider_error` | Meta 401 (before the default was switched) |
| 6d7a4fd6 | objective blocked `tool_unavailable` after a good plan (1 CAD task, correct 40×20×3, holes at ±15 mm) | specialist action was `{"tool": "", "arguments": {"actionId": …, "tool": "cad.analyze"}}`; retry replays the journaled action and blocks again |
| 6aa5ee9b | capture blocked `invalid_geometry` | concept output failed schema validation; effect left `claimed`, retry refused `effect_uncertain` |
| 75722797 | objective blocked `tool_unavailable` | planner returned a task whose capability was not in `{application, cad, firmware, pcb, research}` |
| 590e8e6b, 29d844d0 | objective blocked `tool_unavailable` | specialist `{"tool": null, "arguments": {"actionId": …}}` |

A local, **uncommitted** diagnostic shim (normalising `toolName`/`tool` nested in `arguments`) was tried once on
590e8e6b to see what came next: the run then failed in `authorize` with a bare `ValueError('Unsafe source path')`
because the planned `allowed_paths` were `["cad/bracket_plate/", "cad/bracket_plate/STEP/", …]` and one extra task
had `["validation/bracket_plate/"]`. The shim and all debug prints were reverted; `git status` was clean before this report.

Not achieved: CAD files in the UI, STEP/STL fetch, bounding box / volume (expected ≈ 40×20×3 mm, ≈ 2309 mm³),
CAD preview. No native CAD job was ever started by a run (`kleos2_native_jobs` has 0 rows; `docker ps` empty).

## Scenario C — "a USB-powered LED indicator with a 3.3 V regulator and one resistor-limited LED on a 2-layer board"

* Build b392f5bc: capture worked after one planning message (supplied through the `commands` endpoint, the same call
  the chat box makes). Concept: "USB-Powered LED Indicator – Bare Board", 5 parts (PCB, USB-C connector, LDO, resistor, LED).
* Confirm → `project.solveInterfaces` **succeeded**: a concrete circuit proposal (status `pending`): GCT USB4105 USB-C
  receptacle with 5.1 kΩ CC pull-downs, AMS1117-3.3, 120 Ω 0603 resistor, green 0603 LED, 40×20 mm 2-layer board, no MCU.
  This is the best evidence in the run that the model can produce useful engineering content.
* Explicit approval: clicking **Generate engineering files** returned `409 approval_required` ("Review or revise the
  pending interface proposal…") and the UI showed nothing. The proposal appears only as raw JSON text in the chat
  pane, and the UI has no approve control (`project.approveInterfaces` is never sent by Flutter). Approval worked
  only by typing the exact phrase `approve interfaces` in the chat; the proposal then became `approved`, stage 3.
  The server-side gate itself is sound.
* Generate (202): 3 PCB tasks planned, all blocked `tool_unavailable` — specialist `{"tool": null, "arguments":
  {"tool": "pcb.read", …}}`, and `allowed_paths` of `["pcb/", "ecad/", "hardware/", "electronics/"]`.
* Not achieved: routed KiCad board, ERC/DRC reports, PCB preview, Power-on panel.

## Scenario D — not run

Per the plan, companion-app and firmware generation were skipped because the specialist stage failed in A–C.
(Compile-only firmware and the Flutter web bundle were exercised only by their qualification fixtures, without a model.)

## Scenario E — cancellation and restart

* **Cancel (UI "Cancel run" while the capture was running):** run → `cancelled`, project `cancelled`, no concept
  published, no containers, no later attempts. The in-flight attempt was settled at its full reservation ($0.1987;
  UI shows "reserved", spent 0.0). PASS.
* **Graceful restart (SIGTERM) shortly after the run started:** the in-flight call finished, the restarted worker
  completed the run (`succeeded`) and only one capture attempt exists; journal entries `plan:0`, `capture:design:model`
  completed, `plan:1`, `memory:final` completed after restart. No duplicated work. PASS for this timing.
* **Hard crash (`kill -9` ~1 s after the model call started):** the restarted worker did not re-issue the call; the
  run became `blocked effect_uncertain`, the effect stays `claimed`, the attempt stays at its full reservation.
  Safe against double-spend, but it does **not** resume on its own and the API refuses `project.retry` for it with
  `effect_uncertain`. A new chat message is the only recovery found. PARTIAL (not tested: a crash mid native job,
  which no run reached).

## Defects and findings

Root causes are from code reading plus the journaled effects; model outputs are quoted only where retained.

| ID | Severity | Finding | Root cause / evidence |
|---|---|---|---|
| D1 | blocker (credential) | Meta key rejected | `api.meta.ai` 401 `invalid_api_key`. Needs a valid `KLEOS_META_API_KEY`. |
| D2 | medium | Rejected / cancelled / crashed requests are charged at full worst-case reservation | Attempts with no provider usage settle at `reservedMicroUsd` ($0.1987 capture, $0.2335 background). Six Meta 401s cost $1.30 against the budget although nothing was served. Consider settling a 4xx rejection at 0. |
| D3 | medium | Background memory calls ignored the project's Modal policy | Three Meta attempts with empty `taskId` ($0.2335 each) appeared while projects were on Modal (09:45–09:48), before the server default was switched. Consistent with LangMem/background calls using the platform default provider. Not proven by a trace. |
| D4 | **high** | One malformed specialist action wedges the whole objective | `Action.tool` is `str \| None`; the Modal model returns `null`/`""` (6/6). `specialist.py` raises `DomainError('Model requested an unavailable tool','tool_unavailable')` for it. The model response is journaled by `run_once`, so `project.retry` replays the same action. `tests/test_orchestration.py::test_specialist_cannot_finish_from_prose_or_model_invented_tools` asserts the hard failure, so I did **not** change it (see decisions). |
| D5 | **high** | Planner `allowed_paths` are not validated at plan time | Directory-style paths reach `ExecutionGateway.authorize` → `source_path()` → bare `ValueError('Unsafe source path')`. |
| D6 | medium | Opaque failures | `worker.py` stores only stable codes and logs nothing; a non-`DomainError` becomes `execution_error` with an empty log. I needed temporary tracebacks to find D5. Suggest logging exception type and a redacted frame summary. |
| D7 | medium | Concept schema failures are mislabeled and unrecoverable | Any `ValidationError` in `DesignRunner` becomes `invalid_geometry`; the effect stays `claimed`; `project.retry` → `effect_uncertain`. 3 of 17 Modal capture attempts hit this. The offending output is not retained. |
| D8 | medium (UI) | Blocked objective leaves the UI stuck | Project status stays `queued` with run `blocked`, so the UI shows "queued", spinners and only "Cancel run"; Cancel returns `409 run_conflict` ("Select one active objective"). The retry button exists only for a blocked capture. |
| D9 | medium (UI) | Interface proposals cannot be approved from the UI | No approve control; the 409 `approval_required` is silent; the proposal is shown as raw JSON in the chat; approval requires the undocumented exact text `approve interfaces`. |
| D10 | low | Run inspector for a failed capture/objective shows only "Research succeeded" | Screenshot `061006-flow-before-confirm.png`; failures are not visible there. |
| D11 | info | Image IDs change per build | A rebuild invalidates qualification evidence and pins; documented above. |
| D12 | low | "New build defaults" appear browser-local | A fresh browser context reverted to Meta after "Saved for new builds" was shown. |
| D13 | low | Concept preview geometry | In 6d7a4fd6 the second hole renders as a notch at the plate edge in the 3-D preview (preview only; the stated numbers were correct). |

## Decisions needed from the user

1. **Provide a valid Meta key** (or confirm Modal-only operation). Without it, the default provider cannot run.
2. **D4/D5:** how should a malformed specialist action or plan be handled — fail the objective (current, tested) or
   feed a rejection back to the model for a bounded number of turns? I left the fail-closed test untouched. A schema
   that makes `tool` required when `action == "tool"`, a stronger prompt, and plan-time `allowed_paths` validation
   with a repair turn are the obvious candidates; I did not make any of them.
3. Whether the UI should get a real "Approve interfaces" control (D9) and a retry path for a blocked objective (D8).
4. Whether to commit the fresh qualification records; I did not (they name this container's image IDs).

## Remaining limits

* No model-driven artifact exists, so STEP/STL measurement, CAD/PCB previews, ERC/DRC, firmware, companion app and
  the Power-on simulation panel are unverified here. Independent checker and simulation images were not configured
  (checks would stay `UNKNOWN`).
* One model (Modal Muse-Spark-Open) was exercised for all lanes; results say nothing about Meta's model.
* Stochastic: the same sentence passed and failed different stages across attempts; counts above are small samples.
* Docker Desktop paths, cgroup v2, IPv6 and ECS were not tested.

## Artifacts

Screenshots (not committed; ≈ 8.5 MB total in the container's scratch directory, 58 files), representative names:
`00-home.png`, `708568-ai-models.png`, `727119-newbuild.png`, `756150-s1-start.png`, `143251-s1-capture.png`,
`239153-s1-after-answer.png`, `336596-s1-ready.png`, `609059-s1-blocked.png`, `875353-flow-capture.png`,
`320488-s2-interfaces.png`, `360428-s2-generate-click.png`, `394035-s2-approve-typed.png`, `429408-s2-generate.png`,
`738328-cancel-running.png`, `744079-cancel-after.png`, `805591-restart-running.png`.
Project IDs: B `6d7a4fd6-277c-4b23-bd60-a6b5655f2187`, `75722797-2bff-404e-a0da-ecb0d3701243`,
`590e8e6b-bc61-4b64-9895-b57411243dd6`, `29d844d0-7d5c-4b11-b537-f0f39a211306`; C `b392f5bc-457d-4992-a773-cadbb5b51ed9`;
E `e4e5244b-d6a7-422a-b185-3572e202acc8` (cancel), `bfd65cbd-44bc-4ad1-8db8-9095f7f58167` (restart),
`f2b30935-9eb4-4820-bb97-7b8043a9bb78` (crash). Those projects exist only in the disposable database.
