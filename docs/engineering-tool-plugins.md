# CAD and PCB tool adapters

The defaults remain **build123d** for mechanical generation and **atopile / KiCad**
for PCB generation. Tool selection is independent of Gemini/Meta selection.
Project writers choose installed adapters under **Connectors → Engineering tools**.
New builds use the defaults unless an explicit `toolPolicy` is supplied at creation.

## Public contract

- `GET /api/v1/engineering-tools` returns organization-visible tools, versions,
  installation/qualification status, limitations and `defaultPolicy`.
- `project.create` accepts an optional `toolPolicy`.
- `project.setToolPolicy` accepts `projectId` and the complete policy below, using
  the normal expected revision and durable idempotency key.

```json
{
  "mechanics": {"id": "build123d", "version": "1"},
  "electronics": {"id": "atopile-kicad", "version": "1"}
}
```

Saved choices apply to the next attempt. Active attempts keep their selected
contract and resolved worker identity. Tool choices survive requirements changes.
Changing tools does not accept artifacts or bypass engineering confirmation.

Attempts, artifact provenance, job requests and the run inspector retain the tool
ID/version, policy version and contract/installation hashes. Custom installations
cannot change under the same qualified version. A new version must be registered
for a different installation. Retain old worker images/task definitions until
pinned work has drained.

Explicit **Rebuild saved sources** keeps its original source contract. For the
default tools it may use the current worker image, records that executed identity,
and requires new independent checks. Custom adapters retain their exact qualified
installation; changing the source format requires a new engineering attempt.

## Install an alternative

Installation and qualification are operator actions. Users cannot submit image
names, shell commands, validators or qualification claims through project APIs.
The manifest is server configuration, not an executable project upload.

1. Implement the selected source contract and package the toolchain in an isolated
   worker image containing the current Kleos native supervisor.
2. Add a versioned `EngineeringToolDefinition` to `KLEOS_ENGINEERING_TOOLS` as a JSON
   array. Initially keep `qualified: false`. Optional `tenantIds` restricts the
   adapter to specific organizations; an empty list makes it globally visible.
3. Install its worker under the key `id@version` in `KLEOS_DOCKER_NATIVE_IMAGES`
   or `KLEOS_ECS_TASK_DEFINITIONS`. Custom Docker workers require an image ID or
   `repository@sha256:...`; ECS workers require an explicit task-definition revision.
4. Independently exercise valid/invalid source, supported formats, missing tools,
   resource limits, cancellation/recovery, output hashes and engineering checks.
   Record the report and immutable installation identity.
5. Set `qualified: true` with its `qualificationReference`, then restart compatible
   API/workers. The adapter becomes selectable in its declared lane. Qualification
   means the adapter contract was reviewed; it does not pre-approve designs.

Compose loads non-secret `backend/docker/native-defaults.env` before
`backend/.env`. Override the complete image map in `.env` to add an adapter while
retaining the five existing worker entries. Never put provider keys into tool
manifests or native images.

For Terraform, add the image digest to `native_images["id@version"]` and the manifest
to `engineering_tools`. Terraform provisions the same restricted Fargate shape,
exact RunTask permissions, private networking and no generated-code task role.
Default worker names are preserved. No infrastructure is deployed by these examples.

## Image-owned plugin entry point

The `plugin` runner executes exactly:

```text
/opt/kleos-tools/<id>/run --request <workspace>/.kleos-tool-request.json --output-dir <workspace>/outputs
```

The request contains schema version 1, tool ID/version, lane, accepted requirements
and interfaces, plus source filenames/hashes. Source bytes are already staged in
the working directory. The request excludes database/provider credentials, callback
credentials and dispatch configuration. The command runs under the generated-code
UID with the same CPU/memory/deadline limits, clean environment and network policy
as existing builds. No arbitrary project command is executed on the API host.

`outputs/` and `.kleos-tool-request.json` are reserved; a project cannot supply them
as inputs. The adapter must create every declared output afresh and exit zero.
Outputs must be explicit relative files under `outputs/`. Symlink escapes, missing
files, oversized files and undeclared PASS reports cannot publish candidates.
Files are bounded to 16 MiB each and the encoded output package to 32 MiB.

CAD plugins export `outputs/model.step` and `outputs/model.stl` in millimetres.
PCB plugins export one KiCad PCB and one matching schematic, plus declared
manufacturing outputs. Other native PCB formats need a qualified exporter before
the current independent validator can inspect them. Unsupported validation remains
`UNKNOWN`; it never becomes a generated success statement.

An adapter returns **candidate files only**. Independent validation always uses a
separate validator job/image. Generator-authored check results, stdout and reports
cannot satisfy release gates.

## Examples and limits

- [Parametric CAD template](../backend/examples/engineering-tools/parametric-cad/manifest.json):
  a working reference adapter consuming bounded JSON rather than generated Python.
  Its [image-owned entry point](../backend/examples/engineering-tools/parametric-cad/run)
  creates a rectangular open-top enclosure. It is deliberately not an arbitrary
  mechanical design engine. The shipped manifest stays unqualified by default.
- [Direct KiCad sources](../backend/examples/engineering-tools/direct-kicad.json):
  an alternative PCB source contract using KiCad directly, without atopile.
  The `kicad` runner retains ERC/DRC and Gerber/drill export. It also stays
  unqualified until installed and independently exercised.
- [Actual isolated CAD smoke result](../backend/verification/engineering-plugin-runtime.json):
  reference STEP/STL generation and a separate successful mechanical inspection;
  no hosted inference, accepted release or physical action.

The existing REST/MCP connector contracts remain for qualified read-only services.
They are not a back door for submitting CAD jobs or live commercial operations.
Hosted CAD/PCB APIs needing credentials, paid dispatch or webhooks require their
own qualified digital-job adapter rather than enabling arbitrary connector writes.

The current PCB validator executes KiCad ERC/DRC and independent netlist extraction,
but circuit correspondence to accepted interfaces remains unqualified. Those checks
remain required `UNKNOWN` and block release. Alternative generators do not remove
that limitation. See [KiCad CLI documentation](https://docs.kicad.org/10.0/en/cli/cli.html)
for the underlying commands.
