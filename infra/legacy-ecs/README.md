# Kleos9 AWS deployment

This Terraform module provisions the us-east-1 deployment. It does not
deploy by running tests. Public DNS may use Route53 or an external provider such
as Cloudflare. An issued ACM certificate covering api.DOMAIN and jobs.DOMAIN is
an input. Use encrypted remote Terraform
state with locking.

> **Status after the backend replacement.** The old DBOS/Conductor/Qdrant
> resources and the old release workflow were removed from this tree. The control
> task definitions in `compute.tf` (commands, `KLEOS_*` runtime variables, ECS
> native dispatch, SQS completion queue) still describe the retired runtime and
> must be rewired to the replacement's `python -m kleos.cli` entry points,
> `KLEOS_*` settings and a hosted native/object-store adapter before any
> `terraform apply`. There is intentionally no automatic deployment workflow
> until that wiring is qualified. The edits here were checked with
> `terraform fmt` and a reference scan only; `terraform validate` could not run
> offline, and nothing was planned or applied.

## Deployment sequence

1. Build the control and native images from `backend/Dockerfile` and the native image Dockerfiles under `backend/src/kleos`. Push them to ECR and
   record immutable digests. For first provisioning, create the ECR repositories
   first or use an existing private ECR repository in the same account.
2. Copy terraform.tfvars.example to terraform.tfvars, replace every placeholder,
   and keep activate_services=false. Run terraform init, terraform plan and review
   the resulting infrastructure before applying. The database has deletion
   protection, encrypted storage, daily backups and a required final snapshot.
3. Run the bootstrap task definition once in the private subnets with the trusted
   security group. Its dedicated role alone can read the RDS admin secret and
   write runtime configuration. It creates the application database and login role.
   Rerunning preserves existing passwords.
   The task prints no credentials. Wait for successful task exit.
4. Update the runtime JSON secret with the direct Meta key
   and, for Modal-hosted Muse Spark Open, KLEOS_MODAL_LLM_URL and KLEOS_MODAL_LLM_KEY
   (empty strings are fine and leave Modal off) without changing other keys. Every key
   listed in runtime_secret_keys must exist in the secret before tasks start. Never put credentials in Terraform variables.
   Set the project and daily allowances; a zero allowance intentionally prevents
   paid generation.
5. Run the control task once with command ["alembic", "upgrade", "head"] using
   private subnets and the trusted security group. Review migration logs.
6. Enable activate_services only after the above steps. Roll out versioned worker
   deployments while old workflows drain. Measure the documented load targets
   before expanding invite limits.
7. Create the first owner. Open the app and use the "Set up Kleos9" form that the
   sign-in page shows while no account exists. Its setup token is the Secrets Manager
   secret named by the setup_token_secret output; read it in the AWS console. After
   the owner exists the form disappears and the token no longer works. The owner then
   uses the Admin page to create invite codes, change roles and reset passwords.
   Accounts use email and password; there is no external identity provider. The
   python -m kleos.cli create-invite and reset-password commands remain as a
   fallback for operators with shell access.

Task definitions reference every runtime secret JSON key at startup. Missing
keys or a missing secret version cause ECS startup to fail before Python runs;
the bootstrap creates all required keys, with empty values for disabled optional
providers/connectors. Preserve these empty fields while editing credentials.
For DigiKey configure KLEOS_DIGIKEY_CLIENT_ID and KLEOS_DIGIKEY_CLIENT_SECRET; for
Modal configure MODAL_TOKEN_ID and MODAL_TOKEN_SECRET; KLEOS_WEBHOOK_SECRETS is a
JSON string initially "{}". Optional integration setup does not grant native
workers these secrets. After changing secrets, force a new ECS deployment so
tasks receive the new values. Do not mark a provider available until qualification
and an actual authenticated call succeed.

The public API rejects internal job routes. Native tasks reach a separate HTTPS
load balancer and authenticate with a job-scoped credential. They run in subnets
with no NAT route and have no AWS task role. VPC endpoints permit ECR/log delivery;
the S3 gateway exists for ECR image layers. Generated processes run under a
different UID from the supervisor and receive no callback or provider credentials.
Tool output is always a candidate; only the authoritative quality service publishes.

All native tasks, including application builds, use a read-only root and a private
writable `/tmp` volume. Application jobs copy their pinned Flutter/pub/Gradle
working set there and scope Java home/temp writes to the job. Generated processes
run as UID 10001 without root or an AWS role. CPU/memory profiles, process limits,
deadlines and global/per-project admission are independently enforced. Local
Docker application jobs use disposable disk scratch; Fargate uses task-local
ephemeral storage. See [native capacity](native-capacity.md),
[network hardening/migration](native-network.md).

PostgreSQL is initially single-AZ unless database_multi_az=true.
One NAT gateway is a deliberate initial cost/availability tradeoff. Add redundancy
before requiring continuous availability.

## Operations and recovery

There is no release workflow for the replacement backend yet; the former
main-branch workflow called deleted scripts and was removed. Terraform owns
provisioning. A new release workflow must build the control image from
`backend/Dockerfile`, run `python -m kleos.cli migrate`, and roll API and worker
task definitions only after the hosted-adapter gaps in the status note are closed.
Native tool images remain separately qualified and pinned through infrastructure inputs.

CloudWatch has 30-day logs and alerts for API errors, stuck completion events,
dead letters, RDS storage and EC2 health. Product alarms fire for a daily allowance
of zero or less, any gate violation or reconciliation failure in a minute, and
engineering queue age above five minutes for two periods. An absent worker
heartbeat for three minutes also alerts after services are activated. Product
metrics and heartbeats use the same Environment dimension as the runtime:
environment must be staging or production (default staging). Demo mode stays on
in either environment. Use separate AWS accounts for distinct deployments with
the same environment label.

The dashboard shows queue age, index/outbox backlog, allowance used/remaining,
unknown and failed checks, pass percentage, repairs, safety events and heartbeats.
Gauges come from authoritative records; the ledger and run inspector retain the
underlying evidence. Tune alarm thresholds from staging measurements. OTLP traces
flow through a local collector into X-Ray.
Confirm SNS email subscriptions. API access logs are intentionally not enabled,
to avoid recording signed URLs and token query parameters.

Restore RDS to a new instance and update runtime secrets before restarting any
workers. Restore S3 artifact versions; accepted artifacts have no automatic
expiration. Redacted transcripts expire after 30 days.
Rebuild derived retrieval from PostgreSQL/S3. Freeze connector dispatch during restore and reconcile durable external
intents before resuming. Test restores and the 100-client/10-project load envelope
in a separate staging account; Terraform validation does not prove runtime scale.

All credentials, provider quotas, engineering tool qualifications and actual AWS
permissions still require deployment-time checks. No cloud resources are created
by the repository's unit tests.

## Cloudflare DNS and the production deployment role

Public DNS can stay on Cloudflare. Set public_zone_id="" to skip public Route53
records (private VPC DNS remains on Route53). Use sibling app and api subdomains
for cookie authentication. Request the backend ACM certificate in the deployment
region for api.DOMAIN and jobs.DOMAIN, and copy its validation CNAMEs into Cloudflare
with DNS only. The frontend certificate is managed by this module in us-east-1.
When provisioning it, inspect ACM's validation records and add them to Cloudflare
while Terraform waits for certificate validation. Keep validation records for renewal.
Point app to frontend_dns_target and api to api_dns_target using DNS-only CNAMEs.
The jobs hostname stays private and requires no public routing record.

The role kleos9-production-github-deploy was created separately in account
750420487076. Before the first full apply, use these inputs:

```hcl
name = "kleos9-production"
environment = "production"
github_environment = "production"
github_oidc_provider_arn = "arn:aws:iam::750420487076:oidc-provider/token.actions.githubusercontent.com"
public_zone_id = ""
```

After initializing encrypted remote state and supplying the remaining deployment
inputs, import the existing role rather than recreating it:

```sh
terraform import aws_iam_role.github_deploy kleos9-production-github-deploy
```

Terraform then attaches the scoped release policy. The role has no deployment
permissions until that policy is applied. Its creation trust policy is recorded in
[github-production-trust.json](iam/github-production-trust.json).
Create the GitHub production environment and restrict its deployment branches to
main before enabling deployment permissions. Set AWS_DEPLOY_ROLE_ARN to
arn:aws:iam::750420487076:role/kleos9-production-github-deploy and configure the other
variables the new release workflow requires. Never create access keys for
this role or place root credentials in GitHub.

Production inputs for kleos9.com are recorded in production.tfvars.example.
The ACM certificate requested for app.kleos9.com also covers api.kleos9.com and
jobs.kleos9.com. After supplying complete inputs and initializing remote state,
import it before planning, so Terraform owns renewal and preserves the certificate:

```sh
terraform import aws_acm_certificate.frontend arn:aws:acm:us-east-1:750420487076:certificate/49bca7a9-d159-4f0f-8756-634ea0e2fedc
```

As of certificate creation, it is pending validation. In Cloudflare add all three
ACM CNAMEs with DNS only (grey cloud), retaining them for certificate renewals:

| Name (within kleos9.com) | Target |
|---|---|
| _d57cfe347a8dc92b7868d95d813c561b.app | _d9934c326fe8f7f7575e0e1abb8e502f.wzccmgtwzk.acm-validations.aws |
| _7bcf1ea31782bb827ce949ca59172bc7.api | _777f22c441d950673f3c86ca2cbc086a.wzccmgtwzk.acm-validations.aws |
| _f455aa18a2187f127431f181a2117185.jobs | _79973871d546feae717e3856c12fe151.wzccmgtwzk.acm-validations.aws |

These validate ownership; they do not route application traffic. Add the app and
api routing CNAMEs only after their AWS endpoints are provisioned.

The production stack uses encrypted S3 state in
kleos9-tfstate-750420487076-us-east-1/kleos9/terraform.tfstate with native S3 lockfiles
(Terraform >=1.10). Public endpoint targets are:

- app.kleos9.com: d2efa1tqsh06nz.cloudfront.net (Cloudflare DNS-only CNAME)
- api.kleos9.com: kleos9-production-api-1206920013.us-east-1.elb.amazonaws.com (DNS-only CNAME)

Trusted private subnets use a separate S3 gateway endpoint that also permits
read access to Amazon Linux package repositories. Native worker subnets retain
the restrictive artifact/ECR endpoint.

Keep enable_github_deploy=false until the production GitHub environment has a
main-only branch policy. Set it to true and apply only after that restriction is
verified; the dedicated OIDC role otherwise has no release policy attached.

## Current production deployment

The kleos9-production stack is provisioned in account 750420487076, us-east-1.
Database bootstrap and Alembic migrations succeeded. The production web bundle
is published to the private frontend bucket. The initial six immutable ECR image
references are recorded in production-images.json. Paid model generation remains disabled with zero project/daily allowances until
provider credentials and approved allowances are configured.

Verified through the AWS load balancer using the api.kleos9.com TLS hostname:
health and database readiness return HTTP 200, first-owner setup is required,
and public internal routes return HTTP 404.
The CloudFront distribution serves the Flutter bundle with HTTP 200.

Public Cloudflare app/api CNAMEs are configured. The GitHub production environment
allows deployments from the main branch only and contains the seven deployment
variables below. The dedicated GitHub OIDC role receives its release permissions
with enable_github_deploy=true after this branch restriction is verified.
AWS_DEPLOY_ROLE_ARN is arn:aws:iam::750420487076:role/kleos9-production-github-deploy;
ECS_CLUSTER is arn:aws:ecs:us-east-1:750420487076:cluster/kleos9-production;
CONTROL_ECR_REPOSITORY is 750420487076.dkr.ecr.us-east-1.amazonaws.com/kleos9-production/control;
API_URL is https://api.kleos9.com; FRONTEND_BUCKET is
kleos9-production-frontend-2d2857147b01fba8c793513c91; FRONTEND_DISTRIBUTION_ID is
E3LRD5F7XEDJ82; AWS_REGION is us-east-1.

After DNS is configured, open https://app.kleos9.com and use Set up Kleos9 for the
first owner. Retrieve its one-time token from the AWS Secrets Manager console:
kleos9-production/setup-token-784e0469580b874a970eaafc44. Do not commit the token.

All three ECS services completed their initial rollout with one healthy replica
each. The final provider-backed Terraform plan reported no changes.

For this repository, github_oidc_subject_prefix is
repo:manuu-r@13869998/kleos9@1438875917. Obtain the exact value using
`gh api repos/manuu-r/kleos9/actions/oidc/customization/sub --jq .sub_claim_prefix`.
Newer GitHub repositories include immutable IDs; name-only trust does not match.

## Hosted simulation

Set simulation_image to a digest from simulation_ecr_repository to enable the
separate simulation ECS service. HTTPS paths /api/state, /api/commands, /api/demo/*
and /api/health route to it; /api/v1 remains the real backend. No DNS changes
are required. The simulator has a dedicated security group, no task role or
runtime secrets, and a read-only filesystem. It can pull images and publish logs
through private AWS endpoints; it cannot reach the production database.

It is an explicitly shared public demo, runs one process/replica, and resets to
seed data on reset or redeployment. GitHub release permissions cover its ECR
repository and ECS service and may pass only the existing no-secret native
execution role for it. The frontend exposes an explicit Simulated demo switch
on New build so future real integrations can replace the reference flow gradually.
