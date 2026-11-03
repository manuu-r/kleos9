# Fresh local control-plane containers

This packaging runs `src/kleos` with PostgreSQL, persistent private artifacts and
separate API, worker and preview processes. It does not include old DBOS/Qdrant
runtime code or cloud deployment adapters. The existing Flutter UI stays unchanged.
The [smoke record](local-runtime-qualification.json) qualifies one built snapshot,
not subsequent working-tree edits or unrestricted frontend cutover.

## Build

From the repository root:

```bash
docker build -t kleos2-control:local -f backend/Dockerfile backend
```

The Dockerfile pins official Python3.12.16 and uv0.8.11 image indexes, installs
runtime dependencies from `uv.lock`, and preserves `/app/src`, `migrations` and
`alembic.ini`. No additional project build backend is needed. Its dedicated context
allowlist excludes preserved `.env`, `.data`, old code and private artifacts.
The first build downloads official layers and lock-hashed packages. Compose never
builds or pulls implicitly. If its pinned database image is not cached, fetch it:

```bash
docker pull postgres:17.6@sha256:00bc86618629af00d2937fdc5a5d63db3ff8450acf52f0636ec813c7f4902929
```

The pins were resolved using [Docker registry inspection](https://docs.docker.com/reference/cli/docker/buildx/imagetools/inspect/);
installation follows [uv's Docker integration](https://docs.astral.sh/uv/guides/integration/docker/).

## Private configuration and first start

Create a new private configuration, preserving the old `backend/.env` and databases.
This example refuses to overwrite an existing file and prints only its path:

```bash
python3 - <<'PY'
import os, secrets
from pathlib import Path
path = Path.home() / '.config/kleos2/local-runtime.env'
path.parent.mkdir(parents=True, exist_ok=True)
password = secrets.token_hex(32)
values = {
    'KLEOS_POSTGRES_PASSWORD': password,
    'KLEOS_DATABASE_URL': f'postgresql+psycopg://kleos:{password}@postgres:5432/kleos2_local',
    'KLEOS_SESSION_SECRET': secrets.token_hex(32),
    'KLEOS_SETUP_TOKEN': secrets.token_hex(32),
}
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, 'w') as stream:
    stream.write(''.join(f'{key}={value}\n' for key, value in values.items()))
print(path)
PY

docker compose --env-file "$HOME/.config/kleos2/local-runtime.env" -f backend/compose.yaml config --quiet
docker compose --env-file "$HOME/.config/kleos2/local-runtime.env" -f backend/compose.yaml up -d --wait postgres
docker compose --env-file "$HOME/.config/kleos2/local-runtime.env" -f backend/compose.yaml --profile setup run --rm migrate
docker compose --env-file "$HOME/.config/kleos2/local-runtime.env" -f backend/compose.yaml up -d api
```

Migration explicitly creates replacement business and graph/store schemas and
rejects a legacy application schema. The API checks migrations instead of silently
creating them. It binds loopback8000; `/api/v1/ready` reports operational readiness
and capability limits. PostgreSQL has no exposed host port. Use the private setup
token in the existing first-owner setup flow and retain the session secret.
Use `config --quiet`: printing resolved Compose configuration exposes its secrets.

## Existing providers and explicit workers

Existing `KLEOS_META_API_KEY`, `KLEOS_MODAL_LLM_KEY` and `KLEOS_MODAL_LLM_URL` work
unchanged. API/worker receive both naming sets, with nonempty new aliases taking
precedence; empty aliases do not hide existing credentials. Explicit model catalog,
text/vision selections and the configured Modal fallback key are forwarded when set;
unset selections remain absent so provider defaults still work. To reuse the preserved
provider file, place `--env-file backend/.env` before the new private file. Only
explicitly mapped keys reach containers; legacy database/auth/execution settings
are not inherited and no environment file enters the image.

Starting the API does not start an objective worker. After selecting credentials
and allowances, explicitly enable it:

```bash
docker compose --env-file backend/.env --env-file "$HOME/.config/kleos2/local-runtime.env" \
  -f backend/compose.yaml --profile worker up -d worker
```

A configured worker can incur model charges for admitted work. It was not started
by the packaging smoke. Compose defaults contain no Docker socket, native/checker/
slicer installation or external commercial provider. Those remain separately
configured and qualified; a running API does not establish ECS/native capability.
The image also supports `fabrication-worker`, `native-maintenance`,
`verification-worker` and `external-worker` entrypoints for those explicit setups.

## Separate preview origin

Set `KLEOS_COMPANION_PREVIEW_ENABLED=true` in the private file, recreate the API,
and explicitly start the preview:

```bash
docker compose --env-file "$HOME/.config/kleos2/local-runtime.env" -f backend/compose.yaml up -d api
docker compose --env-file "$HOME/.config/kleos2/local-runtime.env" -f backend/compose.yaml --profile preview up -d preview
```

Preview defaults to loopback8004, distinct from API8000 and workbench5180. It gets
shared database/session state and read-only artifacts, but no provider credentials
or setup token. It has no API/auth routes and disables capability URL access logs.
Custom published ports must match `KLEOS_API_ORIGIN`/`KLEOS_PREVIEW_ORIGIN`.
Forward proxies must preserve the configured preview host and omit capability URLs
from logs. No generated application code executes in the control container.

## Persistence and limits

Named PostgreSQL/artifact volumes are separate from legacy data. API recreation
preserves sessions, projects and immutable files. Stop this project while keeping
its volumes:

```bash
docker compose --env-file "$HOME/.config/kleos2/local-runtime.env" -f backend/compose.yaml \
  --profile worker --profile preview down
```

Do not remove volumes as a repair shortcut. The private file and stable credentials
are needed to reopen the same installation. Control processes run as UID10001 with
read-only roots, dropped capabilities and bounded temporary storage.

Current `.github` deployment and Terraform still describe deleted cloud runtime
paths. They need a separate rewrite; changing entrypoint strings does not supply
missing S3/ECS adapters or shared cloud storage. This local packaging does not
publish images, modify cloud resources, or qualify arbitrary generated apps.
