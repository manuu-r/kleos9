"""Authority-bound independent firmware/application export checks; no build or functional claim."""
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from kleos.artifacts.service import Artifact
from kleos.contracts.models import DomainError
from kleos.execution.gateway import NativeJob
from kleos.execution.security import daemon_socket_profile
from kleos.projects.store import fingerprint
from kleos.validation.export_check import BOARD, MAX_FILE, MAX_TOTAL, OUTPUTS, PROTOCOL
from kleos.validation.export_process import CHECKER_RESOURCES
from kleos.validation.service import CheckerConfiguration, CheckReport

CHECKS = {'firmware': 'firmware.export-integrity', 'application': 'application.web-bundle-integrity'}
EXPECTED_CASES = {
    'firmware': {'valid_firmware': 'PASS', 'wrong_machine': 'FAIL', 'wrong_board': 'UNKNOWN',
        'truncated_elf': 'FAIL', 'binary_mismatch': 'FAIL', 'memory_overflow': 'FAIL',
        'invalid_vectors': 'FAIL', 'unsupported_section': 'UNKNOWN', 'digest_mismatch': 'FAIL',
        'parser_timeout': 'ERROR'},
    'application': {'valid_web': 'PASS', 'missing_bootstrap': 'FAIL', 'missing_font': 'FAIL',
        'invalid_crc': 'FAIL', 'path_traversal': 'FAIL', 'symlink': 'FAIL', 'duplicate_path': 'FAIL',
        'expansion_limit': 'FAIL', 'nul_filename': 'FAIL', 'file_directory_collision': 'FAIL',
        'case_prefix_collision': 'FAIL', 'alternate_zip_name': 'FAIL', 'duplicate_html_attribute': 'FAIL',
        'external_entry_resource': 'UNKNOWN', 'digest_mismatch': 'FAIL',
        'parser_timeout': 'ERROR'},
}


def source_hashes():
    return {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
            for name in ('exports.py', 'export_check.py', 'export_process.py', 'Dockerfile.exports')}


class ExportChecker:
    def __init__(self, repository, blobs, process, control_root, *, daemon_root=None):
        if process.lane not in CHECKS:
            raise ValueError('Unsupported export checker lane')
        self.repository, self.blobs, self.process = repository, blobs, process
        self.lane = process.lane
        self.root = Path(control_root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.daemon_root = Path(daemon_root).resolve() if daemon_root else self.root

    def _authority(self, scope, subject, outputs):
        with self.repository.store.transaction() as session:
            project, _ = self.repository._check(session, scope)
            source_hashes = {ref.path: ref.sha256 for ref in subject.sources}
            job_ids = set()
            bindings = []
            for ref in outputs:
                artifact = session.get(Artifact, ref.id)
                job = session.get(NativeJob, artifact.details.get('nativeJobId')) if artifact else None
                if (artifact is None or artifact.project_id != project.id or artifact.lane != self.lane
                        or artifact.status != 'candidate' or artifact.details.get('role') != 'output'
                        or artifact.details.get('origin') != 'native_candidate'
                        or job is None or job.project_id != project.id or job.run_id != scope.run_id
                        or job.request_hash != artifact.details.get('requestHash')
                        or fingerprint(job.request) != job.request_hash or job.request.get('lane') != self.lane
                        or job.task_id != artifact.details.get('taskId')
                        or job.request.get('imageDigest') != artifact.details.get('imageDigest')
                        or not job.source_manifest
                        or any(source_hashes.get(path) != checksum for path, checksum in job.source_manifest.items())):
                    raise DomainError('Export producer differs from the exact source authority', 'checker_scope')
                job_ids.add(job.id)
                bindings.append({'jobId': job.id, 'requestHash': job.request_hash,
                                 'sourceManifestHash': fingerprint(job.source_manifest)})
            if len(job_ids) != 1:
                raise DomainError('Export set must originate from one native build', 'checker_scope')
            target = None
            if self.lane == 'firmware':
                target = project.data.get('interfaces', {}).get('firmware')
                if target != {'board': BOARD}:
                    raise DomainError('Independent firmware target is not qualified', 'checker_scope')
                if (job.request.get('firmwareTarget') != target or job.request.get('board') != BOARD
                        or job.request.get('firmwareTargetHash') != fingerprint(target)):
                    raise DomainError('Firmware target differs from accepted interface', 'checker_scope')
            return {'firmwareTarget': target, 'producerBindings': bindings}

    async def invoke(self, scope, subject):
        if subject.project_id != scope.project_id or subject.revision != scope.revision:
            raise DomainError('Export checker subject changed', 'scope_conflict')
        refs = subject.sources+subject.outputs
        if await self.repository.resolve_artifacts(scope, [ref.id for ref in refs]) != refs:
            raise DomainError('Export checker binding changed', 'artifact_conflict')
        with self.repository.store.transaction() as session:
            self.repository._check(session, scope)
            outputs = [ref for ref in subject.outputs if session.get(Artifact, ref.id).lane == self.lane]
        if len(outputs) != len(OUTPUTS[self.lane]) or {ref.path for ref in outputs} != OUTPUTS[self.lane]:
            raise DomainError('Independent check requires the complete lane export set', 'checker_scope')
        authority = self._authority(scope, subject, outputs)
        ident = self.lane+'-check-'+uuid4().hex
        request = {'protocol': PROTOCOL, 'lane': self.lane, 'executionId': ident,
                   'subjectHash': subject.subject_hash, **authority, 'files': []}
        folder = Path(tempfile.mkdtemp(prefix='export-check-', dir=self.root))
        folder.chmod(0o755)
        try:
            total = 0
            for ref in outputs:
                content = self.blobs.get(ref.sha256)
                total += len(content)
                if (len(content) != ref.size or len(content) > MAX_FILE or total > MAX_TOTAL
                        or hashlib.sha256(content).hexdigest() != ref.sha256):
                    raise DomainError('Export checker byte bindings differ', 'checker_input')
                target = folder/ref.sha256
                target.write_bytes(content)
                target.chmod(0o644)
                request['files'].append({'path': ref.path, 'sha256': ref.sha256, 'size': ref.size})
            (folder/'request.json').write_text(json.dumps(request, allow_nan=False))
            (folder/'request.json').chmod(0o644)
            result = await self.process.run(self.daemon_root/folder.name, ident, project_id=scope.project_id,
                assert_active=lambda: self.repository.assert_active(scope), deadline_at=scope.deadline_at)
            await self.repository.assert_active(scope)
            if self._authority(scope, subject, outputs) != authority:
                raise DomainError('Export authority changed during check', 'stale_context')
            if (result.get('protocol') != PROTOCOL or result.get('lane') != self.lane
                    or result.get('subjectHash') != subject.subject_hash or result.get('executionId') != ident
                    or result.get('checkerSourceSha256') != source_hashes()['export_check.py']
                    or result.get('status') not in {'PASS', 'FAIL', 'UNKNOWN', 'ERROR'}
                    or not isinstance(result.get('measurements'), dict)):
                raise DomainError('Independent export result binding is invalid', 'checker_output')
            return CheckReport(subject_hash=subject.subject_hash, execution_id=ident, status=result['status'],
                summary=result.get('reason') or 'Independent digital export integrity only',
                measurements={**result['measurements'], 'authorityHash': fingerprint(authority),
                    'physicalAcceptance': False, 'sourceBuildReproduced': False})
        finally:
            shutil.rmtree(folder)


class FirmwareChecker(ExportChecker):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.lane != 'firmware':
            raise ValueError('Firmware checker requires its own lane')


class ApplicationChecker(ExportChecker):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.lane != 'application':
            raise ValueError('Application checker requires its own lane')


def install_export_checker(registry, adapter, evidence_path):
    try:
        descriptor = os.open(evidence_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > 2*1024**2:
                raise ValueError('Invalid evidence file')
            raw = stream.read(2*1024**2+1)
        evidence = json.loads(raw)
        review = evidence.get('independentReview', {})
        reviewed = datetime.fromisoformat(review.get('reviewedAt', ''))
        payload = {key: value for key, value in evidence.items() if key != 'independentReview'}
        valid = (len(raw) <= 2*1024**2
            and evidence.get('schemaVersion') == 'kleos-export-check-qualification-v1'
            and evidence.get('qualified') is True and evidence.get('lane') == adapter.lane
            and evidence.get('imageId') == adapter.process.image
            and re.fullmatch(r'sha256:[a-f0-9]{64}', adapter.process.image)
            and evidence.get('protocol') == PROTOCOL and evidence.get('sourceOverride') is False
            and evidence.get('deployment') == 'local' and evidence.get('containerChecksPassed') is True
            and evidence.get('networkPolicy') == 'docker-desktop-daemon-seccomp'
            and evidence.get('daemonSeccompSha256') == fingerprint(daemon_socket_profile())
            and evidence.get('watchdogSeconds') == 175 and adapter.process.timeout == 180
            and evidence.get('resources') == CHECKER_RESOURCES
            and evidence.get('sourceHashes') == source_hashes()
            and evidence.get('caseStatuses') == EXPECTED_CASES[adapter.lane]
            and isinstance(evidence.get('qualifiedBy'), str) and 1 <= len(evidence['qualifiedBy'].strip()) <= 200
            and isinstance(review.get('reviewer'), str) and 1 <= len(review['reviewer'].strip()) <= 200
            and reviewed.tzinfo is not None and review.get('evidenceSha256') == fingerprint(payload)
            and review.get('approved') is True and review['reviewer'] != evidence['qualifiedBy'])
        if not valid:
            raise ValueError('Qualification mismatch')
    except (OSError, ValueError, TypeError, AttributeError, KeyError):
        raise DomainError('Export checker requires exact-image evidence and independent review', 'checker_unqualified') from None
    config = CheckerConfiguration(key=CHECKS[adapter.lane], version='1', qualified=True,
        installation_hash=fingerprint({'image': adapter.process.image, 'evidence': fingerprint(evidence),
                                       'lane': adapter.lane, 'timeout': adapter.process.timeout}),
        timeout_seconds=adapter.process.timeout)
    registry.register(config, adapter.invoke)
    return config
