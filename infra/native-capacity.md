# Native worker capacity and resource profiles

`backend/kleos/native-resources.json` is the shared, version-controlled default
configuration loaded by backend Settings and Terraform. It describes resource
ceilings, not tool qualification or measured AWS prices.

| Profile | CPU units | Memory MiB | Docker temporary storage MiB | Process limit | Fargate ephemeral GiB |
|---|---:|---:|---:|---:|---:|
| mechanics | 1024 | 2048 | 1024 | 256 | 40 |
| electronics | 2048 | 4096 | 2048 | 256 | 40 |
| firmware | 2048 | 4096 | 2048 | 256 | 40 |
| application | 4096 | 8192 | 6144 | 256 | 40 |
| validate | 2048 | 4096 | 2048 | 256 | 40 |
| `_default` | 2048 | 4096 | 2048 | 256 | 40 |

1024 CPU units is one vCPU. A native image key uses its matching profile, or
`_default` for a custom engineering tool without a dedicated resource profile.
`native_resource_overrides` merges complete five-field profiles over the shared
defaults. Other profiles remain present. Keys can be a built-in lane, `_default`
or a qualified tool's `id@version`. For example:

```hcl
native_resource_overrides = {
  "custom.cad@1" = {
    cpuUnits            = 2048
    memoryMiB           = 8192
    tmpMiB              = 2048
    pidsLimit           = 512
    ephemeralStorageGiB = 60
  }
}
```

Terraform verifies positive integer values, valid Fargate CPU/memory pairs through
16 vCPU, `tmpMiB <= memoryMiB`, 32–1024 processes and 21–200 GiB of ephemeral
storage. The 256 CPU-unit profile permits only 512, 1024 or 2048 MiB memory;
1536 MiB is not supported. The full effective map is supplied to the API/workers
as `KLEOS_NATIVE_RESOURCE_PROFILES`, and native task definitions use the matching
CPU, memory and ephemeral size. The task uses `nproc` and the generated-process
supervisor limit; Fargate does not have Docker's `PidsLimit` parameter.

For the non-application Docker lanes, `tmpMiB` is a temporary-filesystem limit.
New application Docker jobs (sandbox policy3) use a private anonymous disk volume
at `/tmp`; copying the 4.75 GiB Android SDK/dependency working set into tmpfs
exhausted the local 7.65 GiB Docker host before compilation. Disk scratch allows
the kernel to reclaim cached pages. Container cleanup requests anonymous-volume
removal and preserves the shared named control volume. Legacy jobs retain their
original scratch policy. Docker's anonymous volume has no hard byte quota;
`tmpMiB` is informational for this path. CPU, memory, process, deadline, source
and output ceilings remain enforced. This local backend is for development.
Generated application build/session processes receive a bounded 4,096-descriptor
limit for Gradle's classpath; other lanes retain 256. Descriptor or JVM heap
exhaustion is a resource failure and does not trigger a billed code repair.

Fargate does not support
`linuxParameters.tmpfs`: its `/tmp` remains a bind mount backed by the task's
ephemeral storage. Therefore `tmpMiB` is recorded in the resource contract but is
not a separate hard `/tmp` quota on Fargate. The task memory/ephemeral limits,
generated-process limits and artifact/output ceilings remain independent. This
application root filesystem is read-only, like every other native lane. Before
Flutter executes, the supervisor copies the image's SDK and dependency cache to
the individual job's `/tmp` workspace and directs tool writes there. Fargate backs
that workspace with its private ephemeral volume; another job does not reuse it.
The baked SDK and dependency cache remain immutable. Runtime preparation and the
actual Flutter build must be qualified together with the selected image; the
Terraform assertion alone establishes configuration, not build compatibility.

The admission defaults are eight native jobs globally and four per project.
Operators can change `max_native_jobs` and `max_native_jobs_per_project`; both
must be positive integers and the project limit cannot exceed the global limit.
Queued work does not require dedicated native compute. Docker hosts also need
enough actual resources for the configured concurrency; these AWS-oriented
defaults do not certify capacity on a development laptop.

`native_job_reservation_usd` defaults to $0.10 for each native attempt. It is an
admission allowance reservation, not a promise that an AWS job costs $0.10 or a
hard AWS spend cap. Project/daily monetary ceilings remain necessary, and
reconciliation/settlement uses the durable job ledger. Expensive or long-running
tool profiles need measured pricing and appropriately configured allowances.

Control services now wait for the complete native DNS firewall association,
callback DNS record, peering routes, callback security-group rules, endpoint
egress rules and S3 endpoint before starting. This avoids dispatch during partial
network provisioning. See [native-network.md](native-network.md) for the required
network migration and live AWS qualification. Existing jobs must retain their
pinned resource/image/version contract during recovery; changes apply to new jobs.

Verification is local: Terraform formatting/validation and mocked tests cover
default sizing, fallback, overrides, runtime configuration and invalid profiles.
No task definition or service was deployed to AWS for these checks.

AWS references: [Fargate CPU/memory combinations](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/task_definition_parameters.html),
[Fargate parameter limitations](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/fargate-tasks-services.html),
[ephemeral storage limits](https://docs.aws.amazon.com/AmazonECS/latest/APIReference/API_EphemeralStorage.html).
