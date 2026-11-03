# Public commit source import

`project.importRepository` is an API-only command; the Flutter interface is unchanged.
Its payload is `projectId`, `repositoryUrl` (a public `https://github.com/owner/repo`
repository), `commit` (a complete lowercase 40-character commit SHA), and
`expectedEngineeringRevision`. The command envelope accepts `expectedRevision` and
requires `Idempotency-Key`. The response uses the existing command/project/revision
shape. No model, native program, Git command, dependency installer or imported code runs.

This implementation supports **bounded public commit sources plus an explicit
inert metadata set**, not arbitrary repositories. Root `.gitignore`, `.gitattributes`,
`.editorconfig` and `.github/**` text files are retained separately as advisory context.
Other dotfiles, `.git/**`, `.gitmodules`, private paths, symlinks and Git LFS pointers
are rejected instead of silently omitted. Private repositories, branch/tag resolution, submodule
fetching, Git history, arbitrary download hosts and LFS fetching are unsupported.
Unsupported metadata needs an explicit policy extension before general repository
support can be claimed. Existing ZIP uploads retain their original strict behavior.

The server constructs the fixed GitHub archive API path. A maximum of one redirect
may lead to the exact repository/commit on `codeload.github.com`. Each hop resolves
only that fixed hostname, rejects all results if any address is nonpublic, and
connects to one validated literal IP. The original fixed hostname remains the TLS
certificate identity and HTTP Host. There are no automatic redirects, transport
retries, cookies between hops, credentials, inherited proxies or ambient CA settings.
The default client retains normal certificate verification.

Downloads have a 30-second total bound, 5-second connection timeout, at most two
HTTP requests and 8 MiB of raw response bytes. Encoded HTTP bodies are refused.
The installed HTTPX/HTTPCore transport's `sni_hostname` behavior is covered with an
inert network backend that observes the actual TCP target and TLS verification
settings. This follows the [official HTTPCore extension contract](https://www.encode.io/httpcore/extensions/#sni_hostname)
and [HTTPX environment controls](https://www.python-httpx.org/environment_variables/).
Mocked protocol checks do not establish live GitHub availability.

ZIP validation retains the existing 4 MiB per-file, 16 MiB total expansion,
256-entry and 200:1 ratio limits. One enclosing provider directory is removed only
after validating the entire archive. Original paths, normalized source paths, commit,
raw archive SHA-256 and exact per-file content hashes are retained. The raw archive
is an immutable private blob. A GitHub commit identifies source contents, while
the compressed archive can be regenerated with different compression; both identities
are therefore recorded. See [GitHub archive stability](https://docs.github.com/en/repositories/working-with-files/using-files/downloading-source-code-archives#stability-of-source-code-archives).

`kleos2_repository_imports` records admission and the single download attempt before
network IO. Existing global command receipts are checked before fetching. No database
lock spans network IO. At most two unexpired downloads per actor and 64 import
requests per project are admitted. Account, organization membership, project write
authority, tombstones and revisions are checked before and after IO and on replay.
Final publication holds Project, Account and OrganizationMember locks in that order,
consistent with the existing preview path; revocation cannot commit between the
authorization check and publication.

Once downloaded bytes have a durable receipt, a restart can finish publication
without fetching again. Unknown or cancelled downloads never automatically repeat.
A new explicit idempotency key is required for another attempt. Files, source manifest,
import status, command receipt and event publish transactionally; immutable bytes can
remain unreferenced after a rejected publication. Existing paths are never overwritten.
Successful publication advances engineering context once and fences old objectives.

`project.cancelRepositoryImport` uses payload `{projectId, importKey}` and the
envelope's current `expectedRevision`, with its own idempotency key. Only the
request's actor can cancel a fetching, downloaded or uncertain import. Cancellation
immediately fences publication, records a durable event and advances the UI revision
without changing engineering context. The event explicitly records
`transportTerminationConfirmed: false`: an existing bounded read may finish, but
cannot publish. Completed imports cannot be cancelled to remove their sources.
`project.cancel` retains its existing objective/verification meaning.

Verification uses injected HTTP/DNS fixtures, restart/crash receipts, current-source
and deletion races, explicit cancellation, actual runtime HTTP composition, and an
opt-in disposable PostgreSQL test that checks account/member locks during publication.
No user repository or live provider is fetched by these tests.

## Readable metadata without native execution authority

Allowed metadata has a separate `repositoryContextManifest` and canonical
`repositoryContextHash`. Each immutable artifact retains its original path, provider
wrapper path, archive/commit identity and completed import receipt. Metadata is
limited to 64 UTF-8 files, 256 KiB per file and 2 MiB total, within the existing
archive and combined project-entry limits. Embedded NUL bytes, nested hidden paths
and known private names/extensions are rejected. Examples include `.github/.env`,
`.github/secrets/**`, `credentials.*`, `id_rsa*`, `*.pem`, `*.key`, `*.p12`, `*.pfx`,
`*.keystore` and `*.jks`. This path policy does not claim to detect arbitrary secrets
inside otherwise allowed text.

Project context exposes `repositoryMetadata` references and `repositoryMetadataHash`
separately from editable/native `artifacts`. The planner may request the registered
`repository.context_read@1.0.0` research tool with `paths`, `closureHash` and an
optional bounded `maxChars`. The reader rechecks live scope/account/member authority,
the closure, exact bytes and import provenance before returning literal excerpts.
Its PASS denotes retrieval only. Observations retain `instructionAuthority: false`,
`nativeInput: false` and `engineeringValidated: false`; repository comments,
workflows and instructions cannot approve actions or replace application policy.

Metadata IDs remain outside the generic native artifact resolver and source
manifest. They are not renamed to bypass native path rules. Existing qualified
native protocols and images are unchanged. The actual worker integration test
uses a mocked planner to inspect the separate inventory, invoke the read tool and
persist the returned excerpt/hash in a tool receipt, with no native call. Stale,
revoked, deleted, corrupted, oversized and conflicting metadata imports are
covered separately. A conflict rolls back source and metadata publication together;
no file is silently omitted.
