# AWS releases

The GitHub workflow deploy.yml deploys the production environment on pushes to main,
including merged pull requests. It also supports manual runs from main. It runs the
full reusable CI workflow before releasing. Terraform and initial database bootstrap
remain operator steps. Infrastructure changes require a reviewed Terraform apply;
qualified native tool images remain pinned and follow their qualification procedure.

Create a GitHub deployment environment with these variables:

| Variable | Value |
|---|---|
| AWS_DEPLOY_ROLE_ARN | Dedicated deployment role assumed through GitHub OIDC |
| AWS_REGION | us-east-1 initially |
| ECS_CLUSTER | Provisioned cluster ARN |
| CONTROL_ECR_REPOSITORY | Full private ECR control repository URI |
| API_URL | Public HTTPS API origin |
| FRONTEND_BUCKET | Terraform frontend_bucket output |
| FRONTEND_DISTRIBUTION_ID | Terraform frontend_distribution_id output |

Terraform creates the dedicated github_deploy role with OIDC trust restricted to
github_repository and github_environment. If the account already has the GitHub
OIDC provider, set github_oidc_provider_arn to reuse it. Configure the GitHub
environment to allow only main deployments before enabling the role; environment
OIDC subjects do not include the branch. Use production for automatic releases.
No AWS access keys are stored in GitHub. Restrict repository write access and protect
main with required CI checks. For unattended releases, do not require manual
environment approval. The deployment identity
needs push access only to the control ECR repository, ECS describe/register/run/
update/stop permissions for the environment, and PassRole only for its trusted
control and execution roles. It does not need provider API keys, application
database passwords, or permission to read runtime Secrets Manager values. Those
are injected into ECS tasks by the existing execution role.

Complete infra/README.md bootstrap, runtime secret configuration, DNS/TLS and
Conductor setup first. Only the first release of services at zero replicas uses
the workflow's initial_deployment option. Existing services must be stable. Run
the workflow for the reviewed Git commit; a full commit SHA becomes the DBOS
application version. The image receives a unique immutable tag and services use
its resolved ECR digest.

## Release behavior

1. Tests and lint run before obtaining deployment credentials.
2. The control image is built and pushed. Exact old/new task definitions and the
   image digest are recorded in deployment_manifest.json.
3. A one-off VPC task runs Alembic. Deployment proceeds only after observing exit
   code zero. An uncertain timeout leaves the task available for reconciliation.
4. Temporary engineering and connector workers start from their original image,
   application version, environment and IAM roles, using --drain. They retain the
   appropriate DBOS queue listeners and do not dispatch new commands or scheduled
   maintenance. Every old production image must support this flag.
5. A VPC-side DBOS query requires no PENDING, ENQUEUED or DELAYED workflows for the
   older versions for 30 seconds before replacing service replicas. Busy systems
   may require a quieter deployment window; the script does not cancel work to
   force a drain.
6. Engineering workers, connector workers and API roll forward in that order.
   Each ECS deployment must stabilize on its requested task definition; an ECS
   rollback is detected as failure. The public API health endpoint is checked.
7. A second old-version drain check closes the race with work admitted before
   old service replicas stopped. Only then are compatibility workers stopped.

Compatible Conductor recovery must be configured for interrupted workflows.
Native jobs retain their own pinned tool task definitions, networking and model
policy across control releases. Database migrations must be backward compatible
with the old executors while they drain. Destructive migrations require a separate
maintenance procedure; this workflow does not automate schema downgrades.

Terraform provisions the service configuration but ignores service task_definition
changes owned by releases. The release script uses the latest active revision of
each current task-definition family as its configuration base, then replaces only
the control image and DBOS application version. This picks up environment/IAM
changes registered by Terraform while preserving the previous running revision
for drainers. Keep control_image in reviewed infrastructure inputs aligned with
the successful release digest, and avoid unmanaged task-definition registrations.

## Interrupted releases

The workflow always uploads its deployment manifest when available. On failure,
temporary compatibility workers remain available; nothing automatically deletes
them or downgrades the database. Inspect ECS task outcomes and CloudWatch logs,
restore any failed compatible executor, and reconcile migration completion before
retrying. An interrupted workflow may have partially rolled services; consult
the manifest's exact task-definition ARNs and DBOS application versions. Do not
stop retained workers or delete old images until their workflows have drained.
Use a forward repair release where possible; rolling application code back also
requires retaining compatible executors for workflows created by the newer code.

## Cluster migration and signing keys

Keep previous cluster/network resources and exact native task-definition revisions
until their jobs finish. Configure recovery_cluster_arns,
recovery_task_definition_arns and recovery_execution_role_arns in Terraform before
moving the current cluster. These retain scoped recovery permissions and old-cluster
completion events. Reconciliation uses each job's pinned cluster, including
cancellation and uncertain submission discovery.
These recovery settings cover clusters in the same AWS region; regional moves
require a separate migration procedure.

The native callback token currently derives from the persistent session signing
key. Keep that key stable while pending, running or uncertain native jobs remain.
Drain before rotation; a versioned signing-key ring is not implemented. Bootstrap
preserves existing session keys rather than silently rotating them.

## Frontend delivery

Terraform provisions a private, versioned S3 bucket, CloudFront with origin access
control, a DNS-validated ACM certificate in us-east-1 and the frontend Route53
record. frontend_url must be an HTTPS hostname in public_zone_id and on the same
site as the API for cookie authentication. After a healthy backend release, the
workflow uploads the production Flutter bundle, publishes index.html last, then
invalidates CloudFront and waits for completion. Old objects are retained to
support clients with older bundles. Frontend failure does not roll back a migrated
backend; rerun the release after investigating.

Initial provisioning still requires DNS/TLS inputs, control and qualified native
image digests, encrypted remote Terraform state, bootstrap, runtime provider keys,
migrations and activation as described in infra/README.md. Do not enable automatic
releases until that first deployment is healthy and GitHub variables are configured.

## Shared simulation deployment

The same serialized release job deploys mock-backend with mock-backend/deploy.sh
after the healthy control release and before publishing the frontend. Set
SIMULATION_ECR_REPOSITORY to the simulation_ecr_repository Terraform output and
SIMULATION_API_URL to simulation_api_url. The frontend keeps KLEOS_BACKEND=production
and additionally compiles KLEOS_SIMULATION_API_BASE_URL.

The simulation uses a distinct ECS service, immutable image and task definition.
It has no task role or runtime secrets. Its endpoint /api is routed separately
from the authenticated /api/v1 backend. This is public, shared, in-memory demo
data; deployment/reset clears simulated progress. Service updates use stop-before-
start to keep exactly one authoritative demo instance. The workflow checks the
requested revision to detect an ECS rollback and uploads a simulation manifest.
