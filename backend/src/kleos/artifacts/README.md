# Private project artifacts

`service.py` owns authorized file uploads, normalized reference images and native
candidate publication. `storage.py` stores immutable hash-addressed bytes. Source
manifest changes advance engineering context and fence earlier runs; they do not
grant engineering validation or release authority. Generated/native content is
never executed on the API host.

`import_archive.py` adds an explicit `project.importArchive` command. Its payload
contains `projectId` plus the existing upload fields: `expectedEngineeringRevision`,
`sha256`, and `file` with a relative `.zip` name, `encoding: "base64"`,
`mediaType: "application/zip"` and encoded `content`. The command envelope can also
carry `expectedRevision`. The accepted response retains the existing
`commandId/projectId/revision` shape and starts no generation run.

Imports add source paths atomically. Exact bytes are stored as private immutable
blobs; each input artifact records its archive digest, import identity, original
path and submitting actor. An `artifact.archive_imported` event and bounded
`project.data.archiveImports` history identify the imported closure. Existing
paths are never overwritten, and unrelated requirements, interfaces, images and
approved visual concepts remain intact. The source-context revision advances
once for the whole import, invalidating old execution scopes and stale source
bindings. Idempotent replay creates no new files or revisions.

The parser supports single-disk, non-ZIP64 ZIP archives using stored or deflated
entries. Limits are 8 MiB compressed input, 4 MiB per source, 16 MiB total expanded
bytes, 256 entries/current project sources and a 200:1 per-entry expansion ratio.
Central-directory bounds are checked before the ZIP reader allocates entry
objects. Expansion is read in bounded chunks and CRC/declared size checks apply.

Traversal, absolute paths, hidden/private paths, backslashes, NULs, Windows device
names/streams, non-normalized Unicode, case-insensitive collisions, ambiguous
directory casing, links, special files, encryption and alternate Unicode-name
or ZIP64 metadata reject the entire archive. No paths are stripped or renamed,
and no dependency installation, recursive unpacking, repository cloning or
executable detection is performed. Source code remains inert data.

All database publication and its receipt/event occur in one authorized
transaction. A storage failure can leave an unreferenced immutable content blob;
it cannot publish a partial source manifest. Such blobs are not deleted as a
repair shortcut.

Ordinary ZIP artifact uploads remain opaque files. Import is explicitly requested
through the command API; the existing Flutter UI has no project archive picker,
and this change does not add or alter UI controls. Projects containing hidden
files such as `.gitignore`, `.git`, `.env` or unsupported archive formats need a
clean source-only ZIP prepared outside this importer. Accepted imports identify
source context; native tool path/library support and independent engineering
checks remain separate gates.

Focused tests cover malicious ZIP metadata, expansion bounds, exact bytes,
authorization/revisions/replay, database rollback and the real HTTP command path
with no model or generated-code execution.
