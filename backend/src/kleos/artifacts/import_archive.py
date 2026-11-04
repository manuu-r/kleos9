"""Atomic, bounded ZIP source import. Contents are data and are never extracted or executed."""
import hashlib
import io
import re
import stat
import struct
import unicodedata
import zipfile
import zlib
from datetime import UTC, datetime
from pathlib import PurePosixPath
from uuid import uuid4

from kleos.artifacts.service import Artifact, artifact_view, decode
from kleos.artifacts.storage import source_path
from kleos.contracts.models import DomainError
from kleos.projects.store import fingerprint

MAX_ARCHIVE_BYTES = 8 * 1024 * 1024
MAX_SOURCE_BYTES = 4 * 1024 * 1024
MAX_TOTAL_BYTES = 16 * 1024 * 1024
MAX_ENTRIES = 256
MAX_COMPRESSION_RATIO = 200
CENTRAL = struct.Struct('<4s6H3I5H2I')
END = struct.Struct('<4s4H2IH')


def _invalid(message='The ZIP archive is invalid or unsupported', code='archive_invalid'):
    return DomainError(message, code, 422)


def _path(value):
    value = source_path(value)
    if (unicodedata.normalize('NFC', value) != value or ':' in value
            or any(unicodedata.category(character) in {'Cc', 'Cf', 'Cs'} for character in value)
            or any(part.endswith((' ', '.')) or re.fullmatch(r'(?i)(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', part)
                for part in value.split('/'))):
        raise _invalid('Archive paths must be portable unambiguous source paths', 'archive_path_invalid')
    return value


def _portable_prefixes(paths):
    prefixes = {}
    for path in paths:
        components = path.split('/')
        for length in range(1, len(components) + 1):
            prefix = '/'.join(components[:length])
            key = unicodedata.normalize('NFC', prefix).casefold()
            if key in prefixes and prefixes[key] != prefix:
                raise _invalid('Archive directory casing or normalization is ambiguous', 'archive_path_conflict')
            prefixes[key] = prefix


def _central_directory(raw, path_validator=_path):
    """Bound entry count before ZipFile creates an object for every central record."""
    end_at = raw.rfind(b'PK\x05\x06', max(0, len(raw) - 65557))
    if end_at < 0 or end_at + END.size > len(raw):
        raise _invalid()
    signature, disk, central_disk, disk_count, count, size, start, comment = END.unpack_from(raw, end_at)
    if (disk or central_disk or disk_count != count or not 1 <= count <= MAX_ENTRIES
            or start == 0xffffffff or size == 0xffffffff or start + size != end_at
            or end_at + END.size + comment != len(raw) or not raw.startswith(b'PK\x03\x04')):
        raise _invalid('Only bounded single-disk non-ZIP64 archives are supported')
    cursor, total, entries, seen, file_paths, local_offsets = start, 0, [], set(), set(), set()
    while cursor < end_at:
        if len(entries) >= count or cursor + CENTRAL.size > end_at:
            raise _invalid()
        fields = CENTRAL.unpack_from(raw, cursor)
        sig, made, needed, flags, method, _, _, crc, compressed, length, names, extras, comments, entry_disk, _, attributes, local = fields
        boundary = cursor + CENTRAL.size + names + extras + comments
        if (sig != b'PK\x01\x02' or boundary > end_at or not names or entry_disk
                or needed >= 45 or flags & ~0x80e or method not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
                or compressed == 0xffffffff or length == 0xffffffff or local in local_offsets
                or local < 0 or local + 30 > start or raw[local:local + 4] != b'PK\x03\x04'):
            raise _invalid('Unsupported archive entry encoding, encryption, compression or layout')
        local_offsets.add(local)
        name_raw = raw[cursor + CENTRAL.size:cursor + CENTRAL.size + names]
        if b'\x00' in name_raw:
            raise _invalid('NUL archive names are not allowed', 'archive_path_invalid')
        name = name_raw.decode('utf-8' if flags & 0x800 else 'cp437', errors='strict')
        directory = name.endswith('/')
        path = path_validator(name[:-1] if directory else name)
        folded = path.casefold()
        if folded in seen or any(str(parent).casefold() in file_paths for parent in PurePosixPath(path).parents if str(parent) != '.'):
            raise _invalid('Archive contains duplicate or conflicting paths', 'archive_path_conflict')
        if not directory and any(previous.startswith(folded + '/') for previous in seen):
            raise _invalid('Archive contains file/directory conflicts', 'archive_path_conflict')
        seen.add(folded)
        mode = attributes >> 16 if made >> 8 == 3 else 0
        kind = stat.S_IFMT(mode)
        if kind not in {0, stat.S_IFDIR if directory else stat.S_IFREG} or attributes & 0x400:
            raise _invalid('Archive links and special files are not allowed', 'archive_entry_unsafe')
        if bool(attributes & 0x10) and not directory:
            raise _invalid('Ambiguous archive directory attributes', 'archive_entry_unsafe')
        if directory and length:
            raise _invalid('Directory entries cannot contain file bytes')
        if (length > MAX_SOURCE_BYTES or compressed > MAX_ARCHIVE_BYTES
                or length > max(1, compressed) * MAX_COMPRESSION_RATIO):
            raise _invalid('Archive entry exceeds its size or expansion bound', 'archive_limit')
        total += length
        if total > MAX_TOTAL_BYTES:
            raise _invalid('Expanded archive exceeds its total bound', 'archive_limit')
        if not directory:
            file_paths.add(folded)
        # ZIP64 and alternate Unicode path fields add ambiguous filename/size authority.
        extra_at, extra_end = cursor + CENTRAL.size + names, cursor + CENTRAL.size + names + extras
        while extra_at < extra_end:
            if extra_at + 4 > extra_end:
                raise _invalid()
            tag, extra_length = struct.unpack_from('<HH', raw, extra_at)
            extra_at += 4 + extra_length
            if extra_at > extra_end or tag in {0x0001, 0x7075}:
                raise _invalid('ZIP64 and alternate archive paths are not supported')
        entries.append((name, path, directory, length, crc))
        cursor = boundary
    if cursor != end_at or len(entries) != count or not file_paths:
        raise _invalid('Archive must contain bounded source files')
    _portable_prefixes([entry[1] for entry in entries])
    return entries


def read_archive(raw, *, _path_validator=_path):
    if not isinstance(raw, bytes) or len(raw) > MAX_ARCHIVE_BYTES:
        raise _invalid('Archive exceeds its upload bound', 'archive_limit')
    try:
        entries = _central_directory(raw, _path_validator)
        files, total = [], 0
        with zipfile.ZipFile(io.BytesIO(raw), 'r') as archive:
            infos = archive.infolist()
            if len(infos) != len(entries):
                raise _invalid()
            for info, (name, path, directory, length, crc) in zip(infos, entries, strict=True):
                if info.filename != name or info.file_size != length or info.CRC != crc:
                    raise _invalid('Archive filename or size authority disagrees')
                if directory:
                    continue
                content = bytearray()
                with archive.open(info, 'r') as stream:
                    while True:
                        chunk = stream.read(min(65536, MAX_SOURCE_BYTES + 1 - len(content)))
                        if not chunk:
                            break
                        content.extend(chunk)
                        total += len(chunk)
                        if len(content) > MAX_SOURCE_BYTES or total > MAX_TOTAL_BYTES:
                            raise _invalid('Expanded archive exceeds its bound', 'archive_limit')
                if len(content) != length:
                    raise _invalid('Archive entry size differs from its declared size')
                files.append((path, bytes(content)))
        return files
    except DomainError:
        raise
    except (zipfile.BadZipFile, zipfile.LargeZipFile, UnicodeError, ValueError, EOFError, RuntimeError,
            NotImplementedError, struct.error, zlib.error):
        raise _invalid() from None


class ArchiveImportService:
    def __init__(self, artifacts):
        self.artifacts, self.projects = artifacts, artifacts.projects

    def import_archive(self, actor, project_id, body, key, *, expected_revision=None):
        # Reject unauthorized callers before allocating decoded/uncompressed archive bytes.
        with self.projects.transaction() as session:
            self.projects.authorized(session, actor, project_id, write=True)
        if not isinstance(body, dict) or set(body) != {'file', 'sha256', 'expectedEngineeringRevision'}:
            raise _invalid('Use the existing artifact upload payload', 'invalid_request')
        file = body['file']
        if (not isinstance(file, dict) or set(file) - {'path', 'content', 'mediaType', 'encoding'}
                or file.get('encoding') != 'base64' or file.get('mediaType') != 'application/zip'):
            raise _invalid('Archive imports require an explicit base64 ZIP file', 'invalid_request')
        name = _path(file.get('path'))
        if not name.lower().endswith('.zip'):
            raise _invalid('Archive imports require a ZIP filename', 'invalid_request')
        raw = decode(file.get('content'), MAX_ARCHIVE_BYTES)
        digest = hashlib.sha256(raw).hexdigest()
        if body['sha256'] != digest:
            raise _invalid('Archive checksum differs from the supplied checksum', 'hash_mismatch')

        def operation(session):
            return self.publish_files(session, actor, project_id, read_archive(raw), name, digest,
                body['expectedEngineeringRevision'], expected_revision=expected_revision)

        return self.projects._command(actor, key, {'type': 'project.importArchive', 'projectId': project_id,
            'archiveName': name, 'sha256': digest, 'expectedEngineeringRevision': body['expectedEngineeringRevision'],
            'expectedRevision': expected_revision}, operation)

    def publish_files(self, session, actor, project_id, files, name, digest, revision, *,
                      expected_revision=None, provenance=None, original_paths=None):
        """Trusted parser output only. The caller owns the surrounding command transaction."""
        project = self.projects.authorized(session, actor, project_id, write=True)
        if (type(revision) is not int or revision != project.context_revision
                or (expected_revision is not None and expected_revision != project.revision)):
            raise DomainError('Project changed before archive import', 'revision_conflict')
        manifest = dict(project.data.get('sourceManifest', {}))
        paths = {unicodedata.normalize('NFC', path).casefold() for path in manifest}
        imported = {path.casefold() for path, _ in files}
        if len(manifest) + len(files) + len(project.data.get('repositoryContextManifest', {})) > MAX_ENTRIES:
            raise _invalid('Project source count exceeds the import bound', 'archive_limit')
        if any(path in paths or any(path.startswith(prior + '/') or prior.startswith(path + '/') for prior in paths)
                for path in imported):
            raise DomainError('Archive import is add-only; an existing source path conflicts', 'source_conflict')
        _portable_prefixes([*manifest, *(path for path, _ in files)])
        history = list(project.data.get('archiveImports', []))
        if len(history) >= 64:
            raise _invalid('Archive import history reached its bound', 'archive_limit')
        project.advance_revision()
        import_id, views = str(uuid4()), []
        for path, content in files:
            source_hash = self.artifacts.blobs.put(content)
            row = Artifact(id=str(uuid4()), project_id=project.id, revision=project.context_revision,
                name=path, sha256=source_hash, size=len(content), media_type='application/octet-stream',
                status='input', lane='research', details={'origin': 'user_upload', 'actorId': actor.user_id,
                    'archiveImportId': import_id, 'archiveSha256': digest,
                    'archivePath': (original_paths or {}).get(path, path),
                    **({'repository': provenance} if provenance else {})})
            session.add(row)
            manifest[path] = row.id
            views.append(artifact_view(row))
        record = {'id': import_id, 'archiveName': name, 'archiveSha256': digest, 'fileCount': len(files),
            'sourceHashes': fingerprint({view['name']: view['sha256'] for view in views}),
            'revision': project.context_revision, 'actorId': actor.user_id, 'createdAt': datetime.now(UTC).isoformat(),
            **({'repository': provenance} if provenance else {})}
        project.data = {**project.data, 'sourceManifest': manifest, 'archiveImports': [*history, record]}
        self.projects._emit(session, project, 'artifact.archive_imported', {'import': record, 'artifacts': views})
        return {'projectId': project.id, 'revision': project.revision}
