# Kleos9 AWS deployment (near-zero idle cost)

This Terraform module provisions a deployment whose idle cost is roughly **$10/month**: storage,
the stopped host's disk and the database's public IPv4. Everything that computes is billed only while it
runs. Nothing here is applied by tests; `terraform test` uses mocked providers.

The previous ECS/ALB/NAT/RDS/Qdrant stack lives in [legacy-ecs/](legacy-ecs/) with its original
state key. It is kept only so the running legacy deployment can be destroyed after cutover.

## What runs where

| Component | AWS | Idle cost |
|---|---|---|
| Workbench (Flutter web) | S3 + CloudFront | ~$0 |
| API (`kleos.cli serve`, including event streams) | Lambda container image + Lambda Web Adapter, Function URL in response-streaming mode, CloudFront for the hostname. Outside any VPC, so it reaches providers without a NAT | $0 |
| Companion previews (`preview-serve`, opt-in) | Separate Lambda, role and hostname; reads only `preview_runtime_parameters` (no provider credentials or setup token) | $0 |
| Workers (`worker`, optional `fabrication-worker`) and every Docker-based tool: native CAD/PCB/firmware/app jobs, checkers, simulation, slicer, MCP | One x86 EC2 **engineering host** with Docker. Started on demand; powers itself off after `host_idle_minutes` idle | ~$4.80 (60 GiB volume) |
| PostgreSQL | Aurora Serverless v2, 0–`database_max_acu` ACU, pauses after `database_auto_pause_seconds`. Publicly reachable, TLS required | ~$4 (IPv4 + storage) |
| Artifacts | S3 (`artifact_bucket`) | cents |
| Native images | S3 archives in `toolchain_bucket` (cheaper than ECR), cached on the host volume | ~$0.70 |
| Control image | ECR (Lambda requires it), last 10 kept | ~$0.05 |
| Settings and credentials | SSM Parameter Store (standard tier) under `/<name>/runtime/` | $0 |
| Spend alerting | AWS Budgets (`monthly_budget_usd`) | $0 |

There is no NAT gateway, load balancer, VPC interface endpoint, customer-managed KMS key or
Qdrant. Running costs are about $0.17/hour for the host (m6a.xlarge), $0.12 per ACU-hour for the database
(0.5 ACU minimum while awake), and Lambda/CloudFront request charges. These are list-price estimates; confirm in Cost Explorer.

Expect cold paths: the first API request after idle waits for Lambda start and the database resume
(~15–20 s); the first job after idle waits for the host to boot (~1–2 min with a warm image cache).
An open workbench tab polls events every second, which keeps the API and database active.

### Host lifecycle

1. The API calls `ec2:StartInstances` on `KLEOS_WAKE_INSTANCE_ID` after queuing work (a no-op if running).
2. On every boot `kleos-host.service` loads `/<name>/runtime/*` from SSM, syncs qualification
   evidence, loads any missing `images/<image-id-hex>.tar.zst` archive, pulls the control image named by
   the `control_image_parameter` and starts each `host_worker_commands` container. Workers use host
   networking (instance credentials under an IMDS hop limit of 1) and hold the Docker socket; native
   jobs keep their own isolation (no network, read-only root, seccomp, UID 10001).
3. `kleos-idle.timer` runs `kleos.cli idle-check` every minute. After `host_idle_minutes` idle results
   the host arms the external-wait tick if waits are outstanding (exit 10), otherwise disarms it, and
   powers off (instance stops; the volume and image cache remain).
4. The tick is an EventBridge Scheduler schedule that starts the host every `external_wait_tick_minutes`
   while armed. Each tick costs roughly one boot plus `host_idle_minutes` of host time.
5. If idle-check is unavailable or failing, the host powers off after `host_max_session_minutes` uptime.

Only the background host stops. The API stays on Lambda, so idle power-off never takes the site down.

The worker shares a kernel with generated code. Native jobs are isolated by the backend's container
policy, not by a separate VPC as in the legacy design.

## Backend contract this infrastructure expects

These are application changes; until they exist the matching function does not work.

| Need | Contract |
|---|---|
| S3 artifact storage | `KLEOS_ARTIFACT_BUCKET` selects an S3 `BlobStore` keyed by content digest. Required: the API and host otherwise see different local disks |
| SSM settings loader | At startup, before `Settings()`: load every parameter under `KLEOS_SSM_RUNTIME_PATH` (or only the names in `KLEOS_SSM_PARAMETERS` when set) into the environment without overriding existing variables; then sync `qualification/` from `KLEOS_TOOLCHAIN_BUCKET` to `/tmp/kleos/qualification` (Lambda). The host does both itself |
| Host wake | After queuing an objective, check, slicing or native job, call `ec2:StartInstances` for `KLEOS_WAKE_INSTANCE_ID` |
| Idle check | `python -m kleos.cli idle-check`: exit 0 idle, 10 idle with external waits outstanding, 1 busy (pending/running objectives, checks, slicing, native or MCP containers) |
| Lambda packaging | `backend/Dockerfile` copies Lambda Web Adapter to `/opt/extensions/lambda-adapter`; boto3 in the locked dependencies |
| Qualification paths | `qualificationReference` values in tool installations point under `/tmp/kleos/qualification/` (same path on Lambda and host) |

## Deployment sequence

1. Build and push the control image to `control_repository`; record its digest.
2. Copy `terraform.tfvars.example` to `terraform.tfvars`. The state key is `kleos9/serverless.tfstate`,
   never the legacy key. Run `terraform init`, `terraform plan`, review, then apply. Add the
   `certificate_validation_records` output to Cloudflare (DNS only) while apply waits.
3. Create the application role/database in Aurora with the admin secret (`database_admin_secret_arn`)
   and write SecureString parameters, for example:
   `aws ssm put-parameter --type SecureString --name /kleos9/runtime/KLEOS_DATABASE_URL --value 'postgresql+psycopg://...?sslmode=require'`.
   Required: `KLEOS_DATABASE_URL`, `KLEOS_SESSION_SECRET` (≥32 chars), `KLEOS_SETUP_TOKEN`. Optional:
   `KLEOS_META_API_KEY`, `KLEOS_MODAL_API_KEY`, `KLEOS_MODAL_BASE_URL`, `KLEOS_NATIVE_TOOLS`, checker,
   slicer, simulation and MCP installations, limits. Values must be single-line.
4. Upload native images: `docker save <image-id> | zstd -T0 -19 > <image-id-hex>.tar.zst`, copy to
   `s3://<toolchain_bucket>/images/`; copy qualification evidence to `s3://<toolchain_bucket>/qualification/`.
5. Migrate from the host (Session Manager):
   `docker run --rm --network host --env-file /run/kleos/runtime.env --env-file /etc/kleos/host.env <control image> migrate`.
6. Set `control_image`, apply, then point `app`, `api` (and `preview`) at the `dns_targets` output
   with DNS-only CNAMEs. CloudFront aliases are global: release them from the legacy distributions first,
   or provision with temporary hostnames and switch.
7. Create the first owner with the setup token.

## Releases (from an operator workstation)

There are no CI/CD workflows; releases run locally with the operator's AWS credentials:

1. Build `backend/Dockerfile` for linux/amd64, push it to `control_repository` and record the digest.
2. Run migrations on the host (step 5 above) when the release adds revisions.
3. `aws lambda update-function-code --function-name <name>-api --image-uri <repo>@<digest>`
   (and `<name>-preview` when enabled); wait with `aws lambda wait function-updated`.
4. `aws ssm put-parameter --overwrite --name <control_image_parameter> --value <repo>@<digest>`. A
   stopped host picks it up on next start; for a running host,
   `aws ssm send-command --document-name AWS-RunShellScript --instance-ids <host_instance_id> --parameters 'commands=["systemctl restart kleos-host.service"]'`.
5. Build the workbench (`flutter build web --release --dart-define=KLEOS_BACKEND=production
   --dart-define=API_BASE_URL=https://<api_hostname>/api/v1`), `aws s3 sync` it to `frontend_bucket`
   (upload `index.html` last with `Cache-Control: no-cache`), then invalidate `frontend_distribution_id`.

## Retiring the legacy stack

After the new stack serves traffic and data is migrated, from `infra/legacy-ecs/` with the original
inputs: `terraform init` (legacy state key), `terraform plan -destroy`, review. RDS has deletion
protection and a required final snapshot; disable protection deliberately before destroying. Delete
`legacy-ecs/` afterwards.

## Checks

```bash
terraform -chdir=infra fmt -check
terraform -chdir=infra init -backend=false
terraform -chdir=infra validate
terraform -chdir=infra test
```
