# Claude Code guidance for Kleos9

Read and follow [AGENTS.md](AGENTS.md), the shared repository instructions, and
[README.md](README.md) before making changes. Component READMEs contain the
commands and contracts specific to their folders. Keep this file a short entry
point so it does not drift from the shared guidance.

## Project context

- Flutter is the existing customer workbench. Preserve its UI, routes, simulation
  controls and responsive behavior unless the task explicitly changes them.
- FastAPI, SQLAlchemy/Alembic, Pydantic AI and DBOS implement the control plane.
  PostgreSQL is authoritative; Qdrant is derived retrieval; artifacts are immutable.
- Trusted workers coordinate isolated native CAD, PCB, firmware and companion-app
  jobs. Run generated code only inside those native environments.
- Product inference uses Meta, Gemini and configured Modal endpoints. Assistant
  tooling guidance is separate from the platform's provider registry.
- Generated companion apps are separate from `kleos-ui`. Android delivery is a
  development-signed APK plus web/source packages; iOS delivery is source-only.

## Working expectations

Inspect the current branch and user changes first. Keep fixes focused, preserve
camelCase APIs, add migrations for schema changes, and regenerate OpenAPI/Dart
transport together. Prefer offline fixtures and scoped tests over paid model
generation. Keep credentials server-side and out of logs and generated code.

Generators produce candidates. Independent validation and explicit release gates
own acceptance. Preserve tenant checks, revision fences, durable idempotency,
source hashes, budget accounting and cancellation behavior. Unsupported checks
remain visible as `UNKNOWN`; physical/commercial stages remain simulated.

Before finishing, run the relevant commands in [AGENTS.md](AGENTS.md), inspect
`git diff --check`, and report the actual checks and remaining limits. Do not run
cloud deployment, destructive cleanup, paid probes or GPU jobs outside the
authorized task scope. Do not reset paid-probe guards or change the user's data
merely to make a test pass.

Useful references:

- [Backend setup and API](backend/README.md)
- [Flutter setup and transports](kleos-ui/README.md)
- [Native images and qualification](backend/docker/README.md)
- [Engineering tool plugins](docs/engineering-tool-plugins.md)
- [Native session contract](backend/docs/native-sessions.md)
- [Infrastructure and deployment](infra/README.md)
- [Recent engineering verification](backend/docs/reviews/2026-10-31-native-engineering.md)
