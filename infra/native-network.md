# Native execution network isolation

This change separates native engineering jobs from trusted API and worker
networking. It is Terraform source plus local mocked tests; it has not been
applied to AWS or qualified with a live image pull.

## Network boundary

Native tasks use a dedicated IPv4 VPC, `10.43.0.0/16`, with two subnets:
`10.43.0.0/24` and `10.43.1.0/24`. There is no internet gateway, NAT, public-IP
assignment, default route, transit gateway or IPv6 egress in that VPC.
The existing `10.42.0.0/16` VPC retains its API, trusted workers,
hosted simulation, private subnets and NAT/provider connectivity.

The internal callback ALB moves from the former native subnets into the existing
trusted private subnets. Same-account, same-region peering routes only those two
private subnet CIDRs from the native route table. Return routes cover only the
two native subnet CIDRs. Cross-VPC security-group references permit native TCP443
to the internal ALB, and ALB ingress from the native security group. Native tasks
have no ingress rules. Other main-VPC services do not gain native ingress.
Both VPCs are associated with the existing private hosted zone.

| Destination | Native permission |
|---|---|
| Callback ALB | TCP443 through the peer security group; job-scoped API authorization still required |
| Native ECR API/DKR endpoints | TCP443; approved execution roles, token acquisition and exact approved repositories only |
| Native Logs endpoint | TCP443; execution-role `CreateLogStream`/`PutLogEvents` to this application's `native-*` streams |
| S3 gateway endpoint | TCP443 to the S3 prefix list; only `GetObject` on the region's ECR starport layer bucket |
| Route 53 VPC Resolver | Exact original names below; all other names return NXDOMAIN |

Native artifacts now travel through the scoped callback contract. The native S3
endpoint no longer allows access to the application artifact bucket, including
signed transfers to it. ECR layer downloads use AWS's signed service-bucket
requests, so their S3 endpoint policy cannot require the native execution role as
the signing principal; its exception is limited to read-only access to the exact
regional layer bucket. Endpoint policies supplement IAM and do not grant an
application task role. The task execution role remains outside generated code.

## Endpoint and DNS policy

Both trusted and native ECR API/DKR endpoints have explicit matching policies.
`ecr:GetAuthorizationToken` has its required `Resource: "*"` in a separate
statement. Repository operations are limited to `BatchCheckLayerAvailability`,
`BatchGetImage` and `GetDownloadUrlForLayer`, with exact repository ARNs and exact
`aws:PrincipalArn` execution-role conditions. No push, delete or repository
administration action is admitted.

Native repository ARNs include the managed engineering repositories and custom
repositories derived from operator-configured `native_images`. Native images
must be digest-pinned private ECR images in this account and region; mirror
public, cross-account or cross-region images first. There is no free-form
repository wildcard or extra DNS exception variable. The trusted policy also
covers managed control/simulation repositories and explicit private ECR image
references in control/simulation configuration. Public images used by
trusted services continue to use their existing NAT path.

Logs policies permit only stream creation and event delivery. The trusted
endpoint admits the control/native execution roles and explicit recovery roles,
scoped to the existing application log group. The native endpoint admits only
native/recovery execution roles and `native-*` streams in that group. There is no
log reading, log group creation or arbitrary destination.

Route 53 DNS Firewall is associated only with the dedicated native VPC; its VPC
scope is why a separate VPC is necessary. The allow rule accepts these exact
original queries:

- `jobs.<base_domain>` and the internal ALB's exact AWS hostname.
- `api.ecr.<region>.<aws_dns_suffix>` and this account's exact regional DKR registry.
- `logs.<region>.<aws_dns_suffix>`.
- The regional ECR starport bucket's exact regional, legacy-region and global S3 hostnames.
- The provisioned native interface endpoints' exact regional/zonal DNS names.
  DKR wildcard entries returned by AWS are narrowed to this account's registry.

No `*.amazonaws.com`, arbitrary subdomain, model-provider name or broad public
resolver name is allowed. A lower-priority `*` block rule returns NXDOMAIN.
`firewall_fail_open = "DISABLED"` also blocks resolution on firewall failures.
The allow rule uses `TRUST_REDIRECTION_DOMAIN` only for this exact allowlist:
AWS/the private-zone operator controls its CNAME chains, which may change without
being exposed in endpoint DNS entries. Trusting those chains does not allow an
arbitrary original query. Routes, security groups, endpoint policies and IAM
continue to constrain the resulting connections. No outbound UDP/TCP53,
TCP853 or arbitrary HTTPS destination is allowed by the native security group;
external DNS, DoT and DoH cannot provide an unrestricted alternate egress path.

Network policy is a task-level boundary. The generated-code UID restrictions
implemented separately remain necessary to isolate it from the supervisor's
callback authority and any other permitted task-level facilities.

## Deployment migration and qualification

This is a network replacement, not an in-place subnet change. The native subnets,
security group, route table and native S3 endpoint move to another VPC; new native
interface endpoints, DNS firewall resources and peering are added. The trusted
interface endpoints gain restrictive policies. The main private route table keeps
its NAT route and adds callback return routes using the same inline route owner.

Before an eventual deployment:

1. Pause new native dispatch and drain/reconcile existing native tasks, including
   cancelled tasks whose termination has not yet been observed. Stored job
   requests pin old subnet/security-group IDs; replacing those resources while
   old submissions remain recoverable would break recovery. Retain old resources
   explicitly if draining is not possible; do not remove them under active jobs.
2. Review the AWS plan, CIDR availability, quotas, endpoint/DNS Firewall costs,
   ECR image location requirements and repository policies. Move the internal ALB
   to trusted private subnets before retiring its old subnets; account for a
   callback connection interruption during that move.
3. Wait for private endpoints, peering routes, peer security-group rules, hosted
   zone associations and the complete DNS firewall association to be ready before
   resuming native dispatch. Worker deployment must depend on that network gate.
4. Use a canary execution task to prove an approved image pull, permitted native
   log stream and scoped callback. Verify the exact runtime DNS chain, including
   ECR S3 layers, and preservation of trusted provider traffic.
5. Verify arbitrary/encoded subdomains and provider domains return NXDOMAIN;
   denied repository principals/repositories cannot pull; other log groups and
   log-read APIs fail; arbitrary S3 writes and direct non-callback main-VPC
   connections fail. Generated-code isolation must pass independently.
6. Resume queued native work only after these checks. A network rollback needs
   the old network resources and pinned job metadata; restoring an unrestricted
   endpoint policy or broad DNS wildcard is not a rollback procedure.

Local checks use `terraform fmt`, `terraform validate -no-color`, and
`terraform test -filter=tests/native_network.tftest.hcl`. The test providers are
mocked and use disposable in-memory state; they evaluate policies, separation,
DNS allowlist, routing and rejection of an unapproved registry without AWS API
calls. They do not establish live AWS reachability or image compatibility.

References: [ECR private endpoints](https://docs.aws.amazon.com/AmazonECR/latest/userguide/vpc-endpoints.html),
[CloudWatch Logs endpoint policies](https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/cloudwatch-logs-and-interface-VPC.html),
[DNS Firewall fail-closed configuration](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/resolver-dns-firewall-vpc-configuration.html),
[peer security-group references](https://docs.aws.amazon.com/vpc/latest/peering/vpc-peering-security-groups.html).
