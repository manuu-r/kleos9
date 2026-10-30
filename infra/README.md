# Kleos9 AWS deployment

This Terraform module provisions the initial us-east-1 deployment. It does not
deploy by running tests. Existing Route53 DNS and an issued ACM certificate
covering api.DOMAIN and jobs.DOMAIN are inputs. Use encrypted remote Terraform
state with locking; generated Qdrant credentials are sensitive state.

## Deployment sequence

1. Build the control and native images using backend/docker. Push them to ECR and
   record immutable digests. For first provisioning, create the ECR repositories
   first or use an existing private ECR repository in the same account.
2. Copy terraform.tfvars.example to terraform.tfvars, replace every placeholder,
   and keep activate_services=false. Run terraform init, terraform plan and review
   the resulting infrastructure before applying. The database has deletion
   protection, encrypted storage, daily backups and a required final snapshot.
3. Run the bootstrap task definition once in the private subnets with the trusted
   security group. Its dedicated role alone can read the RDS admin secret and
   write runtime configuration. It creates separate application, DBOS and
   Conductor databases and login roles. Rerunning preserves existing passwords.
   The task prints no credentials. Wait for successful task exit.
4. Update the runtime JSON secret with direct Meta, Anthropic and Gemini keys
   without changing other keys. Never put credentials in Terraform variables.
   Set the project and daily allowances; a zero allowance intentionally prevents
   paid generation.
5. Run the control task once with command ["alembic", "upgrade", "head"] using
   private subnets and the trusted security group. Review migration logs. Register
   invited Cognito users and application organization memberships.
6. If using Conductor, obtain its production license, set a digest-pinned image
   and operator SSO issuer/audience, and provide its license secret ARN. Set
   activate_conductor=true while leaving activate_services=false. It uses
   its own database role and an internal TLS listener. Register the application
   through the authenticated self-hosted API from a VPC-connected operator
   environment (for example, VPC CloudShell using the trusted security group)
   and store its key in
   KLEOS_CONDUCTOR_KEY. Conductor is optional until license setup; durable DBOS
   execution still uses PostgreSQL. Its web console is not publicly deployed.
7. Enable activate_services only after the above steps. Roll out versioned worker
   deployments while old workflows drain. Measure the documented load targets
   before expanding invite limits.

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

The application tool image needs a writable Flutter SDK cache. Its fresh task has
a writable root filesystem; source code still runs as UID 10001 without root or
an AWS role. Other native tasks use a read-only root and a writable temporary
volume. Native CPU/memory, tool-specific timeouts and global admission limits are
independently enforced.

Qdrant uses a private EC2 instance, encrypted persistent EBS, authenticated HTTP
within the VPC and daily S3 snapshots. It is initially a single node; this is not
an HA deployment. PostgreSQL is initially single-AZ unless database_multi_az=true.
One NAT gateway is a deliberate initial cost/availability tradeoff. Add redundancy
before requiring continuous availability.

## Operations and recovery

Control releases use the manual workflow and version-aware worker drain described
in [DEPLOYMENT.md](../backend/docker/DEPLOYMENT.md). Terraform owns provisioning;
the release workflow owns service task-definition revisions. Native tool images
remain separately qualified and pinned through infrastructure inputs.

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
expiration. Redacted transcripts and Qdrant snapshots expire after 30 days.
Rebuild retrieval from PostgreSQL/S3 or restore a Qdrant snapshot to compatible
storage. Freeze connector dispatch during restore and reconcile durable external
intents before resuming. Test restores and the 100-client/10-project load envelope
in a separate staging account; Terraform validation does not prove runtime scale.

Conductor configuration follows the
[self-hosting documentation](https://docs.dbos.dev/conductor/self-hosting/hosting-conductor).
All credentials, provider quotas, engineering tool qualifications and actual AWS
permissions still require deployment-time checks. No cloud resources are created
by the repository's unit tests.
