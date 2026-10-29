# Project Approval Plan: Agentic Text-to-DIY Product Platform

**Project:** Kleos9  
**Original plan date:** 29 October 2026  
**Restored:** 30 October 2026, reconstructed from the finalized conversation after accidental deletion  
**Status:** Proposed — pending management approval  
**Decision requested:** Approve the architecture and phased implementation, starting with a complete smartwatch demonstration using simulated external transactions.  
**Budget, staffing and schedule:** To be agreed before implementation commitment.

## 1. Objective

Build a modular agentic platform that turns a user's plain-language request into a coordinated hardware and software design. The platform validates the design, arranges fabrication and testing through qualified connectors, and delivers a prepared DIY kit with accurate assembly instructions.

The intended customer experience is LEGO-like: enjoyable assembly of labeled, compatible parts and prepared electronics, followed by guided activation. The output is an integrated product, with matching mechanical, electronic and software interfaces, rather than unrelated files.

The fixed product process is:

**Text request → clarification and feasibility research → customer concept confirmation → coordinated engineering → independent digital validation → prototype fabrication and physical testing → prepared DIY kit and software package → delivery, guided assembly and activation.**

Connectors supply engineering tools, components, fabrication, human work, printing and logistics underneath this workflow. They do not replace the guided product with a storefront. Reuse accelerates work; new feasible custom designs remain supported.

Kleos9 supplies default engineering tools and lets developers connect qualified alternatives. Customers with suitable 3D printers may print eligible parts at home; otherwise qualified providers supply them.

The first demonstration follows one scoped smartwatch through the complete journey with real generation and supported simulation where implemented, and explicitly simulated external transactions. A separately authorized physical pilot establishes physical performance and assembly outcomes.

**Success means outputs are traceable to user requirements, compatible across disciplines, supported by appropriate evidence, and understandable to the person assembling the product.** An attractive preview or completed agent conversation is insufficient.

## 2. Product scope

### Target capability

- Clarify requirements in simple language and research feasibility before committing to detailed generation.
- Show concepts, features, candidate components, costs, limitations and relevant application screens; obtain concept confirmation.
- Coordinate mechanical design, electronics, firmware and application development through shared interfaces.
- Generate editable code-defined engineering sources, manufacturing exports, software builds, drawings and assembly instructions.
- Supply default tools for each engineering lane and qualify third-party alternatives through a common contract.
- Independently check generated artifacts, solve encoded interface constraints and run applicable simulations.
- Investigate failures, repair designs and pivot when an approach fails, using bounded context and persistent evidence.
- Connect component sources, fabrication providers, procurement browsers, human-task services and printer drivers.
- Commission qualified people for physical inspection, measurements, specialist judgment and assembly trials.
- Evaluate 3D printing first for custom mechanics; use other manufacturing when requirements justify it.
- Support opted-in customer printers, prepared electronics, labeled hardware and a complete DIY assembly experience.
- Track delivery, customer assembly, activation, updates and revision-linked support.

### Initial demonstration boundary

Use a deliberately scoped smartwatch brief to prove the complete process. Include a custom case, selected electronics, firmware and a companion application only where the brief requires it. Clearly distinguish actual artifacts and supported simulations from mocked supplier responses, human jobs, printer jobs and synthetic physical evidence.

The investor demonstration places no real supplier orders, makes no supplier payments, hires nobody, posts no paid jobs, sends no jobs to real printers and arranges no real shipping. Demo mode is enforced server-side, with separate identities and receipts. Synthetic evidence cannot satisfy a live physical-evidence gate.

### Explicit exclusions

- Guaranteeing that every requested invention is feasible or manufacturable on its first attempt.
- Treating model confidence, simulation or generated video as proof of physical reliability.
- Building new CAD kernels, PCB editors, physics engines or workflow runtimes.
- Requiring nine independently deployed microservices.
- Making Modal, GPU hosting or world models mandatory.
- Granting unqualified plugins unrestricted project access, secrets or spending authority.
- Promising universal secrecy while suppliers, model providers or testers need scoped project information.

## 3. Proposed architecture

Use **Flutter → FastAPI → Pydantic AI + engineering context/memory service + DBOS → PostgreSQL + Qdrant + object storage**, with isolated native workers and scoped connectors.

The nine logical business modules share one initial control application. Worker processes are deployed separately for isolation and resource management without turning each domain into a microservice.

| Building block | Responsibility |
|---|---|
| Flutter | One customer-facing Dart codebase for Android, iOS and browser access; adaptive phone, tablet and desktop layouts |
| FastAPI | Typed APIs, request validation, identity/authorization integration, provider webhooks and live progress delivery |
| Pydantic AI | Typed agent outputs, model adapters and tool use inside durable execution |
| DBOS | Recoverable workflows, checkpoints, durable queues, deadlines and external waits |
| PostgreSQL | Authoritative requirements, interfaces, revisions, dependencies, permissions, accepted evidence, budgets, order intents and task records |
| Qdrant | Derived retrieval index for research, datasheets, engineering observations, previous failures and relevant history |
| Context and memory service | Select bounded context, preserve provenance, distinguish hypotheses from facts, and enforce applicability and authority rules |
| Immutable object storage | CAD/PCB sources and exports, software packages, drawings, images, logs and raw evidence |
| Isolated engineering workers | CAD, PCB, compilers, solvers, rendering, slicing and simulation |
| Connector gateway | Qualified API, MCP, browser, human-task and printer-driver execution with scoped credentials and egress |
| Tool registry | Default and qualified third-party tools, versions, supported lanes and project selection |
| Platform MCP server | Limited entry points for external assistants, such as starting a request and reading its status |

FastAPI persists and enqueues long work, then returns a project/task identifier. Model calls, CAD and simulation do not run as blocking work inside customer request handlers. Progress is persisted and clients can reconnect or change devices.

Flutter produces real Android and iOS applications plus a browser build from the same project. Small platform adapters cover capabilities such as notifications, secure storage or hardware access. Qualify 3D viewing, assembly interactions and file handling across target platforms. Provide lightweight previews where interactive rendering is unsuitable.

Commit authoritative changes and an outbox record together in PostgreSQL. Durable background jobs update Qdrant; retrieved information is checked for permission, revision and applicability before action. Qdrant is rebuildable retrieval state, not commercial or release authority. The context service supplies memory behavior beyond similarity search. Access revocation, deletion propagation and index freshness require integration checks.

Run native tools on server workers, optionally adding qualified local workers. Native FreeCAD/KiCad do not run directly in browser WebContainers. Modal can be added as an interchangeable compute backend; credits are not assumed in the business case.

Deploy DBOS with an explicit recovery arrangement. Multiple executors need coordinated recovery and version compatibility. Use infrastructure-as-code for supported infrastructure and versioned application/worker definitions; keep large artifacts outside workflow checkpoints.

### Engineering representation and tool contract

Prefer code-defined, versioned sources where practical:

- **Mechanics:** build123d or CadQuery, with native CAD exports and assembly metadata. External text-to-CAD tools are candidate generators.
- **Electronics:** atopile or tscircuit where suitable, with qualified KiCad-compatible sources and manufacturing outputs. Autonomous layout is an optional connector.
- **Firmware:** Zephyr where appropriate, supported simulations through Renode or Wokwi, and real-device validation when required.

These are intended tool candidates and defaults to qualify, not a claim that every requested board, peripheral or export is already supported. A solver checks encoded dimensions, pin maps, voltage/current budgets and other constraints; omitted physics and unsupported models remain UNKNOWN.

| Lane | Intended built-in default | Plug-in examples |
|---|---|---|
| Mechanical | Kleos9 Mechanical using build123d | Text-to-CAD tools, enclosure or strap specialists |
| Electronics | Kleos9 Electronics using qualified atopile/KiCad tooling and bounded layout support | Autonomous layout and specialist circuit tools |
| Firmware | Kleos9 Firmware using Zephyr and supported Renode simulation | Board-specific drivers and firmware generators |
| Verification | Kleos9 Validators for rules, fit, power, interfaces and guide coverage | Qualified thermal, structural or specialist checks |

Each tool receives a pinned interface revision and returns persisted candidate files with hashes, provenance and limitations. It has no self-approval operation and passes the same independently controlled acceptance rules.

Tools declare reproducible operations versus generative proposals. Reproducible builds must meet their declared reproducibility contract under pinned inputs and environments. Generative attempts preserve the selected response, attempt ID, inputs and model/tool configuration; repeated prompts are not assumed to yield identical bytes. Every candidate is independently validated.

Unsupported inputs return UNKNOWN. Tools run with scoped artifact access and bounded egress. The manifest records tool/model/schema/policy versions. New tool versions are qualified on the golden briefs; accepted releases retain their original history and new eligibility is checked explicitly.

### Nine logical modules

| # | Module | Ownership |
|---|---|---|
| 1 | Kernel | Permissions, task admission, resource limits, audit, fencing and combined gate enforcement |
| 2 | Requirements and engineering coordination | Clarification, feasibility, concept confirmation, shared interfaces, dependency planning and repair/pivot decisions |
| 3 | Mechanical engineering | CAD, materials, tolerances, manufacturing process and assembly geometry |
| 4 | Electronics | Components, schematic, PCB/wiring, power and electrical interfaces |
| 5 | Software and firmware | Drivers, device features, application, builds, flashing and updates |
| 6 | Quality | Independent criteria, verification, simulation coverage, physical/human evidence and acceptance decisions |
| 7 | Commerce | Quotes, mandates, reservations, purchases, payments and reconciliation |
| 8 | Production and human services | Fabrication, home printing, external jobs, tests, preparation and packing |
| 9 | Delivery and lifecycle | Assembly guide, kit delivery, customer assembly, activation and support |

Module 2 owns requirements, Module 6 quality and Module 7 commercial authority. Module 1 enforces their combined gates. The memory/context service supports these modules without becoming a tenth business domain.

## 4. End-to-end workflow

| Stage | Work | Required output or gate |
|---|---|---|
| 1. Capture intent | Clarify features, fit, appearance, environment, price and assembly preferences | Versioned requirements, assumptions and unknowns |
| 2. Research feasibility | Investigate parts, availability, materials, manufacturing and software together | Coherent proposal with concepts, parts, estimates and limitations |
| 3. Confirm concept | Show the customer the proposed experience and tradeoffs | Recorded confirmation before detailed generation spend |
| 4. Establish interfaces | Agree envelopes, mounts, connectors, power, tolerances, protocols and tests | Shared interface specification and independent acceptance criteria |
| 5. Generate in parallel | Create mechanics, electronics, firmware and application outputs with selected tools | Candidate artifacts with exact inputs, dependencies and provenance |
| 6. Verify and revise | Test actual files, diagnose failures, repair or pivot using bounded context | Accepted artifacts, justified experiment, scoped external task or visible blocker |
| 7. Obtain a prototype | Quote and authorize exact parts/manufacture; optionally use the customer's suitable printer | Prototype-specific checks, reservations, order/print intents |
| 8. Test physically | Inspect, measure, run functional tests and assembly trials | Specimen/revision-linked evidence and targeted rework |
| 9. Prepare the kit | Prepare tested subassemblies, labels, fasteners, software, drawings and guide; reconcile packing | Accepted kit manifest, guide and packing evidence |
| 10. Deliver and activate | Ship, guide assembly and activate | Separate shipped, delivered, assembled and activated records |

Prototype procurement precedes physical evidence that requires that prototype. Final customer-kit release requires the applicable physical tests, guide trial and packing checks. These are separate gates.

## 5. Engineering loop, controlled context and recovery

**Objective:** investigate, test, repair and pivot across mechanics, electronics and software while preserving accepted requirements and evidence. A durable task against a design revision is the unit of work. Conversations remain searchable history; each task receives bounded working context rather than an ever-growing transcript.

Module 2 coordinates; Modules 3–5 propose; Module 6 tests independently; Module 1 enforces authority and budgets. Use the existing DBOS execution and memory interfaces.

### Working loop and diagnosis

**Load current revision → identify an unmet requirement → select context → propose an experiment or change → authorize and persist → execute → independently test → diagnose → accept, repair, pivot, research, wait or block.**

An experiment records its hypothesis, expected observation, affected artifacts, test, resource ceiling and stopping condition. A failure record contains expected versus observed behavior, exact artifacts/test versions, supporting measurements/logs, possible causes and the next useful experiment. Suspected causes stay labeled as hypotheses.

| Observation | Response |
|---|---|
| Temporary tool/provider failure | Bounded retry; reconcile any ambiguous external effect first |
| Local implementation defect | Targeted repair and affected/regression checks |
| Mechanical/electrical/software mismatch | Coordinated interface revision and dependent work |
| Missing evidence or unsupported simulator | Relevant source, alternate test or qualified external task |
| Repeated failure of the approach | Compare materially different component, topology, layout or process alternatives |
| Conflicting/infeasible requirements | Explain evidence and seek an authorized scope decision, or report a blocker |

A pivot records what changes, why it may help, preserved constraints, cost and required evidence. The agent cannot silently weaken requirements or tests. Contradictory validators remain unresolved evidence until investigated.

### Context without losing continuity

Build a versioned context packet containing current requirements/interfaces, exact input artifacts, acceptance criteria, the relevant failure, attempted fixes, unresolved questions, selected source excerpts, tool grants and remaining budget. Preserve its manifest, source hashes/versions and selected excerpts for traceability.

Reserve capacity for mandatory constraints and response/tool work. Retrieve optional information within a task-specific budget, deduplicate repeated text and use the smallest sufficient dependency slice. Never silently truncate required constraints; split an oversized task or produce a source-linked focused extraction.

Retain short exchanges within one bounded investigation when helpful. Refresh at a new task, accepted revision, pivot or context-limit checkpoint. Carry a handoff record of goal, discoveries, rejected approaches, open questions and evidence references. Fetch deeper history deliberately when useful. This preserves continuity while limiting irrelevant accumulated context.

### Memory authority and poisoning controls

| Record class | How it enters a later attempt |
|---|---|
| Accepted requirements/interfaces | Authoritative revisioned records; changes use the appropriate approval |
| Validated evidence | Applicable only to recorded inputs, policy and conditions |
| Candidate/extracted claim | Separate from accepted state; source and applicability checks before promotion |
| Research/supplier assertion | Attributed source, observation time, validity and uncertainty |
| Hypothesis/failed approach | Assumptions and observed outcome, not a current fact |
| Summary/retrieval index | Source-linked navigation aid that can be rebuilt |

Filter retrieval by tenant, project permissions and applicability. Mark historical evidence as historical. Surface conflicting or superseded claims instead of blending them into a confident summary. Memory extraction cannot overwrite accepted engineering facts.

External documents, tool outputs and remembered messages are data. Embedded instructions cannot change policy, grants, tests or spending. Enforce the boundary in code and at the connector gateway; keep secrets out of context. Independent parsing and verification are required for release-critical claims. These controls reduce poisoning risk; model judgment alone is insufficient.

Store concise decisions, experiments and evidence rather than relying on private reasoning traces. Provider scores and printer profiles incorporate validated observations only after applicability checks.

### Progress, pivots and stopping

Progress means resolving a required check, reducing a specific uncertainty or adding relevant evidence. Track remaining critical failures, regressions, useful observations and cost since the last accepted checkpoint. Agent turns and rewritten prose are not progress measures.

Maintain an attempt ledger: input/dependency hashes, normalized design parameters, change category, outcome and failure signature. Detect equivalent ineffective attempts and oscillation between previous designs. Similarity retrieval flags possible repetition; genuinely changed inputs or hypotheses may justify another experiment.

Configure task retry limits, a no-progress window, maximum active alternatives and project-wide time/spend limits. Persist them across restarts, model changes and branches. Creating child tasks cannot reset the allowance. Start with one targeted repair branch, allow a small number of justified alternatives and prune using evidence; exhaustive search is unnecessary for ordinary defects.

When progress stops, choose a new diagnostic experiment, materially different approach, scoped external task, authorized requirements decision or visible blocker. Select escalation by cause and capability; do not automatically post every UNKNOWN to a human. Live actions still require the current mandate.

### Acceptance and recovery

Generators upload candidates. Separate validator identities inspect actual files, hashes, units, interfaces, test results and revision freshness. Evidence states are PASS, FAIL, UNKNOWN, ERROR or policy-justified NOT APPLICABLE. A repair fixes its target and passes applicable regressions. Model critique alone cannot establish engineering acceptance.

A battery substitution invalidates affected geometry, electrical/power assumptions, firmware and instructions. Preserve the last accepted baseline while exploring candidates. Publish a compatible manifest through a guarded transaction; stale completions cannot overwrite it. Reuse unchanged evidence through recorded dependency/applicability checks.

Persist task identity, baseline, context manifest, experiment, attempt, budget and pending invocation before execution; persist observations before the next decision. DBOS recovery reuses recorded steps. Retrieval and model calls are explicit recorded steps, with fresh investigation represented by a new attempt. Worker leases and increasing fencing tokens prevent obsolete publication.

Keep external intents separate from computational retries. Repair, pivot or restart cannot silently repeat purchases, job postings or printer dispatches. Reconcile uncertain effects first. External waits release compute and preserve deadlines. The UI shows the current experiment, last useful observation, next check, remaining allowance and pivot/blocker reason.

## 6. Services, IP protection and external effects

Publish a versioned connector contract: capabilities, schemas, authentication, disclosure requirements, quote validity, pricing, job status, retries, idempotency, cancellation and qualification fixtures. Pin and test the supported MCP protocol version. Long jobs use a durable job ID/webhooks or a supported task extension; they do not hold a model request open.

Customers link accounts in Connections, select project scopes and configure mandates. Resolve only qualified capabilities. API or MCP transport availability alone is not manufacturing or purchasing qualification.

Credentials stay in the server credential broker. Give suppliers, testers and tools only necessary files through scoped, expiring access. A public job contains no confidential design files. Revocation blocks future access; it cannot erase downloaded IP or undo accepted orders.

Before an external commitment, persist its intent, exact revision/cart/job, ceiling and reservation. If the provider succeeds before its receipt is stored, mark the outcome uncertain and reconcile. Use idempotency only where supported; never automatically repeat an ambiguous browser purchase. A restored database cannot rewind external orders. Freeze dispatch and reconcile surviving commitments before recovery resumes writes.

## 7. Connector ecosystem

Kleos9 supplies default engineering tools but relies on qualified connectors for physical capacity, procurement and specialist expertise. The extension system must make integration useful to providers and safe for customers.

| Provider benefit | Platform behavior |
|---|---|
| Relevant demand | Route requirements to matching qualified capabilities |
| Executable jobs | Supply exact revision, files, quantities, acceptance criteria and evidence requirements |
| Visible unmet demand | Publish aggregated capability requests without private project details |
| Performance-based routing | Consider fit, price, lead time, measured quality and delivery performance; no paid ranking |
| Repeat work | Preferred-provider settings for appropriate repeat jobs |
| Useful feedback | Show causes of rework and why a quote/job was selected or rejected |
| Multiple entry points | External assistant requests through the platform MCP server reach the same workflow |

| Connector kind | Examples | Qualification |
|---|---|---|
| Service provider | Print farm, PCB assembler, distributor, test lab, packer, human platform | Contract fixtures and a scoped qualification job |
| Engineering tool | Generator, compiler, layout or verification tool | Golden briefs, declared reproducibility/generative recording and independent validation |
| Printer driver | Vendor/community integration | Virtual fixtures, telemetry/cancellation and real calibration before live use |

Provide one manifest, a CLI and a fake local platform: **init → fixtures → conformance → submit → qualify**. Support hosted portals, REST/OpenAPI and MCP adapters to common business operations. Automatically generated mappings still require capability and security checks.

Fixtures cover lost acknowledgements, duplicate webhooks, expired quotes, units, stale inputs, self-approval attempts, missing evidence, offline devices and calibration offsets as appropriate.

Qualification levels are **Listed → Schema-verified → Qualified → Preferred**. Operation grants widen only with demonstrated capability. Changes to connector versions, scope or tool descriptions trigger appropriate requalification. Isolate connector execution, restrict egress and treat descriptions/output as untrusted data. Provider sandbox jobs remain simulated until live authorization.

## 8. Human tasks

Use RentAHuman or another qualified service behind a replaceable interface, plus a hosted-portal fallback. Platform-specific documented operations must be verified before live integration; unsupported operations remain explicit.

Create a scoped human task when:

1. An UNKNOWN identifies physical presence, qualified judgment or equipment that a human can supply.
2. No qualified automated connector covers the capability and a suitable human service does.
3. Investigation stops progressing and a specific review/measurement can resolve the blocker; retry exhaustion alone does not justify a paid job.
4. The workflow requires a physical assembly trial, fit check or other human-performed procedure.

Other cases follow the repair/pivot/wait/blocker decision rules. Check mandates, qualification and spending before posting.

| Capability | Deliverable |
|---|---|
| `human.assembly_check` | Build following the exact guide; step photos and confusing-step notes |
| `human.fit_check` | Fit components into printed parts; photos and measurements |
| `human.physical_verify` | Inspect a specimen against a revision-specific checklist |
| `human.functional_test` | Execute a procedure and submit readings/logs with units |
| `human.engineering_review` | Qualified review of a documented uncertainty |
| `human.kit_pack` | Pack against the manifest and show labeled contents |

The common interface covers posting/status, applicants, assignment, messaging, evidence receipt, revision requests, acceptance, payment and cancellation. Adapter methods map to actual provider capabilities rather than invented endpoints.

A task packet contains outcome, qualifications/tools, specimen and revision, public description, private files, deadline, fee, acceptance criteria and evidence schema. Share confidential files only after assignment. Verify webhook signatures, persist events and deduplicate before processing.

Lifecycle: **DRAFT → SCOPED → AUTHORIZED → POSTED → APPLICATIONS → ASSIGNED → IN_PROGRESS → EVIDENCE_SUBMITTED → REVIEW → ACCEPTED / REVISION_REQUESTED / DISPUTED / EXPIRED / CANCELLED**. Track settlement separately.

An independent validator accepts evidence or requests rework. Old-revision submissions remain historical. Payment for properly performed work is distinct from a passing product test. Use repeat measurements or a second reviewer where the risk requires it. Unavailable qualified workers produce a truthful wait/blocker, not assumed internal rescue.

## 9. Home printing

Customers connect a printer through a qualified driver. Candidate ecosystems include OctoPrint, Klipper/Moonraker, Bambu LAN and Prusa services, subject to actual API, device and authentication qualification. Other makers and developers may supply drivers.

Connection flow: **identify/authorize → read supported printer profile → calibrate dimensional behavior → set project/material/job permissions**. Record build volume, nozzle, material and calibration applicability. The customer chooses home printing or the offered provider route.

Home eligibility requires compatible dimensions, material and process, a suitable current printer profile, available device state and customer opt-in. Printing first time correctly is a target requiring evidence, not a guarantee.

Job flow: slice the accepted part with a qualified profile → persist and authorize dispatch → send once/reconcile → obtain configured local confirmation → observe telemetry → collect completion and physical evidence → record the specimen against the revision.

Exclude home-produced parts from purchased/shipped quantities, but keep them in the complete assembly manifest with their fulfillment source and status. Screws, inserts and other required hardware still need explicit fulfillment.

Offline/silent is never complete. An uncertain dispatch is reconciled before re-sending. A failed print offers bounded rework or provider fulfillment. Required fit checks still apply; a print is a specimen, not automatic manufacturing qualification.

Use a customer-side connector where local networking is required. Declare vendor/cloud recipients and minimize disclosure. Cancellation uses supported stop/cooling procedures; confirm observed status rather than assuming every driver can guarantee a physical stop. Real dispatch requires the live-pilot authorization and relevant customer consent.

## 10. Optional world models

Expose a replaceable capability for proposing assembly problems, physical interaction hypotheses and additional tests. Cosmos and V-JEPA are evaluation candidates; Genesis is a separate physics-simulation candidate. Qualify versions, licensing, resources and usefulness before adoption.

World-model predictions do not approve CAD, electronics, manufacturing or physical safety. Check hypotheses through exact geometry, applicable simulation and physical evidence. Generate customer assembly visuals from accepted CAD and assembly relationships so they match delivered parts.

Adopt these tools only if controlled evaluation improves useful outcomes within the cost budget. They are optional and do not own release authority.

## 11. Implementation phases and acceptance gates

| Phase | Deliverable | Exit criteria |
|---|---|---|
| A. Foundation | FastAPI nine-module skeleton, Flutter shell, Pydantic AI/DBOS, PostgreSQL, Qdrant retrieval adapter, object storage, identity, task contracts and enforced demo mode | Recovery works; stale/unauthorized writes fail; mobile/browser clients share project state; retrieval obeys scope and freshness |
| B. Digital engineering | Concept confirmation, shared interfaces, default tools/tool contract, native generation, solver checks, independent validation, bounded context, attempt ledger and repair/pivot rules; golden set of 20–30 briefs | Supported briefs yield compatible packages; unsupported cases stop truthfully; component changes invalidate correct work; defects repair, infeasible approaches pivot, repetitions stop/escalate |
| C. Complete demonstration | Mock procurement/humans/printers; connector CLI and sandbox; one selectable alternative tool; live evidence/progress and geometry-derived guide; 3–5 design partners using mocks if available | Complete journey is reviewable, including handoffs and home-print options; synthetic evidence is explicit and cannot unlock live release |
| D. Physical pilot | Qualified live providers, limited human-task integration, one or two opted-in printer ecosystems, third-party tool, funded prototype, physical tests and kit preparation | Applicable tests pass; packing is reconciled; representative customer completes an assembly trial |
| E. Production hardening | Recovery/upgrade drills, isolation, connector conformance, monitoring, quotas and measured scaling | Agreed reliability, security, cost and customer-outcome targets pass on a documented workload |

Phase D requires separate authorization of providers, expenditure, human-task posting, customer-printer dispatch, disclosure and pilot scope. Staffing, dates and budgets are set against the actual brief and provider availability.

## 12. Evaluation and operating metrics

Measure engineering correctness separately from orchestration reliability:

- Requirement coverage, incompatible interfaces and critical false acceptance.
- Stale evidence rejection and preserved constraints after changes.
- Repair success without regressions, justified pivots, repetitions/oscillation and cost since useful progress.
- Context size, retrieval relevance/freshness, unsupported claim promotion and continuity after refresh.
- Recovery after crashes, duplicated completions, interrupted steps and credential revocation.
- Ambiguous external outcomes, automatic duplicate commitments and unreserved spending.
- Cost per accepted revision, failed attempts and physical rework.
- Guide coverage, novice assembly completion and activation outcomes.
- Tenant isolation, permitted disclosure and malicious-content resistance.
- Human-task time/evidence acceptance, provider coverage, quote response and onboarding time.
- Tool acceptance/rework and compliance with declared reproducibility.
- Home-print success, fit acceptance, reprints and observed offline/cancel behavior.

Fixtures include wrong units, incompatible components, battery-height changes, inaccessible fasteners, unsupported peripherals, corrupt files, stale human reviews, lost order acknowledgements, incomplete evidence, broken reproducibility and printers disappearing mid-job. Recorded generative variation is not itself a failure.

Loop/context fixtures include repeating an ineffective repair, oscillating designs, stale datasheets retrieved as current, summary-promoted hypotheses, malicious PDF instructions, irrelevant history crowding out constraints, useful failures lost during refresh, contradictory validators and restart during diagnosis. Each must reach an appropriate action within its budget without silently changing requirements or duplicating effects.

Run golden fixtures for model, prompt, tool or configuration changes. Pin versions and report samples and limitations. Require no stale release, cross-tenant disclosure, automatic duplicate commitment, unreserved spend or synthetic-as-physical evidence in release-blocking fixtures. Agree and measure latency, retrieval quality and total cost before claiming production capacity. Registered users and concurrently active engineering jobs are separate sizing inputs.

## 13. Principal risks and responses

| Risk | Response |
|---|---|
| Plausible but wrong engineering | Independent checks, encoded interfaces, physical evidence and explicit unknowns |
| Cross-discipline divergence | Shared revisioned contracts and dependency invalidation |
| Repeated ineffective repair | Persistent budgets, progress checks, repetition detection and pivot/escalation decisions |
| Poisoned, stale or over-compressed context | Source/authority labels, bounded retrieval, current-state checks and refresh fixtures |
| Ambiguous external commitments | Durable intents, reservations, qualified idempotency and reconciliation |
| Missing expertise/provider failure | Qualification, scoped evidence and replaceable providers with honest blockers |
| Poor human evidence | Fixed schemas, independent review and repeat checks where needed |
| Malicious connector/tool | Isolated execution, limited egress, scoped access and requalification |
| Too few providers | Sandbox onboarding, design partners and an aggregated unmet-demand board |
| Variable home-print quality | Applicable calibration, process eligibility, fit tests and provider fallback |
| Unsafe or unobserved printer state | Customer permissions, supported confirmation/stop controls and observed status |
| IP exposure | Minimum disclosure, access after assignment, tenant isolation and audited credentials |
| Unbounded cost | Bounded loops, reservations, concurrency limits and useful-output metrics |
| Poor DIY experience | Labeled prepared parts, geometry-derived guide and physical assembly trial |
| Infrastructure complexity | One initial control application; scale and specialize from measurements |

## 14. Resources and ownership

Assign owners for product, platform/backend, frontend, mechanical engineering, electronics/firmware, quality, security and connector ecosystem. People may cover multiple roles, but independent acceptance and authority remain explicit. External specialists provide physical work and qualified judgment without requiring an internal operator for every routine project.

Budget separately for servers/database, storage/egress, models, retrieval/embedding, native compute, browser sessions, component/fabrication costs, human work, packing and shipping. Include failed experiments and post-credit operating costs. No date, headcount, throughput or total cost is committed by this plan.

Use versioned infrastructure definitions, dependency/image locks and isolated demo/staging/production identities. Test compatible upgrades, backup restoration, artifact availability and external-commitment reconciliation before live operations. Retain released artifact closures and exact kit lineage for support.

## 15. Management approval requested

- [ ] Approve the fixed text-to-DIY-kit objective and process.
- [ ] Approve Flutter, FastAPI, Pydantic AI, PostgreSQL + DBOS + Qdrant + object storage, nine logical modules, isolated workers and scoped connectors.
- [ ] Approve the bounded engineering loop, context authority, independent repair validation and pivot/stopping policy.
- [ ] Approve Phases A–C with simulated external commitments and physical evidence clearly labeled.
- [ ] Approve replaceable human-task integrations and their mock demonstration.
- [ ] Approve default engineering tools, a qualified plug-in contract and connector developer kit.
- [ ] Approve home printing in mocks, with live drivers limited to authorized, opted-in pilot use.
- [ ] Assign accountable owners and agree staffing, budget, milestones and measurable acceptance targets.
- [ ] Keep a separate approval gate for funded physical work and live external commitments.

**Approver:** ____________________  
**Decision / conditions:** ____________________  
**Date:** ____________________

This restored plan records the agreed design direction. It does not establish that the current repository implements every capability, that simulations demonstrate physical performance, or that capacity and costs have been measured. Qualify actual tools, connectors and model behavior during implementation.
